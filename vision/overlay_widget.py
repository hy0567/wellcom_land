"""
감지 결과 오버레이 위젯
QWebEngineView 위에 투명하게 겹쳐서 바운딩 박스/라벨을 표시
"""

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QFont, QPen, QBrush
from PyQt6.QtWidgets import QWidget

from .detector import DetectionResult


# 클래스별 색상 팔레트
_COLORS = [
    QColor(255, 56, 56),     # 빨강
    QColor(255, 157, 56),    # 주황
    QColor(255, 255, 56),    # 노랑
    QColor(56, 255, 56),     # 초록
    QColor(56, 255, 255),    # 시안
    QColor(56, 56, 255),     # 파랑
    QColor(255, 56, 255),    # 마젠타
    QColor(255, 149, 200),   # 핑크
    QColor(128, 255, 128),   # 연두
    QColor(128, 128, 255),   # 연보라
]


class DetectionOverlay(QWidget):
    """WebEngineView 위에 감지 결과를 오버레이로 표시"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._result: Optional[DetectionResult] = None
        self._visible_classes: Optional[list] = None  # None이면 전체 표시
        self._show_confidence = True
        self._show_labels = True
        self._font_size = 12
        self._box_thickness = 2
        self._info_text = ""

        # 마우스 이벤트가 아래 위젯(WebView)으로 통과되도록 설정
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")

    def update_detections(self, result: DetectionResult):
        """감지 결과 업데이트"""
        self._result = result
        count = len(result.boxes) if result else 0
        ms = result.inference_ms if result else 0
        self._info_text = f"Detections: {count} | {ms:.0f}ms"
        self.update()  # repaint 트리거

    def clear(self):
        """오버레이 초기화"""
        self._result = None
        self._info_text = ""
        self.update()

    def set_visible_classes(self, classes: Optional[list]):
        """표시할 클래스 필터 설정 (None이면 전체 표시)"""
        self._visible_classes = classes
        self.update()

    def set_show_confidence(self, show: bool):
        self._show_confidence = show
        self.update()

    def set_show_labels(self, show: bool):
        self._show_labels = show
        self.update()

    def paintEvent(self, event):
        """바운딩 박스, 라벨, 정보 텍스트 렌더링"""
        if self._result is None or not self._result.boxes:
            if self._info_text:
                self._paint_info_bar()
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        result = self._result
        widget_w = self.width()
        widget_h = self.height()
        frame_h, frame_w = result.frame_shape

        if frame_w == 0 or frame_h == 0:
            painter.end()
            return

        # 프레임 좌표 → 위젯 좌표 스케일
        scale_x = widget_w / frame_w
        scale_y = widget_h / frame_h

        font = QFont("Consolas", self._font_size)
        font.setBold(True)
        painter.setFont(font)

        for i, (box, label) in enumerate(zip(result.boxes, result.labels)):
            # 클래스 필터
            if self._visible_classes is not None and label not in self._visible_classes:
                continue

            x1, y1, x2, y2, conf, cls_id = box
            color = _COLORS[int(cls_id) % len(_COLORS)]

            # 스케일링
            sx1 = int(x1 * scale_x)
            sy1 = int(y1 * scale_y)
            sx2 = int(x2 * scale_x)
            sy2 = int(y2 * scale_y)

            # 바운딩 박스
            pen = QPen(color, self._box_thickness)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(sx1, sy1, sx2 - sx1, sy2 - sy1)

            # 라벨 텍스트
            if self._show_labels:
                text = label
                if self._show_confidence:
                    text = f"{label} {conf:.2f}"

                fm = painter.fontMetrics()
                text_w = fm.horizontalAdvance(text) + 8
                text_h = fm.height() + 4

                # 라벨 배경
                bg_color = QColor(color)
                bg_color.setAlpha(180)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(bg_color))
                label_y = max(0, sy1 - text_h)
                painter.drawRect(sx1, label_y, text_w, text_h)

                # 라벨 텍스트
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(sx1 + 4, label_y + fm.ascent() + 2, text)

        painter.end()

        # 정보 바 그리기
        self._paint_info_bar()

    def _paint_info_bar(self):
        """화면 상단에 감지 정보 표시"""
        if not self._info_text:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        font = QFont("Consolas", 10)
        painter.setFont(font)
        fm = painter.fontMetrics()

        text_w = fm.horizontalAdvance(self._info_text) + 16
        text_h = fm.height() + 8

        # 반투명 배경
        bg = QColor(0, 0, 0, 160)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(bg))
        painter.drawRoundedRect(4, 4, text_w, text_h, 4, 4)

        # 텍스트
        painter.setPen(QColor(0, 255, 128))
        painter.drawText(12, 4 + fm.ascent() + 4, self._info_text)

        painter.end()
