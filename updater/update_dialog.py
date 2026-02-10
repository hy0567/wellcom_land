"""업데이트 다이얼로그 — 심플 버전"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QMessageBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from .github_client import ReleaseInfo


class UpdateWorkerThread(QThread):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(bool, str)

    def __init__(self, checker, release_info):
        super().__init__()
        self.checker = checker
        self.release_info = release_info

    def run(self):
        success = self.checker.apply_update(
            self.release_info,
            progress_callback=lambda d, t: self.progress.emit(d, t)
        )
        if success:
            self.finished.emit(True, "완료")
        else:
            self.finished.emit(False, "업데이트 실패")


class UpdateNotifyDialog(QDialog):
    """업데이트 알림 — 버전만 표시"""

    def __init__(self, current_version: str, release_info: ReleaseInfo,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("업데이트")
        self.setFixedSize(300, 130)
        self._init_ui(current_version, release_info)

    def _init_ui(self, current_version, release_info):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        ver = QLabel(f"v{current_version}  →  v{release_info.version}")
        ver.setStyleSheet("font-size: 15px; font-weight: bold;")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(ver)

        btn_layout = QHBoxLayout()
        btn_skip = QPushButton("나중에")
        btn_skip.setFixedWidth(80)
        btn_skip.clicked.connect(self.reject)
        btn_layout.addWidget(btn_skip)

        btn_layout.addStretch()

        btn_update = QPushButton("업데이트")
        btn_update.setFixedWidth(80)
        btn_update.setStyleSheet("background: #4CAF50; color: white; font-weight: bold;")
        btn_update.clicked.connect(self.accept)
        btn_layout.addWidget(btn_update)

        layout.addLayout(btn_layout)


class UpdateDialog(QDialog):
    """업데이트 진행 — 프로그레스바만"""

    update_completed = pyqtSignal(bool)

    def __init__(self, release_info: ReleaseInfo, parent=None):
        super().__init__(parent)
        self.release_info = release_info
        self._success = False
        self.setWindowTitle("업데이트")
        self.setFixedSize(300, 80)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint
        )
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("v%s  %%p%%" % self.release_info.version)
        layout.addWidget(self.progress_bar)

    def start_update(self, checker):
        self.worker = UpdateWorkerThread(checker, self.release_info)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, downloaded, total):
        percent = int(downloaded / total * 100) if total else 0
        self.progress_bar.setValue(percent)

    def _on_finished(self, success, message):
        self._success = success
        if success:
            self.progress_bar.setValue(100)
            self.update_completed.emit(True)
            self.accept()
        else:
            QMessageBox.warning(self, "오류", message)
            self.reject()

    @property
    def is_success(self) -> bool:
        return self._success
