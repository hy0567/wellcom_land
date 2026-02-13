"""
WellcomLAND 다이얼로그
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QSpinBox, QComboBox, QPushButton,
    QDialogButtonBox, QGroupBox, QCheckBox, QLabel,
    QTabWidget, QWidget, QMessageBox, QProgressBar,
    QListWidget, QListWidgetItem, QAbstractItemView
)
from PyQt6.QtCore import Qt, QTimer

from core.kvm_device import KVMDevice
from core.discovery import NetworkScanner, DiscoveryThread, DiscoveredDevice
from config import settings


class AddDeviceDialog(QDialog):
    """장치 추가 다이얼로그"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("KVM 장치 추가")
        self.setMinimumWidth(400)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 폼
        form_layout = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("예: KVM-01")
        form_layout.addRow("이름:", self.name_edit)

        self.ip_edit = QLineEdit()
        self.ip_edit.setPlaceholderText("예: 192.168.0.226")
        form_layout.addRow("IP 주소:", self.ip_edit)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(22)
        form_layout.addRow("SSH 포트:", self.port_spin)

        self.web_port_spin = QSpinBox()
        self.web_port_spin.setRange(1, 65535)
        self.web_port_spin.setValue(80)
        form_layout.addRow("웹 포트:", self.web_port_spin)

        self.username_edit = QLineEdit()
        self.username_edit.setText("root")
        form_layout.addRow("사용자명:", self.username_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setText("luckfox")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form_layout.addRow("비밀번호:", self.password_edit)

        self.group_combo = QComboBox()
        self.group_combo.addItem("기본")
        self.group_combo.setEditable(True)
        form_layout.addRow("그룹:", self.group_combo)

        layout.addLayout(form_layout)

        # 연결 테스트 버튼
        test_btn = QPushButton("연결 테스트")
        test_btn.clicked.connect(self._on_test_connection)
        layout.addWidget(test_btn)

        # 버튼
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.button(QDialogButtonBox.StandardButton.Ok).setText("확인")
        button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _on_test_connection(self):
        """연결 테스트"""
        import paramiko

        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                self.ip_edit.text(),
                port=self.port_spin.value(),
                username=self.username_edit.text(),
                password=self.password_edit.text(),
                timeout=5
            )
            ssh.close()
            QMessageBox.information(self, "성공", "연결 성공!")
        except Exception as e:
            QMessageBox.critical(self, "오류", f"연결 실패: {e}")

    def _on_accept(self):
        """유효성 검사 및 확인"""
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "경고", "이름을 입력하세요")
            return

        if not self.ip_edit.text().strip():
            QMessageBox.warning(self, "경고", "IP 주소를 입력하세요")
            return

        self.accept()

    def get_data(self) -> dict:
        """폼 데이터 반환"""
        return {
            'name': self.name_edit.text().strip(),
            'ip': self.ip_edit.text().strip(),
            'port': self.port_spin.value(),
            'web_port': self.web_port_spin.value(),
            'username': self.username_edit.text(),
            'password': self.password_edit.text(),
            'group': self.group_combo.currentText()
        }


