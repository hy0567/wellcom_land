"""
WellcomLAND 메인 윈도우
아이온2 모드 지원 - 마우스 커서 비활성화 + 무한 회전
"""

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QToolBar, QStatusBar, QMenuBar, QMenu, QMessageBox,
    QTreeWidget, QTreeWidgetItem, QTabWidget, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QLineEdit, QSpinBox, QComboBox, QTextEdit, QProgressBar,
    QDialog, QDialogButtonBox, QApplication, QSlider, QFrame,
    QScrollArea, QGridLayout, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread, QUrl, QPoint, QRect, QByteArray
from PyQt6.QtGui import QAction, QIcon, QColor, QDesktopServices, QCursor, QPainter, QBrush, QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage
from PyQt6.QtWebChannel import QWebChannel

from core import KVMManager, KVMDevice
from core.kvm_device import DeviceStatus, USBStatus
from core.hid_controller import FastHIDController
from .dialogs import AddDeviceDialog, DeviceSettingsDialog, AutoDiscoveryDialog, AppSettingsDialog
from config import settings as app_settings, ICON_PATH
from .device_control import DeviceControlPanel


class InitialStatusCheckThread(QThread):
    """최초 상태 체크 스레드"""
    check_completed = pyqtSignal(dict)

    def __init__(self, manager: KVMManager):
        super().__init__()
        self.manager = manager

    def run(self):
        import socket
        results = {}
        for device in self.manager.get_all_devices():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex((device.ip, device.info.web_port))
                sock.close()
                results[device.name] = result == 0
                print(f"  - {device.name}: {'ONLINE' if result == 0 else 'OFFLINE'}")
            except Exception as e:
                results[device.name] = False
                print(f"  - {device.name}: OFFLINE (오류: {e})")
        self.check_completed.emit(results)


class StatusUpdateThread(QThread):
    """백그라운드 상태 업데이트 스레드"""
    status_updated = pyqtSignal(dict)

    def __init__(self, manager: KVMManager):
        super().__init__()
        self.manager = manager
        self.running = True

    def run(self):
        # 첫 실행 시 충분히 대기 (UI/WebEngine 초기화 완료 후)
        self.msleep(5000)

        while self.running:
            try:
                # SSH 연결 시도 없이 핑만 체크
                status = self._check_status_safe()
                self.status_updated.emit(status)
            except Exception as e:
                print(f"상태 업데이트 오류: {e}")
            self.msleep(5000)

    def _check_status_safe(self) -> dict:
        """안전한 상태 체크 (SSH 연결 시도 없이)"""
        import socket
        results = {}
        for device in self.manager.get_all_devices():
            try:
                # TCP 포트 체크만 (SSH 연결 없이)
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1.0)
                result = sock.connect_ex((device.ip, device.info.web_port))
                sock.close()
                results[device.name] = {'online': result == 0}
            except Exception:
                results[device.name] = {'online': False}
        return results

    def stop(self):
        self.running = False


class KVMThumbnailWidget(QFrame):
    """KVM 장치 썸네일 위젯 - WebRTC 실시간 미리보기 (저비트레이트)"""
    clicked = pyqtSignal(object)  # KVMDevice
    double_clicked = pyqtSignal(object)  # KVMDevice

    # 썸네일용 JavaScript: 보기 전용 (입력 차단) + 저비트레이트
    THUMBNAIL_JS = """
    (function() {
        'use strict';

        var _cssDone = false;
        var _videoDone = false;
        var _qualityDone = false;
        var _inputBlocked = false;

        // 1. CSS 주입 + 입력 차단 오버레이
        function injectCSS() {
            if (_cssDone) return;
            var style = document.getElementById('_thumbCSS');
            if (!style) {
                style = document.createElement('style');
                style.id = '_thumbCSS';
                style.textContent = `
                    html, body, #root {
                        margin: 0 !important;
                        padding: 0 !important;
                        width: 100% !important;
                        height: 100% !important;
                        overflow: hidden !important;
                        background: #000 !important;
                    }
                    #root > * { display: none !important; }
                    video {
                        display: block !important;
                        position: fixed !important;
                        top: 0 !important;
                        left: 0 !important;
                        width: 100vw !important;
                        height: 100vh !important;
                        min-width: 0 !important;
                        min-height: 0 !important;
                        max-width: none !important;
                        max-height: none !important;
                        object-fit: contain !important;
                        z-index: 999999 !important;
                        background: #000 !important;
                        border: none !important;
                        margin: 0 !important;
                        padding: 0 !important;
                        pointer-events: none !important;
                    }
                    /* 입력 차단 오버레이 */
                    #_inputBlocker {
                        position: fixed !important;
                        top: 0 !important;
                        left: 0 !important;
                        width: 100vw !important;
                        height: 100vh !important;
                        z-index: 9999999 !important;
                        background: transparent !important;
                        cursor: default !important;
                    }
                `;
                document.head.appendChild(style);
            }
            _cssDone = true;
        }

        // 2. 입력 차단 (모든 키보드/마우스 이벤트 무시)
        function blockInput() {
            if (_inputBlocked) return;

            // 오버레이 추가
            var blocker = document.createElement('div');
            blocker.id = '_inputBlocker';
            document.body.appendChild(blocker);

            // 모든 입력 이벤트 차단
            var events = ['keydown', 'keyup', 'keypress', 'mousedown', 'mouseup',
                          'click', 'dblclick', 'mousemove', 'wheel', 'contextmenu',
                          'touchstart', 'touchmove', 'touchend'];
            events.forEach(function(evt) {
                document.addEventListener(evt, function(e) {
                    e.stopPropagation();
                    e.preventDefault();
                }, true);
            });

            _inputBlocked = true;
            console.log('[Thumb] Input blocked');
        }

        // 3. video 요소 처리
        function setupVideo() {
            if (_videoDone) return true;

            var video = document.querySelector('video');
            if (!video || !video.srcObject) return false;
            if (video.readyState < 2) return false;

            if (video.parentElement !== document.body) {
                document.body.appendChild(video);
                video.play().catch(function(){});
            }

            console.log('[Thumb] Video ready');
            _videoDone = true;
            return true;
        }

        // 4. 저품질 설정 (10% = 약 660Kbps)
        function setLowQuality() {
            if (_qualityDone) return true;

            var root = document.querySelector('#root');
            if (!root) return false;

            var fiberKey = Object.keys(root).find(function(k) {
                return k.startsWith('__reactFiber$');
            });
            if (!fiberKey) return false;

            var fiber = root[fiberKey];
            var visited = new Set();
            var queue = [fiber];

            while (queue.length > 0) {
                var current = queue.shift();
                if (!current || visited.has(current)) continue;
                visited.add(current);

                if (current.memoizedState) {
                    var state = current.memoizedState;
                    while (state) {
                        if (state.memoizedState && state.memoizedState.rpcDataChannel) {
                            var rpc = state.memoizedState.rpcDataChannel;
                            if (rpc.readyState === 'open') {
                                // 저비트레이트 설정 (10%)
                                rpc.send(JSON.stringify({
                                    jsonrpc: '2.0',
                                    id: Date.now(),
                                    method: 'setStreamQualityFactor',
                                    params: { factor: 0.1 }
                                }));
                                console.log('[Thumb] Quality set to 10% (low bitrate)');
                                _qualityDone = true;
                                return true;
                            }
                        }
                        state = state.next;
                    }
                }

                if (current.child) queue.push(current.child);
                if (current.sibling) queue.push(current.sibling);
                if (visited.size > 500) break;
            }
            return false;
        }

        // 5. 메인 루프
        var attempts = 0;
        function loop() {
            attempts++;
            injectCSS();
            blockInput();
            setupVideo();
            setLowQuality();

            if (attempts < 60) {
                setTimeout(loop, 500);
            }
        }

        setTimeout(loop, 2000);
    })();
    """

    def __init__(self, device: KVMDevice, parent=None):
        super().__init__(parent)
        self.device = device
        self._is_active = False
        self._is_paused = False
        self._use_preview = True
        self._webview = None
        self._init_ui()

    def _init_ui(self):
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
        self.setLineWidth(1)
        self.setFixedSize(200, 150)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)

        # 상태/비디오 표시 영역
        self.status_label = QLabel()
        self.status_label.setFixedSize(196, 125)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("""
            background-color: #1a1a1a;
            color: #888;
            font-size: 11px;
        """)
        self.status_label.setText("로딩 중...")
        layout.addWidget(self.status_label)

        # 장치 이름 라벨
        self.name_label = QLabel(self.device.name)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setStyleSheet("""
            background-color: #333;
            color: white;
            font-size: 10px;
            font-weight: bold;
            padding: 2px;
        """)
        layout.addWidget(self.name_label)

        self._update_style()

    def _create_webview(self):
        """미니 WebView 생성 (WebRTC 지원, 입력 차단)"""
        try:
            if self._webview:
                return

            self._webview = QWebEngineView()
            self._webview.setFixedSize(196, 125)

            # 입력 이벤트 차단 (보기 전용)
            self._webview.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._webview.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

            # WebRTC 권한 자동 허용을 위한 커스텀 Page
            page = QWebEnginePage(self._webview)
            page.featurePermissionRequested.connect(self._on_permission_requested)
            self._webview.setPage(page)

            # 설정
            settings = self._webview.settings()
            settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
            settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)

            # 로드 완료 시 JS 실행
            self._webview.loadFinished.connect(self._on_load_finished)

            # 레이아웃에서 status_label 교체
            layout = self.layout()
            layout.replaceWidget(self.status_label, self._webview)
            self.status_label.hide()
        except Exception as e:
            print(f"[Thumbnail] _create_webview 오류: {e}")
            self._webview = None

    def _on_permission_requested(self, origin, feature):
        """WebRTC 등 권한 자동 허용"""
        page = self.sender()
        # 모든 미디어 권한 허용 (MediaAudioCapture, MediaVideoCapture, MediaAudioVideoCapture 등)
        page.setFeaturePermission(origin, feature, QWebEnginePage.PermissionPolicy.PermissionGrantedByUser)

    def _on_load_finished(self, ok):
        """WebView 로드 완료"""
        if ok and self._webview:
            self._webview.page().runJavaScript(self.THUMBNAIL_JS)

    def start_capture(self):
        """미리보기 시작"""
        try:
            if self._is_active:
                return
            self._is_active = True

            if self.device.status == DeviceStatus.ONLINE and self._use_preview:
                self._create_webview()
                if self._webview:
                    self._webview.show()
                    url = f"http://{self.device.ip}:{self.device.info.web_port}/"
                    self._webview.setUrl(QUrl(url))
                    self.status_label.hide()
            else:
                self._update_status_display()
        except Exception as e:
            print(f"[Thumbnail] start_capture 오류: {e}")
            self._is_active = False

    def stop_capture(self):
        """미리보기 완전 중지 (WebView 언로드)"""
        try:
            self._is_active = False
            self._is_paused = False
            if self._webview:
                self._webview.setUrl(QUrl("about:blank"))
                self._webview.hide()
            self.status_label.show()
            self._update_status_display()
        except Exception as e:
            print(f"[Thumbnail] stop_capture 오류: {e}")

    def pause_capture(self):
        """미리보기 일시정지 (WebView 숨기기만, URL 유지)"""
        try:
            self._is_paused = True
            if self._webview:
                self._webview.hide()
            self.status_label.show()
        except Exception as e:
            print(f"[Thumbnail] pause_capture 오류: {e}")

    def resume_capture(self):
        """미리보기 재개 (일시정지 상태에서 복원)"""
        try:
            if self._is_paused and self._webview and self._is_active:
                self._webview.show()
                self.status_label.hide()
                self._is_paused = False
            elif not self._is_active:
                # 활성화되지 않았으면 새로 시작
                self.start_capture()
        except Exception as e:
            print(f"[Thumbnail] resume_capture 오류: {e}")

    def _update_status_display(self):
        """상태 표시"""
        try:
            self.status_label.show()
            if self._webview:
                self._webview.hide()
        except Exception:
            pass

        if self.device.status == DeviceStatus.ONLINE:
            self.status_label.setText(f"🟢 온라인\n\n{self.device.ip}")
            self.status_label.setStyleSheet("""
                background-color: #1a3a1a;
                color: #4CAF50;
                font-size: 11px;
            """)
        else:
            self.status_label.setText("🔴 오프라인")
            self.status_label.setStyleSheet("""
                background-color: #3a1a1a;
                color: #f44336;
                font-size: 11px;
            """)

    def _update_style(self):
        if self.device.status == DeviceStatus.ONLINE:
            self.setStyleSheet("QFrame { border: 2px solid #4CAF50; background: #1a1a1a; }")
        else:
            self.setStyleSheet("QFrame { border: 2px solid #f44336; background: #1a1a1a; }")

    def update_status(self):
        try:
            self._update_style()
            if self.device.status == DeviceStatus.ONLINE and self._is_active:
                if not self._webview:
                    self.start_capture()
            elif self.device.status != DeviceStatus.ONLINE:
                self.stop_capture()
                self._update_status_display()
        except Exception as e:
            print(f"[Thumbnail] update_status 오류: {e}")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.device)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self.device)
        super().mouseDoubleClickEvent(event)

    def cleanup(self):
        """메모리 정리"""
        try:
            self.stop_capture()
            if self._webview:
                try:
                    self._webview.setUrl(QUrl("about:blank"))
                    self._webview.deleteLater()
                except Exception:
                    pass
                self._webview = None
        except Exception as e:
            print(f"[Thumbnail] cleanup 오류: {e}")


