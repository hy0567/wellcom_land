"""
PyQt6 기반 YOLO 라벨링 도구

labelImg 대체 - PyQt6 호환, 한글 UI, YOLO 형식 지원

사용법:
    python vision/training/labeler.py
    python vision/training/labeler.py --split train
    python vision/training/labeler.py --dir dataset/images/train
"""

import os
import sys
import argparse

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATASET_DIR = os.path.join(PROJECT_DIR, "dataset")

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem, QFileDialog,
    QSlider, QSplitter, QStatusBar, QComboBox, QMessageBox,
    QScrollArea, QGroupBox, QCheckBox
)
from PyQt6.QtCore import Qt, QRect, QPoint, QSize, pyqtSignal, QTimer
from PyQt6.QtGui import (
    QPixmap, QImage, QPainter, QPen, QColor, QBrush, QFont,
    QKeySequence, QShortcut, QAction, QWheelEvent, QMouseEvent,
    QPaintEvent, QResizeEvent, QCursor
)


# 클래스별 색상 팔레트
CLASS_COLORS = [
    QColor(255, 0, 0),       # 0 monster - 빨강
    QColor(255, 100, 0),     # 1 elite_monster - 주황
    QColor(200, 0, 200),     # 2 boss - 보라
    QColor(0, 200, 0),       # 3 npc - 초록
    QColor(0, 150, 255),     # 4 player - 파랑
    QColor(0, 255, 0),       # 5 hp_bar - 연두
    QColor(0, 100, 255),     # 6 mp_bar - 남색
    QColor(255, 255, 0),     # 7 skill_icon - 노랑
    QColor(100, 255, 100),   # 8 minimap - 연초록
    QColor(255, 200, 0),     # 9 quest_marker - 금색
    QColor(200, 200, 200),   # 10 chat_window - 회색
    QColor(0, 255, 255),     # 11 exp_bar - 시안
    QColor(255, 100, 255),   # 12 drop_item - 핑크
    QColor(255, 200, 100),   # 13 loot_bag - 살구
    QColor(200, 150, 0),     # 14 treasure_chest - 갈색
    QColor(0, 200, 150),     # 15 vendor - 청록
    QColor(150, 255, 50),    # 16 gather_node - 연두황
]


def load_classes(dataset_dir: str) -> list[str]:
    """classes.txt에서 클래스 목록 로드.
    classes.txt 형식: "monster (일반 몬스터)" → 그대로 표시용으로 사용
    """
    classes_file = os.path.join(dataset_dir, "classes.txt")
    if os.path.exists(classes_file):
        with open(classes_file, 'r', encoding='utf-8') as f:
            return [line.strip() for line in f if line.strip()]
    # fallback
    return [
        "monster (일반 몬스터)", "elite_monster (정예 몬스터)", "boss (보스)",
        "npc (NPC)", "player (플레이어)",
        "hp_bar (HP 바)", "mp_bar (MP 바)", "skill_icon (스킬 아이콘)",
        "minimap (미니맵)", "quest_marker (퀘스트 마커)",
        "chat_window (채팅창)", "exp_bar (경험치 바)",
        "drop_item (드롭 아이템)", "loot_bag (루팅 가방)", "treasure_chest (보물상자)",
        "vendor (잡화상인)", "gather_node (채집재료)"
    ]


class BBox:
    """바운딩 박스 (YOLO 정규화 좌표)"""

    def __init__(self, class_id: int, cx: float, cy: float, w: float, h: float):
        self.class_id = class_id
        self.cx = cx
        self.cy = cy
        self.w = w
        self.h = h

    def to_yolo_line(self) -> str:
        return f"{self.class_id} {self.cx:.6f} {self.cy:.6f} {self.w:.6f} {self.h:.6f}"

    @staticmethod
    def from_yolo_line(line: str) -> 'BBox':
        parts = line.strip().split()
        if len(parts) != 5:
            return None
        return BBox(int(parts[0]), float(parts[1]), float(parts[2]),
                    float(parts[3]), float(parts[4]))

    def to_pixel_rect(self, img_w: int, img_h: int) -> QRect:
        """정규화 좌표 → 픽셀 좌표 QRect"""
        x = int((self.cx - self.w / 2) * img_w)
        y = int((self.cy - self.h / 2) * img_h)
        w = int(self.w * img_w)
        h = int(self.h * img_h)
        return QRect(x, y, w, h)

    @staticmethod
    def from_pixel_rect(rect: QRect, img_w: int, img_h: int, class_id: int) -> 'BBox':
        """픽셀 좌표 QRect → 정규화 좌표"""
        cx = (rect.x() + rect.width() / 2) / img_w
        cy = (rect.y() + rect.height() / 2) / img_h
        w = rect.width() / img_w
        h = rect.height() / img_h
        return BBox(class_id, cx, cy, w, h)


