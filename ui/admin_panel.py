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

    # 기기 변경 시그널 (이름 변경 등 → 메인 윈도우에서 UI 갱신)
    device_changed = pyqtSignal()

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
        self.user_table.setColumnCount(8)
        self.user_table.setHorizontalHeaderLabels(
            ["ID", "아이디", "이름", "역할", "상태", "클라우드", "기기 할당", "작업"]
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

            # 클라우드 쿼타 표시
            quota = user.get('cloud_quota')
            used = user.get('cloud_used', 0)
            if quota is None:
                quota_text = f"{self._format_size(used)} / 무제한"
            elif quota == 0:
                quota_text = "미사용"
            else:
                quota_text = f"{self._format_size(used)} / {self._format_size(quota)}"
            cloud_item = QTableWidgetItem(quota_text)
            if quota is not None and quota > 0:
                usage_pct = used / quota * 100
                if usage_pct >= 90:
                    cloud_item.setForeground(Qt.GlobalColor.red)
                elif usage_pct >= 70:
                    cloud_item.setForeground(Qt.GlobalColor.darkYellow)
            self.user_table.setItem(row, 5, cloud_item)

            # 기기 할당 버튼 (할당 수 표시)
            device_count = user.get('device_count', 0)
            assign_btn = QPushButton(f"기기 할당 ({device_count})")
            if device_count == 0:
                assign_btn.setStyleSheet("color: #888;")
            assign_btn.clicked.connect(lambda checked, uid=user['id'], uname=user['username']: self._on_assign_devices(uid, uname))
            self.user_table.setCellWidget(row, 6, assign_btn)

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

            self.user_table.setCellWidget(row, 7, action_widget)

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

        sync_btn = QPushButton("🔄 로컬 기기 동기화")
        sync_btn.setToolTip("로컬에서 추가한 기기를 서버에 동기화합니다")
        sync_btn.clicked.connect(self._on_sync_local_devices)
        btn_layout.addWidget(sync_btn)

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
                self.device_changed.emit()
            except Exception as e:
                QMessageBox.warning(self, "오류", f"기기 추가 실패: {e}")

    def _on_edit_device(self, device: dict):
        dlg = DeviceFormDialog(self, device)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            try:
                api_client.admin_update_device(device['id'], data)
                self._load_devices()

                # 이름 변경 시 로컬 매니저에도 반영
                old_name = device.get('name', '')
                new_name = data.get('name', old_name)
                if old_name != new_name:
                    try:
                        from core.kvm_manager import KVMManager
                        from core.database import Database
                        db = Database()
                        record = db.get_device_by_name(old_name)
                        if record:
                            db.update_device(record['id'], name=new_name)
                    except Exception:
                        pass

                # 변경 시그널 발행 → 메인 윈도우 UI 갱신
                self.device_changed.emit()
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
                self.device_changed.emit()
            except Exception as e:
                QMessageBox.warning(self, "오류", f"삭제 실패: {e}")

    def _on_sync_local_devices(self):
        """로컬 DB 기기를 서버에 동기화"""
        try:
            from core.database import Database
            db = Database()
            local_devices = db.get_all_devices()

            if not local_devices:
                QMessageBox.information(self, "동기화", "로컬에 저장된 기기가 없습니다.")
                return

            reply = QMessageBox.question(
                self, "로컬 기기 동기화",
                f"로컬 DB에 {len(local_devices)}개 기기가 있습니다.\n서버에 동기화하시겠습니까?\n(이미 서버에 있는 기기는 건너뜁니다)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

            result = api_client.sync_devices_to_server([
                {
                    'name': d['name'],
                    'ip': d['ip'],
                    'port': d.get('port', 22),
                    'web_port': d.get('web_port', 80),
                    'username': d.get('username', 'root'),
                    'password': d.get('password', 'luckfox'),
                }
                for d in local_devices
            ])

            msg = f"동기화 완료!\n추가: {result['synced']}개\n건너뜀(중복): {result['skipped']}개"
            if result['failed'] > 0:
                msg += f"\n실패: {result['failed']}개"
            QMessageBox.information(self, "동기화 결과", msg)

            # 기기 목록 새로고침
            self._load_devices()

        except Exception as e:
            QMessageBox.warning(self, "오류", f"동기화 실패: {e}")

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """바이트를 읽기 좋은 형태로 변환"""
        if size_bytes == 0:
            return "0B"
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if abs(size_bytes) < 1024.0:
                if unit == 'B':
                    return f"{int(size_bytes)}{unit}"
                return f"{size_bytes:.1f}{unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f}PB"

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

    # 쿼타 옵션 매핑 (표시명 → 바이트)
    QUOTA_MAP = {
        "없음 (클라우드 비활성)": 0,
        "1 GB": 1 * 1024**3,
        "2 GB": 2 * 1024**3,
        "5 GB": 5 * 1024**3,
        "10 GB": 10 * 1024**3,
        "50 GB": 50 * 1024**3,
        "100 GB": 100 * 1024**3,
        "무제한": -1,
    }

    def _init_ui(self):
        is_edit = self._user is not None
        self.setWindowTitle("사용자 수정" if is_edit else "사용자 추가")
        self.setFixedSize(350, 340)

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

        # 클라우드 쿼타
        self.quota_combo = QComboBox()
        self.quota_combo.addItems(list(self.QUOTA_MAP.keys()))
        if is_edit:
            quota = self._user.get('cloud_quota')
            if quota is None:
                self.quota_combo.setCurrentText("무제한")
            elif quota == 0:
                self.quota_combo.setCurrentIndex(0)
            else:
                gb = quota / (1024 ** 3)
                label = f"{int(gb)} GB" if gb == int(gb) else f"{gb:.1f} GB"
                idx = self.quota_combo.findText(label)
                if idx >= 0:
                    self.quota_combo.setCurrentIndex(idx)
                else:
                    self.quota_combo.addItem(label)
                    self.quota_combo.setCurrentText(label)
        layout.addRow("클라우드 쿼타:", self.quota_combo)

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

        # 클라우드 쿼타
        quota_text = self.quota_combo.currentText()
        data['cloud_quota'] = self.QUOTA_MAP.get(quota_text, 0)

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
    """기기 할당 다이얼로그 — 그룹별 분류 + 검색"""

    def __init__(self, parent, username: str, all_devices: list, assigned_ids: set):
        super().__init__(parent)
        self._all_devices = all_devices
        self._assigned_ids = assigned_ids
        self.setWindowTitle(f"'{username}' 기기 할당")
        self.setFixedSize(420, 500)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 검색
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("기기 검색...")
        self.search_input.textChanged.connect(self._on_search)
        layout.addWidget(self.search_input)

        # 선택 카운트
        self.count_label = QLabel()
        self.count_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.count_label)

        # 기기 목록 (그룹별)
        self.device_list = QListWidget()
        self._populate_list()
        self.device_list.itemChanged.connect(self._update_count)
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

        self._update_count()

    def _populate_list(self):
        """그룹별로 기기 목록 생성"""
        self.device_list.clear()

        # 그룹별 분류
        groups = {}
        for dev in self._all_devices:
            group = dev.get('group_name') or 'default'
            if group not in groups:
                groups[group] = []
            groups[group].append(dev)

        for group_name in sorted(groups.keys(), key=lambda x: (x != 'default', x)):
            # 그룹 헤더
            header = QListWidgetItem(f"── {group_name} ({len(groups[group_name])}) ──")
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            header.setForeground(Qt.GlobalColor.darkCyan)
            self.device_list.addItem(header)

            # 기기 항목
            for dev in groups[group_name]:
                item = QListWidgetItem(f"  {dev['name']}  ({dev['ip']})")
                item.setData(Qt.ItemDataRole.UserRole, dev['id'])
                item.setData(Qt.ItemDataRole.UserRole + 1, dev['name'])  # 검색용
                item.setCheckState(
                    Qt.CheckState.Checked if dev['id'] in self._assigned_ids
                    else Qt.CheckState.Unchecked
                )
                self.device_list.addItem(item)

    def _on_search(self, text: str):
        text = text.lower()
        for i in range(self.device_list.count()):
            item = self.device_list.item(i)
            device_id = item.data(Qt.ItemDataRole.UserRole)
            if device_id is None:
                # 그룹 헤더 — 하위에 매칭 있으면 표시
                item.setHidden(False)
                continue
            name = (item.data(Qt.ItemDataRole.UserRole + 1) or '').lower()
            display = item.text().lower()
            item.setHidden(text not in name and text not in display)

    def _set_all(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.device_list.count()):
            item = self.device_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) is not None and not item.isHidden():
                item.setCheckState(state)
        self._update_count()

    def _update_count(self):
        selected = len(self.get_selected_ids())
        total = sum(1 for i in range(self.device_list.count())
                    if self.device_list.item(i).data(Qt.ItemDataRole.UserRole) is not None)
        self.count_label.setText(f"선택: {selected} / {total}")

    def get_selected_ids(self) -> list:
        ids = []
        for i in range(self.device_list.count()):
            item = self.device_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) is not None and item.checkState() == Qt.CheckState.Checked:
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        return ids
