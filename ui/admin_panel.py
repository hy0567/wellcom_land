"""
WellcomLAND 관리자 패널
사용자 관리 + 기기 관리 + 기기 할당
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLabel, QMessageBox, QDialog,
    QFormLayout, QLineEdit, QComboBox, QCheckBox,
    QDialogButtonBox, QListWidget, QListWidgetItem, QSpinBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from api_client import api_client


class LoadThread(QThread):
    """데이터 로드 스레드"""
    data_loaded = pyqtSignal(list)
    load_failed = pyqtSignal(str)

    def __init__(self, func):
        super().__init__()
        self._func = func

    def run(self):
        try:
            data = self._func()
            self.data_loaded.emit(data)
        except Exception as e:
            self.load_failed.emit(str(e))


class AdminPanel(QWidget):
    """관리자 패널 (탭 위젯 내부)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._threads = []
        self._init_ui()
        self._load_all_data()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        admin_tabs = QTabWidget()

        # 사용자 관리 탭
        self.user_tab = self._create_user_tab()
        admin_tabs.addTab(self.user_tab, "사용자 관리")

        # 기기 관리 탭
        self.device_tab = self._create_device_tab()
        admin_tabs.addTab(self.device_tab, "기기 관리")

        layout.addWidget(admin_tabs)

    # ===== 사용자 관리 =====
    def _create_user_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # 상단 버튼
        btn_layout = QHBoxLayout()
        add_btn = QPushButton("+ 사용자 추가")
        add_btn.clicked.connect(self._on_add_user)
        btn_layout.addWidget(add_btn)

        refresh_btn = QPushButton("새로고침")
        refresh_btn.clicked.connect(self._load_users)
        btn_layout.addWidget(refresh_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # 테이블
        self.user_table = QTableWidget()
        self.user_table.setColumnCount(7)
        self.user_table.setHorizontalHeaderLabels(
            ["ID", "아이디", "이름", "역할", "상태", "기기 할당", "작업"]
        )
        self.user_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.user_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.user_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.user_table)

        return tab

    def _load_users(self):
        t = LoadThread(api_client.admin_get_users)
        t.data_loaded.connect(self._on_users_loaded)
        t.load_failed.connect(lambda e: print(f"[Admin] 사용자 로드 실패: {e}"))
        self._threads.append(t)
        t.start()

    def _on_users_loaded(self, users: list):
        self.user_table.setRowCount(len(users))
        self._users = users

        for row, user in enumerate(users):
            self.user_table.setItem(row, 0, QTableWidgetItem(str(user['id'])))
            self.user_table.setItem(row, 1, QTableWidgetItem(user['username']))
            self.user_table.setItem(row, 2, QTableWidgetItem(user.get('display_name') or ''))
            self.user_table.setItem(row, 3, QTableWidgetItem(
                "관리자" if user['role'] == 'admin' else "사용자"
            ))

            status_item = QTableWidgetItem("활성" if user['is_active'] else "비활성")
            if not user['is_active']:
                status_item.setForeground(Qt.GlobalColor.red)
            self.user_table.setItem(row, 4, status_item)

            # 기기 할당 버튼
            assign_btn = QPushButton("기기 할당")
            assign_btn.clicked.connect(lambda checked, uid=user['id'], uname=user['username']: self._on_assign_devices(uid, uname))
            self.user_table.setCellWidget(row, 5, assign_btn)

            # 작업 버튼
            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(2, 2, 2, 2)

            edit_btn = QPushButton("수정")
            edit_btn.clicked.connect(lambda checked, u=user: self._on_edit_user(u))
            action_layout.addWidget(edit_btn)

            if user['username'] != 'admin':
                del_btn = QPushButton("삭제")
                del_btn.setStyleSheet("color: red;")
                del_btn.clicked.connect(lambda checked, uid=user['id'], uname=user['username']: self._on_delete_user(uid, uname))
                action_layout.addWidget(del_btn)

            self.user_table.setCellWidget(row, 6, action_widget)

    def _on_add_user(self):
        dlg = UserFormDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            try:
                api_client.admin_create_user(data)
                self._load_users()
            except Exception as e:
                QMessageBox.warning(self, "오류", f"사용자 추가 실패: {e}")

    def _on_edit_user(self, user: dict):
        dlg = UserFormDialog(self, user)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            try:
                api_client.admin_update_user(user['id'], data)
                self._load_users()
            except Exception as e:
                QMessageBox.warning(self, "오류", f"사용자 수정 실패: {e}")

    def _on_delete_user(self, user_id: int, username: str):
        reply = QMessageBox.question(
            self, "사용자 삭제",
            f"'{username}' 사용자를 삭제하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                api_client.admin_delete_user(user_id)
                self._load_users()
            except Exception as e:
                QMessageBox.warning(self, "오류", f"삭제 실패: {e}")

    def _on_assign_devices(self, user_id: int, username: str):
        try:
            all_devices = api_client.admin_get_all_devices()
            assigned = api_client.admin_get_user_devices(user_id)
            assigned_ids = {d['id'] for d in assigned}
        except Exception as e:
            QMessageBox.warning(self, "오류", f"데이터 로드 실패: {e}")
            return

        dlg = DeviceAssignDialog(self, username, all_devices, assigned_ids)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            selected_ids = dlg.get_selected_ids()
            try:
                api_client.admin_assign_devices(user_id, selected_ids)
                self._load_users()
                QMessageBox.information(self, "완료", f"{len(selected_ids)}개 기기가 할당되었습니다.")
            except Exception as e:
                QMessageBox.warning(self, "오류", f"기기 할당 실패: {e}")

    # ===== 기기 관리 =====
    def _create_device_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        btn_layout = QHBoxLayout()
        add_btn = QPushButton("+ 기기 추가")
        add_btn.clicked.connect(self._on_add_device)
        btn_layout.addWidget(add_btn)

        refresh_btn = QPushButton("새로고침")
        refresh_btn.clicked.connect(self._load_devices)
        btn_layout.addWidget(refresh_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self.device_table = QTableWidget()
        self.device_table.setColumnCount(7)
        self.device_table.setHorizontalHeaderLabels(
            ["ID", "이름", "IP", "웹 포트", "그룹", "상태", "작업"]
        )
        self.device_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.device_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.device_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.device_table)

        return tab

    def _load_devices(self):
        t = LoadThread(api_client.admin_get_all_devices)
        t.data_loaded.connect(self._on_devices_loaded)
        t.load_failed.connect(lambda e: print(f"[Admin] 기기 로드 실패: {e}"))
        self._threads.append(t)
        t.start()

    def _on_devices_loaded(self, devices: list):
        self.device_table.setRowCount(len(devices))
        self._devices = devices

        for row, dev in enumerate(devices):
            self.device_table.setItem(row, 0, QTableWidgetItem(str(dev['id'])))
            self.device_table.setItem(row, 1, QTableWidgetItem(dev['name']))
            self.device_table.setItem(row, 2, QTableWidgetItem(dev['ip']))
            self.device_table.setItem(row, 3, QTableWidgetItem(str(dev['web_port'])))
            self.device_table.setItem(row, 4, QTableWidgetItem(dev.get('group_name') or 'default'))

            status_item = QTableWidgetItem("활성" if dev['is_active'] else "비활성")
            if not dev['is_active']:
                status_item.setForeground(Qt.GlobalColor.red)
            self.device_table.setItem(row, 5, status_item)

            # 작업 버튼
            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(2, 2, 2, 2)

            edit_btn = QPushButton("수정")
            edit_btn.clicked.connect(lambda checked, d=dev: self._on_edit_device(d))
            action_layout.addWidget(edit_btn)

            del_btn = QPushButton("삭제")
            del_btn.setStyleSheet("color: red;")
            del_btn.clicked.connect(lambda checked, did=dev['id'], dname=dev['name']: self._on_delete_device(did, dname))
            action_layout.addWidget(del_btn)

            self.device_table.setCellWidget(row, 6, action_widget)

    def _on_add_device(self):
        dlg = DeviceFormDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            try:
                api_client.admin_create_device(data)
                self._load_devices()
            except Exception as e:
                QMessageBox.warning(self, "오류", f"기기 추가 실패: {e}")

    def _on_edit_device(self, device: dict):
        dlg = DeviceFormDialog(self, device)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            try:
                api_client.admin_update_device(device['id'], data)
                self._load_devices()
            except Exception as e:
                QMessageBox.warning(self, "오류", f"기기 수정 실패: {e}")

    def _on_delete_device(self, device_id: int, name: str):
        reply = QMessageBox.question(
            self, "기기 삭제",
            f"'{name}' 기기를 삭제하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                api_client.admin_delete_device(device_id)
                self._load_devices()
            except Exception as e:
                QMessageBox.warning(self, "오류", f"삭제 실패: {e}")

    def _load_all_data(self):
        self._load_users()
        self._load_devices()


# ===== 다이얼로그 =====

class UserFormDialog(QDialog):
    """사용자 추가/수정 다이얼로그"""

    def __init__(self, parent=None, user=None):
        super().__init__(parent)
        self._user = user
        self._init_ui()

    def _init_ui(self):
        is_edit = self._user is not None
        self.setWindowTitle("사용자 수정" if is_edit else "사용자 추가")
        self.setFixedSize(350, 280)

        layout = QFormLayout(self)

        self.username_input = QLineEdit()
        if is_edit:
            self.username_input.setText(self._user['username'])
            self.username_input.setReadOnly(True)
        layout.addRow("아이디:", self.username_input)

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("변경 시에만 입력" if is_edit else "비밀번호")
        layout.addRow("비밀번호:", self.password_input)

        self.display_name_input = QLineEdit()
        if is_edit and self._user.get('display_name'):
            self.display_name_input.setText(self._user['display_name'])
        layout.addRow("표시 이름:", self.display_name_input)

        self.role_combo = QComboBox()
        self.role_combo.addItems(["user", "admin"])
        if is_edit:
            self.role_combo.setCurrentText(self._user['role'])
        layout.addRow("역할:", self.role_combo)

        if is_edit:
            self.active_cb = QCheckBox("활성")
            self.active_cb.setChecked(self._user.get('is_active', True))
            layout.addRow("상태:", self.active_cb)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_data(self) -> dict:
        data = {}
        if not self._user:
            data['username'] = self.username_input.text().strip()
        password = self.password_input.text()
        if password:
            data['password'] = password
        name = self.display_name_input.text().strip()
        if name:
            data['display_name'] = name
        data['role'] = self.role_combo.currentText()
        if self._user and hasattr(self, 'active_cb'):
            data['is_active'] = self.active_cb.isChecked()
        return data


class DeviceFormDialog(QDialog):
    """기기 추가/수정 다이얼로그"""

    def __init__(self, parent=None, device=None):
        super().__init__(parent)
        self._device = device
        self._init_ui()

    def _init_ui(self):
        is_edit = self._device is not None
        self.setWindowTitle("기기 수정" if is_edit else "기기 추가")
        self.setFixedSize(380, 350)

        layout = QFormLayout(self)

        self.name_input = QLineEdit()
        if is_edit:
            self.name_input.setText(self._device['name'])
        layout.addRow("이름:", self.name_input)

        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("192.168.68.xxx")
        if is_edit:
            self.ip_input.setText(self._device['ip'])
        layout.addRow("IP:", self.ip_input)

        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(self._device.get('port', 22) if is_edit else 22)
        layout.addRow("SSH 포트:", self.port_input)

        self.web_port_input = QSpinBox()
        self.web_port_input.setRange(1, 65535)
        self.web_port_input.setValue(self._device.get('web_port', 80) if is_edit else 80)
        layout.addRow("웹 포트:", self.web_port_input)

        self.username_input = QLineEdit()
        self.username_input.setText(self._device.get('username', 'root') if is_edit else 'root')
        layout.addRow("SSH 사용자:", self.username_input)

        self.password_input = QLineEdit()
        self.password_input.setText(self._device.get('password', 'luckfox') if is_edit else 'luckfox')
        layout.addRow("SSH 비밀번호:", self.password_input)

        self.desc_input = QLineEdit()
        if is_edit and self._device.get('description'):
            self.desc_input.setText(self._device['description'])
        layout.addRow("설명:", self.desc_input)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_data(self) -> dict:
        data = {
            'name': self.name_input.text().strip(),
            'ip': self.ip_input.text().strip(),
            'port': self.port_input.value(),
            'web_port': self.web_port_input.value(),
            'username': self.username_input.text().strip(),
            'password': self.password_input.text().strip(),
        }
        desc = self.desc_input.text().strip()
        if desc:
            data['description'] = desc
        return data


class DeviceAssignDialog(QDialog):
    """기기 할당 다이얼로그"""

    def __init__(self, parent, username: str, all_devices: list, assigned_ids: set):
        super().__init__(parent)
        self._all_devices = all_devices
        self._assigned_ids = assigned_ids
        self.setWindowTitle(f"'{username}' 기기 할당")
        self.setFixedSize(400, 450)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel("할당할 기기를 선택하세요:")
        layout.addWidget(info)

        self.device_list = QListWidget()
        for dev in self._all_devices:
            item = QListWidgetItem(f"{dev['name']} ({dev['ip']})")
            item.setData(Qt.ItemDataRole.UserRole, dev['id'])
            item.setCheckState(
                Qt.CheckState.Checked if dev['id'] in self._assigned_ids
                else Qt.CheckState.Unchecked
            )
            self.device_list.addItem(item)
        layout.addWidget(self.device_list)

        # 전체 선택/해제
        btn_layout = QHBoxLayout()
        select_all = QPushButton("전체 선택")
        select_all.clicked.connect(lambda: self._set_all(True))
        btn_layout.addWidget(select_all)

        deselect_all = QPushButton("전체 해제")
        deselect_all.clicked.connect(lambda: self._set_all(False))
        btn_layout.addWidget(deselect_all)
        layout.addLayout(btn_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_all(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.device_list.count()):
            self.device_list.item(i).setCheckState(state)

    def get_selected_ids(self) -> list:
        ids = []
        for i in range(self.device_list.count()):
            item = self.device_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        return ids
