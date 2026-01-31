"""업데이트 확인 및 진행 다이얼로그"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QTextEdit, QMessageBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from .github_client import ReleaseInfo


class UpdateWorkerThread(QThread):
    """백그라운드 업데이트 작업 스레드"""
    progress = pyqtSignal(int, int)         # downloaded, total
    status_changed = pyqtSignal(str)        # status message
    finished = pyqtSignal(bool, str)        # success, message

    def __init__(self, checker, release_info):
        super().__init__()
        self.checker = checker
        self.release_info = release_info

    def run(self):
        self.status_changed.emit("다운로드 중...")
        success = self.checker.apply_update(
            self.release_info,
            progress_callback=lambda d, t: self.progress.emit(d, t)
        )
        if success:
            self.finished.emit(True, "업데이트가 완료되었습니다.\n프로그램을 재시작합니다.")
        else:
            self.finished.emit(False, "업데이트에 실패했습니다.\n기존 버전으로 실행합니다.")


class UpdateNotifyDialog(QDialog):
    """업데이트 알림 다이얼로그 (업데이트 확인 시 먼저 표시)"""

    def __init__(self, current_version: str, release_info: ReleaseInfo,
                 parent=None):
        super().__init__(parent)
        self.release_info = release_info
        self.setWindowTitle("WellcomLAND 업데이트")
        self.setFixedSize(450, 300)
        self._init_ui(current_version)

    def _init_ui(self, current_version: str):
        layout = QVBoxLayout(self)

        # 제목
        title = QLabel(f"새 버전이 있습니다!")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #4CAF50;")
        layout.addWidget(title)

        # 버전 정보
        ver_label = QLabel(
            f"현재 버전: v{current_version}\n"
            f"최신 버전: v{self.release_info.version}"
        )
        ver_label.setStyleSheet("font-size: 13px; margin: 5px 0;")
        layout.addWidget(ver_label)

        # 릴리스 노트
        notes_label = QLabel("변경사항:")
        notes_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        layout.addWidget(notes_label)

        notes = QTextEdit()
        notes.setReadOnly(True)
        notes.setMaximumHeight(100)
        notes.setPlainText(self.release_info.release_notes[:1000])
        notes.setStyleSheet("background: #2a2a2a; border: 1px solid #555;")
        layout.addWidget(notes)

        layout.addStretch()

        # 버튼
        btn_layout = QHBoxLayout()

        self.btn_skip = QPushButton("나중에")
        self.btn_skip.setStyleSheet(
            "padding: 8px 20px; background: #555; color: white; "
            "border-radius: 4px;"
        )
        self.btn_skip.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_skip)

        btn_layout.addStretch()

        self.btn_update = QPushButton("지금 업데이트")
        self.btn_update.setStyleSheet(
            "padding: 8px 20px; background: #4CAF50; color: white; "
            "border-radius: 4px; font-weight: bold;"
        )
        self.btn_update.clicked.connect(self.accept)
        btn_layout.addWidget(self.btn_update)

        layout.addLayout(btn_layout)


class UpdateDialog(QDialog):
    """업데이트 진행 다이얼로그 (다운로드 + 설치)"""

    update_completed = pyqtSignal(bool)  # 재시작 필요 여부

    def __init__(self, release_info: ReleaseInfo, parent=None):
        super().__init__(parent)
        self.release_info = release_info
        self._success = False
        self.setWindowTitle("WellcomLAND 업데이트")
        self.setFixedSize(450, 200)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint
        )
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 제목
        title = QLabel(f"v{self.release_info.version} 업데이트 설치 중...")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        # 진행률 바
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #555;
                border-radius: 4px;
                text-align: center;
                height: 25px;
            }
            QProgressBar::chunk {
                background: #4CAF50;
                border-radius: 3px;
            }
        """)
        layout.addWidget(self.progress_bar)

        # 상태 메시지
        self.status_label = QLabel("준비 중...")
        self.status_label.setStyleSheet("color: #aaa;")
        layout.addWidget(self.status_label)

        layout.addStretch()

    def start_update(self, checker):
        """업데이트 시작"""
        self.worker = UpdateWorkerThread(checker, self.release_info)
        self.worker.progress.connect(self._on_progress)
        self.worker.status_changed.connect(self._on_status)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, downloaded, total):
        percent = int(downloaded / total * 100) if total else 0
        self.progress_bar.setValue(percent)
        mb_down = downloaded / 1024 / 1024
        mb_total = total / 1024 / 1024
        self.status_label.setText(
            f"다운로드 중... {mb_down:.1f}MB / {mb_total:.1f}MB"
        )

    def _on_status(self, status):
        self.status_label.setText(status)

    def _on_finished(self, success, message):
        self._success = success
        self.status_label.setText(message)
        if success:
            self.progress_bar.setValue(100)
            self.update_completed.emit(True)
            self.accept()
        else:
            QMessageBox.warning(self, "업데이트 실패", message)
            self.reject()

    @property
    def is_success(self) -> bool:
        return self._success
