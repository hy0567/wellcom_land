"""
WellcomLAND 장치 제어 패널
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QPushButton, QLabel, QLineEdit, QTextEdit,
    QSlider, QSpinBox, QComboBox, QGridLayout,
    QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent

from core.kvm_device import KVMDevice


class KeyboardWidget(QWidget):
    """가상 키보드 위젯"""

    key_pressed = pyqtSignal(str, int)  # key, modifiers

    def __init__(self):
        super().__init__()
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 수정자 키
        mod_layout = QHBoxLayout()

        self.ctrl_btn = QPushButton("Ctrl")
        self.ctrl_btn.setCheckable(True)
        mod_layout.addWidget(self.ctrl_btn)

        self.shift_btn = QPushButton("Shift")
        self.shift_btn.setCheckable(True)
        mod_layout.addWidget(self.shift_btn)

        self.alt_btn = QPushButton("Alt")
        self.alt_btn.setCheckable(True)
        mod_layout.addWidget(self.alt_btn)

        self.win_btn = QPushButton("Win")
        self.win_btn.setCheckable(True)
        mod_layout.addWidget(self.win_btn)

        layout.addLayout(mod_layout)

        # 기능키
        func_layout = QHBoxLayout()
        for i in range(1, 13):
            btn = QPushButton(f"F{i}")
            btn.setFixedWidth(40)
            btn.clicked.connect(lambda checked, k=f"f{i}": self._on_key_click(k))
            func_layout.addWidget(btn)
        layout.addLayout(func_layout)

        # 특수키
        special_layout = QHBoxLayout()

        keys = [("Esc", "esc"), ("Tab", "tab"), ("Enter", "enter"),
                ("Backspace", "backspace"), ("Space", "space")]

        for label, key in keys:
            btn = QPushButton(label)
            btn.clicked.connect(lambda checked, k=key: self._on_key_click(k))
            special_layout.addWidget(btn)

        layout.addLayout(special_layout)

        # 방향키
        arrow_layout = QHBoxLayout()
        arrow_layout.addStretch()

        arrows = [("위", "up"), ("아래", "down"), ("왼쪽", "left"), ("오른쪽", "right")]
        for label, key in arrows:
            btn = QPushButton(label)
            btn.setFixedSize(50, 40)
            btn.clicked.connect(lambda checked, k=key: self._on_key_click(k))
            arrow_layout.addWidget(btn)

        arrow_layout.addStretch()
        layout.addLayout(arrow_layout)

        # 텍스트 입력
        text_layout = QHBoxLayout()
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("전송할 텍스트 입력...")
        text_layout.addWidget(self.text_input)

        send_btn = QPushButton("텍스트 전송")
        send_btn.clicked.connect(self._on_send_text)
        text_layout.addWidget(send_btn)

        layout.addLayout(text_layout)

    def _get_modifiers(self) -> int:
        """현재 수정자 상태 반환"""
        mod = 0
        if self.ctrl_btn.isChecked():
            mod |= 0x01
        if self.shift_btn.isChecked():
            mod |= 0x02
        if self.alt_btn.isChecked():
            mod |= 0x04
        if self.win_btn.isChecked():
            mod |= 0x08
        return mod

    def _on_key_click(self, key: str):
        """키 버튼 클릭 처리"""
        self.key_pressed.emit(key, self._get_modifiers())
        # 수정자 리셋
        self.ctrl_btn.setChecked(False)
        self.shift_btn.setChecked(False)
        self.alt_btn.setChecked(False)
        self.win_btn.setChecked(False)

    def _on_send_text(self):
        """텍스트 전송"""
        text = self.text_input.text()
        if text:
            for char in text:
                self.key_pressed.emit(char, 0)
            self.text_input.clear()


class MouseWidget(QWidget):
    """마우스 제어 위젯"""

    mouse_move = pyqtSignal(int, int)  # dx, dy
    mouse_click = pyqtSignal(str)  # button

    def __init__(self):
        super().__init__()
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 마우스 버튼
        btn_layout = QHBoxLayout()

        left_btn = QPushButton("왼쪽 클릭")
        left_btn.clicked.connect(lambda: self.mouse_click.emit("left"))
        btn_layout.addWidget(left_btn)

        middle_btn = QPushButton("휠 클릭")
        middle_btn.clicked.connect(lambda: self.mouse_click.emit("middle"))
        btn_layout.addWidget(middle_btn)

        right_btn = QPushButton("오른쪽 클릭")
        right_btn.clicked.connect(lambda: self.mouse_click.emit("right"))
        btn_layout.addWidget(right_btn)

        layout.addLayout(btn_layout)

        # 이동 컨트롤
        move_group = QGroupBox("이동")
        move_layout = QGridLayout(move_group)

        # 방향 버튼
        up_btn = QPushButton("위")
        up_btn.clicked.connect(lambda: self.mouse_move.emit(0, -10))
        move_layout.addWidget(up_btn, 0, 1)

        left_btn = QPushButton("왼쪽")
        left_btn.clicked.connect(lambda: self.mouse_move.emit(-10, 0))
        move_layout.addWidget(left_btn, 1, 0)

        right_btn = QPushButton("오른쪽")
        right_btn.clicked.connect(lambda: self.mouse_move.emit(10, 0))
        move_layout.addWidget(right_btn, 1, 2)

        down_btn = QPushButton("아래")
        down_btn.clicked.connect(lambda: self.mouse_move.emit(0, 10))
        move_layout.addWidget(down_btn, 2, 1)

        # 속도
        speed_layout = QHBoxLayout()
        speed_layout.addWidget(QLabel("속도:"))
        self.speed_spin = QSpinBox()
        self.speed_spin.setRange(1, 100)
        self.speed_spin.setValue(10)
        speed_layout.addWidget(self.speed_spin)
        move_layout.addLayout(speed_layout, 3, 0, 1, 3)

        layout.addWidget(move_group)


class DeviceControlPanel(QWidget):
    """장치 제어 패널"""

    def __init__(self):
        super().__init__()
        self.device: KVMDevice = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 장치 정보
        self.device_label = QLabel("선택된 장치 없음")
        self.device_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(self.device_label)

        # 구분선
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(line)

        # 키보드 섹션
        keyboard_group = QGroupBox("키보드")
        keyboard_layout = QVBoxLayout(keyboard_group)

        self.keyboard_widget = KeyboardWidget()
        self.keyboard_widget.key_pressed.connect(self._on_key_pressed)
        keyboard_layout.addWidget(self.keyboard_widget)

        layout.addWidget(keyboard_group)

        # 마우스 섹션
        mouse_group = QGroupBox("마우스")
        mouse_layout = QVBoxLayout(mouse_group)

        self.mouse_widget = MouseWidget()
        self.mouse_widget.mouse_move.connect(self._on_mouse_move)
        self.mouse_widget.mouse_click.connect(self._on_mouse_click)
        mouse_layout.addWidget(self.mouse_widget)

        layout.addWidget(mouse_group)

        # SSH 명령 섹션
        cmd_group = QGroupBox("SSH 명령")
        cmd_layout = QVBoxLayout(cmd_group)

        cmd_input_layout = QHBoxLayout()
        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText("SSH 명령 입력...")
        self.cmd_input.returnPressed.connect(self._on_execute_command)
        cmd_input_layout.addWidget(self.cmd_input)

        exec_btn = QPushButton("실행")
        exec_btn.clicked.connect(self._on_execute_command)
        cmd_input_layout.addWidget(exec_btn)

        cmd_layout.addLayout(cmd_input_layout)

        self.cmd_output = QTextEdit()
        self.cmd_output.setReadOnly(True)
        self.cmd_output.setMaximumHeight(150)
        self.cmd_output.setStyleSheet("font-family: 'Consolas', monospace;")
        cmd_layout.addWidget(self.cmd_output)

        layout.addWidget(cmd_group)

        layout.addStretch()

    def set_device(self, device: KVMDevice):
        """현재 장치 설정"""
        self.device = device
        if device:
            status = "연결됨" if device.is_connected() else "연결 안됨"
            self.device_label.setText(f"{device.name} ({device.ip}) - {status}")
        else:
            self.device_label.setText("선택된 장치 없음")

    def _on_key_pressed(self, key: str, modifiers: int):
        """키 입력 처리"""
        if not self.device or not self.device.is_connected():
            return

        self.device.send_key(key, modifiers)

    def _on_mouse_move(self, dx: int, dy: int):
        """마우스 이동 처리"""
        if not self.device or not self.device.is_connected():
            return

        speed = self.mouse_widget.speed_spin.value()
        self.device.send_mouse_relative(dx * speed // 10, dy * speed // 10)

    def _on_mouse_click(self, button: str):
        """마우스 클릭 처리"""
        if not self.device or not self.device.is_connected():
            return

        self.device.mouse_click(button)

    def _on_execute_command(self):
        """SSH 명령 실행"""
        if not self.device or not self.device.is_connected():
            self.cmd_output.setText("오류: 장치에 연결되어 있지 않습니다")
            return

        cmd = self.cmd_input.text()
        if not cmd:
            return

        out, err = self.device._exec_command(cmd)
        output = out if out else err
        self.cmd_output.setText(output)
        self.cmd_input.clear()