class ImageCanvas(QWidget):
    """이미지 + 바운딩 박스 캔버스"""

    box_changed = pyqtSignal()  # 박스가 변경되었을 때

    HANDLE_SIZE = 6  # 리사이즈 핸들 크기

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._pixmap = None
        self._img_w = 0
        self._img_h = 0
        self._boxes: list[BBox] = []
        self._class_names: list[str] = []
        self._current_class_id = 0
        self._selected_box_idx = -1

        # 표시 관련
        self._zoom = 1.0
        self._offset_x = 0
        self._offset_y = 0
        self._fit_mode = True  # 창에 맞춤 모드

        # 드래그 상태
        self._drawing = False
        self._draw_start = None
        self._draw_end = None
        self._moving = False
        self._move_start = None
        self._resizing = False
        self._resize_handle = ""  # "tl", "tr", "bl", "br", "t", "b", "l", "r"
        self._resize_start_rect = None

        # 팬 (우클릭 드래그)
        self._panning = False
        self._pan_start = None

    def set_image(self, pixmap: QPixmap):
        self._pixmap = pixmap
        if pixmap:
            self._img_w = pixmap.width()
            self._img_h = pixmap.height()
        self._fit_mode = True
        self._selected_box_idx = -1
        self.update()

    def set_boxes(self, boxes: list[BBox]):
        self._boxes = boxes
        self._selected_box_idx = -1
        self.update()

    def set_class_names(self, names: list[str]):
        self._class_names = names

    def set_current_class(self, class_id: int):
        self._current_class_id = class_id

    def get_boxes(self) -> list[BBox]:
        return self._boxes

    def get_selected_index(self) -> int:
        return self._selected_box_idx

    def set_selected_index(self, idx: int):
        self._selected_box_idx = idx
        self.update()

    def delete_selected(self):
        if 0 <= self._selected_box_idx < len(self._boxes):
            self._boxes.pop(self._selected_box_idx)
            self._selected_box_idx = -1
            self.box_changed.emit()
            self.update()

    def change_selected_class(self, class_id: int):
        if 0 <= self._selected_box_idx < len(self._boxes):
            self._boxes[self._selected_box_idx].class_id = class_id
            self.box_changed.emit()
            self.update()

    # --- 좌표 변환 ---

    def _get_display_rect(self) -> QRect:
        """이미지가 표시되는 위젯 내 영역"""
        if not self._pixmap:
            return QRect()

        if self._fit_mode:
            # 위젯에 맞춤
            w_ratio = self.width() / self._img_w
            h_ratio = self.height() / self._img_h
            self._zoom = min(w_ratio, h_ratio)
            disp_w = int(self._img_w * self._zoom)
            disp_h = int(self._img_h * self._zoom)
            self._offset_x = (self.width() - disp_w) // 2
            self._offset_y = (self.height() - disp_h) // 2
        else:
            disp_w = int(self._img_w * self._zoom)
            disp_h = int(self._img_h * self._zoom)

        return QRect(self._offset_x, self._offset_y, disp_w, disp_h)

    def _widget_to_image(self, pos: QPoint) -> QPoint:
        """위젯 좌표 → 이미지 픽셀 좌표"""
        dr = self._get_display_rect()
        if dr.width() == 0 or dr.height() == 0:
            return QPoint(0, 0)
        ix = int((pos.x() - dr.x()) / self._zoom)
        iy = int((pos.y() - dr.y()) / self._zoom)
        ix = max(0, min(ix, self._img_w))
        iy = max(0, min(iy, self._img_h))
        return QPoint(ix, iy)

    def _image_to_widget(self, pos: QPoint) -> QPoint:
        """이미지 픽셀 좌표 → 위젯 좌표"""
        dr = self._get_display_rect()
        wx = int(pos.x() * self._zoom + dr.x())
        wy = int(pos.y() * self._zoom + dr.y())
        return QPoint(wx, wy)

    def _box_to_widget_rect(self, box: BBox) -> QRect:
        """BBox → 위젯 좌표 QRect"""
        pr = box.to_pixel_rect(self._img_w, self._img_h)
        tl = self._image_to_widget(pr.topLeft())
        br = self._image_to_widget(pr.bottomRight())
        return QRect(tl, br)

    def _hit_test_handle(self, pos: QPoint, box_idx: int) -> str:
        """마우스 위치가 리사이즈 핸들 위인지 확인"""
        if box_idx < 0 or box_idx >= len(self._boxes):
            return ""
        wr = self._box_to_widget_rect(self._boxes[box_idx])
        hs = self.HANDLE_SIZE

        corners = {
            "tl": wr.topLeft(),
            "tr": wr.topRight(),
            "bl": wr.bottomLeft(),
            "br": wr.bottomRight(),
        }
        for name, corner in corners.items():
            if abs(pos.x() - corner.x()) <= hs and abs(pos.y() - corner.y()) <= hs:
                return name

        # 변 핸들
        mid_t = QPoint(wr.center().x(), wr.top())
        mid_b = QPoint(wr.center().x(), wr.bottom())
        mid_l = QPoint(wr.left(), wr.center().y())
        mid_r = QPoint(wr.right(), wr.center().y())
        edges = {"t": mid_t, "b": mid_b, "l": mid_l, "r": mid_r}
        for name, mid in edges.items():
            if abs(pos.x() - mid.x()) <= hs and abs(pos.y() - mid.y()) <= hs:
                return name

        return ""

    def _hit_test_box(self, pos: QPoint) -> int:
        """마우스 위치에 있는 박스 인덱스 (-1: 없음)"""
        for i in range(len(self._boxes) - 1, -1, -1):
            wr = self._box_to_widget_rect(self._boxes[i])
            if wr.contains(pos):
                return i
        return -1

    # --- 이벤트 핸들러 ---

    def mousePressEvent(self, event: QMouseEvent):
        if not self._pixmap:
            return

        pos = event.pos()

        if event.button() == Qt.MouseButton.RightButton:
            # 우클릭: 팬
            self._panning = True
            self._pan_start = pos
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        # 선택된 박스의 핸들 확인
        if self._selected_box_idx >= 0:
            handle = self._hit_test_handle(pos, self._selected_box_idx)
            if handle:
                self._resizing = True
                self._resize_handle = handle
                self._resize_start_rect = self._boxes[self._selected_box_idx].to_pixel_rect(
                    self._img_w, self._img_h)
                self._move_start = self._widget_to_image(pos)
                return

        # 기존 박스 클릭 확인
        hit = self._hit_test_box(pos)
        if hit >= 0:
            self._selected_box_idx = hit
            self._moving = True
            self._move_start = self._widget_to_image(pos)
            self.box_changed.emit()
            self.update()
            return

        # 새 박스 그리기 시작
        self._selected_box_idx = -1
        self._drawing = True
        self._draw_start = self._widget_to_image(pos)
        self._draw_end = self._draw_start
        self.box_changed.emit()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if not self._pixmap:
            return

        pos = event.pos()

        if self._panning and self._pan_start:
            dx = pos.x() - self._pan_start.x()
            dy = pos.y() - self._pan_start.y()
            self._offset_x += dx
            self._offset_y += dy
            self._pan_start = pos
            self._fit_mode = False
            self.update()
            return

        if self._drawing:
            self._draw_end = self._widget_to_image(pos)
            self.update()
            return

        if self._moving and self._move_start:
            img_pos = self._widget_to_image(pos)
            dx = (img_pos.x() - self._move_start.x()) / self._img_w
            dy = (img_pos.y() - self._move_start.y()) / self._img_h
            box = self._boxes[self._selected_box_idx]
            box.cx = max(box.w / 2, min(1 - box.w / 2, box.cx + dx))
            box.cy = max(box.h / 2, min(1 - box.h / 2, box.cy + dy))
            self._move_start = img_pos
            self.update()
            return

        if self._resizing and self._resize_start_rect:
            img_pos = self._widget_to_image(pos)
            dx = img_pos.x() - self._move_start.x()
            dy = img_pos.y() - self._move_start.y()
            r = QRect(self._resize_start_rect)
            h = self._resize_handle

            if "l" in h:
                r.setLeft(r.left() + dx)
            if "r" in h:
                r.setRight(r.right() + dx)
            if "t" in h:
                r.setTop(r.top() + dy)
            if "b" in h:
                r.setBottom(r.bottom() + dy)

            # 최소 크기 보장
            if r.width() >= 5 and r.height() >= 5:
                norm = r.normalized()
                box = self._boxes[self._selected_box_idx]
                box.cx = (norm.x() + norm.width() / 2) / self._img_w
                box.cy = (norm.y() + norm.height() / 2) / self._img_h
                box.w = norm.width() / self._img_w
                box.h = norm.height() / self._img_h
            self.update()
            return

        # 커서 모양 변경
        if self._selected_box_idx >= 0:
            handle = self._hit_test_handle(pos, self._selected_box_idx)
            if handle in ("tl", "br"):
                self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
            elif handle in ("tr", "bl"):
                self.setCursor(QCursor(Qt.CursorShape.SizeBDiagCursor))
            elif handle in ("t", "b"):
                self.setCursor(QCursor(Qt.CursorShape.SizeVerCursor))
            elif handle in ("l", "r"):
                self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))
            else:
                hit = self._hit_test_box(pos)
                if hit >= 0:
                    self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
                else:
                    self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        else:
            hit = self._hit_test_box(pos)
            if hit >= 0:
                self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            else:
                self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.RightButton:
            self._panning = False
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._drawing and self._draw_start and self._draw_end:
            # 새 박스 생성
            x1 = min(self._draw_start.x(), self._draw_end.x())
            y1 = min(self._draw_start.y(), self._draw_end.y())
            x2 = max(self._draw_start.x(), self._draw_end.x())
            y2 = max(self._draw_start.y(), self._draw_end.y())

            if (x2 - x1) >= 5 and (y2 - y1) >= 5:
                rect = QRect(x1, y1, x2 - x1, y2 - y1)
                new_box = BBox.from_pixel_rect(rect, self._img_w, self._img_h,
                                               self._current_class_id)
                self._boxes.append(new_box)
                self._selected_box_idx = len(self._boxes) - 1
                self.box_changed.emit()

        if self._moving or self._resizing:
            self.box_changed.emit()

        self._drawing = False
        self._draw_start = None
        self._draw_end = None
        self._moving = False
        self._move_start = None
        self._resizing = False
        self._resize_handle = ""
        self._resize_start_rect = None
        self.update()

    def wheelEvent(self, event: QWheelEvent):
        if not self._pixmap:
            return

        # 줌
        delta = event.angleDelta().y()
        old_zoom = self._zoom
        if delta > 0:
            self._zoom = min(5.0, self._zoom * 1.15)
        else:
            self._zoom = max(0.1, self._zoom / 1.15)

        if self._fit_mode:
            self._fit_mode = False
            # 현재 표시 위치 기준으로 offset 계산
            dr = self._get_display_rect()
            self._offset_x = dr.x()
            self._offset_y = dr.y()

        # 마우스 위치를 기준으로 줌
        mouse_pos = event.position().toPoint()
        factor = self._zoom / old_zoom
        self._offset_x = int(mouse_pos.x() - (mouse_pos.x() - self._offset_x) * factor)
        self._offset_y = int(mouse_pos.y() - (mouse_pos.y() - self._offset_y) * factor)

        self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete or event.key() == Qt.Key.Key_Backspace:
            self.delete_selected()
        elif event.key() == Qt.Key.Key_F:
            self._fit_mode = True
            self.update()
        super().keyPressEvent(event)

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 배경
        painter.fillRect(self.rect(), QColor(40, 40, 40))

        if not self._pixmap:
            painter.setPen(QColor(150, 150, 150))
            painter.setFont(QFont("", 14))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "이미지를 선택하세요")
            painter.end()
            return

        # 이미지 그리기
        dr = self._get_display_rect()
        painter.drawPixmap(dr, self._pixmap)

        # 바운딩 박스 그리기
        for i, box in enumerate(self._boxes):
            wr = self._box_to_widget_rect(box)
            color = CLASS_COLORS[box.class_id % len(CLASS_COLORS)]

            is_selected = (i == self._selected_box_idx)

            # 박스 테두리
            pen_width = 3 if is_selected else 2
            pen = QPen(color, pen_width)
            if is_selected:
                pen.setStyle(Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 30)))
            painter.drawRect(wr)

            # 클래스 라벨
            cls_name = self._class_names[box.class_id] if box.class_id < len(self._class_names) else f"cls{box.class_id}"
            label_text = f"{cls_name}"
            font = QFont("", 9, QFont.Weight.Bold)
            painter.setFont(font)
            fm = painter.fontMetrics()
            text_w = fm.horizontalAdvance(label_text) + 8
            text_h = fm.height() + 4

            label_rect = QRect(wr.left(), wr.top() - text_h, text_w, text_h)
            if label_rect.top() < dr.top():
                label_rect.moveTop(wr.top())

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawRect(label_rect)

            painter.setPen(QColor(255, 255, 255))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label_text)

            # 선택된 박스의 리사이즈 핸들
            if is_selected:
                hs = self.HANDLE_SIZE
                handles = [
                    wr.topLeft(), wr.topRight(), wr.bottomLeft(), wr.bottomRight(),
                    QPoint(wr.center().x(), wr.top()),
                    QPoint(wr.center().x(), wr.bottom()),
                    QPoint(wr.left(), wr.center().y()),
                    QPoint(wr.right(), wr.center().y()),
                ]
                painter.setPen(QPen(QColor(255, 255, 255), 1))
                painter.setBrush(QBrush(color))
                for hp in handles:
                    painter.drawRect(hp.x() - hs, hp.y() - hs, hs * 2, hs * 2)

        # 그리는 중인 박스
        if self._drawing and self._draw_start and self._draw_end:
            p1 = self._image_to_widget(self._draw_start)
            p2 = self._image_to_widget(self._draw_end)
            color = CLASS_COLORS[self._current_class_id % len(CLASS_COLORS)]
            pen = QPen(color, 2, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 40)))
            painter.drawRect(QRect(p1, p2).normalized())

        painter.end()

    def resizeEvent(self, event: QResizeEvent):
        if self._fit_mode:
            self.update()