class DeviceSettingsDialog(QDialog):
    """장치 설정 다이얼로그"""

    def __init__(self, device: KVMDevice, parent=None):
        super().__init__(parent)
        self.device = device
        self.setWindowTitle(f"설정 - {device.name}")
        self.setMinimumWidth(500)
        self._init_ui()
        self._load_settings()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 탭
        tabs = QTabWidget()

        # 연결 탭
        conn_tab = QWidget()
        conn_layout = QFormLayout(conn_tab)

        self.ip_edit = QLineEdit()
        conn_layout.addRow("IP 주소:", self.ip_edit)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        conn_layout.addRow("SSH 포트:", self.port_spin)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        conn_layout.addRow("비밀번호:", self.password_edit)

        tabs.addTab(conn_tab, "연결")

        # 마우스 탭
        mouse_tab = QWidget()
        mouse_layout = QVBoxLayout(mouse_tab)

        self.absolute_check = QCheckBox("절대 마우스 활성화")
        mouse_layout.addWidget(self.absolute_check)

        self.relative_check = QCheckBox("상대 마우스 활성화")
        mouse_layout.addWidget(self.relative_check)

        mouse_layout.addStretch()

        tabs.addTab(mouse_tab, "마우스")

        # USB 탭
        usb_tab = QWidget()
        usb_layout = QVBoxLayout(usb_tab)

        self.mass_storage_check = QCheckBox("대용량 저장소 활성화")
        usb_layout.addWidget(self.mass_storage_check)

        self.keyboard_check = QCheckBox("키보드 활성화")
        usb_layout.addWidget(self.keyboard_check)

        usb_layout.addStretch()

        tabs.addTab(usb_tab, "USB 장치")

        layout.addWidget(tabs)

        # 버튼
        button_layout = QHBoxLayout()

        apply_btn = QPushButton("적용")
        apply_btn.clicked.connect(self._on_apply)
        button_layout.addWidget(apply_btn)

        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.close)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _load_settings(self):
        """현재 설정 로드"""
        self.ip_edit.setText(self.device.ip)
        self.port_spin.setValue(self.device.info.port)
        self.password_edit.setText(self.device.info.password)

        # 장치에서 설정 로드
        if self.device.is_connected():
            config = self.device.get_config()
            usb_devices = config.get('usb_devices', {})

            self.absolute_check.setChecked(usb_devices.get('absolute_mouse', True))
            self.relative_check.setChecked(usb_devices.get('relative_mouse', True))
            self.mass_storage_check.setChecked(usb_devices.get('mass_storage', True))
            self.keyboard_check.setChecked(usb_devices.get('keyboard', True))

    def _on_apply(self):
        """설정 적용"""
        if not self.device.is_connected():
            QMessageBox.warning(self, "경고", "장치에 연결되어 있지 않습니다")
            return

        try:
            # 마우스 설정 적용
            self.device.set_mouse_mode(
                self.absolute_check.isChecked(),
                self.relative_check.isChecked()
            )

            QMessageBox.information(self, "성공", "설정이 적용되었습니다. 변경사항을 적용하려면 장치를 재부팅하세요.")
        except Exception as e:
            QMessageBox.critical(self, "오류", f"설정 적용 실패: {e}")


