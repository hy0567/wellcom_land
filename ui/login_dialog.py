"""
WellcomLAND 로그인 다이얼로그
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QCheckBox, QMessageBox,
    QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QIcon, QFont, QPixmap

from config import settings, ICON_PATH
from api_client import api_client


class LoginThread(QThread):
    """백그라운드 로그인 스레드"""
    login_success = pyqtSignal(dict)
    login_failed = pyqtSignal(str)

    def __init__(self, username: str, password: str):
        super().__init__()
        self.username = username
        self.password = password

    def run(self):
        try:
            result = api_client.login(self.username, self.password)
            self.login_success.emit(result)
        except Exception as e:
            error_msg = str(e)
            if '401' in error_msg:
                self.login_failed.emit("아이디 또는 비밀번호가 올바르지 않습니다.")
            elif '연결' in error_msg or 'Connection' in error_msg:
                self.login_failed.emit("서버에 연결할 수 없습니다.\n네트워크 연결을 확인해주세요.")
            else:
                self.login_failed.emit(f"로그인 실패: {error_msg}")


class TokenVerifyThread(QThread):
    """저장된 토큰 검증 스레드"""
    verify_success = pyqtSignal()
    verify_failed = pyqtSignal()

    def run(self):
        if api_client.verify_token():
            self.verify_success.emit()
        else:
            self.verify_failed.emit()


class LoginDialog(QDialog):
    """로그인 다이얼로그"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._login_thread = None
        self._verify_thread = None
        self._logged_in = False
        self._init_ui()
        self._try_auto_login()

    def _init_ui(self):
        self.setWindowTitle("WellcomLAND 로그인")
        self.setFixedSize(400, 360)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        if ICON_PATH:
            import os
            if os.path.exists(ICON_PATH):
                self.setWindowIcon(QIcon(ICON_PATH))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(15)

        # 타이틀
        title = QLabel("WellcomLAND")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = QFont()
        title_font.setPointSize(20)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        subtitle = QLabel("지리는 KVM 가주왕")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #666;")
        layout.addWidget(subtitle)

        layout.addSpacing(10)

        # 구분선
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #ddd;")
        layout.addWidget(line)

        layout.addSpacing(5)

        # 아이디
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("아이디")
        self.username_input.setMinimumHeight(38)
        self.username_input.setStyleSheet("""
            QLineEdit {
                padding: 8px 12px;
                border: 1px solid #ccc;
                border-radius: 4px;
                font-size: 14px;
            }
            QLineEdit:focus {
                border: 1px solid #4a90d9;
            }
        """)
        # 저장된 사용자명 복원
        saved_username = settings.get('server.username', '')
        if saved_username:
            self.username_input.setText(saved_username)
        layout.addWidget(self.username_input)

        # 비밀번호
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("비밀번호")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setMinimumHeight(38)
        self.password_input.setStyleSheet("""
            QLineEdit {
                padding: 8px 12px;
                border: 1px solid #ccc;
                border-radius: 4px;
                font-size: 14px;
            }
            QLineEdit:focus {
                border: 1px solid #4a90d9;
            }
        """)
        self.password_input.returnPressed.connect(self._on_login)
        layout.addWidget(self.password_input)

        # 자동 로그인 체크박스
        self.auto_login_cb = QCheckBox("자동 로그인")
        self.auto_login_cb.setChecked(settings.get('server.auto_login', False))
        layout.addWidget(self.auto_login_cb)

        # 로그인 버튼
        self.login_btn = QPushButton("로그인")
        self.login_btn.setMinimumHeight(42)
        self.login_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a90d9;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #357abd;
            }
            QPushButton:pressed {
                background-color: #2a5f9e;
            }
            QPushButton:disabled {
                background-color: #ccc;
            }
        """)
        self.login_btn.clicked.connect(self._on_login)
        layout.addWidget(self.login_btn)

        # 상태 라벨
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #999; font-size: 12px;")
        layout.addWidget(self.status_label)

        # 포커스 설정
        if saved_username:
            self.password_input.setFocus()
        else:
            self.username_input.setFocus()

    def _try_auto_login(self):
        """자동 로그인 시도 (저장된 토큰 검증)"""
        token = settings.get('server.token', '')
        auto_login = settings.get('server.auto_login', False)

        if token and auto_login:
            self.status_label.setText("자동 로그인 중...")
            self.login_btn.setEnabled(False)
            self._verify_thread = TokenVerifyThread()
            self._verify_thread.verify_success.connect(self._on_auto_login_success)
            self._verify_thread.verify_failed.connect(self._on_auto_login_failed)
            self._verify_thread.start()

    def _on_auto_login_success(self):
        self._logged_in = True
        self.accept()

    def _on_auto_login_failed(self):
        self.status_label.setText("토큰 만료. 다시 로그인해주세요.")
        self.login_btn.setEnabled(True)
        self.password_input.setFocus()

    def _on_login(self):
        username = self.username_input.text().strip()
        password = self.password_input.text().strip()

        if not username:
            self.status_label.setText("아이디를 입력해주세요.")
            self.status_label.setStyleSheet("color: #e74c3c; font-size: 12px;")
            self.username_input.setFocus()
            return

        if not password:
            self.status_label.setText("비밀번호를 입력해주세요.")
            self.status_label.setStyleSheet("color: #e74c3c; font-size: 12px;")
            self.password_input.setFocus()
            return

        self.login_btn.setEnabled(False)
        self.status_label.setText("로그인 중...")
        self.status_label.setStyleSheet("color: #999; font-size: 12px;")

        self._login_thread = LoginThread(username, password)
        self._login_thread.login_success.connect(self._on_login_success)
        self._login_thread.login_failed.connect(self._on_login_failed)
        self._login_thread.start()

    def _on_login_success(self, result: dict):
        # 자동 로그인 설정 저장
        settings.set('server.auto_login', self.auto_login_cb.isChecked())
        self._logged_in = True
        self.accept()

    def _on_login_failed(self, error: str):
        self.login_btn.setEnabled(True)
        self.status_label.setText(error)
        self.status_label.setStyleSheet("color: #e74c3c; font-size: 12px;")
        self.password_input.setFocus()
        self.password_input.selectAll()

    @property
    def logged_in(self) -> bool:
        return self._logged_in