class LabelerWindow(QMainWindow):
    """메인 라벨링 윈도우"""

    def __init__(self, img_dir: str, lbl_dir: str, classes: list[str]):
        super().__init__()
        self.img_dir = img_dir
        self.lbl_dir = lbl_dir
        self.classes = classes
        self.images: list[str] = []
        self.current_idx = -1
        self.modified = False

        self._init_ui()
        self._load_image_list()
        self._setup_shortcuts()

        if self.images:
            self._go_to(0)

    def _init_ui(self):
        self.setWindowTitle("YOLO 라벨링 도구")
        self.resize(1400, 900)
        self.setStyleSheet("""
            QMainWindow { background: #2b2b2b; }
            QLabel { color: #ddd; }
            QPushButton {
                background: #3c3f41; color: #ddd; border: 1px solid #555;
                padding: 5px 12px; border-radius: 3px; font-size: 12px;
            }
            QPushButton:hover { background: #4c5052; }
            QPushButton:pressed { background: #2d6099; }
            QPushButton:checked { background: #365880; border-color: #4a88c7; }
            QListWidget {
                background: #2b2b2b; color: #ddd; border: 1px solid #555;
                font-size: 11px;
            }
            QListWidget::item:selected { background: #365880; }
            QListWidget::item:hover { background: #3c3f41; }
            QComboBox {
                background: #3c3f41; color: #ddd; border: 1px solid #555;
                padding: 4px 8px; border-radius: 3px; font-size: 12px;
            }
            QComboBox:hover { border-color: #4a88c7; }
            QComboBox QAbstractItemView { background: #3c3f41; color: #ddd; }
            QGroupBox {
                color: #aaa; border: 1px solid #555; border-radius: 4px;
                margin-top: 8px; padding-top: 16px; font-size: 11px;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
            QStatusBar { background: #1a1a1a; color: #aaa; font-size: 11px; }
            QSlider::groove:horizontal {
                background: #555; height: 4px; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #4a88c7; width: 14px; height: 14px;
                margin: -5px 0; border-radius: 7px;
            }
        """)

        # 중앙 위젯
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # 좌측: 이미지 목록
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_header = QLabel("이미지 목록")
        left_header.setStyleSheet("font-weight: bold; font-size: 12px; padding: 4px;")
        left_layout.addWidget(left_header)

        self.image_list = QListWidget()
        self.image_list.currentRowChanged.connect(self._on_image_selected)
        left_layout.addWidget(self.image_list)

        # 이미지 목록 하단 정보
        self.list_info_label = QLabel("")
        self.list_info_label.setStyleSheet("font-size: 10px; color: #888; padding: 2px 4px;")
        left_layout.addWidget(self.list_info_label)

        left_panel.setFixedWidth(200)
        main_layout.addWidget(left_panel)

        # 중앙: 캔버스
        self.canvas = ImageCanvas()
        self.canvas.set_class_names(self.classes)
        self.canvas.box_changed.connect(self._on_box_changed)
        main_layout.addWidget(self.canvas, 1)

        # 우측: 도구 패널
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        # 클래스 선택
        cls_group = QGroupBox("클래스 선택")
        cls_layout = QVBoxLayout(cls_group)

        self.class_combo = QComboBox()
        for i, name in enumerate(self.classes):
            color = CLASS_COLORS[i % len(CLASS_COLORS)]
            self.class_combo.addItem(f"{i}: {name}")
        self.class_combo.currentIndexChanged.connect(self._on_class_changed)
        cls_layout.addWidget(self.class_combo)

        # 클래스 빠른 선택 버튼 (0-9)
        cls_btn_layout = QHBoxLayout()
        for i in range(min(10, len(self.classes))):
            btn = QPushButton(str(i))
            btn.setFixedSize(28, 28)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {CLASS_COLORS[i].name()}; color: white;
                    font-weight: bold; font-size: 11px; border: none; border-radius: 3px;
                }}
                QPushButton:hover {{ opacity: 0.8; }}
            """)
            btn.clicked.connect(lambda checked, idx=i: self._select_class(idx))
            cls_btn_layout.addWidget(btn)
        cls_layout.addLayout(cls_btn_layout)

        right_layout.addWidget(cls_group)

        # 박스 목록
        box_group = QGroupBox("바운딩 박스")
        box_layout = QVBoxLayout(box_group)

        self.box_list = QListWidget()
        self.box_list.currentRowChanged.connect(self._on_box_list_selected)
        box_layout.addWidget(self.box_list)

        box_btn_layout = QHBoxLayout()
        btn_del = QPushButton("삭제 (Del)")
        btn_del.clicked.connect(self.canvas.delete_selected)
        box_btn_layout.addWidget(btn_del)

        btn_change = QPushButton("클래스 변경")
        btn_change.clicked.connect(self._change_box_class)
        box_btn_layout.addWidget(btn_change)
        box_layout.addLayout(box_btn_layout)

        btn_clear = QPushButton("전체 삭제")
        btn_clear.setStyleSheet("QPushButton { color: #ff6b6b; }")
        btn_clear.clicked.connect(self._clear_all_boxes)
        box_layout.addWidget(btn_clear)

        right_layout.addWidget(box_group)

        # 네비게이션
        nav_group = QGroupBox("네비게이션")
        nav_layout = QVBoxLayout(nav_group)

        nav_btn_layout = QHBoxLayout()
        btn_prev = QPushButton("◀ 이전 (A)")
        btn_prev.clicked.connect(self._prev_image)
        nav_btn_layout.addWidget(btn_prev)

        btn_next = QPushButton("다음 (D) ▶")
        btn_next.clicked.connect(self._next_image)
        nav_btn_layout.addWidget(btn_next)
        nav_layout.addLayout(nav_btn_layout)

        btn_save = QPushButton("저장 (Ctrl+S)")
        btn_save.setStyleSheet("QPushButton { background: #365880; font-weight: bold; }")
        btn_save.clicked.connect(self._save_labels)
        nav_layout.addWidget(btn_save)

        self.auto_save_check = QCheckBox("자동 저장 (이미지 이동 시)")
        self.auto_save_check.setChecked(True)
        self.auto_save_check.setStyleSheet("color: #aaa; font-size: 11px;")
        nav_layout.addWidget(self.auto_save_check)

        right_layout.addWidget(nav_group)

        # 도움말
        help_group = QGroupBox("단축키")
        help_layout = QVBoxLayout(help_group)
        shortcuts = [
            "A/D: 이전/다음 이미지",
            "0-9: 클래스 빠른 선택",
            "Del: 선택 박스 삭제",
            "F: 이미지 창에 맞춤",
            "Ctrl+S: 저장",
            "좌클릭 드래그: 새 박스",
            "좌클릭 박스: 선택/이동",
            "핸들 드래그: 크기 조절",
            "우클릭 드래그: 화면 이동",
            "마우스 휠: 확대/축소",
        ]
        for s in shortcuts:
            lbl = QLabel(s)
            lbl.setStyleSheet("font-size: 10px; color: #888;")
            help_layout.addWidget(lbl)
        right_layout.addWidget(help_group)

        right_layout.addStretch()

        right_panel.setFixedWidth(250)
        main_layout.addWidget(right_panel)

        # 상태 바
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("준비")

    def _setup_shortcuts(self):
        # Ctrl+S: 저장
        QShortcut(QKeySequence("Ctrl+S"), self, self._save_labels)
        # A: 이전
        QShortcut(QKeySequence("A"), self, self._prev_image)
        # D: 다음
        QShortcut(QKeySequence("D"), self, self._next_image)
        # 0-9: 클래스 선택
        for i in range(10):
            if i < len(self.classes):
                QShortcut(QKeySequence(str(i)), self, lambda idx=i: self._select_class(idx))

    def _load_image_list(self):
        """이미지 목록 로드"""
        if not os.path.exists(self.img_dir):
            return

        self.images = sorted([
            f for f in os.listdir(self.img_dir)
            if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp'))
        ])

        self.image_list.clear()
        for img_name in self.images:
            # 라벨 존재 여부 표시
            lbl_name = os.path.splitext(img_name)[0] + ".txt"
            lbl_path = os.path.join(self.lbl_dir, lbl_name)
            has_label = os.path.exists(lbl_path)

            # 라벨에 박스가 있는지 확인
            box_count = 0
            if has_label:
                with open(lbl_path, 'r') as f:
                    box_count = sum(1 for line in f if line.strip())

            prefix = f"[{box_count}]" if has_label else "[  ]"
            item = QListWidgetItem(f"{prefix} {img_name}")
            if has_label and box_count > 0:
                item.setForeground(QColor(100, 200, 100))
            elif has_label:
                item.setForeground(QColor(200, 200, 100))
            else:
                item.setForeground(QColor(200, 100, 100))
            self.image_list.addItem(item)

        # 통계
        labeled = sum(1 for img in self.images
                      if os.path.exists(os.path.join(
                          self.lbl_dir, os.path.splitext(img)[0] + ".txt")))
        self.list_info_label.setText(
            f"전체: {len(self.images)}장 | 라벨: {labeled}개")

    def _go_to(self, idx: int):
        """지정 인덱스 이미지로 이동"""
        if idx < 0 or idx >= len(self.images):
            return

        # 현재 이미지 자동 저장
        if self.modified and self.auto_save_check.isChecked():
            self._save_labels()

        self.current_idx = idx
        img_name = self.images[idx]
        img_path = os.path.join(self.img_dir, img_name)

        # 이미지 로드
        pixmap = QPixmap(img_path)
        if pixmap.isNull():
            self.status_bar.showMessage(f"이미지 로드 실패: {img_name}")
            return

        self.canvas.set_image(pixmap)

        # 라벨 로드
        lbl_name = os.path.splitext(img_name)[0] + ".txt"
        lbl_path = os.path.join(self.lbl_dir, lbl_name)
        boxes = []
        if os.path.exists(lbl_path):
            with open(lbl_path, 'r') as f:
                for line in f:
                    box = BBox.from_yolo_line(line)
                    if box:
                        boxes.append(box)

        self.canvas.set_boxes(boxes)
        self.modified = False
        self._update_box_list()

        # 이미지 목록 동기화
        self.image_list.blockSignals(True)
        self.image_list.setCurrentRow(idx)
        self.image_list.blockSignals(False)

        self.status_bar.showMessage(
            f"[{idx + 1}/{len(self.images)}] {img_name} "
            f"({pixmap.width()}x{pixmap.height()}) - 박스: {len(boxes)}개")
        self.setWindowTitle(f"YOLO 라벨링 - {img_name}")

    def _on_image_selected(self, row: int):
        if row >= 0:
            self._go_to(row)

    def _on_class_changed(self, idx: int):
        self.canvas.set_current_class(idx)

    def _select_class(self, idx: int):
        if idx < len(self.classes):
            self.class_combo.setCurrentIndex(idx)

    def _on_box_changed(self):
        self.modified = True
        self._update_box_list()

    def _update_box_list(self):
        """박스 목록 UI 업데이트"""
        self.box_list.blockSignals(True)
        self.box_list.clear()
        boxes = self.canvas.get_boxes()
        for i, box in enumerate(boxes):
            cls_name = self.classes[box.class_id] if box.class_id < len(self.classes) else f"cls{box.class_id}"
            item = QListWidgetItem(f"#{i} {cls_name}")
            color = CLASS_COLORS[box.class_id % len(CLASS_COLORS)]
            item.setForeground(color)
            self.box_list.addItem(item)

        sel = self.canvas.get_selected_index()
        if 0 <= sel < self.box_list.count():
            self.box_list.setCurrentRow(sel)
        self.box_list.blockSignals(False)

    def _on_box_list_selected(self, row: int):
        self.canvas.set_selected_index(row)

    def _change_box_class(self):
        """선택된 박스의 클래스를 현재 선택된 클래스로 변경"""
        cls_id = self.class_combo.currentIndex()
        self.canvas.change_selected_class(cls_id)
        self._update_box_list()

    def _clear_all_boxes(self):
        reply = QMessageBox.question(
            self, "확인", "모든 바운딩 박스를 삭제하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.canvas.set_boxes([])
            self.modified = True
            self._update_box_list()

    def _save_labels(self):
        """현재 이미지의 라벨 저장"""
        if self.current_idx < 0 or self.current_idx >= len(self.images):
            return

        os.makedirs(self.lbl_dir, exist_ok=True)

        img_name = self.images[self.current_idx]
        lbl_name = os.path.splitext(img_name)[0] + ".txt"
        lbl_path = os.path.join(self.lbl_dir, lbl_name)

        boxes = self.canvas.get_boxes()
        lines = [box.to_yolo_line() for box in boxes]

        with open(lbl_path, 'w') as f:
            f.write('\n'.join(lines))

        self.modified = False
        self.status_bar.showMessage(
            f"저장 완료: {lbl_name} ({len(boxes)}개 박스)")

        # 이미지 목록 업데이트
        item = self.image_list.item(self.current_idx)
        if item:
            prefix = f"[{len(boxes)}]" if boxes else "[0]"
            item.setText(f"{prefix} {img_name}")
            if boxes:
                item.setForeground(QColor(100, 200, 100))
            else:
                item.setForeground(QColor(200, 200, 100))

    def _prev_image(self):
        if self.current_idx > 0:
            self._go_to(self.current_idx - 1)

    def _next_image(self):
        if self.current_idx < len(self.images) - 1:
            self._go_to(self.current_idx + 1)

    def closeEvent(self, event):
        if self.modified:
            reply = QMessageBox.question(
                self, "저장", "변경사항을 저장하시겠습니까?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                QMessageBox.StandardButton.Cancel)
            if reply == QMessageBox.StandardButton.Yes:
                self._save_labels()
            elif reply == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
        event.accept()


def main():
    parser = argparse.ArgumentParser(description="PyQt6 YOLO 라벨링 도구")
    parser.add_argument("--split", default="train", choices=["train", "val"],
                        help="라벨링할 분할 (기본: train)")
    parser.add_argument("--dir", default="", help="이미지 디렉터리 (직접 지정)")
    parser.add_argument("--labels", default="", help="라벨 디렉터리 (직접 지정)")
    args = parser.parse_args()

    if args.dir:
        img_dir = args.dir
        lbl_dir = args.labels if args.labels else img_dir.replace("images", "labels")
    else:
        img_dir = os.path.join(DATASET_DIR, "images", args.split)
        lbl_dir = os.path.join(DATASET_DIR, "labels", args.split)

    classes = load_classes(DATASET_DIR)

    print(f"[라벨링] 이미지: {img_dir}")
    print(f"[라벨링] 라벨: {lbl_dir}")
    print(f"[라벨링] 클래스: {len(classes)}개")
    print()

    app = QApplication(sys.argv)
    window = LabelerWindow(img_dir, lbl_dir, classes)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