class BatchCommandDialog(QDialog):
    """일괄 명령 다이얼로그"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("일괄 명령")
        self.setMinimumWidth(500)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 명령 입력
        layout.addWidget(QLabel("SSH 명령:"))
        self.command_edit = QLineEdit()
        self.command_edit.setPlaceholderText("예: cat /version")
        layout.addWidget(self.command_edit)

        # 버튼
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.button(QDialogButtonBox.StandardButton.Ok).setText("실행")
        button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_command(self) -> str:
        return self.command_edit.text()


class AutoDiscoveryDialog(QDialog):
    """자동 검색 다이얼로그 - 네트워크에서 KVM 장치 자동 탐지"""

    def __init__(self, existing_ips: list = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("KVM 장치 자동 검색")
        self.setMinimumSize(500, 400)
        self.existing_ips = existing_ips or []
        self.discovered_devices: list[DiscoveredDevice] = []
        self.scan_thread: DiscoveryThread = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 네트워크 정보
        info_group = QGroupBox("네트워크 설정")
        info_layout = QVBoxLayout(info_group)

        # 로컬 IP 표시
        local_ip = NetworkScanner.get_local_ip()
        network_base = ".".join(local_ip.split(".")[:3])

        ip_layout = QHBoxLayout()
        ip_layout.addWidget(QLabel(f"로컬 IP: {local_ip}"))
        ip_layout.addStretch()
        ip_layout.addWidget(QLabel(f"스캔 범위: {network_base}.1-254"))
        info_layout.addLayout(ip_layout)

        # 포트 설정
        port_layout = QHBoxLayout()
        port_layout.addWidget(QLabel("스캔 포트:"))
        self.port_edit = QLineEdit("80, 8080")
        self.port_edit.setPlaceholderText("포트 (쉼표로 구분)")
        port_layout.addWidget(self.port_edit)
        info_layout.addLayout(port_layout)

        layout.addWidget(info_group)

        # 진행 상태
        progress_layout = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("대기 중...")
        self.status_label.setFixedWidth(100)
        progress_layout.addWidget(self.status_label)

        layout.addLayout(progress_layout)

        # 발견된 장치 목록
        devices_group = QGroupBox("발견된 장치")
        devices_layout = QVBoxLayout(devices_group)

        self.device_list = QListWidget()
        self.device_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        devices_layout.addWidget(self.device_list)

        # 선택 버튼
        select_layout = QHBoxLayout()
        btn_select_all = QPushButton("전체 선택")
        btn_select_all.clicked.connect(self._select_all)
        select_layout.addWidget(btn_select_all)

        btn_select_new = QPushButton("신규만 선택")
        btn_select_new.clicked.connect(self._select_new_only)
        select_layout.addWidget(btn_select_new)

        select_layout.addStretch()
        devices_layout.addLayout(select_layout)

        layout.addWidget(devices_group)

        # 버튼
        button_layout = QHBoxLayout()

        self.btn_scan = QPushButton("스캔 시작")
        self.btn_scan.clicked.connect(self._toggle_scan)
        self.btn_scan.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
            }
            QPushButton:hover { background-color: #45a049; }
        """)
        button_layout.addWidget(self.btn_scan)

        button_layout.addStretch()

        self.btn_add = QPushButton("선택 장치 추가")
        self.btn_add.clicked.connect(self._on_add_clicked)
        self.btn_add.setEnabled(False)
        button_layout.addWidget(self.btn_add)

        btn_cancel = QPushButton("취소")
        btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(btn_cancel)

        layout.addLayout(button_layout)

    def _toggle_scan(self):
        """스캔 시작/중지 토글"""
        if self.scan_thread and self.scan_thread.isRunning():
            self._stop_scan()
        else:
            self._start_scan()

    def _start_scan(self):
        """스캔 시작"""
        # 포트 파싱
        try:
            ports = [int(p.strip()) for p in self.port_edit.text().split(",") if p.strip()]
            if not ports:
                ports = [80, 8080]
        except ValueError:
            ports = [80, 8080]

        # 목록 초기화
        self.device_list.clear()
        self.discovered_devices.clear()
        self.progress_bar.setValue(0)

        # 스캔 스레드 시작
        self.scan_thread = DiscoveryThread(ports=ports, parent=self)
        self.scan_thread.device_found.connect(self._on_device_found)
        self.scan_thread.progress_updated.connect(self._on_progress)
        self.scan_thread.scan_completed.connect(self._on_scan_completed)
        self.scan_thread.scan_error.connect(self._on_scan_error)
        self.scan_thread.start()

        # UI 업데이트
        self.btn_scan.setText("스캔 중지")
        self.btn_scan.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
            }
            QPushButton:hover { background-color: #d32f2f; }
        """)
        self.status_label.setText("스캔 중...")
        self.port_edit.setEnabled(False)

    def _stop_scan(self):
        """스캔 중지"""
        if self.scan_thread:
            self.scan_thread.stop()
            # 짧은 대기 후 타임아웃 — UI 멈춤 방지
            if not self.scan_thread.wait(1000):
                # 1초 내 종료 안되면 UI만 먼저 리셋, 스레드는 백그라운드 정리
                self.scan_thread.finished.connect(self.scan_thread.deleteLater)

        self._reset_ui()

    def _reset_ui(self):
        """UI 초기화"""
        self.btn_scan.setText("스캔 시작")
        self.btn_scan.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
            }
            QPushButton:hover { background-color: #45a049; }
        """)
        self.port_edit.setEnabled(True)

    def _on_device_found(self, device: DiscoveredDevice):
        """장치 발견 시"""
        self.discovered_devices.append(device)

        # 기존 장치 여부 확인
        is_existing = device.ip in self.existing_ips

        item = QListWidgetItem()
        if is_existing:
            item.setText(f"[기존] {device.name} - {device.ip}:{device.port}")
            item.setForeground(Qt.GlobalColor.gray)
        else:
            item.setText(f"[신규] {device.name} - {device.ip}:{device.port}")
            item.setForeground(Qt.GlobalColor.darkGreen)

        item.setData(Qt.ItemDataRole.UserRole, device)
        self.device_list.addItem(item)

        self.btn_add.setEnabled(True)

    def _on_progress(self, current: int, total: int):
        """진행 상태 업데이트"""
        percent = int(current / total * 100) if total > 0 else 0
        self.progress_bar.setValue(percent)
        self.status_label.setText(f"{current}/{total}")

    def _on_scan_completed(self, devices: list):
        """스캔 완료"""
        self._reset_ui()
        count = len(devices)
        new_count = sum(1 for d in devices if d.ip not in self.existing_ips)
        self.status_label.setText(f"완료: {count}개 (신규 {new_count}개)")

    def _on_scan_error(self, error: str):
        """스캔 오류"""
        self._reset_ui()
        self.status_label.setText("오류!")
        QMessageBox.critical(self, "스캔 오류", f"스캔 중 오류 발생: {error}")

    def _select_all(self):
        """전체 선택"""
        self.device_list.selectAll()

    def _select_new_only(self):
        """신규 장치만 선택"""
        self.device_list.clearSelection()
        for i in range(self.device_list.count()):
            item = self.device_list.item(i)
            device = item.data(Qt.ItemDataRole.UserRole)
            if device and device.ip not in self.existing_ips:
                item.setSelected(True)

    def _on_add_clicked(self):
        """선택 장치 추가 버튼 클릭 - 스캔 중지 후 선택 목록 캐시"""
        # 선택된 장치를 미리 캐시 (accept() 후 위젯 접근 문제 방지)
        self._cached_selected = []
        for item in self.device_list.selectedItems():
            device = item.data(Qt.ItemDataRole.UserRole)
            if device:
                self._cached_selected.append(device)

        # 스캔 중이면 중지 (논블로킹)
        if self.scan_thread and self.scan_thread.isRunning():
            self.scan_thread.stop()

        self.accept()

    def get_selected_devices(self) -> list[DiscoveredDevice]:
        """선택된 장치 목록 반환"""
        # 캐시된 선택 목록이 있으면 사용
        if hasattr(self, '_cached_selected') and self._cached_selected:
            return self._cached_selected

        selected = []
        for item in self.device_list.selectedItems():
            device = item.data(Qt.ItemDataRole.UserRole)
            if device:
                selected.append(device)
        return selected

    def closeEvent(self, event):
        """다이얼로그 닫힐 때 스레드 정리"""
        if self.scan_thread and self.scan_thread.isRunning():
            self.scan_thread.stop()
            if not self.scan_thread.wait(500):
                self.scan_thread.finished.connect(self.scan_thread.deleteLater)
        super().closeEvent(event)