class GridViewTab(QWidget):
    """전체 KVM 그리드 뷰 탭 - 미니 웹뷰로 실시간 미리보기"""
    device_selected = pyqtSignal(object)  # KVMDevice
    device_double_clicked = pyqtSignal(object)  # KVMDevice

    def __init__(self, manager: KVMManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.thumbnails: list[KVMThumbnailWidget] = []
        self._is_visible = False
        self._live_preview_enabled = True  # 실시간 미리보기 활성화
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # 상단 컨트롤
        control_layout = QHBoxLayout()
        title_label = QLabel("전체 KVM 미리보기")
        title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        control_layout.addWidget(title_label)

        self.status_label = QLabel("🎬 실시간 미리보기 (저비트레이트)")
        self.status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        control_layout.addWidget(self.status_label)

        control_layout.addStretch()

        # 실시간 미리보기 토글 버튼
        self.btn_toggle_preview = QPushButton("🎬 미리보기 ON")
        self.btn_toggle_preview.setCheckable(True)
        self.btn_toggle_preview.setChecked(True)
        self.btn_toggle_preview.setStyleSheet("""
            QPushButton { background-color: #4CAF50; color: white; padding: 5px 10px; border-radius: 4px; }
            QPushButton:checked { background-color: #4CAF50; }
            QPushButton:!checked { background-color: #666; }
        """)
        self.btn_toggle_preview.clicked.connect(self._toggle_live_preview)
        control_layout.addWidget(self.btn_toggle_preview)

        self.btn_refresh = QPushButton("🔄 새로고침")
        self.btn_refresh.clicked.connect(self.refresh_all)
        control_layout.addWidget(self.btn_refresh)

        layout.addLayout(control_layout)

        # 스크롤 영역
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # 그리드 컨테이너
        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(10)
        self.grid_layout.setContentsMargins(10, 10, 10, 10)

        self.scroll_area.setWidget(self.grid_container)
        layout.addWidget(self.scroll_area)

    def _toggle_live_preview(self):
        """실시간 미리보기 토글"""
        self._live_preview_enabled = self.btn_toggle_preview.isChecked()

        if self._live_preview_enabled:
            self.btn_toggle_preview.setText("🎬 미리보기 ON")
            self.status_label.setText("🎬 실시간 미리보기 (저비트레이트)")
            self.status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
            # 모든 썸네일 미리보기 활성화
            for thumb in self.thumbnails:
                thumb._use_preview = True
                if self._is_visible:
                    thumb.start_capture()
        else:
            self.btn_toggle_preview.setText("🎬 미리보기 OFF")
            self.status_label.setText("상태만 표시 (리소스 절약)")
            self.status_label.setStyleSheet("color: #888;")
            # 모든 썸네일 미리보기 비활성화
            for thumb in self.thumbnails:
                thumb._use_preview = False
                thumb.stop_capture()
                thumb._update_status_display()

    def load_devices(self):
        """장치 목록 로드 및 그리드 구성"""
        try:
            print("[GridView] load_devices 시작...")
            # 기존 썸네일 정리
            self._stop_all_captures()
            for thumb in self.thumbnails:
                try:
                    thumb.cleanup()
                    thumb.deleteLater()
                except Exception:
                    pass
            self.thumbnails.clear()

            # 그리드 레이아웃 초기화
            while self.grid_layout.count():
                item = self.grid_layout.takeAt(0)
                if item and item.widget():
                    try:
                        item.widget().deleteLater()
                    except Exception:
                        pass

            # 장치 목록 가져오기
            devices = self.manager.get_all_devices()

            # 열 수 계산 (창 크기에 따라 조정, 최소 4개)
            cols = max(4, self.scroll_area.width() // 210)

            for idx, device in enumerate(devices):
                row = idx // cols
                col = idx % cols

                thumb = KVMThumbnailWidget(device)
                thumb._use_preview = self._live_preview_enabled
                thumb.clicked.connect(self._on_thumbnail_clicked)
                thumb.double_clicked.connect(self._on_thumbnail_double_clicked)
                self.thumbnails.append(thumb)
                self.grid_layout.addWidget(thumb, row, col)

            # 빈 공간 채우기
            if devices:
                self.grid_layout.setRowStretch(len(devices) // cols + 1, 1)
                self.grid_layout.setColumnStretch(cols, 1)

            print(f"[GridView] load_devices 완료 - {len(self.thumbnails)}개 썸네일 생성")

            # 탭이 보이는 상태면 캡처 시작
            print(f"[GridView] _is_visible: {self._is_visible}")
            if self._is_visible:
                print("[GridView] _start_all_captures 호출...")
                self._start_all_captures()
        except Exception as e:
            print(f"[GridView] load_devices 오류: {e}")
            import traceback
            traceback.print_exc()

    def _start_all_captures(self):
        """모든 썸네일 캡처 시작/재개 (순차적으로 로드하여 충돌 방지)"""
        try:
            print(f"[GridView] _start_all_captures - preview_enabled: {self._live_preview_enabled}, thumbs: {len(self.thumbnails)}")
            if not self._live_preview_enabled:
                # 실시간 미리보기가 비활성화면 상태만 업데이트
                for thumb in self.thumbnails:
                    try:
                        thumb._update_status_display()
                    except Exception:
                        pass
                return

            for i, thumb in enumerate(self.thumbnails):
                # 일시정지 상태면 즉시 재개, 아니면 지연 시작
                if thumb._is_paused:
                    print(f"[GridView] thumb[{i}] resume_capture")
                    thumb.resume_capture()
                else:
                    # 각 썸네일을 300ms 간격으로 로드 (WebView 동시 생성 방지)
                    print(f"[GridView] thumb[{i}] start_capture 예약 ({i * 300}ms)")
                    QTimer.singleShot(i * 300, thumb.start_capture)
        except Exception as e:
            print(f"[GridView] _start_all_captures 오류: {e}")

    def _stop_all_captures(self):
        """모든 썸네일 캡처 완전 중지 (WebView 언로드 - 비트레이트 해제)"""
        try:
            print("[GridView] _stop_all_captures - 모든 WebView 중지")
            for thumb in self.thumbnails:
                try:
                    thumb.stop_capture()  # 완전 중지 (about:blank로 변경)
                except Exception as e:
                    print(f"[GridView] stop_capture 오류: {e}")
        except Exception as e:
            print(f"[GridView] _stop_all_captures 오류: {e}")

    def refresh_all(self):
        """모든 썸네일 즉시 새로고침"""
        try:
            for thumb in self.thumbnails:
                try:
                    thumb.update_status()
                except Exception as e:
                    print(f"[GridView] refresh 오류: {e}")
        except Exception as e:
            print(f"[GridView] refresh_all 오류: {e}")

    def update_device_status(self):
        """장치 상태 업데이트"""
        try:
            for thumb in self.thumbnails:
                try:
                    thumb.update_status()
                except Exception as e:
                    print(f"[GridView] update_status 오류: {e}")
        except Exception as e:
            print(f"[GridView] update_device_status 오류: {e}")

    def _on_thumbnail_clicked(self, device):
        self.device_selected.emit(device)

    def _on_thumbnail_double_clicked(self, device):
        self.device_double_clicked.emit(device)

    def on_tab_activated(self):
        """탭이 활성화될 때 호출 (외부에서 호출)"""
        try:
            print(f"[GridView] on_tab_activated - thumbnails: {len(self.thumbnails)}, devices: {len(self.manager.get_all_devices())}")
            self._is_visible = True
            # 처음 로드 또는 장치 수 변경 시 로드
            if len(self.thumbnails) != len(self.manager.get_all_devices()):
                print("[GridView] load_devices 예약...")
                QTimer.singleShot(500, self.load_devices)
            else:
                print("[GridView] _start_all_captures 예약...")
                QTimer.singleShot(300, self._start_all_captures)
        except Exception as e:
            print(f"[GridView] on_tab_activated 오류: {e}")

    def on_tab_deactivated(self):
        """탭이 비활성화될 때 호출 (외부에서 호출)"""
        try:
            print("[GridView] on_tab_deactivated - WebView 중지 및 비트레이트 해제")
            self._is_visible = False
            self._stop_all_captures()
        except Exception as e:
            print(f"[GridView] on_tab_deactivated 오류: {e}")

    def cleanup(self):
        """메모리 정리"""
        try:
            self._stop_all_captures()
            for thumb in self.thumbnails:
                try:
                    thumb.cleanup()
                except Exception as e:
                    print(f"[GridView] thumbnail cleanup 오류: {e}")
            self.thumbnails.clear()
        except Exception as e:
            print(f"[GridView] cleanup 오류: {e}")


class Aion2WebPage(QWebEnginePage):
    """아이온2 모드 지원 웹 페이지 - Pointer Lock API 활성화"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Pointer Lock 권한 자동 허용
        self.featurePermissionRequested.connect(self._on_permission_requested)

    def _on_permission_requested(self, origin, feature):
        """권한 요청 자동 허용 (마우스 락)"""
        if feature == QWebEnginePage.Feature.MouseLock:
            self.setFeaturePermission(origin, feature,
                                       QWebEnginePage.PermissionPolicy.PermissionGrantedByUser)
        else:
            self.setFeaturePermission(origin, feature,
                                       QWebEnginePage.PermissionPolicy.PermissionDeniedByUser)


class LiveViewDialog(QDialog):
    """
    1:1 실시간 제어 다이얼로그
    아이온2 모드: 마우스 커서 비활성화 + Pointer Lock API로 무한 회전
    레이아웃 최적화: 원격 화면 최대화
    """

    # JavaScript: 아이온2 모드 구현 (Pointer Lock API 사용) - 고성능 최적화 버전 v2
    # 핵심: 마우스 커서 비활성화 + 무한 회전 + ALT로 커서 일시 활성화
    # 최적화: 즉시 전송 모드 + 고주파 이벤트 처리 + 제로 지연 + 메모리 풀링
    AION2_MODE_JS = """
    (function() {
        'use strict';

        // 기존 핸들러 정리
        if (window._aion2Mode) {
            window._aion2Mode.stop();
        }

        // 성능 최적화: 전역 변수로 핫패스 최적화
        var _active = false;
        var _altPressed = false;
        var _enabled = true;
        var _sensitivity = %SENSITIVITY%;
        var _canvas = null;

        // 즉시 전송 모드 (RAF 배칭 vs 즉시 전송)
        var _immediateMode = true;  // true = 최소 지연, false = 배칭

        // 배칭 모드용 변수
        var _pendingDX = 0;
        var _pendingDY = 0;
        var _rafId = null;

        // 재사용 객체 (GC 방지)
        var _moveEvent = { dx: 0, dy: 0 };

        // 바인딩된 핸들러 캐시
        var _handlers = {};

        window._aion2Mode = {
            get active() { return _active; },
            get sensitivity() { return _sensitivity; },
            set sensitivity(v) { _sensitivity = v; },

            start: function() {
                // 비디오/캔버스 요소 찾기 (우선순위 순)
                _canvas = document.querySelector('video') ||
                          document.querySelector('canvas#stream') ||
                          document.querySelector('canvas') ||
                          document.querySelector('[data-stream]') ||
                          document.body;

                if (!_canvas) {
                    console.error('[아이온2] 비디오 요소를 찾을 수 없음');
                    return false;
                }

                // Pointer Lock API 폴리필
                _canvas.requestPointerLock = _canvas.requestPointerLock ||
                                             _canvas.mozRequestPointerLock ||
                                             _canvas.webkitRequestPointerLock;

                // 핸들러 바인딩 (한 번만)
                _handlers.click = this._onClick;
                _handlers.lockChange = this._onLockChange;
                _handlers.keyDown = this._onKeyDown;
                _handlers.keyUp = this._onKeyUp;
                _handlers.mouseMove = this._onMouseMove;
                _handlers.renderFrame = this._renderFrame;

                // 이벤트 리스너 등록
                _canvas.addEventListener('click', _handlers.click, { passive: true });
                document.addEventListener('pointerlockchange', _handlers.lockChange);
                document.addEventListener('mozpointerlockchange', _handlers.lockChange);
                document.addEventListener('keydown', _handlers.keyDown);
                document.addEventListener('keyup', _handlers.keyUp);

                // 즉시 Lock 시도
                try { _canvas.requestPointerLock(); } catch(e) {}

                _enabled = true;
                console.log('[아이온2] 모드 시작 (즉시전송:', _immediateMode, ')');
                return true;
            },

            stop: function() {
                _active = false;
                _altPressed = false;
                _enabled = false;

                // RAF 정지
                if (_rafId) {
                    cancelAnimationFrame(_rafId);
                    _rafId = null;
                }

                // Pointer Lock 해제
                if (document.exitPointerLock) {
                    document.exitPointerLock();
                }

                // 이벤트 리스너 제거
                if (_canvas) {
                    _canvas.removeEventListener('click', _handlers.click);
                }
                document.removeEventListener('pointerlockchange', _handlers.lockChange);
                document.removeEventListener('mozpointerlockchange', _handlers.lockChange);
                document.removeEventListener('keydown', _handlers.keyDown);
                document.removeEventListener('keyup', _handlers.keyUp);
                document.removeEventListener('mousemove', _handlers.mouseMove);

                console.log('[아이온2] 모드 종료');
            },

            _onClick: function() {
                if (_enabled && !_altPressed && !document.pointerLockElement) {
                    _canvas.requestPointerLock();
                }
            },

            _onLockChange: function() {
                var locked = document.pointerLockElement === _canvas ||
                             document.mozPointerLockElement === _canvas;

                if (locked) {
                    _active = true;
                    _pendingDX = 0;
                    _pendingDY = 0;

                    // 마우스 이벤트 리스너 (passive로 성능 최적화)
                    document.addEventListener('mousemove', _handlers.mouseMove, { passive: true });

                    // 배칭 모드일 때만 RAF 시작
                    if (!_immediateMode && !_rafId) {
                        _rafId = requestAnimationFrame(_handlers.renderFrame);
                    }

                    console.log('[아이온2] 마우스 잠금 활성화');
                } else {
                    _active = false;
                    document.removeEventListener('mousemove', _handlers.mouseMove);

                    if (_rafId) {
                        cancelAnimationFrame(_rafId);
                        _rafId = null;
                    }
                    console.log('[아이온2] 마우스 잠금 해제');
                }
            },

            _onKeyDown: function(e) {
                // ALT 키: 커서 일시 활성화
                if (e.keyCode === 18) {
                    if (!_altPressed && _active) {
                        _altPressed = true;
                        document.exitPointerLock();
                    }
                    e.preventDefault();
                }
            },

            _onKeyUp: function(e) {
                // ALT 키 해제: 다시 마우스 잠금
                if (e.keyCode === 18) {
                    if (_altPressed) {
                        _altPressed = false;
                        if (_enabled && _canvas) {
                            _canvas.requestPointerLock();
                        }
                    }
                    e.preventDefault();
                }
            },

            _onMouseMove: function(e) {
                if (!_active || _altPressed) return;

                var dx = e.movementX;
                var dy = e.movementY;

                // 제로 이동 무시
                if (dx === 0 && dy === 0) return;

                if (_immediateMode) {
                    // 즉시 전송 모드: 지연 없이 바로 전송
                    var scaledDx = dx * _sensitivity;
                    var scaledDy = dy * _sensitivity;

                    // PicoKVM WebRTC DataChannel로 전송
                    if (window._pointer && window._pointer.sendMouse) {
                        window._pointer.sendMouse(scaledDx, scaledDy);
                    } else if (window.sendMouseRelative) {
                        window.sendMouseRelative(scaledDx, scaledDy);
                    }
                } else {
                    // 배칭 모드: RAF에서 일괄 처리
                    _pendingDX += dx;
                    _pendingDY += dy;
                }
            },

            _renderFrame: function() {
                if (!_active) return;

                // 배칭된 마우스 이동 처리
                if (_pendingDX !== 0 || _pendingDY !== 0) {
                    var dx = _pendingDX * _sensitivity;
                    var dy = _pendingDY * _sensitivity;
                    _pendingDX = 0;
                    _pendingDY = 0;

                    if (window._pointer && window._pointer.sendMouse) {
                        window._pointer.sendMouse(dx, dy);
                    } else if (window.sendMouseRelative) {
                        window.sendMouseRelative(dx, dy);
                    }
                }

                _rafId = requestAnimationFrame(_handlers.renderFrame);
            },

            setSensitivity: function(value) {
                _sensitivity = value;
            },

            // 즉시 전송 모드 토글 (디버그용)
            setImmediateMode: function(enabled) {
                _immediateMode = enabled;
                console.log('[아이온2] 즉시전송 모드:', _immediateMode);
            }
        };

        return window._aion2Mode.start();
    })();
    """

    AION2_STOP_JS = """
    (function() {
        if (window._aion2Mode) {
            window._aion2Mode.stop();
        }
        return true;
    })();
    """

    # PicoKVM UI 정리 - 비디오 스트림만 표시
    CLEAN_UI_JS = """
    (function() {
        'use strict';

        // 스타일 주입
        var style = document.createElement('style');
        style.id = 'wellcomland-clean-ui';
        style.textContent = `
            /* 상단 헤더 숨김 */
            header, .header, nav, .navbar, .nav-bar,
            [class*="header"], [class*="Header"],
            [class*="navbar"], [class*="Navbar"] {
                display: none !important;
            }

            /* 사이드바/메뉴 숨김 */
            aside, .sidebar, .side-bar, .menu,
            [class*="sidebar"], [class*="Sidebar"],
            [class*="menu"], [class*="Menu"] {
                display: none !important;
            }

            /* 푸터 숨김 */
            footer, .footer, [class*="footer"], [class*="Footer"] {
                display: none !important;
            }

            /* 툴바/버튼 영역 숨김 */
            .toolbar, .tool-bar, .buttons, .controls,
            [class*="toolbar"], [class*="Toolbar"],
            [class*="button-bar"], [class*="control-bar"] {
                display: none !important;
            }

            /* PicoKVM 특정 요소 숨김 */
            .kvm-header, .kvm-footer, .kvm-sidebar,
            .connection-status, .device-info,
            [class*="status-bar"], [class*="info-bar"] {
                display: none !important;
            }

            /* 비디오/캔버스 전체화면 */
            video, canvas, #stream, .stream,
            [class*="stream"], [class*="video"],
            [class*="canvas"], [class*="display"] {
                position: fixed !important;
                top: 0 !important;
                left: 0 !important;
                width: 100vw !important;
                height: 100vh !important;
                object-fit: contain !important;
                z-index: 9999 !important;
                background: #000 !important;
            }

            /* body 배경 검정 */
            body {
                background: #000 !important;
                overflow: hidden !important;
                margin: 0 !important;
                padding: 0 !important;
            }

            /* 모든 다른 요소 숨김 (비디오 제외) */
            body > *:not(video):not(canvas):not(#stream):not(.stream):not(script):not(style) {
                display: none !important;
            }
        `;

        // 기존 스타일 제거 후 추가
        var existing = document.getElementById('wellcomland-clean-ui');
        if (existing) existing.remove();
        document.head.appendChild(style);

        // 비디오/캔버스 요소 찾기
        var video = document.querySelector('video') ||
                    document.querySelector('canvas#stream') ||
                    document.querySelector('canvas') ||
                    document.querySelector('[class*="stream"]');

        if (video) {
            // 비디오를 body 직접 자식으로 이동
            document.body.appendChild(video);
            console.log('[WellcomLAND] UI 정리 완료 - 비디오 전체화면');
            return true;
        }

        console.log('[WellcomLAND] 비디오 요소를 찾는 중...');
        return false;
    })();
    """

    # UI 복원
    RESTORE_UI_JS = """
    (function() {
        var style = document.getElementById('wellcomland-clean-ui');
        if (style) style.remove();
        location.reload();
    })();
    """

    def __init__(self, device: KVMDevice, parent=None):
        super().__init__(parent)
        self.device = device
        self.setWindowTitle(f"{device.name} ({device.ip})")
        self.setMinimumSize(1280, 800)

        # HID 컨트롤러 (백업용)
        self.hid = FastHIDController(
            device.ip,
            device.info.port,
            device.info.username,
            device.info.password
        )

        self.game_mode_active = False
        self.sensitivity = 1.0
        self.control_bar_visible = True
        self._quality_timer = None  # 품질 변경 디바운싱용 타이머
        self._pending_quality = None  # 대기 중인 품질 값
        self._previous_quality = 80  # 저지연 모드 해제 시 복원할 품질
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 상단 컨트롤 바 - 컴팩트하게
        self.control_widget = QWidget()
        control_bar = QHBoxLayout(self.control_widget)
        control_bar.setContentsMargins(5, 2, 5, 2)
        control_bar.setSpacing(5)

        self.status_label = QLabel(f"{self.device.name}")
        self.status_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 11px;")
        control_bar.addWidget(self.status_label)

        control_bar.addStretch()

        # 민감도 - 컴팩트 (설정에서 기본값 로드)
        default_sensitivity = app_settings.get('aion2.sensitivity', 1.0)
        lbl = QLabel("감도:")
        lbl.setStyleSheet("color: #ccc; font-size: 11px;")
        control_bar.addWidget(lbl)
        self.sensitivity_slider = QSlider(Qt.Orientation.Horizontal)
        self.sensitivity_slider.setRange(1, 30)
        self.sensitivity_slider.setValue(int(default_sensitivity * 10))
        self.sensitivity_slider.setFixedWidth(60)
        self.sensitivity_slider.valueChanged.connect(self._on_sensitivity_changed)
        control_bar.addWidget(self.sensitivity_slider)

        self.sensitivity_label = QLabel(f"{default_sensitivity:.1f}")
        self.sensitivity_label.setStyleSheet("color: #ccc; font-size: 11px;")
        self.sensitivity_label.setFixedWidth(25)
        control_bar.addWidget(self.sensitivity_label)
        self.sensitivity = default_sensitivity

        control_bar.addStretch()

        # 마우스 모드 버튼 (Absolute/Relative)
        self.mouse_mode_absolute = True  # 기본: Absolute
        self.btn_mouse_mode = QPushButton("🖱 Abs")
        self.btn_mouse_mode.setToolTip("Absolute: 일반작업\nRelative: 3D게임")
        self.btn_mouse_mode.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                padding: 3px 8px;
                border-radius: 3px;
                font-size: 11px;
            }
            QPushButton:hover { background-color: #1976D2; }
        """)
        self.btn_mouse_mode.clicked.connect(self._toggle_mouse_mode)
        control_bar.addWidget(self.btn_mouse_mode)

        # 아이온2 모드 버튼 - 컴팩트
        self.btn_game_mode = QPushButton("아이온2 (G)")
        self.btn_game_mode.setStyleSheet("""
            QPushButton {
                background-color: #FF5722;
                color: white;
                padding: 3px 10px;
                border-radius: 3px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover { background-color: #E64A19; }
        """)
        self.btn_game_mode.clicked.connect(self._toggle_game_mode)
        control_bar.addWidget(self.btn_game_mode)

        btn_fullscreen = QPushButton("전체 (F11)")
        btn_fullscreen.setStyleSheet("padding: 3px 8px; font-size: 11px;")
        btn_fullscreen.clicked.connect(self._toggle_fullscreen)
        control_bar.addWidget(btn_fullscreen)

        btn_hide = QPushButton("바 숨김 (H)")
        btn_hide.setStyleSheet("padding: 3px 8px; font-size: 11px;")
        btn_hide.clicked.connect(self._toggle_control_bar)
        control_bar.addWidget(btn_hide)

        # 원본 UI 토글 버튼
        self.btn_original_ui = QPushButton("원본 UI")
        self.btn_original_ui.setStyleSheet("padding: 3px 8px; font-size: 11px;")
        self.btn_original_ui.setCheckable(True)
        self.btn_original_ui.clicked.connect(self._toggle_original_ui)
        control_bar.addWidget(self.btn_original_ui)

        # 구분선
        sep = QLabel("|")
        sep.setStyleSheet("color: #555; font-size: 11px;")
        control_bar.addWidget(sep)

        # 비디오 품질 슬라이더 (지연 완화용)
        quality_lbl = QLabel("품질:")
        quality_lbl.setStyleSheet("color: #ccc; font-size: 11px;")
        control_bar.addWidget(quality_lbl)

        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(10, 100)
        self.quality_slider.setValue(80)  # 기본 80%
        self.quality_slider.setFixedWidth(60)
        self.quality_slider.setToolTip("낮을수록 지연↓ 화질↓\n높을수록 지연↑ 화질↑")
        self.quality_slider.valueChanged.connect(self._on_quality_changed)
        control_bar.addWidget(self.quality_slider)

        self.quality_label = QLabel("80%")
        self.quality_label.setStyleSheet("color: #ccc; font-size: 11px;")
        self.quality_label.setFixedWidth(30)
        control_bar.addWidget(self.quality_label)

        # 저지연 모드 버튼
        self.low_latency_mode = False
        self.btn_low_latency = QPushButton("저지연")
        self.btn_low_latency.setToolTip("저지연 모드: 품질↓ 지연↓\n(게임/실시간 작업용)")
        self.btn_low_latency.setStyleSheet("""
            QPushButton {
                background-color: #607D8B;
                color: white;
                padding: 3px 8px;
                border-radius: 3px;
                font-size: 11px;
            }
            QPushButton:hover { background-color: #546E7A; }
        """)
        self.btn_low_latency.clicked.connect(self._toggle_low_latency_mode)
        control_bar.addWidget(self.btn_low_latency)

        btn_close = QPushButton("X")
        btn_close.setStyleSheet("padding: 3px 8px; font-size: 11px; color: #f44;")
        btn_close.clicked.connect(self.close)
        control_bar.addWidget(btn_close)

        self.control_widget.setStyleSheet("background-color: #1a1a1a;")
        self.control_widget.setFixedHeight(28)
        layout.addWidget(self.control_widget)

        # 아이온2 모드 안내 바 - 더 컴팩트
        self.game_mode_bar = QLabel("  아이온2 모드 | 클릭: 잠금 | ALT: 커서 | ESC: 해제")
        self.game_mode_bar.setStyleSheet("""
            background-color: #4CAF50;
            color: white;
            padding: 3px;
            font-weight: bold;
            font-size: 11px;
        """)
        self.game_mode_bar.setFixedHeight(22)
        self.game_mode_bar.hide()
        layout.addWidget(self.game_mode_bar)

        # 웹뷰 (KVM 화면) - 최대 공간 사용 + 성능 최적화
        self.web_view = QWebEngineView()
        self.aion2_page = Aion2WebPage(self.web_view)
        self.web_view.setPage(self.aion2_page)

        settings = self.web_view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)
        # 성능 최적화 설정 - 아이온2 모드용
        settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, False)
        # 추가 최적화
        settings.setAttribute(QWebEngineSettings.WebAttribute.FocusOnNavigationEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AllowWindowActivationFromJavaScript, True)

        web_port = self.device.info.web_port if hasattr(self.device.info, 'web_port') else 80
        url = f"http://{self.device.ip}:{web_port}"
        self.web_view.setUrl(QUrl(url))
        layout.addWidget(self.web_view, 1)  # stretch factor 1 - 최대 공간

        # 페이지 로드 완료 시 처리
        self.web_view.loadFinished.connect(self._on_page_loaded)

    def _toggle_control_bar(self):
        """상단 바 토글"""
        self.control_bar_visible = not self.control_bar_visible
        self.control_widget.setVisible(self.control_bar_visible)

    def _on_page_loaded(self, ok):
        if ok:
            self.status_label.setText(f"{self.device.name} - 연결됨")
            # UI 정리 (비디오만 표시) - 약간의 지연 후 실행
            QTimer.singleShot(500, self._clean_kvm_ui)

    def _clean_kvm_ui(self):
        """PicoKVM UI 정리 - 비디오 스트림만 표시"""
        self.web_view.page().runJavaScript(self.CLEAN_UI_JS, self._on_clean_ui_result)

    def _on_clean_ui_result(self, result):
        """UI 정리 결과"""
        if not result:
            # 비디오를 못 찾으면 1초 후 재시도
            QTimer.singleShot(1000, self._clean_kvm_ui)

    def _toggle_original_ui(self):
        """원본 PicoKVM UI 토글"""
        if self.btn_original_ui.isChecked():
            # 원본 UI 표시
            self.web_view.page().runJavaScript(self.RESTORE_UI_JS)
            self.btn_original_ui.setText("깔끔 UI")
        else:
            # 깔끔 UI (비디오만)
            self._clean_kvm_ui()
            self.btn_original_ui.setText("원본 UI")

    def _toggle_mouse_mode(self):
        """마우스 모드 전환 (Absolute <-> Relative) - Luckfox PicoKVM 지원"""
        self.mouse_mode_absolute = not self.mouse_mode_absolute

        if self.mouse_mode_absolute:
            self.btn_mouse_mode.setText("🖱 Abs")
            self.btn_mouse_mode.setStyleSheet("""
                QPushButton {
                    background-color: #2196F3;
                    color: white;
                    padding: 3px 8px;
                    border-radius: 3px;
                    font-size: 11px;
                }
                QPushButton:hover { background-color: #1976D2; }
            """)
            mode = "abs"
        else:
            self.btn_mouse_mode.setText("🎮 Rel")
            self.btn_mouse_mode.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    padding: 3px 8px;
                    border-radius: 3px;
                    font-size: 11px;
                }
                QPushButton:hover { background-color: #45a049; }
            """)
            mode = "rel"

        # JavaScript를 통한 마우스 모드 변경 (Luckfox PicoKVM)
        self._set_mouse_mode_api(mode)

    def _set_mouse_mode_api(self, mode: str):
        """
        마우스 모드 변경 - Luckfox PicoKVM 지원

        Luckfox PicoKVM은 PiKVM과 다른 펌웨어를 사용합니다.
        JavaScript를 통한 웹 UI 조작 방식을 우선 사용합니다.
        """
        # Luckfox PicoKVM은 HTTP API가 없으므로 JavaScript 방식만 사용
        # (PiKVM API는 호환되지 않음)
        mode_name = "Absolute" if mode == "abs" else "Relative"
        print(f"[WellcomLAND] 마우스 모드 변경: {mode_name} (JavaScript 방식)")

        # JavaScript를 통한 UI 조작은 메인 스레드에서 실행
        QTimer.singleShot(0, lambda: self._apply_mouse_mode_js(mode))

    def _apply_mouse_mode_js(self, mode: str):
        """
        JavaScript로 마우스 모드 변경 - Luckfox PicoKVM Zustand 스토어 직접 접근

        Luckfox PicoKVM 웹 UI 구조:
        - Zustand 스토어: Yt 함수로 상태 접근
        - mouseMode: 'absolute' | 'relative'
        - setMouseMode(mode) 함수로 변경
        """
        is_absolute = mode == "abs"
        mode_text = "absolute" if is_absolute else "relative"

        js = f"""
        (function() {{
            'use strict';
            var targetMode = '{mode_text}';
            console.log('[WellcomLAND] 마우스 모드 변경 시도:', targetMode);

            // 방법 1: React Fiber를 통한 Zustand 스토어 접근
            // Luckfox PicoKVM은 React + Zustand 사용
            try {{
                // React 컴포넌트의 Fiber에서 hooks 찾기
                var findReactFiber = function(dom) {{
                    var key = Object.keys(dom).find(function(k) {{
                        return k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$');
                    }});
                    return key ? dom[key] : null;
                }};

                // video 또는 canvas 요소에서 시작
                var rootEl = document.querySelector('video') || document.querySelector('canvas') || document.body;
                var fiber = findReactFiber(rootEl);

                // Fiber 트리를 순회하며 Zustand 스토어 찾기
                var visited = new Set();
                var findStore = function(node, depth) {{
                    if (!node || depth > 50 || visited.has(node)) return null;
                    visited.add(node);

                    // memoizedState에서 Zustand 훅 찾기
                    var state = node.memoizedState;
                    while (state) {{
                        if (state.memoizedState && typeof state.memoizedState === 'object') {{
                            var s = state.memoizedState;
                            // Zustand 스토어 특성: mouseMode와 setMouseMode 존재
                            if (s.mouseMode !== undefined && typeof s.setMouseMode === 'function') {{
                                return s;
                            }}
                            // getState 함수가 있는 경우 (Zustand store)
                            if (typeof s.getState === 'function') {{
                                var storeState = s.getState();
                                if (storeState.mouseMode !== undefined) {{
                                    return storeState;
                                }}
                            }}
                        }}
                        state = state.next;
                    }}

                    // 자식/형제 노드 탐색
                    return findStore(node.child, depth + 1) ||
                           findStore(node.sibling, depth + 1) ||
                           findStore(node.return, depth + 1);
                }};

                if (fiber) {{
                    var store = findStore(fiber, 0);
                    if (store && store.setMouseMode) {{
                        store.setMouseMode(targetMode);
                        console.log('[WellcomLAND] Zustand 스토어에서 setMouseMode 호출 성공');
                        return 'zustand_fiber';
                    }}
                }}
            }} catch(e) {{
                console.log('[WellcomLAND] React Fiber 접근 실패:', e.message);
            }}

            // 방법 2: 전역 객체에서 스토어 찾기
            try {{
                var globalKeys = Object.keys(window);
                for (var i = 0; i < globalKeys.length; i++) {{
                    var key = globalKeys[i];
                    try {{
                        var obj = window[key];
                        if (obj && typeof obj === 'object') {{
                            // Zustand 스토어 패턴
                            if (typeof obj.getState === 'function' && typeof obj.setState === 'function') {{
                                var state = obj.getState();
                                if (state && 'mouseMode' in state && typeof state.setMouseMode === 'function') {{
                                    state.setMouseMode(targetMode);
                                    console.log('[WellcomLAND] 전역 스토어에서 setMouseMode 호출:', key);
                                    return 'global_store';
                                }}
                            }}
                        }}
                    }} catch(e) {{}}
                }}
            }} catch(e) {{
                console.log('[WellcomLAND] 전역 스토어 검색 실패:', e.message);
            }}

            // 방법 3: localStorage/sessionStorage를 통한 상태 변경 시도
            try {{
                var storageKey = 'kvm-settings';
                var stored = localStorage.getItem(storageKey);
                if (stored) {{
                    var settings = JSON.parse(stored);
                    if (settings.state && settings.state.mouseMode !== undefined) {{
                        settings.state.mouseMode = targetMode;
                        localStorage.setItem(storageKey, JSON.stringify(settings));
                        console.log('[WellcomLAND] localStorage 설정 변경');
                        // 페이지 새로고침 없이 적용하려면 이벤트 발생
                        window.dispatchEvent(new StorageEvent('storage', {{
                            key: storageKey,
                            newValue: JSON.stringify(settings)
                        }}));
                        return 'localStorage';
                    }}
                }}
            }} catch(e) {{
                console.log('[WellcomLAND] localStorage 접근 실패:', e.message);
            }}

            // 방법 4: CustomEvent를 통한 상태 변경 요청
            try {{
                var event = new CustomEvent('wellcomland-mouse-mode', {{
                    detail: {{ mode: targetMode }}
                }});
                document.dispatchEvent(event);
                console.log('[WellcomLAND] CustomEvent 발송');
            }} catch(e) {{}}

            console.log('[WellcomLAND] 마우스 모드 변경 실패 - 수동으로 웹 UI에서 변경하세요');
            console.log('[WellcomLAND] 현재 상태 확인: 설정 메뉴에서 Mouse Mode 옵션을 찾아보세요');
            return null;
        }})();
        """
        self.web_view.page().runJavaScript(js, self._on_mouse_mode_js_result)

    def _on_mouse_mode_js_result(self, result):
        """JavaScript 마우스 모드 변경 결과 처리"""
        if result:
            mode_text = "Absolute" if self.mouse_mode_absolute else "Relative"
            print(f"[WellcomLAND] 마우스 모드 변경 성공 (방법: {result})")
            self.status_label.setText(f"{self.device.name} - {mode_text}")
        else:
            print("[WellcomLAND] 마우스 모드 변경: 웹 UI에서 지원하지 않거나 요소를 찾지 못함")

    def _on_sensitivity_changed(self, value):
        self.sensitivity = value / 10.0
        self.sensitivity_label.setText(f"{self.sensitivity:.1f}")

        # 아이온2 모드 활성화 중이면 민감도 업데이트
        if self.game_mode_active:
            js = f"if(window._aion2Mode) window._aion2Mode.setSensitivity({self.sensitivity});"
            self.web_view.page().runJavaScript(js)

    def _on_quality_changed(self, value):
        """비디오 품질 변경 - 디바운싱 적용 (슬라이더 멈춤 후 500ms 대기)"""
        self.quality_label.setText(f"{value}%")
        self._pending_quality = value

        # 기존 타이머 취소
        if self._quality_timer is not None:
            self._quality_timer.stop()

        # 새 타이머 설정 (500ms 후 실행)
        self._quality_timer = QTimer()
        self._quality_timer.setSingleShot(True)
        self._quality_timer.timeout.connect(self._apply_quality_change)
        self._quality_timer.start(500)

    def _apply_quality_change(self):
        """실제 품질 변경 적용 - WebRTC DataChannel을 통한 JavaScript 방식"""
        if self._pending_quality is None:
            return

        value = self._pending_quality
        self._pending_quality = None

        # 슬라이더 값(10-100)을 Luckfox PicoKVM의 quality factor(0.1-1.0)로 변환
        # 10% -> 0.1, 50% -> 0.5, 100% -> 1.0
        quality_factor = value / 100.0

        # JavaScript로 Zustand 스토어의 rpcDataChannel에 직접 RPC 전송
        # Luckfox PicoKVM은 tr(n=>n.rpcDataChannel)로 DataChannel 접근
        js = f"""
        (function() {{
            'use strict';
            var quality = {quality_factor};
            console.log('[WellcomLAND] 품질 변경 시도:', quality, '(슬라이더:', {value}, '%)');

            // Zustand 스토어에서 rpcDataChannel 찾기
            var findRpcDataChannel = function() {{
                // React Fiber에서 Zustand 스토어 찾기
                var rootEl = document.getElementById('root');
                if (!rootEl) return null;

                var fiberKey = Object.keys(rootEl).find(function(k) {{
                    return k.startsWith('__reactFiber$') || k.startsWith('__reactContainer$');
                }});
                if (!fiberKey) return null;

                var fiber = rootEl[fiberKey];
                var visited = new Set();
                var rpcChannel = null;

                // Fiber 트리 순회
                var traverse = function(node, depth) {{
                    if (!node || depth > 200) return;
                    var nodeId = node.stateNode ? 'has_stateNode' : 'no_stateNode';
                    if (visited.has(node)) return;
                    visited.add(node);

                    // memoizedState 체인 탐색
                    var state = node.memoizedState;
                    var stateCount = 0;
                    while (state && stateCount < 50) {{
                        stateCount++;
                        var s = state.memoizedState;

                        // RTCDataChannel 직접 찾기
                        if (s && s.label === 'rpc' && s.readyState && typeof s.send === 'function') {{
                            rpcChannel = s;
                            console.log('[WellcomLAND] rpcDataChannel 발견! (직접)');
                            return;
                        }}

                        // 객체 내부 탐색
                        if (s && typeof s === 'object') {{
                            // Zustand 스토어 상태 객체
                            if (s.rpcDataChannel && typeof s.rpcDataChannel.send === 'function') {{
                                rpcChannel = s.rpcDataChannel;
                                console.log('[WellcomLAND] rpcDataChannel 발견! (Zustand 스토어)');
                                return;
                            }}
                            // 배열인 경우
                            if (Array.isArray(s)) {{
                                for (var i = 0; i < s.length; i++) {{
                                    if (s[i] && s[i].label === 'rpc' && typeof s[i].send === 'function') {{
                                        rpcChannel = s[i];
                                        console.log('[WellcomLAND] rpcDataChannel 발견! (배열)');
                                        return;
                                    }}
                                }}
                            }}
                            // 일반 객체
                            for (var key in s) {{
                                try {{
                                    var val = s[key];
                                    if (val && val.label === 'rpc' && typeof val.send === 'function') {{
                                        rpcChannel = val;
                                        console.log('[WellcomLAND] rpcDataChannel 발견! (객체 속성:', key, ')');
                                        return;
                                    }}
                                }} catch(e) {{}}
                            }}
                        }}

                        state = state.next;
                    }}

                    // 자식, 형제 노드 탐색
                    if (!rpcChannel) traverse(node.child, depth + 1);
                    if (!rpcChannel) traverse(node.sibling, depth + 1);
                }};

                traverse(fiber, 0);
                return rpcChannel;
            }};

            var dc = findRpcDataChannel();

            if (dc && dc.readyState === 'open') {{
                var msg = JSON.stringify({{
                    jsonrpc: '2.0',
                    id: Date.now(),
                    method: 'setStreamQualityFactor',
                    params: {{ factor: quality }}
                }});
                dc.send(msg);
                console.log('[WellcomLAND] RPC 전송 성공:', msg);
                return 'rpcDataChannel';
            }} else if (dc) {{
                console.log('[WellcomLAND] DataChannel 상태:', dc.readyState);
                return null;
            }}

            console.log('[WellcomLAND] rpcDataChannel을 찾지 못함');
            return null;
        }})();
        """
        self.web_view.page().runJavaScript(js, self._on_quality_js_result)

    def _on_quality_js_result(self, result):
        """JavaScript 품질 변경 결과"""
        if result:
            print(f"[WellcomLAND] 품질 변경 성공 (방법: {result})")
        else:
            print("[WellcomLAND] 품질 변경 실패 - rpcDataChannel을 찾지 못함")

    def _toggle_low_latency_mode(self):
        """
        저지연 모드 토글 - 게임/실시간 작업용 최적화

        적용되는 최적화:
        1. 품질 팩터 최소화 (0.1) - 인코딩 시간 감소
        2. 오디오 비활성화 - 대역폭/처리 부하 감소
        """
        self.low_latency_mode = not self.low_latency_mode

        if self.low_latency_mode:
            # 저지연 모드 활성화
            self._previous_quality = self.quality_slider.value()  # 이전 값 저장
            self.quality_slider.setValue(10)  # 10% = 0.1 factor

            # 오디오 비활성화 (대역폭 절약)
            self._set_audio_mode_js(False)

            self.btn_low_latency.setText("저지연 ✓")
            self.btn_low_latency.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    padding: 3px 8px;
                    border-radius: 3px;
                    font-size: 11px;
                    font-weight: bold;
                }
                QPushButton:hover { background-color: #45a049; }
            """)
            self.status_label.setText(f"{self.device.name} - 저지연")
            print("[WellcomLAND] 저지연 모드 활성화 (품질: 10%, 오디오: OFF)")
        else:
            # 저지연 모드 비활성화: 이전 설정 복원
            previous = getattr(self, '_previous_quality', 80)
            self.quality_slider.setValue(previous)

            # 오디오 복원
            self._set_audio_mode_js(True)

            self.btn_low_latency.setText("저지연")
            self.btn_low_latency.setStyleSheet("""
                QPushButton {
                    background-color: #607D8B;
                    color: white;
                    padding: 3px 8px;
                    border-radius: 3px;
                    font-size: 11px;
                }
                QPushButton:hover { background-color: #546E7A; }
            """)
            self.status_label.setText(f"{self.device.name}")
            print(f"[WellcomLAND] 저지연 모드 비활성화 (품질: {previous}%, 오디오: ON)")

    def _set_audio_mode_js(self, enabled: bool):
        """오디오 모드 설정 - WebRTC DataChannel을 통한 RPC"""
        mode = "pcm" if enabled else "disabled"

        js = f"""
        (function() {{
            'use strict';
            var mode = '{mode}';

            // rpcDataChannel 찾기 (품질 설정과 동일한 방식)
            var findRpcDataChannel = function() {{
                var rootEl = document.getElementById('root');
                if (!rootEl) return null;

                var fiberKey = Object.keys(rootEl).find(function(k) {{
                    return k.startsWith('__reactFiber$') || k.startsWith('__reactContainer$');
                }});
                if (!fiberKey) return null;

                var fiber = rootEl[fiberKey];
                var visited = new Set();
                var rpcChannel = null;

                var traverse = function(node, depth) {{
                    if (!node || depth > 200 || visited.has(node)) return;
                    visited.add(node);

                    var state = node.memoizedState;
                    var stateCount = 0;
                    while (state && stateCount < 50) {{
                        stateCount++;
                        var s = state.memoizedState;

                        if (s && s.label === 'rpc' && typeof s.send === 'function') {{
                            rpcChannel = s;
                            return;
                        }}

                        if (s && typeof s === 'object') {{
                            if (s.rpcDataChannel && typeof s.rpcDataChannel.send === 'function') {{
                                rpcChannel = s.rpcDataChannel;
                                return;
                            }}
                        }}
                        state = state.next;
                    }}

                    if (!rpcChannel) traverse(node.child, depth + 1);
                    if (!rpcChannel) traverse(node.sibling, depth + 1);
                }};

                traverse(fiber, 0);
                return rpcChannel;
            }};

            var dc = findRpcDataChannel();
            if (dc && dc.readyState === 'open') {{
                var msg = JSON.stringify({{
                    jsonrpc: '2.0',
                    id: Date.now(),
                    method: 'setAudioMode',
                    params: {{ mode: mode }}
                }});
                dc.send(msg);
                console.log('[WellcomLAND] 오디오 모드 변경:', mode);
                return true;
            }}
            return false;
        }})();
        """
        self.web_view.page().runJavaScript(js)

    def _toggle_game_mode(self):
        if self.game_mode_active:
            self._stop_game_mode()
        else:
            self._start_game_mode()

    def _start_game_mode(self):
        """아이온2 모드 시작 - Pointer Lock API 사용"""
        self.game_mode_active = True

        # JavaScript로 아이온2 모드 활성화
        js = self.AION2_MODE_JS.replace("%SENSITIVITY%", str(self.sensitivity))
        self.web_view.page().runJavaScript(js, self._on_aion2_mode_result)

        # UI 업데이트
        self.game_mode_bar.show()
        self.btn_game_mode.setText("해제 (ESC)")
        self.btn_game_mode.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                padding: 3px 10px;
                border-radius: 3px;
                font-weight: bold;
                font-size: 11px;
            }
        """)
        self.status_label.setText(f"{self.device.name} - 아이온2")
        self.status_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 11px;")

        # 웹뷰에 포커스
        self.web_view.setFocus()

    def _on_aion2_mode_result(self, result):
        """아이온2 모드 JavaScript 실행 결과"""
        if not result:
            # Pointer Lock 실패 시 대체 메시지
            self.game_mode_bar.setText("  화면 클릭하여 마우스 잠금 | ALT: 커서 | ESC: 해제")

    def _stop_game_mode(self):
        """아이온2 모드 중지"""
        self.game_mode_active = False

        # JavaScript로 아이온2 모드 해제
        self.web_view.page().runJavaScript(self.AION2_STOP_JS)

        # UI 업데이트
        self.game_mode_bar.hide()
        self.btn_game_mode.setText("아이온2 (G)")
        self.btn_game_mode.setStyleSheet("""
            QPushButton {
                background-color: #FF5722;
                color: white;
                padding: 3px 10px;
                border-radius: 3px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover { background-color: #E64A19; }
        """)
        self.status_label.setText(f"{self.device.name}")
        self.status_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 11px;")

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.control_widget.show()
        else:
            self.showFullScreen()
            # 전체화면에서도 컨트롤 바는 유지 (H로 숨길 수 있음)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_G and not self.game_mode_active:
            self._start_game_mode()
        elif event.key() == Qt.Key.Key_H:
            self._toggle_control_bar()
        elif event.key() == Qt.Key.Key_F11:
            self._toggle_fullscreen()
        elif event.key() == Qt.Key.Key_Escape:
            if self.game_mode_active:
                self._stop_game_mode()
            elif self.isFullScreen():
                self.showNormal()
            else:
                self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self._stop_game_mode()
        self.hid.disconnect()
        super().closeEvent(event)


class MainWindow(QMainWindow):
    """메인 애플리케이션 윈도우"""

    def __init__(self):
        super().__init__()

        self.manager = KVMManager()
        self.manager.load_devices_from_db()

        self.status_thread: StatusUpdateThread = None
        self.current_device: KVMDevice = None
        self._initializing = True  # 초기화 중 플래그

        self._init_ui()
        self._create_menus()
        self._create_toolbar()
        self._create_statusbar()
        self._load_device_list()

        # 최초 상태 체크 및 그리드 뷰 초기화 (동기적으로 수행)
        print("[MainWindow] 최초 상태 체크 및 그리드 뷰 초기화 시작...")
        QTimer.singleShot(500, self._initial_status_check)

        # 상태 모니터링은 나중에 시작 (WebEngine 초기화 후)
        QTimer.singleShot(5000, self._start_monitoring)

    def _init_ui(self):
        self.setWindowTitle("WellcomLAND")
        self.setMinimumSize(1400, 900)

        # 윈도우 아이콘 설정 (타이틀바 + 작업표시줄)
        if ICON_PATH:
            import os
            if os.path.exists(ICON_PATH):
                self.setWindowIcon(QIcon(ICON_PATH))

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout(central_widget)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = self._create_device_list_panel()
        splitter.addWidget(left_panel)

        right_panel = self._create_right_panel()
        splitter.addWidget(right_panel)

        splitter.setSizes([280, 1120])
        main_layout.addWidget(splitter)

    def _create_device_list_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        header_layout = QHBoxLayout()
        header_label = QLabel("KVM 장치 목록")
        header_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        header_layout.addWidget(header_label)

        add_btn = QPushButton("+")
        add_btn.setFixedSize(30, 30)
        add_btn.setToolTip("새 장치 추가")
        add_btn.clicked.connect(self._on_add_device)
        header_layout.addWidget(add_btn)

        layout.addLayout(header_layout)

        self.device_tree = QTreeWidget()
        self.device_tree.setHeaderLabels(["이름", "상태"])
        self.device_tree.setColumnWidth(0, 160)
        self.device_tree.itemClicked.connect(self._on_device_selected)
        self.device_tree.itemDoubleClicked.connect(self._on_device_double_clicked)
        self.device_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.device_tree.customContextMenuRequested.connect(self._on_device_context_menu)
        layout.addWidget(self.device_tree)

        self.stats_label = QLabel("전체: 0 | 온라인: 0 | 오프라인: 0")
        layout.addWidget(self.stats_label)

        return panel

    def _create_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        self.tab_widget = QTabWidget()

        # 전체 목록 탭 (그리드 뷰) - 첫 번째 탭으로
        self.grid_view_tab = GridViewTab(self.manager)
        self.grid_view_tab.device_selected.connect(self._on_grid_device_selected)
        self.grid_view_tab.device_double_clicked.connect(self._on_grid_device_double_clicked)
        self.tab_widget.addTab(self.grid_view_tab, "전체 목록")

        self.live_tab = self._create_live_tab()
        self.tab_widget.addTab(self.live_tab, "실시간 제어")

        self.overview_tab = self._create_overview_tab()
        self.tab_widget.addTab(self.overview_tab, "개요")

        self.control_panel = DeviceControlPanel()
        self.tab_widget.addTab(self.control_panel, "키보드/마우스")

        self.monitor_tab = self._create_monitor_tab()
        self.tab_widget.addTab(self.monitor_tab, "모니터")

        self.batch_tab = self._create_batch_tab()
        self.tab_widget.addTab(self.batch_tab, "일괄 작업")

        # 탭 변경 시그널 연결
        self.tab_widget.currentChanged.connect(self._on_tab_changed)

        layout.addWidget(self.tab_widget)
        return panel

    def _on_tab_changed(self, index):
        """탭 변경 시 호출"""
        try:
            # 초기화 중에는 탭 변경 무시
            if hasattr(self, '_initializing') and self._initializing:
                print(f"[MainWindow] _on_tab_changed 무시 (초기화 중)")
                return

            current_widget = self.tab_widget.widget(index)
            # 전체 목록 탭 활성화
            if current_widget == self.grid_view_tab:
                print("[MainWindow] 전체 목록 탭 활성화")
                self.grid_view_tab.on_tab_activated()
            else:
                # 다른 탭으로 이동 시 미리보기 중지
                print("[MainWindow] 다른 탭으로 이동 - 미리보기 중지")
                if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
                    self.grid_view_tab.on_tab_deactivated()
        except Exception as e:
            print(f"[MainWindow] _on_tab_changed 오류: {e}")

    def _create_live_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        info_group = QGroupBox("1:1 실시간 제어 (아이온2 모드 지원)")
        info_layout = QVBoxLayout(info_group)

        info_label = QLabel(
            "<b>아이온2 모드 (3D 게임용):</b><br>"
            "1. 장치 더블클릭 → 실시간 제어 창<br>"
            "2. <span style='color:#FF5722; font-weight:bold;'>아이온2 모드 (G)</span> 버튼 클릭 또는 G 키<br>"
            "3. 화면 클릭 → 마우스 커서 숨김 + <b>무한 회전</b> 활성화<br>"
            "4. <b>ALT 키</b>: 커서 일시 활성화 (UI 클릭용)<br>"
            "5. <b>ESC</b>로 아이온2 모드 해제<br><br>"
            "<b style='color:#4CAF50;'>※ 아이온2 모드 핵심:</b><br>"
            "   • 마우스 커서가 <b>비활성화</b>되고 움직임이 바로 <b>시점 회전</b>됩니다<br>"
            "   • <b>ALT 누르면</b> 커서가 보이고, 놓으면 다시 무한 회전 모드<br>"
            "   • 해상도와 관계없이 <b>무한 회전</b> (화면 끝에서 안 멈춤!)"
        )
        info_label.setWordWrap(True)
        info_layout.addWidget(info_label)

        self.live_device_label = QLabel("선택된 장치: 없음")
        self.live_device_label.setStyleSheet("font-weight: bold; font-size: 16px; margin: 10px;")
        info_layout.addWidget(self.live_device_label)

        layout.addWidget(info_group)

        btn_layout = QHBoxLayout()

        self.btn_start_live = QPushButton("실시간 제어 시작")
        self.btn_start_live.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-size: 16px;
                font-weight: bold;
                padding: 15px 30px;
                border-radius: 8px;
            }
            QPushButton:hover { background-color: #45a049; }
            QPushButton:disabled { background-color: #cccccc; }
        """)
        self.btn_start_live.setEnabled(False)
        self.btn_start_live.clicked.connect(self._on_start_live_control)
        btn_layout.addWidget(self.btn_start_live)

        self.btn_open_web = QPushButton("브라우저에서 열기")
        self.btn_open_web.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                font-size: 14px;
                padding: 15px 30px;
                border-radius: 8px;
            }
            QPushButton:hover { background-color: #1976D2; }
            QPushButton:disabled { background-color: #cccccc; }
        """)
        self.btn_open_web.setEnabled(False)
        self.btn_open_web.clicked.connect(self._on_open_web_browser)
        btn_layout.addWidget(self.btn_open_web)

        layout.addLayout(btn_layout)

        quick_group = QGroupBox("빠른 작업")
        quick_layout = QHBoxLayout(quick_group)

        for text, handler in [("SSH 연결", self._on_connect_device),
                               ("SSH 해제", self._on_disconnect_device),
                               ("USB 재연결", self._on_reconnect_usb),
                               ("재부팅", self._on_reboot_device)]:
            btn = QPushButton(text)
            btn.clicked.connect(handler)
            quick_layout.addWidget(btn)

        layout.addWidget(quick_group)
        layout.addStretch()

        return widget

    def _create_overview_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        info_group = QGroupBox("장치 정보")
        info_layout = QVBoxLayout(info_group)

        self.info_table = QTableWidget(8, 2)
        self.info_table.setHorizontalHeaderLabels(["항목", "값"])
        self.info_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.info_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        for i, prop in enumerate(["이름", "IP 주소", "상태", "USB 상태", "버전", "가동시간", "온도", "메모리"]):
            self.info_table.setItem(i, 0, QTableWidgetItem(prop))
            self.info_table.setItem(i, 1, QTableWidgetItem("-"))

        info_layout.addWidget(self.info_table)
        layout.addWidget(info_group)
        layout.addStretch()

        return widget

    def _create_monitor_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        log_group = QGroupBox("USB 로그")
        log_layout = QVBoxLayout(log_group)

        self.usb_log_text = QTextEdit()
        self.usb_log_text.setReadOnly(True)
        self.usb_log_text.setStyleSheet("font-family: 'Consolas', monospace;")
        log_layout.addWidget(self.usb_log_text)

        btn_refresh_log = QPushButton("로그 새로고침")
        btn_refresh_log.clicked.connect(self._on_refresh_usb_log)
        log_layout.addWidget(btn_refresh_log)

        layout.addWidget(log_group)
        return widget

    def _create_batch_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        group_layout = QHBoxLayout()
        group_layout.addWidget(QLabel("대상:"))
        self.batch_target_combo = QComboBox()
        self.batch_target_combo.addItem("전체 장치")
        group_layout.addWidget(self.batch_target_combo)
        layout.addLayout(group_layout)

        actions_group = QGroupBox("일괄 작업")
        actions_layout = QHBoxLayout(actions_group)

        for text, handler in [("전체 SSH 연결", self._on_connect_all),
                               ("전체 SSH 해제", self._on_disconnect_all),
                               ("전체 상태 새로고침", self._on_refresh_all_status)]:
            btn = QPushButton(text)
            btn.clicked.connect(handler)
            actions_layout.addWidget(btn)

        layout.addWidget(actions_group)

        results_group = QGroupBox("결과")
        results_layout = QVBoxLayout(results_group)
        self.batch_results_table = QTableWidget()
        self.batch_results_table.setColumnCount(3)
        self.batch_results_table.setHorizontalHeaderLabels(["장치", "상태", "결과"])
        self.batch_results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        results_layout.addWidget(self.batch_results_table)
        layout.addWidget(results_group)

        layout.addStretch()
        return widget

    def _create_menus(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("파일")
        add_action = QAction("장치 추가", self)
        add_action.setShortcut("Ctrl+N")
        add_action.triggered.connect(self._on_add_device)
        file_menu.addAction(add_action)

        # 자동 검색 메뉴
        discover_action = QAction("자동 검색...", self)
        discover_action.setShortcut("Ctrl+D")
        discover_action.triggered.connect(self._on_auto_discover)
        file_menu.addAction(discover_action)

        file_menu.addSeparator()
        exit_action = QAction("종료", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        device_menu = menubar.addMenu("장치")
        live_action = QAction("실시간 제어", self)
        live_action.setShortcut("Ctrl+L")
        live_action.triggered.connect(self._on_start_live_control)
        device_menu.addAction(live_action)
        device_menu.addSeparator()
        device_menu.addAction("SSH 연결", self._on_connect_device)
        device_menu.addAction("SSH 해제", self._on_disconnect_device)
        device_menu.addSeparator()
        device_menu.addAction("설정", self._on_device_settings)

        tools_menu = menubar.addMenu("도구")
        tools_menu.addAction("자동 검색...", self._on_auto_discover)
        tools_menu.addSeparator()
        tools_menu.addAction("전체 SSH 연결", self._on_connect_all)
        tools_menu.addAction("전체 SSH 해제", self._on_disconnect_all)
        tools_menu.addSeparator()
        settings_action = QAction("환경 설정...", self)
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self._on_app_settings)
        tools_menu.addAction(settings_action)

        help_menu = menubar.addMenu("도움말")
        help_menu.addAction("WellcomLAND 정보", self._show_about)

    def _create_toolbar(self):
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        toolbar.addAction("장치 추가", self._on_add_device)
        toolbar.addAction("자동 검색", self._on_auto_discover)
        toolbar.addSeparator()
        toolbar.addAction("실시간 제어", self._on_start_live_control)
        toolbar.addSeparator()
        toolbar.addAction("전체 연결", self._on_connect_all)
        toolbar.addAction("새로고침", self._on_refresh_all_status)

    def _create_statusbar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("준비됨")

    def _initial_status_check(self):
        """최초 실행 시 장치 상태 체크 후 그리드 뷰 초기화 (비동기)"""
        try:
            print("[MainWindow] 최초 장치 상태 체크 시작 (백그라운드)...")
            self.status_bar.showMessage("장치 상태 확인 중...")

            # 백그라운드 스레드에서 상태 체크
            self._init_check_thread = InitialStatusCheckThread(self.manager)
            self._init_check_thread.check_completed.connect(self._on_initial_check_done)
            self._init_check_thread.start()

        except Exception as e:
            print(f"[MainWindow] 최초 상태 체크 오류: {e}")
            import traceback
            traceback.print_exc()
            self._initializing = False

    def _on_initial_check_done(self, results: dict):
        """초기 상태 체크 완료 콜백"""
        try:
            print("[MainWindow] 상태 체크 완료, UI 업데이트...")
            # 장치 상태 업데이트
            for device in self.manager.get_all_devices():
                if results.get(device.name, False):
                    device.status = DeviceStatus.ONLINE
                else:
                    device.status = DeviceStatus.OFFLINE

            # UI 업데이트
            self._load_device_list()
            self._init_grid_preview()
            self.status_bar.showMessage("준비됨")
            print("[MainWindow] 최초 상태 체크 완료")

        except Exception as e:
            print(f"[MainWindow] 초기 상태 체크 결과 처리 오류: {e}")
            self._initializing = False

    def _init_grid_preview(self):
        """최초 실행 시 그리드 뷰 미리보기 초기화"""
        try:
            if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
                print("[GridPreview] 초기화 시작...")
                # 전체 목록 탭을 현재 탭으로 설정
                self.tab_widget.setCurrentIndex(0)
                # 직접 on_tab_activated 호출
                self.grid_view_tab.on_tab_activated()
                print("[GridPreview] 초기화 완료")

            # 초기화 완료 - 이제 탭 변경 시그널 허용
            self._initializing = False
            print("[MainWindow] 초기화 완료 - 탭 변경 시그널 활성화")
        except Exception as e:
            print(f"[GridPreview] 초기화 오류: {e}")
            self._initializing = False

    def _load_device_list(self):
        # 현재 확장 상태 저장
        expanded_groups = set()
        for i in range(self.device_tree.topLevelItemCount()):
            item = self.device_tree.topLevelItem(i)
            if item and item.isExpanded():
                expanded_groups.add(item.text(0))

        # 현재 선택된 항목 저장
        selected_device_name = None
        current_item = self.device_tree.currentItem()
        if current_item:
            selected_device_name = current_item.data(0, Qt.ItemDataRole.UserRole)

        # 업데이트 중 시그널 차단
        self.device_tree.blockSignals(True)
        self.device_tree.clear()

        groups = {}
        for device in self.manager.get_all_devices():
            group = device.info.group
            if group not in groups:
                groups[group] = []
            groups[group].append(device)

        item_to_select = None

        for group_name, devices in groups.items():
            group_item = QTreeWidgetItem([group_name, f"({len(devices)}개)"])
            self.device_tree.addTopLevelItem(group_item)

            # 확장 상태 복원 (첫 로드시 또는 이전에 확장되어 있었던 경우)
            if not expanded_groups or group_name in expanded_groups:
                group_item.setExpanded(True)

            for device in devices:
                status_text = "온라인" if device.status == DeviceStatus.ONLINE else "오프라인"
                device_item = QTreeWidgetItem([device.name, status_text])
                device_item.setData(0, Qt.ItemDataRole.UserRole, device.name)
                self._update_device_item_color(device_item, device.status)
                group_item.addChild(device_item)

                # 이전에 선택된 항목 기억
                if selected_device_name and device.name == selected_device_name:
                    item_to_select = device_item

        # 시그널 차단 해제
        self.device_tree.blockSignals(False)

        # 선택 항목 복원 (트리 구성 완료 후)
        if item_to_select:
            self.device_tree.setCurrentItem(item_to_select)

        self._update_statistics()

    def _update_device_item_color(self, item: QTreeWidgetItem, status: DeviceStatus):
        colors = {DeviceStatus.ONLINE: "green", DeviceStatus.OFFLINE: "red"}
        item.setForeground(1, QColor(colors.get(status, "gray")))

    def _update_statistics(self):
        stats = self.manager.get_statistics()
        self.stats_label.setText(f"전체: {stats['total']} | 온라인: {stats['online']} | 오프라인: {stats['offline']}")

    def _start_monitoring(self):
        self.status_thread = StatusUpdateThread(self.manager)
        self.status_thread.status_updated.connect(self._on_status_updated)
        self.status_thread.start()

    def _on_status_updated(self, status: dict):
        # 상태 결과를 장치에 반영
        for device_name, device_status in status.items():
            device = self.manager.get_device(device_name)
            if device:
                if device_status.get('online', False):
                    device.status = DeviceStatus.ONLINE
                else:
                    device.status = DeviceStatus.OFFLINE

        self._load_device_list()
        if self.current_device:
            self._update_device_info()
        # 그리드 뷰 상태 업데이트
        self.grid_view_tab.update_device_status()

    def _on_grid_device_selected(self, device: KVMDevice):
        """그리드 뷰에서 장치 클릭 - 선택만 (탭 이동 없음)"""
        self.current_device = device
        self._update_device_info()
        self.control_panel.set_device(device)

    def _on_grid_device_double_clicked(self, device: KVMDevice):
        """그리드 뷰에서 장치 더블클릭 - 실시간 제어 창 열기"""
        self.current_device = device
        self._on_start_live_control()

    def _on_device_selected(self, item: QTreeWidgetItem, column: int):
        device_name = item.data(0, Qt.ItemDataRole.UserRole)
        if device_name:
            self.current_device = self.manager.get_device(device_name)
            self._update_device_info()
            self._update_live_tab()
            self.control_panel.set_device(self.current_device)

    def _on_device_double_clicked(self, item: QTreeWidgetItem, column: int):
        device_name = item.data(0, Qt.ItemDataRole.UserRole)
        if device_name:
            self.current_device = self.manager.get_device(device_name)
            self._on_start_live_control()

    def _update_live_tab(self):
        if self.current_device:
            self.live_device_label.setText(f"선택된 장치: {self.current_device.name} ({self.current_device.ip})")
            self.btn_start_live.setEnabled(True)
            self.btn_open_web.setEnabled(True)
        else:
            self.live_device_label.setText("선택된 장치: 없음")
            self.btn_start_live.setEnabled(False)
            self.btn_open_web.setEnabled(False)

    def _update_device_info(self):
        if not self.current_device:
            return
        device = self.current_device
        self.info_table.item(0, 1).setText(device.name)
        self.info_table.item(1, 1).setText(device.ip)
        self.info_table.item(2, 1).setText("온라인" if device.status == DeviceStatus.ONLINE else "오프라인")
        self.info_table.item(3, 1).setText("정상" if device.usb_status == USBStatus.CONNECTED else "연결 끊김")
        self.info_table.item(4, 1).setText(device.system_version or "-")

        if device.is_connected():
            info = device.get_system_info()
            self.info_table.item(5, 1).setText(info.get('uptime', '-'))
            temp = info.get('temperature', 0)
            self.info_table.item(6, 1).setText(f"{temp:.1f}°C" if temp else "-")
            mem_used, mem_total = info.get('memory_used', 0), info.get('memory_total', 0)
            self.info_table.item(7, 1).setText(f"{mem_used}/{mem_total} MB" if mem_total else "-")

    def _on_device_context_menu(self, pos):
        item = self.device_tree.itemAt(pos)
        if not item or not item.data(0, Qt.ItemDataRole.UserRole):
            return

        menu = QMenu()
        menu.addAction("실시간 제어", self._on_start_live_control)
        menu.addAction("브라우저에서 열기", self._on_open_web_browser)
        menu.addSeparator()
        menu.addAction("SSH 연결", self._on_connect_device)
        menu.addAction("SSH 해제", self._on_disconnect_device)
        menu.addSeparator()
        menu.addAction("설정", self._on_device_settings)
        menu.addSeparator()
        menu.addAction("삭제", self._on_delete_device)
        menu.exec(self.device_tree.mapToGlobal(pos))

    def _on_start_live_control(self):
        if not self.current_device:
            QMessageBox.warning(self, "경고", "장치를 먼저 선택해주세요.")
            return

        # 1:1 제어 시작 전: 해당 장치의 미리보기 중지
        self._stop_device_preview(self.current_device)

        dialog = LiveViewDialog(self.current_device, self)
        dialog.exec()

        # 1:1 제어 종료 후: 해당 장치의 미리보기 재시작
        self._restart_device_preview(self.current_device)

    def _stop_device_preview(self, device: KVMDevice):
        """특정 장치의 미리보기 중지"""
        if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
            for thumb in self.grid_view_tab.thumbnails:
                if thumb.device.name == device.name:
                    thumb.stop_capture()
                    break

    def _restart_device_preview(self, device: KVMDevice):
        """특정 장치의 미리보기 재시작"""
        if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
            # 전체 목록 탭이 활성화되어 있고 미리보기가 켜져 있을 때만
            if self.grid_view_tab._is_visible and self.grid_view_tab._live_preview_enabled:
                for thumb in self.grid_view_tab.thumbnails:
                    if thumb.device.name == device.name:
                        # 약간의 지연 후 재시작 (WebRTC 연결 정리 대기)
                        QTimer.singleShot(500, thumb.start_capture)
                        break

    def _on_open_web_browser(self):
        if not self.current_device:
            return
        web_port = getattr(self.current_device.info, 'web_port', 80)
        QDesktopServices.openUrl(QUrl(f"http://{self.current_device.ip}:{web_port}"))

    def _on_add_device(self):
        dialog = AddDeviceDialog(self)
        if dialog.exec():
            data = dialog.get_data()
            try:
                self.manager.add_device(**data)
                self._load_device_list()
                self.grid_view_tab.load_devices()  # 그리드 뷰 새로고침
                self.status_bar.showMessage(f"장치 '{data['name']}' 추가됨")
            except Exception as e:
                QMessageBox.critical(self, "오류", f"장치 추가 실패: {e}")

    def _on_auto_discover(self):
        """자동 검색 다이얼로그 열기"""
        # 기존 장치 IP 목록
        existing_ips = [d.ip for d in self.manager.get_all_devices()]

        dialog = AutoDiscoveryDialog(existing_ips, self)
        if dialog.exec():
            selected = dialog.get_selected_devices()
            if not selected:
                return

            added_count = 0
            skipped_count = 0

            for device in selected:
                # 이미 존재하는지 확인
                if device.ip in existing_ips:
                    skipped_count += 1
                    continue

                try:
                    self.manager.add_device(
                        name=device.name,
                        ip=device.ip,
                        port=22,  # SSH 기본 포트
                        web_port=device.port,
                        username="root",
                        password="luckfox",
                        group="auto_discovery"
                    )
                    added_count += 1
                    existing_ips.append(device.ip)
                except Exception as e:
                    print(f"장치 추가 실패 ({device.ip}): {e}")

            # UI 새로고침
            self._load_device_list()
            self.grid_view_tab.load_devices()

            # 결과 메시지
            msg = f"{added_count}개 장치 추가됨"
            if skipped_count > 0:
                msg += f" (중복 {skipped_count}개 제외)"
            self.status_bar.showMessage(msg)

            if added_count > 0:
                QMessageBox.information(self, "자동 검색 완료", msg)

    def _on_delete_device(self):
        if not self.current_device:
            return
        if QMessageBox.question(self, "삭제 확인", f"'{self.current_device.name}' 삭제?",
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.manager.remove_device(self.current_device.name)
            self.current_device = None
            self._load_device_list()
            self.grid_view_tab.load_devices()  # 그리드 뷰 새로고침
            self._update_live_tab()

    def _on_device_settings(self):
        if self.current_device:
            DeviceSettingsDialog(self.current_device, self).exec()

    def _on_connect_device(self):
        if not self.current_device:
            return
        self.status_bar.showMessage(f"{self.current_device.name} SSH 연결 중...")
        if self.current_device.connect():
            self.status_bar.showMessage(f"{self.current_device.name} SSH 연결됨")
        else:
            self.status_bar.showMessage(f"{self.current_device.name} SSH 연결 실패")
        self._load_device_list()
        self._update_device_info()

    def _on_disconnect_device(self):
        if self.current_device:
            self.current_device.disconnect()
            self._load_device_list()
            self._update_device_info()
            self.status_bar.showMessage(f"{self.current_device.name} SSH 해제됨")

    def _on_reboot_device(self):
        if not self.current_device:
            return
        if QMessageBox.question(self, "재부팅 확인", f"'{self.current_device.name}' 재부팅?",
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            if not self.current_device.is_connected():
                self.current_device.connect()
            self.current_device.reboot()
            self.status_bar.showMessage(f"{self.current_device.name} 재부팅 중...")

    def _on_reconnect_usb(self):
        if self.current_device:
            if not self.current_device.is_connected():
                self.current_device.connect()
            self.current_device.reconnect_usb()
            self.status_bar.showMessage(f"{self.current_device.name} USB 재연결됨")

    def _on_refresh_usb_log(self):
        if self.current_device:
            if not self.current_device.is_connected():
                self.current_device.connect()
            self.usb_log_text.setText(self.current_device.get_dmesg_usb(50))

    def _on_connect_all(self):
        self.status_bar.showMessage("전체 SSH 연결 중...")
        results = self.manager.connect_all()
        success = sum(1 for v in results.values() if v)
        self.status_bar.showMessage(f"{success}/{len(results)}개 SSH 연결됨")
        self._load_device_list()

    def _on_disconnect_all(self):
        self.manager.disconnect_all()
        self._load_device_list()
        self.status_bar.showMessage("전체 SSH 해제됨")

    def _on_refresh_all_status(self):
        """상태 새로고침 (백그라운드 스레드에서 실행)"""
        try:
            self.status_bar.showMessage("상태 새로고침 중...")

            # 백그라운드 스레드에서 상태 체크 실행
            def do_refresh():
                import socket
                results = {}
                for device in self.manager.get_all_devices():
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(2)  # 2초 타임아웃
                        result = sock.connect_ex((device.ip, device.info.web_port))
                        sock.close()
                        results[device.name] = result == 0
                    except Exception:
                        results[device.name] = False
                return results

            def on_refresh_done(future):
                try:
                    results = future.result()
                    # UI 업데이트는 메인 스레드에서
                    for device in self.manager.get_all_devices():
                        if results.get(device.name, False):
                            device.status = DeviceStatus.ONLINE
                        else:
                            device.status = DeviceStatus.OFFLINE

                    self._load_device_list()
                    if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
                        self.grid_view_tab.update_device_status()
                    self.status_bar.showMessage("상태 새로고침 완료")
                except Exception as e:
                    print(f"[MainWindow] 새로고침 결과 처리 오류: {e}")
                    self.status_bar.showMessage("새로고침 오류")

            from concurrent.futures import ThreadPoolExecutor
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(do_refresh)
            future.add_done_callback(lambda f: QTimer.singleShot(0, lambda: on_refresh_done(f)))
            executor.shutdown(wait=False)

        except Exception as e:
            print(f"[MainWindow] 새로고침 오류: {e}")
            self.status_bar.showMessage("새로고침 오류")

    def _on_app_settings(self):
        """환경 설정 다이얼로그 열기"""
        dialog = AppSettingsDialog(self)
        dialog.exec()

    def _show_about(self):
        from version import __version__
        QMessageBox.about(self, "WellcomLAND 정보",
                          f"<h2>WellcomLAND</h2><p>버전 {__version__}</p>"
                          "<p>다중 KVM 장치 관리 솔루션</p>"
                          "<hr><p><b>아이온2 모드 (G 키):</b></p>"
                          "<p>• 마우스 커서 비활성화</p>"
                          "<p>• 마우스 움직임 = 시점 회전</p>"
                          "<p>• 무한 회전 (화면 끝에서 안 멈춤)</p>"
                          "<p>• ESC로 해제</p>"
                          "<hr><p><small>아이온2 게임과 동일한 조작 방식</small></p>")

    def closeEvent(self, event):
        try:
            # 상태 모니터링 스레드 종료
            if self.status_thread:
                self.status_thread.stop()
                self.status_thread.wait(3000)  # 최대 3초 대기

            # 그리드 뷰 웹뷰 정리
            if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
                try:
                    self.grid_view_tab.cleanup()
                except Exception as e:
                    print(f"[MainWindow] grid_view_tab cleanup 오류: {e}")

            # 모든 SSH 연결 해제
            try:
                self.manager.disconnect_all()
            except Exception as e:
                print(f"[MainWindow] disconnect_all 오류: {e}")

        except Exception as e:
            print(f"[MainWindow] closeEvent 오류: {e}")

        event.accept()