class AppSettingsDialog(QDialog):
    """애플리케이션 설정 다이얼로그"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("환경 설정")
        self.setMinimumSize(500, 400)
        self._init_ui()
        self._load_settings()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 일반 설정
        general_group = QGroupBox("일반")
        general_layout = QFormLayout(general_group)

        self.confirm_delete_check = QCheckBox("장치 삭제 시 확인")
        general_layout.addRow("", self.confirm_delete_check)

        self.auto_scan_check = QCheckBox("시작 시 자동 스캔")
        general_layout.addRow("", self.auto_scan_check)

        layout.addWidget(general_group)

        # 1:1 제어 설정
        live_group = QGroupBox("1:1 제어")
        live_layout = QFormLayout(live_group)

        self.remember_resolution_check = QCheckBox("마지막 창 크기 기억")
        live_layout.addRow("", self.remember_resolution_check)

        # 해상도 표시 (현재 저장된 값)
        saved_w = settings.get('liveview.last_width', 1920)
        saved_h = settings.get('liveview.last_height', 1080)
        self.resolution_label = QLabel(f"현재 저장: {saved_w} x {saved_h}")
        self.resolution_label.setStyleSheet("color: #888; font-size: 11px;")
        live_layout.addRow("", self.resolution_label)

        layout.addWidget(live_group)

        # 그래픽 설정
        gpu_group = QGroupBox("그래픽")
        gpu_layout = QFormLayout(gpu_group)

        self.software_gl_check = QCheckBox("소프트웨어 렌더링 (GPU 문제 시)")
        self.software_gl_check.setToolTip(
            "1:1 제어 시 프로그램이 강제종료되는 경우 활성화하세요.\n"
            "GPU 대신 CPU로 렌더링하여 안정성이 향상됩니다.\n"
            "변경 후 프로그램을 재시작해야 적용됩니다."
        )
        gpu_layout.addRow("", self.software_gl_check)

        # 현재 상태 표시
        import os
        from config import DATA_DIR
        _flag = os.path.join(DATA_DIR, ".gpu_crash")
        if os.path.exists(_flag):
            gpu_status = QLabel("⚠ GPU 크래시 감지됨 — 현재 소프트웨어 렌더링 중")
            gpu_status.setStyleSheet("color: #FFC107; font-size: 11px;")
        else:
            gpu_status = QLabel("현재: GPU 하드웨어 가속")
            gpu_status.setStyleSheet("color: #4CAF50; font-size: 11px;")
        gpu_layout.addRow("", gpu_status)

        layout.addWidget(gpu_group)

        # 버튼
        button_layout = QHBoxLayout()

        btn_reset = QPushButton("기본값으로 초기화")
        btn_reset.clicked.connect(self._reset_settings)
        button_layout.addWidget(btn_reset)

        button_layout.addStretch()

        btn_save = QPushButton("저장")
        btn_save.clicked.connect(self._save_settings)
        btn_save.setStyleSheet("font-weight: bold;")
        button_layout.addWidget(btn_save)

        btn_cancel = QPushButton("취소")
        btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(btn_cancel)

        layout.addLayout(button_layout)

    def _load_settings(self):
        """설정 로드"""
        # 일반
        self.confirm_delete_check.setChecked(settings.get('general.confirm_delete', True))
        self.auto_scan_check.setChecked(settings.get('discovery.auto_scan_on_start', False))

        # 1:1 제어
        self.remember_resolution_check.setChecked(settings.get('liveview.remember_resolution', True))

        # 그래픽
        import os
        from config import DATA_DIR
        _flag = os.path.join(DATA_DIR, ".gpu_crash")
        self.software_gl_check.setChecked(
            settings.get('graphics.software_rendering', False) or os.path.exists(_flag)
        )

    def _save_settings(self):
        """설정 저장"""
        # 일반
        settings.set('general.confirm_delete', self.confirm_delete_check.isChecked(), False)
        settings.set('discovery.auto_scan_on_start', self.auto_scan_check.isChecked(), False)

        # 1:1 제어
        settings.set('liveview.remember_resolution', self.remember_resolution_check.isChecked(), False)

        # 그래픽 — 소프트웨어 렌더링 토글
        sw_render = self.software_gl_check.isChecked()
        import os
        from config import DATA_DIR
        _flag = os.path.join(DATA_DIR, ".gpu_crash")
        _was_sw = os.path.exists(_flag)
        settings.set('graphics.software_rendering', sw_render, False)
        if sw_render:
            # 플래그 파일 생성 (다음 실행에서 소프트웨어 렌더링)
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                with open(_flag, 'w') as f:
                    f.write("manual=True\n")
            except Exception:
                pass
        else:
            # 플래그 파일 제거 (다음 실행에서 GPU 모드)
            try:
                if os.path.exists(_flag):
                    os.remove(_flag)
            except Exception:
                pass

        # 저장
        settings.save()

        # 그래픽 설정 변경 시 재시작 안내
        if sw_render != _was_sw:
            QMessageBox.information(self, "설정 저장",
                "설정이 저장되었습니다.\n\n"
                "그래픽 설정 변경은 프로그램을 재시작해야 적용됩니다.")
        else:
            QMessageBox.information(self, "설정 저장", "설정이 저장되었습니다.")
        self.accept()

    def _reset_settings(self):
        """설정 초기화"""
        if QMessageBox.question(
            self, "초기화 확인",
            "모든 설정을 기본값으로 초기화하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            settings.reset()
            self._load_settings()
            QMessageBox.information(self, "초기화 완료", "설정이 초기화되었습니다.")
