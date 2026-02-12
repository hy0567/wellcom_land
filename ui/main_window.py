"""
WellcomLAND 메인 윈도우
아이온2 모드 지원 - 마우스 커서 비활성화 + 무한 회전
"""

import math
import os
import struct
import sys
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QToolBar, QStatusBar, QMenuBar, QMenu, QMessageBox,
    QTreeWidget, QTreeWidgetItem, QTabWidget, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QLineEdit, QSpinBox, QComboBox, QTextEdit, QProgressBar,
    QDialog, QDialogButtonBox, QApplication, QSlider, QFrame,
    QScrollArea, QGridLayout, QSizePolicy, QInputDialog
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread, QUrl, QPoint, QRect, QByteArray
from PyQt6.QtGui import QAction, QIcon, QColor, QDesktopServices, QCursor, QPainter, QBrush, QPen, QPixmap, QShortcut, QKeySequence
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage, QWebEngineScript
from PyQt6.QtWebChannel import QWebChannel

from core import KVMManager, KVMDevice
from core.kvm_device import DeviceStatus, USBStatus
from core.hid_controller import FastHIDController
from .dialogs import AddDeviceDialog, DeviceSettingsDialog, AutoDiscoveryDialog, AppSettingsDialog
from config import settings as app_settings, ICON_PATH, LOG_DIR
from .device_control import DeviceControlPanel
from .admin_panel import AdminPanel

try:
    from vision import VisionController, DetectionOverlay
    VISION_AVAILABLE = True
except ImportError:
    VISION_AVAILABLE = False


class InitialStatusCheckThread(QThread):
    """최초 상태 체크 스레드 (병렬 TCP 체크)

    릴레이 경유(100.x) 장치는 타임아웃을 3초로 늘림.
    서버 API heartbeat 정보도 병행 참조.
    50개+ 장치: ThreadPoolExecutor로 병렬 처리.
    """
    check_completed = pyqtSignal(dict)

    def __init__(self, manager: KVMManager):
        super().__init__()
        self.manager = manager

    def _check_single(self, device, server_status: dict) -> tuple:
        """단일 장치 TCP 체크 (병렬 워커용)"""
        import socket
        try:
            ip = device.ip
            port = device.info.web_port
            is_relay = ip.startswith('100.')
            timeout = 3.0 if is_relay else 1.0

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((ip, port))
            sock.close()

            if result == 0:
                return device.name, True
            else:
                srv_online = server_status.get(device.name, False)
                if srv_online and is_relay:
                    return device.name, True
                return device.name, False
        except Exception:
            srv_online = server_status.get(device.name, False)
            return device.name, bool(srv_online)

    def run(self):
        results = {}

        # 서버 heartbeat 상태 조회
        server_status = {}
        try:
            from api_client import api_client
            if api_client.is_logged_in:
                remote_kvms = api_client.get_remote_kvm_list()
                if remote_kvms:
                    for rkvm in remote_kvms:
                        name = rkvm.get('kvm_name', '')
                        if name:
                            server_status[name] = bool(rkvm.get('is_online'))
        except Exception:
            pass

        devices = self.manager.get_all_devices()

        if len(devices) <= 20:
            # 소규모: 순차 처리
            for device in devices:
                name, online = self._check_single(device, server_status)
                results[name] = online
                print(f"  - {name}: {'ONLINE' if online else 'OFFLINE'}")
        else:
            # 대규모: 병렬 처리
            workers = min(20, len(devices))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(self._check_single, d, server_status): d
                    for d in devices
                }
                for future in futures:
                    try:
                        name, online = future.result(timeout=5)
                        results[name] = online
                        print(f"  - {name}: {'ONLINE' if online else 'OFFLINE'}")
                    except Exception as e:
                        device = futures[future]
                        results[device.name] = False
                        print(f"  - {device.name}: OFFLINE (오류: {e})")

        self.check_completed.emit(results)


class StatusUpdateThread(QThread):
    """백그라운드 상태 업데이트 스레드 (병렬 TCP 체크)

    1. 로컬 KVM (192.168.x): TCP 포트 체크 (1초 타임아웃)
    2. 릴레이 KVM (100.x): TCP 포트 체크 (3초 타임아웃) + 서버 API 병행
    3. 서버 heartbeat 정보로 보완 (TCP 실패 시 서버 is_online 참조)
    4. 50개+ 장치: ThreadPoolExecutor로 병렬 처리 (순차→병렬, 최대 20 워커)
    """
    status_updated = pyqtSignal(dict)

    def __init__(self, manager: KVMManager):
        super().__init__()
        self.manager = manager
        self.running = True
        self._server_status_cache = {}  # kvm_name → is_online (서버 API 캐시)
        self._server_check_counter = 0  # 서버 API 호출 주기 카운터

    def run(self):
        # 첫 실행 시 충분히 대기 (UI/WebEngine 초기화 완료 후)
        self.msleep(5000)

        while self.running:
            try:
                # 매 6회(30초)마다 서버 API에서 온라인 상태 갱신
                self._server_check_counter += 1
                if self._server_check_counter >= 6:
                    self._server_check_counter = 0
                    self._refresh_server_status()

                # TCP 포트 체크 (병렬)
                status = self._check_status_parallel()
                self.status_updated.emit(status)
            except Exception as e:
                print(f"상태 업데이트 오류: {e}")

            # 장치 수에 따라 모니터링 간격 조정 (20대 이하: 5초, 50대 이상: 10초)
            device_count = len(self.manager.get_all_devices())
            interval = 5000 if device_count <= 20 else (8000 if device_count <= 50 else 10000)
            self.msleep(interval)

    def _refresh_server_status(self):
        """서버 API에서 KVM 온라인 상태 가져오기 (heartbeat 기반)"""
        try:
            from api_client import api_client
            if not api_client.is_logged_in:
                return
            remote_kvms = api_client.get_remote_kvm_list()
            if remote_kvms:
                for rkvm in remote_kvms:
                    name = rkvm.get('kvm_name', '')
                    if name:
                        self._server_status_cache[name] = bool(rkvm.get('is_online'))
        except Exception:
            pass

    def _check_single_device(self, device) -> tuple:
        """단일 장치 TCP 체크 (병렬 실행용)"""
        import socket
        try:
            ip = device.ip
            port = device.info.web_port
            is_relay = ip.startswith('100.')
            timeout = 3.0 if is_relay else 1.0

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((ip, port))
            sock.close()

            if result == 0:
                return device.name, {'online': True}
            else:
                server_online = self._server_status_cache.get(device.name, None)
                if server_online is True and is_relay:
                    return device.name, {'online': True}
                else:
                    return device.name, {'online': False}
        except Exception:
            server_online = self._server_status_cache.get(device.name, False)
            return device.name, {'online': bool(server_online)}

    def _check_status_parallel(self) -> dict:
        """병렬 TCP 상태 체크 (50개+ 장치 대응)

        ThreadPoolExecutor로 모든 장치를 병렬 TCP 체크.
        20대 이하: 순차 (오버헤드 최소화)
        20대 초과: 병렬 (최대 20 워커)
        """
        devices = self.manager.get_all_devices()
        results = {}

        if len(devices) <= 20:
            # 소규모: 순차 처리 (스레드풀 오버헤드 회피)
            for device in devices:
                if not self.running:
                    break
                name, status = self._check_single_device(device)
                results[name] = status
        else:
            # 대규모: 병렬 처리
            workers = min(20, len(devices))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(self._check_single_device, d): d for d in devices}
                for future in futures:
                    if not self.running:
                        break
                    try:
                        name, status = future.result(timeout=5)
                        results[name] = status
                    except Exception:
                        device = futures[future]
                        results[device.name] = {'online': False}

        return results

    def stop(self):
        self.running = False


class SFTPUploadThread(QThread):
    """SFTP 파일 업로드 스레드"""
    progress = pyqtSignal(int, str)   # (percent, label)
    finished_ok = pyqtSignal(str)     # success message
    finished_err = pyqtSignal(str)    # error message

    def __init__(self, device, local_path, remote_path):
        super().__init__()
        self.device = device
        self.local_path = local_path
        self.remote_path = remote_path

    def run(self):
        import os
        try:
            filename = os.path.basename(self.local_path)

            self.progress.emit(0, f"{filename}\nSSH 연결 중...")

            def on_progress(transferred, total):
                if total > 0:
                    pct = int((transferred / total) * 100)
                    if total < 1024 * 1024:
                        txt = f"{filename}\n{transferred//1024}KB / {total//1024}KB"
                    else:
                        txt = f"{filename}\n{transferred/(1024*1024):.1f}MB / {total/(1024*1024):.1f}MB"
                    self.progress.emit(pct, txt)

            # upload_file_sftp가 자체 SSH 연결을 생성 (lock 간섭 없음)
            ok = self.device.upload_file_sftp(self.local_path, self.remote_path, on_progress)
            if ok:
                self.finished_ok.emit(f"'{filename}' → {self.device.name}:{self.remote_path}")
            else:
                self.finished_err.emit("SFTP 업로드 실패")
        except Exception as e:
            self.finished_err.emit(str(e))


class CloudUploadThread(QThread):
    """클라우드 파일 업로드 스레드"""
    finished_ok = pyqtSignal(str)
    finished_err = pyqtSignal(str)

    def __init__(self, local_path):
        super().__init__()
        self.local_path = local_path

    def run(self):
        try:
            import os
            from api_client import api_client
            filename = os.path.basename(self.local_path)
            result = api_client.upload_file(self.local_path)
            self.finished_ok.emit(f"'{filename}' 클라우드 업로드 완료")
        except Exception as e:
            self.finished_err.emit(str(e))


class USBWorkerThread(QThread):
    """USB Mass Storage 작업 스레드 (파일목록/마운트/해제/클라우드)"""
    files_ready = pyqtSignal(list)      # 파일 목록 결과
    cloud_files_ready = pyqtSignal(list) # 클라우드 파일 목록 결과
    progress = pyqtSignal(str)          # 상태 메시지
    finished_ok = pyqtSignal(str)       # 성공 메시지
    finished_err = pyqtSignal(str)      # 실패 메시지

    # 작업 모드
    MODE_LIST = "list"
    MODE_MOUNT = "mount"
    MODE_EJECT = "eject"
    MODE_CLOUD_LIST = "cloud_list"
    MODE_CLOUD_MOUNT = "cloud_mount"  # 클라우드 다운로드 + 마운트

    def __init__(self, device, mode="list", file_path=None,
                 download_url=None, token=None, filename=None):
        super().__init__()
        self.device = device
        self.mode = mode
        self.file_path = file_path
        self.download_url = download_url
        self.token = token
        self.filename = filename

    def run(self):
        try:
            if self.mode == self.MODE_CLOUD_LIST:
                # 클라우드 파일 목록 (SSH 불필요, API 호출)
                try:
                    from api_client import api_client
                    files = api_client.get_files()
                    self.cloud_files_ready.emit(files)
                except Exception as e:
                    self.cloud_files_ready.emit([])
                return

            if self.mode == self.MODE_CLOUD_MOUNT:
                # 클라우드 → KVM 다운로드 → 마운트
                self.progress.emit("다운로드 중...")
                dest = f"/tmp/{self.filename}"
                ok, msg = self.device.download_from_url(
                    self.download_url, dest, self.token
                )
                if not ok:
                    self.finished_err.emit(f"다운로드 실패: {msg}")
                    return

                self.progress.emit("USB 마운트 중...")
                ok, msg = self.device.mount_usb_mass_storage(dest)
                if ok:
                    self.finished_ok.emit(msg)
                else:
                    self.finished_err.emit(msg)
                return

            # 기존 로컬 모드 — SSH 필요
            import paramiko
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                self.device.info.ip,
                port=self.device.info.port,
                username=self.device.info.username,
                password=self.device.info.password,
                timeout=10
            )

            try:
                if self.mode == self.MODE_LIST:
                    stdin, stdout, stderr = ssh.exec_command(
                        "ls -1p /tmp/ 2>/dev/null | grep -v '/$' | grep -v -E '^(usb_drive\\.img)$'",
                        timeout=10
                    )
                    out = stdout.read().decode().strip()
                    files = [f.strip() for f in out.split('\n') if f.strip()] if out else []
                    self.files_ready.emit(files)

                elif self.mode == self.MODE_MOUNT:
                    self.progress.emit("USB 마운트 중...")
                    ok, msg = self.device.mount_usb_mass_storage(self.file_path)
                    if ok:
                        self.finished_ok.emit(msg)
                    else:
                        self.finished_err.emit(msg)

                elif self.mode == self.MODE_EJECT:
                    self.progress.emit("USB 해제 중...")
                    ok, msg = self.device.unmount_usb_mass_storage()
                    if ok:
                        self.finished_ok.emit(msg)
                    else:
                        self.finished_err.emit(msg)
            finally:
                ssh.close()

        except Exception as e:
            if self.mode in (self.MODE_LIST,):
                self.files_ready.emit([])
            elif self.mode == self.MODE_CLOUD_LIST:
                self.cloud_files_ready.emit([])
            else:
                self.finished_err.emit(str(e))


class KVMThumbnailWidget(QFrame):
    """KVM 장치 썸네일 위젯 - WebRTC 실시간 미리보기 (저비트레이트)"""
    clicked = pyqtSignal(object)  # KVMDevice
    double_clicked = pyqtSignal(object)  # KVMDevice
    right_clicked = pyqtSignal(object, object)  # KVMDevice, QPoint (global pos)

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

            _videoDone = true;
            return true;
        }

        // 4-1. 썸네일 저FPS 모드: pause/play 사이클 (CPU 대폭 절약)
        // WebRTC 수신 트랙은 applyConstraints가 무시되므로
        // video를 주기적으로 pause→play 반복하여 실질 FPS를 ~1fps로 제한
        var _fpsLimitId = null;
        function startLowFpsMode() {
            if (_fpsLimitId) return;
            var video = document.querySelector('video');
            if (!video) return;

            function tick() {
                if (!video || !video.srcObject) return;
                // 잠깐 play → 200ms 후 pause (초당 ~1프레임만 렌더)
                video.play().catch(function(){});
                setTimeout(function() {
                    if (video && video.srcObject) {
                        video.pause();
                    }
                }, 200);
                _fpsLimitId = setTimeout(tick, 2000);
            }
            // 최초 1회 play 후 사이클 시작
            video.play().catch(function(){});
            _fpsLimitId = setTimeout(tick, 2000);
        }

        // 4. 저품질 설정 (10% = 약 660Kbps)
        // ★ CPU 최적화: Fiber 탐색 횟수 제한 + 캐싱
        var _qualityAttempts = 0;
        var _cachedRpc = null;
        function setLowQuality() {
            if (_qualityDone) return true;

            // 최대 10회까지만 Fiber 탐색 시도 (CPU 보호)
            _qualityAttempts++;
            if (_qualityAttempts > 10) {
                _qualityDone = true;  // 포기 — 더 이상 탐색 안함
                return true;
            }

            // 캐싱된 RPC 채널 재사용
            if (_cachedRpc && _cachedRpc.readyState === 'open') {
                _cachedRpc.send(JSON.stringify({
                    jsonrpc: '2.0', id: Date.now(),
                    method: 'setStreamQualityFactor',
                    params: { factor: 0.05 }
                }));
                _qualityDone = true;
                return true;
            }

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
                                _cachedRpc = rpc;
                                rpc.send(JSON.stringify({
                                    jsonrpc: '2.0',
                                    id: Date.now(),
                                    method: 'setStreamQualityFactor',
                                    params: { factor: 0.05 }
                                }));
                                _qualityDone = true;
                                return true;
                            }
                        }
                        state = state.next;
                    }
                }

                if (current.child) queue.push(current.child);
                if (current.sibling) queue.push(current.sibling);
                if (visited.size > 200) break;  // 탐색 범위 축소 (500→200)
            }
            return false;
        }

        // 5. 메인 루프 (완료 시 즉시 중단 — CPU 최적화)
        var attempts = 0;
        function loop() {
            attempts++;
            injectCSS();
            blockInput();
            var videoReady = setupVideo();
            var qualityReady = setLowQuality();

            // video + CSS 준비 완료 시그널 (Python 폴링용)
            if (_cssDone && _videoDone) {
                window._thumbReady = true;
            }

            // ★ 모든 작업 완료 시 저FPS 모드 시작 + 루프 중단
            if (_cssDone && _videoDone && _qualityDone && _inputBlocked) {
                startLowFpsMode();
                return;
            }

            if (attempts < 30) {
                // 적응형 간격: 초기 빠르게, 이후 느리게
                var delay = attempts < 5 ? 300 : (attempts < 15 ? 1000 : 2000);
                setTimeout(loop, delay);
            }
        }

        setTimeout(loop, 2000);

    })();
    """

    # 크롭용 JS 템플릿: video의 CSS만 변경 (DOM 이동 없음, body overflow:hidden 활용)
    CROP_JS_TEMPLATE = """
    (function() {{
        var cs = document.getElementById('_cropStyle');
        if (!cs) {{
            cs = document.createElement('style');
            cs.id = '_cropStyle';
            document.head.appendChild(cs);
        }}
        cs.textContent = `
            video {{
                width: {wvw}vw !important;
                height: {hvh}vh !important;
                left: {lvw}vw !important;
                top: {tvh}vh !important;
                object-fit: fill !important;
            }}
        `;
    }})();
    """

    def __init__(self, device: KVMDevice, parent=None):
        super().__init__(parent)
        self.device = device
        self._is_active = False
        self._is_paused = False
        self._use_preview = True
        self._webview = None
        self._crop_region = None  # (x, y, w, h) or None
        self._stream_status = "idle"  # idle, loading, connected, dead
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

        # 장치 이름 라벨 (상태 색상 점 포함)
        self.name_label = QLabel(self.device.name)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setTextFormat(Qt.TextFormat.RichText)
        self.name_label.setStyleSheet("""
            background-color: #333;
            color: white;
            font-size: 10px;
            font-weight: bold;
            padding: 2px;
        """)
        self._update_name_label()
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

            # 설정 (CPU 최적화: 불필요한 기능 비활성화)
            settings = self._webview.settings()
            settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
            settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)
            # CPU 절약: 불필요한 기능 끄기
            settings.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages, False)
            settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)

            # 로드 완료 시 JS 실행
            self._webview.loadFinished.connect(self._on_load_finished)

            # 렌더 프로세스 종료 감지
            page.renderProcessTerminated.connect(self._on_render_terminated)

            # 레이아웃에서 status_label 교체
            layout = self.layout()
            layout.replaceWidget(self.status_label, self._webview)
            self.status_label.hide()
        except Exception as e:
            print(f"[Thumbnail] _create_webview 오류: {e}")
            self._webview = None

    def _inject_ice_patch_thumbnail(self):
        """릴레이 접속 시 WebRTC ICE candidate 패치 (thumbnail용)"""
        try:
            relay_ip = self.device.ip
            web_port = self.device.info.web_port
            # UDP 릴레이 포트
            udp_port = getattr(self.device.info, '_udp_relay_port', None)
            if not udp_port:
                udp_port = 28000 + (web_port - 18000) if web_port >= 18000 else 28000 + int(relay_ip.split('.')[-1])
            tcp_port = web_port

            ice_js = (
                "(function(){var R='%s',U=%d,T=%d,_n=0;"
                # notifyUdpPort function
                "function N(p){if(_n===p)return;_n=p;"
                "fetch('http://'+R+':'+T+'/_wellcomland/set_udp_port?port='+p,{mode:'no-cors'}).catch(function(){})}"
                "var O=window.RTCPeerConnection;"
                "window.RTCPeerConnection=function(c){var p=new O(c);"
                "var oa=p.addIceCandidate.bind(p);"
                "p.addIceCandidate=function(cd){"
                "if(cd&&cd.candidate){"
                "var s=cd.candidate.replace(/(\\\\d{1,3}\\\\.\\\\d{1,3}\\\\.\\\\d{1,3}\\\\.\\\\d{1,3})\\\\s+(\\\\d+)\\\\s+typ\\\\s+host/g,"
                "function(m,ip,pt){if(ip===R)return m;N(parseInt(pt));return R+' '+U+' typ host'});"
                "if(s!==cd.candidate)cd=new RTCIceCandidate({candidate:s,sdpMid:cd.sdpMid,sdpMLineIndex:cd.sdpMLineIndex})}"
                "return oa(cd)};"
                "var os=p.setRemoteDescription.bind(p);"
                "p.setRemoteDescription=function(d){"
                "if(d&&d.sdp){var s=d.sdp;"
                "s=s.replace(/c=IN IP4 (\\\\d+\\\\.\\\\d+\\\\.\\\\d+\\\\.\\\\d+)/g,"
                "function(m,ip){return ip==='0.0.0.0'||ip===R?m:'c=IN IP4 '+R});"
                "d=new RTCSessionDescription({type:d.type,sdp:s})}"
                "return os(d)};"
                "return p};"
                "window.RTCPeerConnection.prototype=O.prototype;"
                "window.RTCPeerConnection.generateCertificate=O.generateCertificate;"
                "})();" % (relay_ip, udp_port, tcp_port)
            )

            script = QWebEngineScript()
            script.setName("wellcomland-ice-patch-thumb")
            script.setSourceCode(ice_js)
            script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            script.setRunsOnSubFrames(True)

            scripts = self._webview.page().scripts()
            for old in scripts.find("wellcomland-ice-patch-thumb"):
                scripts.remove(old)
            scripts.insert(script)
        except Exception as e:
            print(f"[Thumbnail] ICE patch 주입 실패: {e}")

    def _on_permission_requested(self, origin, feature):
        """WebRTC 등 권한 자동 허용"""
        page = self.sender()
        # 모든 미디어 권한 허용 (MediaAudioCapture, MediaVideoCapture, MediaAudioVideoCapture 등)
        page.setFeaturePermission(origin, feature, QWebEnginePage.PermissionPolicy.PermissionGrantedByUser)

    def _on_load_finished(self, ok):
        """WebView 로드 완료"""
        # 비활성 상태면 무시 (stop 후 about:blank 로드 이벤트 차단)
        if not self._is_active:
            return
        print(f"[Thumbnail] _on_load_finished: ok={ok}, device={self.device.name}, crop={self._crop_region}")
        if ok and self._webview:
            self._stream_status = "connected"
            self._update_name_label()
            self._webview.page().runJavaScript(self.THUMBNAIL_JS)
            # 크롭 설정이 있으면 THUMBNAIL_JS 준비 완료 후 크롭 적용 (폴링)
            if self._crop_region:
                print(f"[Thumbnail] 크롭 폴링 시작 예약 (500ms): {self.device.name}")
                QTimer.singleShot(500, lambda: self._poll_and_inject_crop(0))
        elif not ok and self._webview:
            self._stream_status = "dead"
            self._update_name_label()
            print(f"[Thumbnail] 로드 실패: {self.device.name}")

    def start_capture(self):
        """미리보기 시작"""
        try:
            # 1:1 제어 중인 장치는 미리보기 차단 (WebRTC 단일 스트림 충돌 방지)
            main_win = self.window()
            if hasattr(main_win, '_live_control_device') and main_win._live_control_device == self.device.name:
                print(f"[Thumbnail] start_capture 차단 (1:1 제어 중): {self.device.name}")
                return
            if self._is_active:
                print(f"[Thumbnail] start_capture 건너뜀 (이미 활성): {self.device.name}")
                return
            self._is_active = True
            self._stream_status = "loading"
            self._update_name_label()

            if self.device.status == DeviceStatus.ONLINE and self._use_preview:
                self._create_webview()
                if self._webview:
                    self._webview.show()
                    url = f"http://{self.device.ip}:{self.device.info.web_port}/"
                    print(f"[Thumbnail] start_capture: {self.device.name} → {url} (crop={self._crop_region})")
                    # 릴레이 접속 시 ICE 패치 주입
                    if self.device.ip.startswith('100.'):
                        self._inject_ice_patch_thumbnail()
                    self._webview.setUrl(QUrl(url))
                    self.status_label.hide()
            else:
                self._stream_status = "idle"
                self._update_name_label()
                self._update_status_display()
                print(f"[Thumbnail] start_capture: {self.device.name} — 오프라인 또는 미리보기 비활성")
        except Exception as e:
            print(f"[Thumbnail] start_capture 오류: {e}")
            self._is_active = False

    def stop_capture(self):
        """미리보기 완전 중지 (WebView 언로드 — WebRTC 연결 해제)"""
        try:
            self._is_active = False
            self._is_paused = False
            self._stream_status = "idle"
            self._update_name_label()
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

    def set_crop_region(self, region):
        """부분제어 크롭 영역 설정 (None이면 해제)"""
        self._crop_region = region
        if self._webview and self._is_active:
            if region:
                self._poll_and_inject_crop(0)
            else:
                self._clear_crop_css()

    def _inject_crop_css(self):
        """크롭 CSS 주입 (video DOM 이동 없이 CSS만 변경)"""
        if not self._crop_region or not self._webview:
            return
        x, y, w, h = self._crop_region
        # video 확대: 1/w, 1/h 배
        wvw = (1.0 / w) * 100.0   # width in vw
        hvh = (1.0 / h) * 100.0   # height in vh
        # video 위치 이동: -x/w, -y/h
        lvw = -(x / w) * 100.0    # left in vw
        tvh = -(y / h) * 100.0    # top in vh
        js = self.CROP_JS_TEMPLATE.format(wvw=wvw, hvh=hvh, lvw=lvw, tvh=tvh)
        try:
            self._webview.page().runJavaScript(js)
        except Exception:
            pass

    def _clear_crop_css(self):
        """크롭 CSS 제거 (원래 THUMBNAIL_JS 스타일로 복원)"""
        if not self._webview:
            return
        js = """
        (function() {
            var cs = document.getElementById('_cropStyle');
            if (cs) cs.remove();
        })();
        """
        try:
            self._webview.page().runJavaScript(js)
        except Exception:
            pass

    def _poll_and_inject_crop(self, attempt):
        """THUMBNAIL_JS 준비 완료를 폴링 후 크롭 CSS 주입 (적응형 간격)"""
        if not self._crop_region or not self._webview or not self._is_active:
            return
        if attempt >= 20:
            # 타임아웃 — 폴백으로 강제 주입
            print(f"[Thumbnail] crop 폴링 타임아웃, 강제 주입: {self.device.name}")
            self._inject_crop_css()
            return
        # 적응형 폴링: 처음 5회는 100ms, 이후 300ms
        interval = 100 if attempt < 5 else 300
        try:
            def on_result(ready):
                if not self._is_active:
                    return
                if ready:
                    print(f"[Thumbnail] crop 준비 완료 (attempt={attempt}): {self.device.name}")
                    self._inject_crop_css()
                else:
                    QTimer.singleShot(interval, lambda: self._poll_and_inject_crop(attempt + 1))
            self._webview.page().runJavaScript(
                "window._thumbReady === true", on_result
            )
        except Exception:
            pass

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

    def _update_name_label(self):
        """name_label에 상태 색상 점 표시 (JS 없이 Qt 시그널만 사용)"""
        name = self.device.name
        if self._stream_status == "connected":
            dot = '<span style="color:#4CAF50;">●</span>'
        elif self._stream_status == "loading":
            dot = '<span style="color:#FF9800;">●</span>'
        elif self._stream_status == "dead":
            dot = '<span style="color:#f44336;">●</span>'
        else:
            dot = ""
        if dot:
            self.name_label.setText(f'{dot} {name}')
        else:
            self.name_label.setText(name)

    def _on_render_terminated(self, terminationStatus, exitCode):
        """WebView 렌더 프로세스 종료 감지"""
        print(f"[Thumbnail] 렌더 프로세스 종료: {self.device.name} (status={terminationStatus}, code={exitCode})")
        self._stream_status = "dead"
        self._update_name_label()

        # 비정상/강제 종료 시 GPU 크래시 카운트 (클래스 변수로 공유)
        if terminationStatus in (1, 2):
            if not hasattr(KVMThumbnailWidget, '_gpu_crash_count'):
                KVMThumbnailWidget._gpu_crash_count = 0
            KVMThumbnailWidget._gpu_crash_count += 1
            print(f"[Thumbnail] GPU 크래시 횟수: {KVMThumbnailWidget._gpu_crash_count}")

            if KVMThumbnailWidget._gpu_crash_count >= 3:
                try:
                    from config import DATA_DIR
                    flag_path = os.path.join(DATA_DIR, ".gpu_crash")
                    os.makedirs(DATA_DIR, exist_ok=True)
                    with open(flag_path, 'w') as f:
                        f.write(f"thumbnail_crash={KVMThumbnailWidget._gpu_crash_count}\n")
                    print(f"[Thumbnail] GPU 크래시 플래그 생성 → 다음 실행에서 소프트웨어 렌더링")
                except Exception as e:
                    print(f"[Thumbnail] GPU 크래시 플래그 생성 실패: {e}")

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

    def contextMenuEvent(self, event):
        self.right_clicked.emit(self.device, event.globalPos())
        event.accept()

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
    device_right_clicked = pyqtSignal(object, object)  # KVMDevice, QPoint

    # 가상 스크롤링: 동시 스트림 최대 수 (보이는 것 + 버퍼)
    MAX_ACTIVE_STREAMS = 12

    def __init__(self, manager: KVMManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.thumbnails: list[KVMThumbnailWidget] = []
        self._is_visible = False
        self._live_preview_enabled = True  # 실시간 미리보기 활성화
        self._filter_group = None  # None이면 전체, 문자열이면 해당 그룹만
        self._crop_region = None  # 부분제어 크롭 영역
        self._load_in_progress = False  # load_devices 중복 호출 방지
        self._active_streams: set = set()  # 현재 스트림 중인 썸네일 인덱스
        self._scroll_debounce_timer = None  # 스크롤 디바운스 타이머
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # 상단 컨트롤
        control_layout = QHBoxLayout()
        title_label = QLabel("전체 KVM 미리보기")
        title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        control_layout.addWidget(title_label)

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

        self.btn_clear_crop = QPushButton("✕ 부분제어 해제")
        self.btn_clear_crop.setStyleSheet(
            "QPushButton { background-color: #FF5722; color: white; padding: 5px 10px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #E64A19; }"
        )
        self.btn_clear_crop.clicked.connect(self._on_clear_crop_clicked)
        self.btn_clear_crop.setVisible(False)
        control_layout.addWidget(self.btn_clear_crop)

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

        # 스크롤 이벤트 → 가상 스크롤링 (보이는 썸네일만 스트림)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)

    def _on_scroll_changed(self):
        """스크롤 위치 변경 시 가시 영역 썸네일만 스트림 (디바운스 200ms)"""
        if not self._is_visible or not self._live_preview_enabled:
            return
        # 30개 이하면 전체 스트림 (가상 스크롤링 불필요)
        if len(self.thumbnails) <= self.MAX_ACTIVE_STREAMS:
            return
        # 디바운스: 200ms 이내 추가 스크롤 시 이전 타이머 취소
        if self._scroll_debounce_timer is not None:
            try:
                self._scroll_debounce_timer.stop()
            except Exception:
                pass
        self._scroll_debounce_timer = QTimer()
        self._scroll_debounce_timer.setSingleShot(True)
        self._scroll_debounce_timer.timeout.connect(self._update_visible_streams)
        self._scroll_debounce_timer.start(200)

    def _get_visible_thumb_indices(self) -> set:
        """현재 스크롤 뷰포트에 보이는 썸네일 인덱스 반환 (+ 상하 1행 버퍼)"""
        viewport_rect = self.scroll_area.viewport().rect()
        # 뷰포트를 그리드 컨테이너 좌표로 변환
        scroll_y = self.scroll_area.verticalScrollBar().value()
        visible_top = scroll_y - 160  # 상단 1행 버퍼
        visible_bottom = scroll_y + viewport_rect.height() + 160  # 하단 1행 버퍼

        visible = set()
        for i, thumb in enumerate(self.thumbnails):
            y = thumb.y()
            if visible_top <= y <= visible_bottom:
                visible.add(i)
        return visible

    def _update_visible_streams(self):
        """보이는 썸네일만 스트림하고, 보이지 않는 것은 중지"""
        if not self._is_visible or not self._live_preview_enabled:
            return
        # 소규모: 전체 스트림 유지
        if len(self.thumbnails) <= self.MAX_ACTIVE_STREAMS:
            return

        visible = self._get_visible_thumb_indices()

        # 보이지 않는 활성 스트림 중지
        to_stop = self._active_streams - visible
        for idx in to_stop:
            if 0 <= idx < len(self.thumbnails):
                try:
                    self.thumbnails[idx].stop_capture()
                    self.thumbnails[idx]._update_status_display()
                except Exception:
                    pass

        # 새로 보이는 썸네일 스트림 시작
        to_start = visible - self._active_streams
        delay = 0
        for idx in sorted(to_start):
            if 0 <= idx < len(self.thumbnails):
                thumb = self.thumbnails[idx]
                if thumb.device.status == DeviceStatus.ONLINE and not thumb._is_active:
                    def start_if_valid(t=thumb):
                        if t in self.thumbnails:
                            t.start_capture()
                    QTimer.singleShot(delay, start_if_valid)
                    delay += 100

        self._active_streams = visible
        # print(f"[GridView] 가시 스트림 업데이트: {len(visible)}개 활성, {len(to_stop)}개 중지, {len(to_start)}개 시작")

    def _toggle_live_preview(self):
        """실시간 미리보기 토글"""
        self._live_preview_enabled = self.btn_toggle_preview.isChecked()

        if self._live_preview_enabled:
            self.btn_toggle_preview.setText("🎬 미리보기 ON")
            # 모든 썸네일 미리보기 활성화
            for thumb in self.thumbnails:
                thumb._use_preview = True
                if self._is_visible:
                    thumb.start_capture()
        else:
            self.btn_toggle_preview.setText("🎬 미리보기 OFF")
            # 모든 썸네일 미리보기 비활성화
            for thumb in self.thumbnails:
                thumb._use_preview = False
                thumb.stop_capture()
                thumb._update_status_display()

    def load_devices(self):
        """장치 목록 로드 및 그리드 구성 (증분 업데이트 지원)

        장치 목록이 변경되지 않았으면 스킵, 소규모 변경은 증분 처리.
        """
        if self._load_in_progress:
            print("[GridView] load_devices 건너뜀 - 이미 진행 중")
            return
        self._load_in_progress = True
        try:
            # 장치 목록 가져오기 (그룹 필터 적용)
            all_devices = self.manager.get_all_devices()
            if self._filter_group is not None:
                devices = [d for d in all_devices if (d.info.group or 'default') == self._filter_group]
            else:
                devices = all_devices

            # ★ 증분 업데이트: 기존 목록과 비교
            current_names = {thumb.device.name for thumb in self.thumbnails}
            new_names = {d.name for d in devices}

            if current_names == new_names and len(self.thumbnails) == len(devices):
                # 변경 없음 → 스킵 (상태 업데이트만)
                print(f"[GridView] load_devices 스킵 - 변경 없음 ({len(devices)}개)")
                self._load_in_progress = False
                return

            # 소규모 변경 (추가/삭제 5개 이하): 증분 처리
            added = new_names - current_names
            removed = current_names - new_names
            if len(added) + len(removed) <= 5 and self.thumbnails:
                print(f"[GridView] 증분 업데이트: +{len(added)} -{len(removed)}")
                self._incremental_update(devices, added, removed)
                self._load_in_progress = False
                return

            # ★ 전체 재구성 (대규모 변경)
            print(f"[GridView] load_devices 전체 재구성 ({len(devices)}개)...")
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

            cols = max(4, self.scroll_area.width() // 210)

            self.setUpdatesEnabled(False)
            try:
                for idx, device in enumerate(devices):
                    row = idx // cols
                    col = idx % cols

                    thumb = KVMThumbnailWidget(device)
                    thumb._use_preview = self._live_preview_enabled
                    if self._crop_region:
                        thumb._crop_region = self._crop_region
                    thumb.clicked.connect(self._on_thumbnail_clicked)
                    thumb.double_clicked.connect(self._on_thumbnail_double_clicked)
                    thumb.right_clicked.connect(self._on_thumbnail_right_clicked)
                    self.thumbnails.append(thumb)
                    self.grid_layout.addWidget(thumb, row, col)

                if devices:
                    self.grid_layout.setRowStretch(len(devices) // cols + 1, 1)
                    self.grid_layout.setColumnStretch(cols, 1)
            finally:
                self.setUpdatesEnabled(True)

            print(f"[GridView] load_devices 완료 - {len(self.thumbnails)}개 썸네일 생성")

            if self._is_visible:
                self._start_all_captures()
        except Exception as e:
            print(f"[GridView] load_devices 오류: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._load_in_progress = False

    def _incremental_update(self, devices, added_names: set, removed_names: set):
        """증분 그리드 업데이트 (소규모 변경 시 전체 재구성 방지)"""
        try:
            self.setUpdatesEnabled(False)

            # 1) 삭제된 장치 제거
            for name in removed_names:
                for i, thumb in enumerate(self.thumbnails):
                    if thumb.device.name == name:
                        thumb.stop_capture()
                        thumb.cleanup()
                        self.grid_layout.removeWidget(thumb)
                        thumb.deleteLater()
                        self.thumbnails.pop(i)
                        self._active_streams.discard(i)
                        print(f"[GridView] 증분 삭제: {name}")
                        break

            # 2) 추가된 장치 append
            cols = max(4, self.scroll_area.width() // 210)
            device_map = {d.name: d for d in devices}
            for name in added_names:
                device = device_map.get(name)
                if not device:
                    continue
                idx = len(self.thumbnails)
                row = idx // cols
                col = idx % cols

                thumb = KVMThumbnailWidget(device)
                thumb._use_preview = self._live_preview_enabled
                if self._crop_region:
                    thumb._crop_region = self._crop_region
                thumb.clicked.connect(self._on_thumbnail_clicked)
                thumb.double_clicked.connect(self._on_thumbnail_double_clicked)
                thumb.right_clicked.connect(self._on_thumbnail_right_clicked)
                self.thumbnails.append(thumb)
                self.grid_layout.addWidget(thumb, row, col)
                print(f"[GridView] 증분 추가: {name}")

                # 보이는 상태면 캡처 시작
                if self._is_visible and self._live_preview_enabled:
                    if len(self.thumbnails) <= self.MAX_ACTIVE_STREAMS:
                        QTimer.singleShot(200, thumb.start_capture)

            self.setUpdatesEnabled(True)
        except Exception as e:
            self.setUpdatesEnabled(True)
            print(f"[GridView] 증분 업데이트 오류: {e}")

    def _start_all_captures(self):
        """썸네일 캡처 시작 (가상 스크롤링: 대규모 시 보이는 것만)"""
        try:
            print(f"[GridView] _start_all_captures - preview_enabled: {self._live_preview_enabled}, thumbs: {len(self.thumbnails)}, crop={self._crop_region}")
            if not self._live_preview_enabled:
                # 실시간 미리보기가 비활성화면 상태만 업데이트
                for thumb in self.thumbnails:
                    try:
                        thumb._update_status_display()
                    except Exception:
                        pass
                return

            # ★ 탭의 크롭 영역을 모든 기존 썸네일에 전파 (부분제어 핵심 수정)
            if self._crop_region:
                for thumb in self.thumbnails:
                    thumb._crop_region = self._crop_region
                print(f"[GridView] 크롭 영역 전파 완료: {self._crop_region} → {len(self.thumbnails)}개 썸네일")

            # ★ 대규모(MAX_ACTIVE_STREAMS 초과): 보이는 썸네일만 스트림
            if len(self.thumbnails) > self.MAX_ACTIVE_STREAMS:
                print(f"[GridView] 가상 스크롤링 모드 ({len(self.thumbnails)}개 > {self.MAX_ACTIVE_STREAMS})")
                # 오프라인 장치는 상태만 표시
                for thumb in self.thumbnails:
                    if thumb.device.status != DeviceStatus.ONLINE:
                        thumb._update_status_display()
                # 짧은 지연 후 레이아웃 안정화 대기 → 가시 영역만 시작
                QTimer.singleShot(300, self._update_visible_streams)
                return

            # ★ 소규모: 기존 방식 (전체 순차 시작)
            current_thumbs = list(self.thumbnails)  # 스냅샷
            for i, thumb in enumerate(current_thumbs):
                if thumb._is_paused:
                    thumb.resume_capture()
                else:
                    def start_if_valid(t=thumb):
                        if t in self.thumbnails:
                            t.start_capture()
                    QTimer.singleShot(i * 100, start_if_valid)
        except Exception as e:
            print(f"[GridView] _start_all_captures 오류: {e}")

    def _stop_all_captures(self):
        """모든 썸네일 캡처 완전 중지 (WebView 언로드 - 비트레이트 해제)"""
        try:
            print("[GridView] _stop_all_captures - 모든 WebView 중지")
            self._active_streams.clear()
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

    def _on_thumbnail_right_clicked(self, device, pos):
        self.device_right_clicked.emit(device, pos)

    def _get_filtered_device_count(self) -> int:
        """현재 필터에 맞는 장치 수 반환"""
        all_devices = self.manager.get_all_devices()
        if self._filter_group is not None:
            return len([d for d in all_devices if (d.info.group or 'default') == self._filter_group])
        return len(all_devices)

    def on_tab_activated(self):
        """탭이 활성화될 때 호출 (외부에서 호출)

        탭 전환 시 이전 탭은 stop_capture(WebRTC 해제)되므로,
        재활성화 시 항상 새로 캡처를 시작해야 함.
        """
        try:
            expected = self._get_filtered_device_count()
            print(f"[GridView] on_tab_activated - thumbnails: {len(self.thumbnails)}, expected: {expected}, filter: {self._filter_group}")
            self._is_visible = True

            if self._load_in_progress:
                print("[GridView] on_tab_activated 건너뜀 - load 진행 중")
                return

            # 장치 수 변경 시 전체 리로드
            if len(self.thumbnails) != expected:
                print("[GridView] load_devices 예약...")
                QTimer.singleShot(150, self.load_devices)
            else:
                # 이미 썸네일 위젯이 있으면 캡처만 재시작
                # (stop 상태이므로 start_capture 필요)
                print("[GridView] _start_all_captures 예약...")
                QTimer.singleShot(100, self._start_all_captures)
        except Exception as e:
            print(f"[GridView] on_tab_activated 오류: {e}")

    def on_tab_deactivated(self):
        """탭이 비활성화될 때 호출 - stop (WebRTC 연결 해제)

        KVM은 동시에 1개 연결만 지원하므로, 비활성 탭에서
        WebRTC 연결을 유지하면 다른 탭에서 같은 KVM에 접속 불가.
        → 완전 중지하여 WebRTC 연결 해제.
        """
        try:
            print(f"[GridView] on_tab_deactivated - stop (filter: {self._filter_group})")
            self._is_visible = False
            self._stop_all_captures()
        except Exception as e:
            print(f"[GridView] on_tab_deactivated 오류: {e}")

    def _pause_all_captures(self):
        """모든 썸네일 일시정지 (WebView URL 유지, 새로고침만 중지)"""
        for thumb in self.thumbnails:
            try:
                thumb.pause_capture()
            except Exception:
                pass

    def _resume_all_captures(self):
        """일시정지된 썸네일 재개"""
        if not self._live_preview_enabled:
            return
        for thumb in self.thumbnails:
            try:
                if thumb._is_paused:
                    thumb.resume_capture()
                elif not thumb._is_active:
                    thumb.start_capture()
            except Exception:
                pass

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

    # ─── 부분제어 크롭 ──────────────────────────────────────

    def apply_partial_crop(self, region: tuple):
        """모든 썸네일에 영역 크롭 적용
        Args:
            region: (x, y, w, h) 0~1 비율
        """
        self._crop_region = region
        for thumb in self.thumbnails:
            thumb.set_crop_region(region)

        # 상단 타이틀 변경
        self._update_title_for_crop(region)

    def clear_partial_crop(self):
        """크롭 해제 — 원래 전체 화면으로 복귀"""
        self._crop_region = None
        for thumb in self.thumbnails:
            thumb.set_crop_region(None)

        # 타이틀 복원
        self._update_title_for_crop(None)

    def _on_clear_crop_clicked(self):
        """부분제어 해제 버튼 클릭 — 크롭 해제 후 전체 화면 복구"""
        print("[부분제어] 해제 버튼 클릭")
        self._stop_all_captures()
        self.clear_partial_crop()
        self.btn_clear_crop.setVisible(False)
        QTimer.singleShot(300, self.on_tab_activated)

    def _update_title_for_crop(self, region):
        """부분제어 상태에 따라 타이틀 변경"""
        # _init_ui에서 생성한 title_label 찾기
        layout = self.layout()
        if layout and layout.count() > 0:
            ctrl_layout = layout.itemAt(0)
            if ctrl_layout and ctrl_layout.layout():
                title_item = ctrl_layout.layout().itemAt(0)
                if title_item and title_item.widget():
                    label = title_item.widget()
                    if region:
                        x, y, w, h = region
                        label.setText(
                            f"전체 KVM 미리보기  [부분제어: "
                            f"({x:.0%},{y:.0%})~({x+w:.0%},{y+h:.0%})]"
                        )
                        label.setStyleSheet(
                            "font-weight:bold; font-size:14px; color:#00BCD4;"
                        )
                    else:
                        label.setText("전체 KVM 미리보기")
                        label.setStyleSheet(
                            "font-weight:bold; font-size:14px;"
                        )
        # 부분제어 해제 버튼 표시/숨김
        if hasattr(self, 'btn_clear_crop'):
            self.btn_clear_crop.setVisible(region is not None)


class RegionSelectOverlay(QWidget):
    """드래그로 사각 영역을 선택하는 투명 오버레이"""
    region_selected = pyqtSignal(float, float, float, float)  # x, y, w, h (0~1 비율)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._start = None
        self._current = None
        self._selecting = False

    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self.setFocus()

    def paintEvent(self, event):
        painter = QPainter(self)
        # 반투명 검정 배경
        painter.fillRect(self.rect(), QColor(0, 0, 0, 120))

        if self._start and self._current:
            rect = QRect(self._start, self._current).normalized()
            # 선택 영역은 투명하게 비우기
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(rect, Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            # 빨간 테두리
            painter.setPen(QPen(QColor(255, 50, 50), 2))
            painter.drawRect(rect)

        # 안내 텍스트
        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.drawText(10, 20, "드래그로 영역 선택 | ESC: 취소")
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.pos()
            self._current = event.pos()
            self._selecting = True
            self.update()

    def mouseMoveEvent(self, event):
        if self._selecting:
            self._current = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._selecting:
            self._selecting = False
            self._current = event.pos()
            rect = QRect(self._start, self._current).normalized()
            w = self.width()
            h = self.height()
            if w > 0 and h > 0 and rect.width() > 10 and rect.height() > 10:
                rx = rect.x() / w
                ry = rect.y() / h
                rw = rect.width() / w
                rh = rect.height() / h
                self.hide()
                self.region_selected.emit(rx, ry, rw, rh)
            else:
                # 너무 작은 영역 — 무시
                self._start = None
                self._current = None
                self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._start = None
            self._current = None
            self.hide()


class PartialControlDialog(QDialog):
    """부분제어 — 그룹 KVM들의 동일 영역을 격자 표시 + 입력 브로드캐스트"""

    # PicoKVM UI 정리 + 영역 크롭 JavaScript
    CROP_JS_TEMPLATE = """
    (function() {{
        'use strict';
        var _done = false;
        function apply() {{
            if (_done) return;
            // UI 정리
            var style = document.createElement('style');
            style.textContent = `
                header, nav, aside, footer,
                .header, .sidebar, .footer, .toolbar, .controls,
                [class*="header"], [class*="sidebar"], [class*="footer"],
                [class*="toolbar"], [class*="status-bar"], [class*="info-bar"],
                [class*="navbar"], [class*="menu"], [class*="button-bar"],
                [class*="control-bar"] {{ display: none !important; }}
                body {{ background: #000 !important; overflow: hidden !important; margin: 0 !important; padding: 0 !important; }}
                body > *:not(video):not(canvas):not(script):not(style) {{ display: none !important; }}
                video, canvas {{
                    display: block !important;
                    position: fixed !important;
                    top: 0 !important; left: 0 !important;
                    width: 100vw !important; height: 100vh !important;
                    object-fit: fill !important;
                    z-index: 9999 !important;
                    background: #000 !important;
                    transform-origin: 0 0 !important;
                    transform: scale({sx}, {sy}) translate({tx}%, {ty}%) !important;
                }}
            `;
            document.head.appendChild(style);
            var v = document.querySelector('video') || document.querySelector('canvas');
            if (v) {{
                document.body.appendChild(v);
                _done = true;
            }}
        }}
        var n = 0;
        function loop() {{
            apply();
            if (!_done && n < 60) {{ n++; setTimeout(loop, 500); }}
        }}
        setTimeout(loop, 2000);
    }})();
    """

    # HID 키코드 매핑 (Qt Key → HID)
    QT_TO_HID = {
        Qt.Key.Key_A: 0x04, Qt.Key.Key_B: 0x05, Qt.Key.Key_C: 0x06, Qt.Key.Key_D: 0x07,
        Qt.Key.Key_E: 0x08, Qt.Key.Key_F: 0x09, Qt.Key.Key_G: 0x0A, Qt.Key.Key_H: 0x0B,
        Qt.Key.Key_I: 0x0C, Qt.Key.Key_J: 0x0D, Qt.Key.Key_K: 0x0E, Qt.Key.Key_L: 0x0F,
        Qt.Key.Key_M: 0x10, Qt.Key.Key_N: 0x11, Qt.Key.Key_O: 0x12, Qt.Key.Key_P: 0x13,
        Qt.Key.Key_Q: 0x14, Qt.Key.Key_R: 0x15, Qt.Key.Key_S: 0x16, Qt.Key.Key_T: 0x17,
        Qt.Key.Key_U: 0x18, Qt.Key.Key_V: 0x19, Qt.Key.Key_W: 0x1A, Qt.Key.Key_X: 0x1B,
        Qt.Key.Key_Y: 0x1C, Qt.Key.Key_Z: 0x1D,
        Qt.Key.Key_1: 0x1E, Qt.Key.Key_2: 0x1F, Qt.Key.Key_3: 0x20, Qt.Key.Key_4: 0x21,
        Qt.Key.Key_5: 0x22, Qt.Key.Key_6: 0x23, Qt.Key.Key_7: 0x24, Qt.Key.Key_8: 0x25,
        Qt.Key.Key_9: 0x26, Qt.Key.Key_0: 0x27,
        Qt.Key.Key_Return: 0x28, Qt.Key.Key_Escape: 0x29, Qt.Key.Key_Backspace: 0x2A,
        Qt.Key.Key_Tab: 0x2B, Qt.Key.Key_Space: 0x2C,
        Qt.Key.Key_F1: 0x3A, Qt.Key.Key_F2: 0x3B, Qt.Key.Key_F3: 0x3C, Qt.Key.Key_F4: 0x3D,
        Qt.Key.Key_F5: 0x3E, Qt.Key.Key_F6: 0x3F, Qt.Key.Key_F7: 0x40, Qt.Key.Key_F8: 0x41,
        Qt.Key.Key_F9: 0x42, Qt.Key.Key_F10: 0x43, Qt.Key.Key_F11: 0x44, Qt.Key.Key_F12: 0x45,
        Qt.Key.Key_Up: 0x52, Qt.Key.Key_Down: 0x51, Qt.Key.Key_Left: 0x50, Qt.Key.Key_Right: 0x4F,
    }

    def __init__(self, devices: list, region: tuple, parent=None):
        super().__init__(parent)
        self.devices = devices
        self.region = region  # (x, y, w, h) 0~1 비율
        self.hid_controllers: list[FastHIDController] = []
        self.web_views: list[QWebEngineView] = []
        self._executor = ThreadPoolExecutor(max_workers=len(devices))

        self.setWindowTitle(f"부분제어 — {len(devices)}대")
        self.resize(1600, 900)
        self._init_ui()
        self._connect_hids()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 상단 정보 바
        info_bar = QWidget()
        info_bar.setFixedHeight(26)
        info_bar.setStyleSheet("background-color:#1a1a1a;")
        hbox = QHBoxLayout(info_bar)
        hbox.setContentsMargins(5, 2, 5, 2)
        hbox.setSpacing(8)

        x, y, w, h = self.region
        info_label = QLabel(
            f"부분제어 | {len(self.devices)}대 | "
            f"영역: ({x:.0%}, {y:.0%}) ~ ({x+w:.0%}, {y+h:.0%})"
        )
        info_label.setStyleSheet("color:#4CAF50; font-weight:bold; font-size:11px;")
        hbox.addWidget(info_label)
        hbox.addStretch()

        btn_close = QPushButton("X")
        btn_close.setStyleSheet("padding:2px 7px; font-size:11px; border-radius:3px; background-color:#333; color:#f44;")
        btn_close.clicked.connect(self.close)
        hbox.addWidget(btn_close)

        layout.addWidget(info_bar)

        # 격자 WebView 영역
        grid_widget = QWidget()
        self._grid_layout = QGridLayout(grid_widget)
        self._grid_layout.setSpacing(2)
        self._grid_layout.setContentsMargins(2, 2, 2, 2)

        cols = max(1, math.ceil(math.sqrt(len(self.devices))))
        rows = max(1, math.ceil(len(self.devices) / cols))

        x, y, w, h = self.region
        sx = 1.0 / w
        sy = 1.0 / h
        tx = -x * 100.0
        ty = -y * 100.0
        crop_js = self.CROP_JS_TEMPLATE.format(sx=sx, sy=sy, tx=tx, ty=ty)

        for idx, device in enumerate(self.devices):
            r = idx // cols
            c = idx % cols

            container = QWidget()
            container_layout = QVBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setSpacing(0)

            # WebView
            wv = QWebEngineView()
            page = QWebEnginePage(wv)
            page.featurePermissionRequested.connect(
                lambda origin, feature, p=page: p.setFeaturePermission(
                    origin, feature, QWebEnginePage.PermissionPolicy.PermissionGrantedByUser
                )
            )
            wv.setPage(page)

            ws = wv.settings()
            ws.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            ws.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
            ws.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)
            ws.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)

            # 로드 완료 시 크롭 JS 주입
            wv.loadFinished.connect(
                lambda ok, view=wv, js=crop_js: view.page().runJavaScript(js) if ok else None
            )

            url = f"http://{device.ip}:{device.info.web_port}/"
            wv.setUrl(QUrl(url))

            container_layout.addWidget(wv, 1)

            # 기기명 라벨
            name_label = QLabel(device.name)
            name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_label.setStyleSheet("background-color:#333; color:white; font-size:10px; font-weight:bold; padding:2px;")
            container_layout.addWidget(name_label)

            self._grid_layout.addWidget(container, r, c)
            self.web_views.append(wv)

        layout.addWidget(grid_widget, 1)

    def _connect_hids(self):
        """모든 기기의 HID 컨트롤러 연결 (백그라운드)"""
        for device in self.devices:
            hid = FastHIDController(
                device.ip, device.info.port,
                device.info.username, device.info.password
            )
            self.hid_controllers.append(hid)

        # 병렬 연결
        def connect_hid(hid):
            try:
                hid.connect()
            except Exception as e:
                print(f"[PartialControl] HID 연결 실패: {e}")

        for hid in self.hid_controllers:
            self._executor.submit(connect_hid, hid)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return

        hid_code = self.QT_TO_HID.get(event.key())
        if hid_code is None:
            super().keyPressEvent(event)
            return

        # Qt 수정자 → HID 수정자
        mods = 0
        qt_mods = event.modifiers()
        if qt_mods & Qt.KeyboardModifier.ControlModifier:
            mods |= 0x01
        if qt_mods & Qt.KeyboardModifier.ShiftModifier:
            mods |= 0x02
        if qt_mods & Qt.KeyboardModifier.AltModifier:
            mods |= 0x04

        report_down = struct.pack('BBBBBBBB', mods, 0, hid_code, 0, 0, 0, 0, 0)
        report_up = struct.pack('BBBBBBBB', 0, 0, 0, 0, 0, 0, 0, 0)

        def send_key(hid):
            try:
                hex_down = ''.join(f'\\x{b:02x}' for b in report_down)
                hex_up = ''.join(f'\\x{b:02x}' for b in report_up)
                hid._cmd_queue.put(f"echo -ne '{hex_down}' > /dev/hidg0")
                hid._cmd_queue.put(f"echo -ne '{hex_up}' > /dev/hidg0")
            except Exception:
                pass

        for hid in self.hid_controllers:
            if hid.is_connected():
                self._executor.submit(send_key, hid)

    def closeEvent(self, event):
        # WebView 정리
        for wv in self.web_views:
            try:
                wv.setUrl(QUrl("about:blank"))
                wv.deleteLater()
            except Exception:
                pass
        self.web_views.clear()

        # HID 연결 해제
        for hid in self.hid_controllers:
            try:
                hid.disconnect()
            except Exception:
                pass
        self.hid_controllers.clear()

        self._executor.shutdown(wait=False)
        super().closeEvent(event)


class Aion2WebPage(QWebEnginePage):
    """아이온2 모드 지원 웹 페이지 - Pointer Lock API 활성화"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Pointer Lock 권한 자동 허용
        self.featurePermissionRequested.connect(self._on_permission_requested)

    def _on_permission_requested(self, origin, feature):
        """권한 요청 자동 허용 (마우스 락, 미디어 등 모든 권한)"""
        self.setFeaturePermission(origin, feature,
                                   QWebEnginePage.PermissionPolicy.PermissionGrantedByUser)


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

        // 부드러운 이동을 위한 RAF 배칭 모드 사용
        var _immediateMode = false;  // false = RAF 배칭 (부드러운 이동)

        // 배칭 모드용 변수
        var _pendingDX = 0;
        var _pendingDY = 0;
        var _rafId = null;

        // 이동 보정: 소수점 누적 (정밀도 유지)
        var _fracDX = 0;
        var _fracDY = 0;

        // 최대 이동량 제한 (한 프레임당)
        var _maxDelta = 25;

        // 재사용 객체 (GC 방지)
        var _moveEvent = { dx: 0, dy: 0 };

        // 마우스 전송 헬퍼 (클램핑 + 분할 전송)
        function _sendMouseClamped(dx, dy) {
            // 소수점 누적 처리
            dx += _fracDX;
            dy += _fracDY;
            var idx = Math.round(dx);
            var idy = Math.round(dy);
            _fracDX = dx - idx;
            _fracDY = dy - idy;

            if (idx === 0 && idy === 0) return;

            // 큰 이동은 분할 전송 (부드러운 이동)
            var sendFn = (window._pointer && window._pointer.sendMouse)
                ? function(x, y) { window._pointer.sendMouse(x, y); }
                : (window.sendMouseRelative
                    ? function(x, y) { window.sendMouseRelative(x, y); }
                    : null);

            if (!sendFn) return;

            while (idx !== 0 || idy !== 0) {
                var sx = Math.max(-_maxDelta, Math.min(_maxDelta, idx));
                var sy = Math.max(-_maxDelta, Math.min(_maxDelta, idy));
                sendFn(sx, sy);
                idx -= sx;
                idy -= sy;
            }
        }

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
                    // 즉시 전송 모드: 클램핑 적용
                    _sendMouseClamped(dx * _sensitivity, dy * _sensitivity);
                } else {
                    // 배칭 모드: RAF에서 일괄 처리 (부드러운 이동)
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

                    _sendMouseClamped(dx, dy);
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

        # 마지막 창 크기 복원 (설정에서 기억 활성화된 경우)
        if app_settings.get('liveview.remember_resolution', True):
            w = app_settings.get('liveview.last_width', 1920)
            h = app_settings.get('liveview.last_height', 1080)
        else:
            w, h = 1920, 1080
        self.resize(w, h)
        print(f"[LiveView] __init__ 시작: {device.name} ({device.ip}) [{w}x{h}]")

        # HID 컨트롤러 (SSH 직접 접속 — 릴레이 접속 시 사용 불가)
        self._is_relay = device.ip.startswith('100.')
        if self._is_relay:
            # 릴레이 접속: SSH HID 사용 불가 → 웹 기반 입력만 사용
            hid_ip = getattr(device.info, '_kvm_local_ip', device.ip)
            self.hid = FastHIDController(hid_ip, device.info.port,
                                         device.info.username, device.info.password)
            # SSH 연결은 시도하지 않음 (접근 불가)
            print(f"[LiveView] 릴레이 접속 — SSH HID 비활성 (웹 입력만 사용)")
        else:
            self.hid = FastHIDController(
                device.ip,
                device.info.port,
                device.info.username,
                device.info.password
            )

        self.game_mode_active = False
        self.sensitivity = 0.5
        self.control_bar_visible = True
        self._quality_timer = None  # 품질 변경 디바운싱용 타이머
        self._pending_quality = None  # 대기 중인 품질 값
        self._previous_quality = 80  # 저지연 모드 해제 시 복원할 품질
        self._page_loaded = False
        print(f"[LiveView] _init_ui 호출 전")
        self._init_ui()
        print(f"[LiveView] _init_ui 완료, _load_kvm_url 호출")
        # URL 로드 (WebView 생성 후 바로 시작)
        self._load_kvm_url()
        print(f"[LiveView] __init__ 완료")

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 공통 버튼 스타일 ──
        _btn_style = "padding:2px 7px; font-size:11px; border-radius:3px;"
        _sep_style = "color:#555; font-size:11px;"

        # ═══════════════════════════════════════════════
        #  1줄 — 제어 바 (입력 + 영상)
        # ═══════════════════════════════════════════════
        self.control_widget = QWidget()
        control_bar = QHBoxLayout(self.control_widget)
        control_bar.setContentsMargins(5, 2, 5, 2)
        control_bar.setSpacing(4)

        # 기기명
        self.status_label = QLabel(f"{self.device.name}")
        self.status_label.setStyleSheet("color:#4CAF50; font-weight:bold; font-size:11px;")
        control_bar.addWidget(self.status_label)

        sep0 = QLabel("|"); sep0.setStyleSheet(_sep_style)
        control_bar.addWidget(sep0)

        # ── 입력 그룹: 감도, 마우스모드, 아이온2 ──
        default_sensitivity = app_settings.get('aion2.sensitivity', 0.5)
        lbl = QLabel("감도:")
        lbl.setStyleSheet("color:#ccc; font-size:11px;")
        control_bar.addWidget(lbl)
        self.sensitivity_slider = QSlider(Qt.Orientation.Horizontal)
        self.sensitivity_slider.setRange(1, 30)
        self.sensitivity_slider.setValue(int(default_sensitivity * 10))
        self.sensitivity_slider.setFixedWidth(55)
        self.sensitivity_slider.valueChanged.connect(self._on_sensitivity_changed)
        control_bar.addWidget(self.sensitivity_slider)
        self.sensitivity_label = QLabel(f"{default_sensitivity:.1f}")
        self.sensitivity_label.setStyleSheet("color:#ccc; font-size:11px;")
        self.sensitivity_label.setFixedWidth(22)
        control_bar.addWidget(self.sensitivity_label)
        self.sensitivity = default_sensitivity

        self.mouse_mode_absolute = True
        self.btn_mouse_mode = QPushButton("Abs")
        self.btn_mouse_mode.setToolTip("Absolute: 일반작업\nRelative: 3D게임")
        self.btn_mouse_mode.setStyleSheet(f"{_btn_style} background-color:#2196F3; color:white;")
        self.btn_mouse_mode.clicked.connect(self._toggle_mouse_mode)
        control_bar.addWidget(self.btn_mouse_mode)

        self.btn_game_mode = QPushButton("아이온2")
        self.btn_game_mode.setToolTip("Alt+F1: 시작 (자동 Rel 전환)\nAlt+F2: 해제 (자동 Abs 복원)\nALT: 커서 일시 표시")
        self.btn_game_mode.setStyleSheet(f"{_btn_style} background-color:#FF5722; color:white; font-weight:bold;")
        self.btn_game_mode.clicked.connect(self._toggle_game_mode)
        control_bar.addWidget(self.btn_game_mode)

        btn_hangul = QPushButton("한/영")
        btn_hangul.setToolTip("한/영 전환 (Right Alt)\n단축키: Ctrl+Space")
        btn_hangul.setStyleSheet(f"{_btn_style} background-color:#795548; color:white;")
        btn_hangul.clicked.connect(self._send_hangul_toggle)
        control_bar.addWidget(btn_hangul)

        sep1 = QLabel("|"); sep1.setStyleSheet(_sep_style)
        control_bar.addWidget(sep1)

        # ── 영상 그룹: 품질, 저지연 ──
        quality_lbl = QLabel("품질:")
        quality_lbl.setStyleSheet("color:#ccc; font-size:11px;")
        control_bar.addWidget(quality_lbl)
        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(10, 100)
        self.quality_slider.setValue(80)
        self.quality_slider.setFixedWidth(55)
        self.quality_slider.setToolTip("낮을수록 지연↓ 화질↓")
        self.quality_slider.valueChanged.connect(self._on_quality_changed)
        control_bar.addWidget(self.quality_slider)
        self.quality_label = QLabel("80%")
        self.quality_label.setStyleSheet("color:#ccc; font-size:11px;")
        self.quality_label.setFixedWidth(28)
        control_bar.addWidget(self.quality_label)

        self.low_latency_mode = False
        self.btn_low_latency = QPushButton("저지연")
        self.btn_low_latency.setToolTip("저지연 모드: 품질↓ 지연↓\n(게임/실시간 작업용)")
        self.btn_low_latency.setStyleSheet(f"{_btn_style} background-color:#607D8B; color:white;")
        self.btn_low_latency.clicked.connect(self._toggle_low_latency_mode)
        control_bar.addWidget(self.btn_low_latency)

        sep2 = QLabel("|"); sep2.setStyleSheet(_sep_style)
        control_bar.addWidget(sep2)

        # ── 창 그룹: 전체화면, 닫기 ──
        btn_fullscreen = QPushButton("전체(F11)")
        btn_fullscreen.setStyleSheet(f"{_btn_style} background-color:#333; color:#ddd;")
        btn_fullscreen.clicked.connect(self._toggle_fullscreen)
        control_bar.addWidget(btn_fullscreen)

        btn_close = QPushButton("X")
        btn_close.setStyleSheet(f"{_btn_style} background-color:#333; color:#f44;")
        btn_close.clicked.connect(self.close)
        control_bar.addWidget(btn_close)

        self.control_widget.setStyleSheet("background-color:#1a1a1a;")
        self.control_widget.setFixedHeight(26)
        layout.addWidget(self.control_widget)

        # ═══════════════════════════════════════════════
        #  2줄 — 기능 바 (USB + Vision)
        # ═══════════════════════════════════════════════
        self._usb_thread = None

        self.shortcut_bar = QWidget()
        self.shortcut_bar.setFixedHeight(26)
        self.shortcut_bar.setStyleSheet("background-color:#222;")
        func_bar = QHBoxLayout(self.shortcut_bar)
        func_bar.setContentsMargins(5, 2, 5, 2)
        func_bar.setSpacing(4)

        # ── USB 그룹 ──
        self.btn_usb_mount = QPushButton("USB 마운트")
        self.btn_usb_mount.setToolTip("클라우드 파일을 USB 드라이브로 마운트\n(연결된 PC에서 USB로 인식)")
        self.btn_usb_mount.setStyleSheet(f"{_btn_style} background-color:#FF9800; color:white; font-weight:bold;")
        self.btn_usb_mount.clicked.connect(self._on_usb_mount)
        func_bar.addWidget(self.btn_usb_mount)

        self.btn_usb_eject = QPushButton("USB 해제")
        self.btn_usb_eject.setToolTip("USB Mass Storage 드라이브 해제")
        self.btn_usb_eject.setStyleSheet(f"{_btn_style} background-color:#795548; color:white;")
        self.btn_usb_eject.clicked.connect(self._on_usb_eject)
        func_bar.addWidget(self.btn_usb_eject)

        self.btn_kb_reset = QPushButton("⌨ 리셋")
        self.btn_kb_reset.setToolTip("키보드 HID 리셋\n키보드가 안 먹힐 때 사용\n(stuck key 해제 + HID 장치 재연결)")
        self.btn_kb_reset.setStyleSheet(f"{_btn_style} background-color:#E91E63; color:white;")
        self.btn_kb_reset.clicked.connect(self._on_keyboard_reset)
        func_bar.addWidget(self.btn_kb_reset)

        sep_pc = QLabel("|"); sep_pc.setStyleSheet(_sep_style)
        func_bar.addWidget(sep_pc)

        # ── 부분제어 ──
        self.btn_partial_control = QPushButton("부분제어")
        self.btn_partial_control.setToolTip("그룹 KVM 미리보기에 선택 영역만 크롭 표시")
        self.btn_partial_control.setStyleSheet(f"{_btn_style} background-color:#00BCD4; color:white; font-weight:bold;")
        self.btn_partial_control.clicked.connect(self._start_partial_control)
        func_bar.addWidget(self.btn_partial_control)

        # ── Vision 그룹 (YOLO) ──
        if VISION_AVAILABLE:
            sep_v = QLabel("|"); sep_v.setStyleSheet(_sep_style)
            func_bar.addWidget(sep_v)

            self.btn_vision = QPushButton("Vision")
            self.btn_vision.setToolTip("YOLO 이미지 인식 on/off (V)")
            self.btn_vision.setStyleSheet(f"{_btn_style} background-color:#9C27B0; color:white;")
            self.btn_vision.clicked.connect(self._toggle_vision)
            func_bar.addWidget(self.btn_vision)

            self.btn_vision_settings = QPushButton("V-Set")
            self.btn_vision_settings.setToolTip("Vision 설정")
            self.btn_vision_settings.setStyleSheet(f"{_btn_style} background-color:#333; color:#ddd;")
            self.btn_vision_settings.clicked.connect(self._show_vision_settings)
            func_bar.addWidget(self.btn_vision_settings)

            self.btn_rec = QPushButton("Rec")
            self.btn_rec.setToolTip("학습 데이터 수집 on/off (R)")
            self.btn_rec.setStyleSheet(f"{_btn_style} background-color:#607D8B; color:white;")
            self.btn_rec.clicked.connect(self._toggle_recording)
            func_bar.addWidget(self.btn_rec)

            self.rec_count_label = QLabel("")
            self.rec_count_label.setStyleSheet("color:#f44; font-size:11px; font-weight:bold;")
            func_bar.addWidget(self.rec_count_label)

        func_bar.addStretch()

        # 수집 모드 상태
        self._recording = False
        self._rec_timer = None
        self._rec_count = 0
        self._rec_output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                             "dataset", "images", "raw") if not getattr(sys, 'frozen', False) \
            else os.path.join(os.path.dirname(sys.executable), "dataset", "images", "raw")
        self._rec_input_log = []  # 캡처 간 입력 이벤트 버퍼
        self._rec_input_injected = False  # JS 이벤트 후킹 여부

        layout.addWidget(self.shortcut_bar)

        # 아이온2 모드 안내 바 - 더 컴팩트
        self.game_mode_bar = QLabel("  아이온2 모드 | 클릭: 잠금 | ALT: 커서 | Alt+F2: 해제")
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
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)
        # GPU 부하 최소화 (WebRTC 비디오 디코딩만 사용)
        settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.FocusOnNavigationEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AllowWindowActivationFromJavaScript, True)
        # PicoKVM 페이지의 이미지는 필요 (CLEAN_UI_JS에서 비디오 찾기 전까지)
        # settings.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages, False)

        layout.addWidget(self.web_view, 1)  # stretch factor 1 - 최대 공간

        # 로딩 오버레이
        self._loading_overlay = QLabel(self.web_view)
        self._loading_overlay.setText(f"{self.device.name} 연결 중...")
        self._loading_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_overlay.setStyleSheet("""
            QLabel {
                background-color: rgba(26, 26, 26, 220);
                color: #4CAF50;
                font-size: 18px;
                font-weight: bold;
            }
        """)

        # Vision 오버레이 (WebView 위에 투명하게 표시)
        self.vision_controller = None
        if VISION_AVAILABLE:
            self._vision_overlay = DetectionOverlay(self.web_view)
            self._vision_overlay.setGeometry(self.web_view.rect())
            self._vision_overlay.hide()

            self.vision_controller = VisionController(
                web_view=self.web_view,
                overlay=self._vision_overlay,
                hid_controller=self.hid,
                log_dir=LOG_DIR,
            )
            self.vision_controller.status_changed.connect(self._on_vision_status_changed)

            # 설정에서 모델 경로 로드 (페이지 로드 후 지연 실행)
            model_path = app_settings.get('vision.model_path', '')
            if model_path:
                QTimer.singleShot(2000, lambda p=model_path: self.vision_controller.load_model(p))

            # 설정 적용
            self.vision_controller.set_fps(app_settings.get('vision.capture_fps', 2))
            self.vision_controller.set_confidence(app_settings.get('vision.confidence', 0.5))
            self.vision_controller.set_auto_action(app_settings.get('vision.auto_action_enabled', False))
            self.vision_controller.set_log_enabled(app_settings.get('vision.log_enabled', True))

            # 액션 규칙 로드
            rules = app_settings.get('vision.action_rules', [])
            if rules:
                self.vision_controller.load_action_rules(rules)

        # 페이지 로드 완료 시 처리
        self.web_view.loadFinished.connect(self._on_page_loaded)

        # 렌더 프로세스 크래시 감지 → 자동 재연결
        self.aion2_page.renderProcessTerminated.connect(self._on_render_process_terminated)
        self._reconnect_count = 0
        self._max_reconnect = 5
        self._reconnect_timer = None

        # ── 글로벌 단축키 (QShortcut) ──
        # WebEngineView가 포커스를 가져가도 다이얼로그 레벨에서 키를 잡음
        sc_start = QShortcut(QKeySequence("Alt+F1"), self)
        sc_start.setContext(Qt.ShortcutContext.WindowShortcut)
        sc_start.activated.connect(lambda: self._start_game_mode() if not self.game_mode_active else None)

        sc_stop = QShortcut(QKeySequence("Alt+F2"), self)
        sc_stop.setContext(Qt.ShortcutContext.WindowShortcut)
        sc_stop.activated.connect(lambda: self._stop_game_mode() if self.game_mode_active else None)

        sc_hangul = QShortcut(QKeySequence("Ctrl+Space"), self)
        sc_hangul.setContext(Qt.ShortcutContext.WindowShortcut)
        sc_hangul.activated.connect(self._send_hangul_toggle)

    def _send_hangul_toggle(self):
        """한/영 전환 — Right Alt (독립 SSH exec_command로 전송)"""
        import threading

        def _do_send():
            try:
                import paramiko
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(self.device.ip, port=self.device.info.port,
                            username=self.device.info.username,
                            password=self.device.info.password, timeout=5)

                # Right Alt (modifier 0x40) press → release
                script = (
                    "echo -ne '\\x40\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
                    "usleep 150000; "
                    "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
                    "echo HANGUL_OK"
                )
                stdin, stdout, stderr = ssh.exec_command(script, timeout=5)
                result = stdout.read().decode('utf-8', errors='replace').strip()
                ssh.close()

                if 'HANGUL_OK' in result:
                    print("[HID] 한/영 전환 완료 (Right Alt)")
                else:
                    print(f"[HID] 한/영 전환 결과 불명: {result}")
            except Exception as e:
                print(f"[HID] 한/영 전환 오류: {e}")

        threading.Thread(target=_do_send, daemon=True).start()

    def _toggle_control_bar(self):
        """상단 바 + 단축키 바 토글"""
        self.control_bar_visible = not self.control_bar_visible
        self.control_widget.setVisible(self.control_bar_visible)
        self.shortcut_bar.setVisible(self.control_bar_visible)

    def _load_kvm_url(self):
        """KVM URL 로드 시작

        릴레이 접속(Tailscale IP)인 경우 WebRTC ICE candidate 패치 스크립트를
        UserScript로 주입하여 미디어 스트림이 릴레이를 통과하도록 함.

        GPU 크래시 방어: URL 로드 직전에 플래그 파일을 생성하여,
        로드 중 프로세스가 죽어도 다음 실행에서 소프트웨어 렌더링으로 전환.
        """
        web_port = self.device.info.web_port if hasattr(self.device.info, 'web_port') else 80
        url = f"http://{self.device.ip}:{web_port}"
        print(f"[LiveView] URL 로드: {url}")

        # GPU 크래시 방어: URL 로드 전 플래그 생성
        # 페이지 로드 성공 시 _on_page_loaded에서 제거
        self._set_gpu_loading_flag(True)

        # 릴레이 접속 감지 (Tailscale IP로 접속하는 경우)
        relay_ip = self.device.ip
        is_relay = relay_ip.startswith('100.')

        if is_relay:
            self._inject_ice_patch(relay_ip, web_port)
            print(f"[LiveView] 릴레이 접속 — ICE 패치 주입 완료")

        self.web_view.setUrl(QUrl(url))

    def _set_gpu_loading_flag(self, create: bool):
        """GPU 크래시 감지용 플래그 파일 관리

        create=True: URL 로드 직전에 생성 (프로세스 즉사 대비)
        create=False: 페이지 로드 성공 후 제거 (정상 동작 확인)
        수동 설정(manual=True) 플래그는 건드리지 않음.
        """
        try:
            from config import DATA_DIR
            flag_path = os.path.join(DATA_DIR, ".gpu_crash")

            if create:
                # 수동 설정된 플래그가 이미 있으면 건드리지 않음
                if os.path.exists(flag_path):
                    try:
                        with open(flag_path, 'r') as f:
                            if 'manual=True' in f.read():
                                return
                    except Exception:
                        pass
                os.makedirs(DATA_DIR, exist_ok=True)
                with open(flag_path, 'w') as f:
                    f.write("loading=True\n")
            else:
                # 정상 종료 → 플래그 제거 (수동 설정은 유지)
                if os.path.exists(flag_path):
                    try:
                        with open(flag_path, 'r') as f:
                            content = f.read()
                        if 'manual=True' not in content:
                            os.remove(flag_path)
                            print(f"[LiveView] GPU 플래그 제거 (정상 종료)")
                    except Exception:
                        pass
        except Exception as e:
            print(f"[LiveView] GPU 플래그 처리 오류: {e}")

    def _set_gpu_streaming_flag(self):
        """GPU 크래시 감지: 스트리밍 중 플래그 설정

        페이지 로드 성공 후 WebRTC 스트리밍 단계로 전환.
        정상 종료(closeEvent)에서만 제거됨.
        스트리밍 중 access violation으로 프로세스가 죽으면
        이 플래그가 남아 다음 실행에서 소프트웨어 렌더링 전환.
        """
        try:
            from config import DATA_DIR
            flag_path = os.path.join(DATA_DIR, ".gpu_crash")
            # 수동 설정된 플래그는 건드리지 않음
            if os.path.exists(flag_path):
                try:
                    with open(flag_path, 'r') as f:
                        if 'manual=True' in f.read():
                            return
                except Exception:
                    pass
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(flag_path, 'w') as f:
                f.write(f"streaming=True\ndevice={self.device.name}\n")
            print(f"[LiveView] GPU 스트리밍 플래그 설정 (크래시 시 소프트웨어 렌더링 전환)")
        except Exception as e:
            print(f"[LiveView] GPU 스트리밍 플래그 설정 오류: {e}")

    def _inject_ice_patch(self, relay_ip: str, relay_port: int):
        """WebRTC ICE candidate를 릴레이 IP로 교체하는 UserScript 주입

        RTCPeerConnection을 래핑하여:
        1. 원격 ICE candidate 수신 시 KVM 로컬 IP → 릴레이 IP로 교체
        2. WebRTC signaling의 SDP에서도 IP 교체
        3. KVM의 실제 UDP 포트를 관제 PC에 알려줌 (/_wellcomland/set_udp_port)
        이렇게 하면 브라우저가 릴레이 IP로 미디어를 전송하고,
        관제 PC의 UDP 릴레이가 실제 KVM으로 전달함.
        """
        from PyQt6.QtWebEngineCore import QWebEngineScript

        # UDP 릴레이 포트 계산
        # _udp_relay_port가 직접 설정되어 있으면 사용, 아니면 TCP 포트에서 계산
        udp_port = getattr(self.device.info, '_udp_relay_port', None)
        if not udp_port:
            udp_port = 28000 + (relay_port - 18000) if relay_port >= 18000 else 28000

        # TCP 릴레이 포트 (set_udp_port 요청 전송용)
        tcp_port = relay_port

        ice_patch_js = """
(function() {
    'use strict';

    const RELAY_IP = '%RELAY_IP%';
    const RELAY_UDP_PORT = %UDP_PORT%;
    const RELAY_TCP_PORT = %TCP_PORT%;
    let _notifiedPort = 0;

    console.log('[WellcomLAND] ICE patch loaded — relay:', RELAY_IP,
                'udp:', RELAY_UDP_PORT, 'tcp:', RELAY_TCP_PORT);

    // KVM의 실제 UDP 포트를 관제 PC에 알려주는 함수
    function notifyUdpPort(kvmPort) {
        if (_notifiedPort === kvmPort) return;
        _notifiedPort = kvmPort;
        console.log('[WellcomLAND] Notifying relay of KVM UDP port:', kvmPort);
        fetch('http://' + RELAY_IP + ':' + RELAY_TCP_PORT +
              '/_wellcomland/set_udp_port?port=' + kvmPort,
              {mode: 'no-cors'}).catch(function(){});
    }

    // RTCPeerConnection 래핑
    const OriginalRTCPeerConnection = window.RTCPeerConnection;

    window.RTCPeerConnection = function(config) {
        console.log('[WellcomLAND] RTCPeerConnection intercepted', config);

        const pc = new OriginalRTCPeerConnection(config);

        // addIceCandidate 래핑 — 원격에서 받은 candidate의 IP를 릴레이로 교체
        const origAddIceCandidate = pc.addIceCandidate.bind(pc);
        pc.addIceCandidate = function(candidate) {
            if (candidate && candidate.candidate) {
                const orig = candidate.candidate;
                const patched = orig.replace(
                    /(\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3})\\s+(\\d+)\\s+typ\\s+host/g,
                    function(match, ip, port) {
                        if (ip === RELAY_IP) return match;
                        // KVM의 실제 UDP 포트를 관제 PC에 알림
                        notifyUdpPort(parseInt(port));
                        console.log('[WellcomLAND] ICE rewrite:', ip + ':' + port,
                                    '->', RELAY_IP + ':' + RELAY_UDP_PORT);
                        return RELAY_IP + ' ' + RELAY_UDP_PORT + ' typ host';
                    }
                );
                if (patched !== orig) {
                    candidate = new RTCIceCandidate({
                        candidate: patched,
                        sdpMid: candidate.sdpMid,
                        sdpMLineIndex: candidate.sdpMLineIndex,
                        usernameFragment: candidate.usernameFragment,
                    });
                }
            }
            return origAddIceCandidate(candidate);
        };

        // setRemoteDescription 래핑 — SDP 내의 IP도 교체
        const origSetRemoteDesc = pc.setRemoteDescription.bind(pc);
        pc.setRemoteDescription = function(desc) {
            if (desc && desc.sdp) {
                let sdp = desc.sdp;
                sdp = sdp.replace(
                    /c=IN IP4 (\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3})/g,
                    function(match, ip) {
                        if (ip === '0.0.0.0' || ip === '127.0.0.1' || ip === RELAY_IP) return match;
                        console.log('[WellcomLAND] SDP IP rewrite:', ip, '->', RELAY_IP);
                        return 'c=IN IP4 ' + RELAY_IP;
                    }
                );
                sdp = sdp.replace(
                    /a=candidate:(.*?)(\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3})\\s+(\\d+)\\s+typ\\s+host/g,
                    function(match, prefix, ip, port) {
                        if (ip === RELAY_IP) return match;
                        notifyUdpPort(parseInt(port));
                        console.log('[WellcomLAND] SDP candidate rewrite:', ip + ':' + port);
                        return 'a=candidate:' + prefix + RELAY_IP + ' ' + RELAY_UDP_PORT + ' typ host';
                    }
                );
                desc = new RTCSessionDescription({type: desc.type, sdp: sdp});
            }
            return origSetRemoteDesc(desc);
        };

        return pc;
    };

    window.RTCPeerConnection.prototype = OriginalRTCPeerConnection.prototype;
    window.RTCPeerConnection.generateCertificate = OriginalRTCPeerConnection.generateCertificate;
})();
""".replace('%RELAY_IP%', relay_ip).replace('%UDP_PORT%', str(udp_port)).replace('%TCP_PORT%', str(tcp_port))

        # UserScript로 주입 (페이지 JS보다 먼저 실행)
        script = QWebEngineScript()
        script.setName("wellcomland-ice-patch")
        script.setSourceCode(ice_patch_js)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)

        # 기존 패치 제거 후 재등록
        scripts = self.web_view.page().scripts()
        for old in scripts.find("wellcomland-ice-patch"):
            scripts.remove(old)
        scripts.insert(script)

    def _on_page_loaded(self, ok):
        self._page_loaded = True
        print(f"[LiveView] _on_page_loaded: ok={ok}")
        # 로딩 오버레이 숨기기
        if hasattr(self, '_loading_overlay') and self._loading_overlay:
            self._loading_overlay.hide()
        if ok:
            self._reconnect_count = 0  # 성공 시 재연결 카운터 리셋
            self.status_label.setText(f"{self.device.name} - 연결됨")
            # GPU 크래시 플래그: loading → streaming 전환
            # closeEvent에서만 제거 (정상 종료 시)
            # 크래시 시 streaming=True 플래그가 남아 다음 실행에서 소프트웨어 렌더링 전환
            self._set_gpu_streaming_flag()
            # UI 정리 (비디오만 표시) - 약간의 지연 후 실행
            QTimer.singleShot(500, self._clean_kvm_ui)
            # WebRTC 연결 상태 모니터링 주입
            QTimer.singleShot(2000, self._inject_webrtc_monitor)
            # WebRTC 비디오 스트림 시작 모니터링 (크래시 진단용)
            QTimer.singleShot(3000, self._log_webrtc_phase)
            QTimer.singleShot(8000, self._log_webrtc_phase)
            QTimer.singleShot(15000, self._log_webrtc_phase)
        else:
            self.status_label.setText(f"{self.device.name} - 연결 실패")
            # 로드 실패: GPU 플래그 제거 (네트워크 실패는 GPU 문제 아님)
            self._set_gpu_loading_flag(False)
            # 자동 재시도
            self._schedule_reconnect("페이지 로드 실패")

    def _log_webrtc_phase(self):
        """WebRTC 비디오 스트림 상태를 로그에 기록 (크래시 진단용)"""
        js = """
        (function() {
            var info = {};
            // video 요소 상태
            var v = document.querySelector('video');
            if (v) {
                info.video = {
                    readyState: v.readyState,
                    paused: v.paused,
                    width: v.videoWidth,
                    height: v.videoHeight,
                    srcObj: !!v.srcObject
                };
            }
            // canvas 요소 상태
            var c = document.querySelector('canvas');
            if (c) {
                info.canvas = { width: c.width, height: c.height };
            }
            // RTCPeerConnection 상태
            if (window._origRTCPeerConnection || window.RTCPeerConnection) {
                var pcs = window._wellcom_pcs || [];
                info.rtc_count = pcs.length;
            }
            return JSON.stringify(info);
        })();
        """
        try:
            self.web_view.page().runJavaScript(js, self._on_webrtc_phase_result)
        except Exception:
            pass

    def _on_webrtc_phase_result(self, result):
        if result:
            print(f"[LiveView] WebRTC 상태: {result}")

    def _on_render_process_terminated(self, status, exit_code):
        """렌더 프로세스 크래시 감지 → 자동 재연결 + GPU 크래시 플래그"""
        status_names = {0: "정상 종료", 1: "비정상 종료", 2: "강제 종료"}
        reason = status_names.get(status, f"알 수 없음({status})")
        print(f"[LiveView] 렌더 프로세스 종료: {reason} (exit_code={exit_code})")
        self.status_label.setText(f"{self.device.name} - 연결 끊김")
        self.status_label.setStyleSheet("color: #FF5252; font-weight: bold; font-size: 11px;")

        # 비정상/강제 종료 시 GPU 크래시 플래그 생성
        # → 다음 실행에서 소프트웨어 렌더링으로 폴백
        if status in (1, 2):
            if not hasattr(self, '_gpu_crash_count'):
                self._gpu_crash_count = 0
            self._gpu_crash_count += 1
            print(f"[LiveView] GPU 크래시 횟수: {self._gpu_crash_count}")

            if self._gpu_crash_count >= 2:
                try:
                    from config import DATA_DIR
                    os.makedirs(DATA_DIR, exist_ok=True)
                    flag_path = os.path.join(DATA_DIR, ".gpu_crash")
                    with open(flag_path, 'w') as f:
                        f.write(f"crash_count={self._gpu_crash_count}\nexit_code={exit_code}\n")
                    print(f"[LiveView] GPU 크래시 플래그 생성 → 다음 실행에서 소프트웨어 렌더링")
                except Exception as e:
                    print(f"[LiveView] GPU 크래시 플래그 생성 실패: {e}")

        self._schedule_reconnect(f"렌더 프로세스 {reason}")

    def _schedule_reconnect(self, reason: str):
        """자동 재연결 스케줄링 (최대 횟수 제한)"""
        if self._reconnect_count >= self._max_reconnect:
            print(f"[LiveView] 최대 재연결 횟수 초과 ({self._max_reconnect}회)")
            self.status_label.setText(f"{self.device.name} - 연결 실패 (재시도 초과)")
            self.status_label.setStyleSheet("color: #FF5252; font-weight: bold; font-size: 11px;")
            return

        self._reconnect_count += 1
        delay = min(2000 * self._reconnect_count, 10000)  # 2초~10초 백오프
        print(f"[LiveView] {reason} → {delay/1000:.0f}초 후 재연결 ({self._reconnect_count}/{self._max_reconnect})")
        self.status_label.setText(f"{self.device.name} - 재연결 중... ({self._reconnect_count}/{self._max_reconnect})")
        self.status_label.setStyleSheet("color: #FFC107; font-weight: bold; font-size: 11px;")

        # 로딩 오버레이 표시
        if hasattr(self, '_loading_overlay') and self._loading_overlay:
            self._loading_overlay.setText(f"재연결 중... ({self._reconnect_count}/{self._max_reconnect})")
            self._loading_overlay.show()

        self._reconnect_timer = QTimer.singleShot(delay, self._do_reconnect)

    def _do_reconnect(self):
        """실제 재연결 수행"""
        try:
            print(f"[LiveView] 재연결 시도: {self.device.name}")
            self._load_kvm_url()
        except Exception as e:
            print(f"[LiveView] 재연결 실패: {e}")
            self._schedule_reconnect(f"재연결 예외: {e}")

    def _inject_webrtc_monitor(self):
        """WebRTC 연결 상태 모니터링 JavaScript 주입"""
        js = """
        (function() {
            if (window._wellcom_rtc_monitor) return;
            window._wellcom_rtc_monitor = true;

            // RTCPeerConnection 감시
            var origPC = window._origRTCPeerConnection || window.RTCPeerConnection;
            var patchedPC = function(config) {
                var pc = new origPC(config);
                pc.addEventListener('iceconnectionstatechange', function() {
                    var state = pc.iceConnectionState;
                    console.log('[WellcomRTC] ICE state: ' + state);
                    if (state === 'disconnected' || state === 'failed' || state === 'closed') {
                        document.title = 'WELLCOM_RTC_' + state.toUpperCase();
                    } else if (state === 'connected' || state === 'completed') {
                        document.title = 'WELLCOM_RTC_CONNECTED';
                    }
                });
                pc.addEventListener('connectionstatechange', function() {
                    console.log('[WellcomRTC] Connection state: ' + pc.connectionState);
                });
                return pc;
            };
            patchedPC.prototype = origPC.prototype;
            if (!window._origRTCPeerConnection) {
                window._origRTCPeerConnection = origPC;
            }
            window.RTCPeerConnection = patchedPC;
            true;
        })();
        """
        self.web_view.page().runJavaScript(js)
        # 타이틀 변경 감시 시작
        self.web_view.page().titleChanged.connect(self._on_webrtc_title_changed)

    def _on_webrtc_title_changed(self, title: str):
        """WebRTC 상태 변경 감지 (title로 전달)"""
        if not title.startswith('WELLCOM_RTC_'):
            return
        state = title.replace('WELLCOM_RTC_', '')
        print(f"[LiveView] WebRTC 상태 변경: {state}")
        if state in ('DISCONNECTED', 'FAILED', 'CLOSED'):
            self.status_label.setText(f"{self.device.name} - WebRTC 끊김")
            self.status_label.setStyleSheet("color: #FF5252; font-weight: bold; font-size: 11px;")
            self._schedule_reconnect(f"WebRTC {state}")
        elif state == 'CONNECTED':
            self.status_label.setText(f"{self.device.name} - 연결됨")
            self.status_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 11px;")

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
        """아이온2 모드 시작 - Pointer Lock API 사용 + 자동 Rel 전환"""
        self.game_mode_active = True

        # 마우스 모드를 Relative로 자동 전환
        if self.mouse_mode_absolute:
            self._toggle_mouse_mode()

        # JavaScript로 아이온2 모드 활성화
        js = self.AION2_MODE_JS.replace("%SENSITIVITY%", str(self.sensitivity))
        self.web_view.page().runJavaScript(js, self._on_aion2_mode_result)

        # UI 업데이트
        self.game_mode_bar.show()
        self.btn_game_mode.setText("해제 (Alt+F2)")
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
            self.game_mode_bar.setText("  화면 클릭하여 마우스 잠금 | ALT: 커서 | Alt+F2: 해제")

    def _stop_game_mode(self):
        """아이온2 모드 중지 + 자동 Abs 복원"""
        self.game_mode_active = False

        # JavaScript로 아이온2 모드 해제
        self.web_view.page().runJavaScript(self.AION2_STOP_JS)

        # 마우스 모드를 Absolute로 자동 복원
        if not self.mouse_mode_absolute:
            self._toggle_mouse_mode()

        # UI 업데이트
        self.game_mode_bar.hide()
        self.btn_game_mode.setText("아이온2 (Alt+F1)")
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

    # ─── USB Mass Storage ─────────────────────────────────

    def _on_usb_mount(self):
        """USB 마운트: 클라우드 파일 목록 조회 → 선택 → 다운로드+마운트"""
        try:
            if self._usb_thread and self._usb_thread.isRunning():
                QMessageBox.warning(self, "USB", "USB 작업이 진행 중입니다.")
                return

            from api_client import api_client
            if not api_client.is_logged_in:
                QMessageBox.warning(self, "USB 마운트", "로그인이 필요합니다.")
                return

            self.btn_usb_mount.setEnabled(False)
            self.btn_usb_mount.setText("조회 중...")

            self._usb_thread = USBWorkerThread(self.device, mode=USBWorkerThread.MODE_CLOUD_LIST)
            self._usb_thread.cloud_files_ready.connect(self._on_cloud_files_ready)
            self._usb_thread.start()
        except Exception as e:
            self.btn_usb_mount.setEnabled(True)
            self.btn_usb_mount.setText("USB 마운트")
            print(f"[USB 마운트 오류] {e}")

    def _on_usb_files_ready(self, files):
        """로컬 파일 목록 수신 → 선택 → 마운트"""
        try:
            self.btn_usb_mount.setEnabled(True)
            self.btn_usb_mount.setText("USB 마운트")

            if not files:
                QMessageBox.information(
                    self, "USB 마운트",
                    "KVM /tmp에 파일이 없습니다.\n"
                    "먼저 '파일 전송'으로 파일을 업로드하세요."
                )
                return

            selected, ok = QInputDialog.getItem(
                self, "USB 마운트 (로컬)", "마운트할 파일 선택:", files, 0, False
            )
            if not ok or not selected:
                return

            file_path = f"/tmp/{selected}"

            self.btn_usb_mount.setEnabled(False)
            self.btn_usb_mount.setText("마운트 중...")
            self.btn_usb_eject.setEnabled(False)

            self._usb_thread = USBWorkerThread(self.device, mode=USBWorkerThread.MODE_MOUNT, file_path=file_path)
            self._usb_thread.progress.connect(self._on_usb_progress)
            self._usb_thread.finished_ok.connect(self._on_usb_mount_done)
            self._usb_thread.finished_err.connect(self._on_usb_mount_error)
            self._usb_thread.start()
        except Exception as e:
            self.btn_usb_mount.setEnabled(True)
            self.btn_usb_mount.setText("USB 마운트")
            self.btn_usb_eject.setEnabled(True)
            print(f"[USB 파일선택 오류] {e}")

    def _on_cloud_files_ready(self, files):
        """클라우드 파일 목록 수신 → 전체 목록에서 선택 → 다운로드+마운트"""
        try:
            self.btn_usb_mount.setEnabled(True)
            self.btn_usb_mount.setText("USB 마운트")

            if not files:
                QMessageBox.information(
                    self, "USB 마운트",
                    "클라우드에 파일이 없습니다.\n"
                    "먼저 우클릭 → '클라우드 업로드'로 파일을 업로드하세요."
                )
                return

            from api_client import api_client

            # 쿼타 정보
            quota_str = ""
            try:
                qi = api_client.get_quota()
                q = qi.get('quota')
                used = qi.get('used', 0)
                if q is None:
                    quota_str = f"사용: {used // (1024*1024)}MB / 무제한"
                elif q > 0:
                    quota_str = f"사용: {used // (1024*1024)}MB / {q // (1024*1024)}MB"
            except Exception:
                pass

            # 파일 목록 표시 (이름 + 크기)
            display_list = []
            for f in files:
                size_mb = f.get('size', 0) / (1024 * 1024)
                name = f.get('filename', '?')
                if size_mb >= 1:
                    display_list.append(f"{name} ({size_mb:.1f}MB)")
                else:
                    size_kb = f.get('size', 0) / 1024
                    display_list.append(f"{name} ({size_kb:.1f}KB)")

            label = f"파일 {len(files)}개"
            if quota_str:
                label = f"{quota_str} | 파일 {len(files)}개"

            selected, ok = QInputDialog.getItem(
                self, "USB 마운트", label, display_list, 0, False
            )
            if not ok or not selected:
                return

            # 선택된 인덱스로 파일 정보 찾기
            idx = display_list.index(selected)
            file_info = files[idx]

            download_url = api_client.get_file_download_url(file_info['id'])
            token = api_client._token

            self.btn_usb_mount.setEnabled(False)
            self.btn_usb_mount.setText("다운로드 중...")
            self.btn_usb_eject.setEnabled(False)

            self._usb_thread = USBWorkerThread(
                self.device,
                mode=USBWorkerThread.MODE_CLOUD_MOUNT,
                download_url=download_url,
                token=token,
                filename=file_info['filename'],
            )
            self._usb_thread.progress.connect(self._on_usb_progress)
            self._usb_thread.finished_ok.connect(self._on_usb_mount_done)
            self._usb_thread.finished_err.connect(self._on_usb_mount_error)
            self._usb_thread.start()
        except Exception as e:
            self.btn_usb_mount.setEnabled(True)
            self.btn_usb_mount.setText("USB 마운트")
            self.btn_usb_eject.setEnabled(True)
            print(f"[클라우드 마운트 오류] {e}")

    def _on_keyboard_reset(self):
        """키보드 HID 리셋 — kvm_app 재시작으로 /dev/hidg0 fd 갱신"""
        import threading

        def _do_reset():
            try:
                import paramiko, time

                print("[HID] 키보드 리셋 시작 (kvm_app 재시작)...")

                # 별도 SSH 연결 (exec_command 사용)
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(
                    self.device.ip,
                    port=self.device.info.port,
                    username=self.device.info.username,
                    password=self.device.info.password,
                    timeout=5
                )

                shell = ssh.invoke_shell()
                time.sleep(0.3)
                if shell.recv_ready():
                    shell.recv(4096)

                # 1. kvm_app 종료
                shell.send("killall kvm_app 2>/dev/null && echo KVM_APP_KILLED\n")
                time.sleep(1.0)

                # 2. kvm_app 재시작
                shell.send("/userdata/picokvm/bin/kvm_app > /tmp/kvm_app.log 2>&1 &\n")
                time.sleep(0.5)
                shell.send("echo KVM_APP_RESTARTED\n")
                time.sleep(1.0)

                out = ''
                while shell.recv_ready():
                    out += shell.recv(4096).decode('utf-8', errors='replace')
                print(f"[HID] 리셋 결과: {out.strip()}")

                shell.close()
                ssh.close()
                print("[HID] 키보드 HID 리셋 완료 (kvm_app 재시작)")
            except Exception as e:
                print(f"[HID] 키보드 리셋 오류: {e}")

        self.btn_kb_reset.setEnabled(False)
        self.btn_kb_reset.setText("리셋 중...")
        threading.Thread(target=_do_reset, daemon=True).start()
        # kvm_app 재시작 + WebRTC 재연결 시간 고려하여 5초 후 버튼 복원
        QTimer.singleShot(5000, lambda: (
            self.btn_kb_reset.setEnabled(True),
            self.btn_kb_reset.setText("⌨ 리셋")
        ))

    def _on_usb_eject(self):
        """USB Mass Storage 해제 (백그라운드)"""
        try:
            if self._usb_thread and self._usb_thread.isRunning():
                QMessageBox.warning(self, "USB", "USB 작업이 진행 중입니다.")
                return

            self.btn_usb_eject.setEnabled(False)
            self.btn_usb_eject.setText("해제 중...")
            self.btn_usb_mount.setEnabled(False)

            self._usb_thread = USBWorkerThread(self.device, mode=USBWorkerThread.MODE_EJECT)
            self._usb_thread.progress.connect(self._on_usb_progress)
            self._usb_thread.finished_ok.connect(self._on_usb_eject_done)
            self._usb_thread.finished_err.connect(self._on_usb_eject_error)
            self._usb_thread.start()
        except Exception as e:
            self.btn_usb_eject.setEnabled(True)
            self.btn_usb_eject.setText("USB 해제")
            self.btn_usb_mount.setEnabled(True)
            print(f"[USB 해제 오류] {e}")

    def _on_usb_progress(self, msg):
        try:
            self.btn_usb_mount.setText(msg[:20])
        except Exception:
            pass

    def _on_usb_mount_done(self, msg):
        try:
            self.btn_usb_mount.setEnabled(True)
            self.btn_usb_mount.setText("USB 마운트")
            self.btn_usb_eject.setEnabled(True)
        except Exception:
            pass
        try:
            QMessageBox.information(self, "USB 마운트", f"{msg}\n\n연결된 PC에서 새 USB 드라이브를 확인하세요.")
        except Exception:
            pass

    def _on_usb_mount_error(self, msg):
        try:
            self.btn_usb_mount.setEnabled(True)
            self.btn_usb_mount.setText("USB 마운트")
            self.btn_usb_eject.setEnabled(True)
        except Exception:
            pass
        try:
            QMessageBox.warning(self, "USB 마운트 실패", msg)
        except Exception:
            pass

    def _on_usb_eject_done(self, msg):
        try:
            self.btn_usb_eject.setEnabled(True)
            self.btn_usb_eject.setText("USB 해제")
            self.btn_usb_mount.setEnabled(True)
        except Exception:
            pass
        try:
            QMessageBox.information(self, "USB 해제", msg)
        except Exception:
            pass

    def _on_usb_eject_error(self, msg):
        try:
            self.btn_usb_eject.setEnabled(True)
            self.btn_usb_eject.setText("USB 해제")
            self.btn_usb_mount.setEnabled(True)
        except Exception:
            pass
        try:
            QMessageBox.warning(self, "USB 해제 실패", msg)
        except Exception:
            pass

    # ─── 부분제어 ──────────────────────────────────────────

    def _start_partial_control(self):
        """부분제어 시작 — 영역 선택 오버레이 표시"""
        # 영역 선택 오버레이
        if not hasattr(self, '_region_overlay') or self._region_overlay is None:
            self._region_overlay = RegionSelectOverlay(self.web_view)
            self._region_overlay.region_selected.connect(self._on_region_selected)

        self._region_overlay.setGeometry(self.web_view.rect())
        self._region_overlay.show()

    def _on_region_selected(self, x, y, w, h):
        """영역 선택 완료 → LiveViewDialog 닫고 GridViewTab에 크롭 적용"""
        main_win = self.parent()
        group = self.device.info.group or 'default'
        print(f"[부분제어] 영역 선택: ({x}, {y}, {w}, {h}), group={group}, device={self.device.name}")

        # 부분제어 플래그 설정 (close 후 _restart_device_preview 방지)
        self._partial_control_closing = True

        # LiveViewDialog 닫기
        self.close()

        # MainWindow의 GridViewTab들에 크롭 적용
        if hasattr(main_win, '_apply_partial_crop'):
            print(f"[부분제어] _apply_partial_crop 호출: group={group}, region=({x},{y},{w},{h})")
            main_win._apply_partial_crop(group, (x, y, w, h))
        else:
            print("[부분제어] 경고: MainWindow에 _apply_partial_crop 없음")

    # ─── Vision 기능 ─────────────────────────────────────

    def _toggle_vision(self):
        """Vision(YOLO) 모드 토글"""
        if not VISION_AVAILABLE or self.vision_controller is None:
            return

        if self.vision_controller.is_running:
            self.vision_controller.stop()
            self._vision_overlay.hide()
        else:
            if not self.vision_controller._detector.is_model_loaded:
                model_path = app_settings.get('vision.model_path', '')
                if not model_path:
                    QMessageBox.warning(
                        self, "Vision",
                        "YOLO 모델이 설정되지 않았습니다.\n"
                        "V-Set 버튼에서 모델 경로를 설정해주세요."
                    )
                    return
                self.vision_controller.load_model(model_path)

            self._vision_overlay.show()
            self.vision_controller.start()

    def _on_vision_status_changed(self, status: str):
        """Vision 상태 변경 시 UI 업데이트"""
        if not VISION_AVAILABLE:
            return

        if status == "running":
            self.btn_vision.setText("Vision ON")
            self.btn_vision.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    padding: 3px 8px;
                    border-radius: 3px;
                    font-size: 11px;
                    font-weight: bold;
                }
            """)
        elif status == "error":
            self.btn_vision.setText("Vision ERR")
            self.btn_vision.setStyleSheet("""
                QPushButton {
                    background-color: #f44336;
                    color: white;
                    padding: 3px 8px;
                    border-radius: 3px;
                    font-size: 11px;
                }
            """)
        else:
            self.btn_vision.setText("Vision")
            self.btn_vision.setStyleSheet("""
                QPushButton {
                    background-color: #9C27B0;
                    color: white;
                    padding: 3px 8px;
                    border-radius: 3px;
                    font-size: 11px;
                }
                QPushButton:hover { background-color: #7B1FA2; }
            """)

    def _show_vision_settings(self):
        """Vision 설정 다이얼로그"""
        if not VISION_AVAILABLE:
            return

        dialog = VisionSettingsDialog(self.vision_controller, self)
        dialog.exec()

    # ─── 데이터 수집 (Rec) ─────────────────────────────────

    def _toggle_recording(self):
        """학습 데이터 수집 모드 토글"""
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    # 입력 이벤트 캡처 JS - keydown/keyup/mousedown/mouseup을 기록
    REC_INPUT_HOOK_JS = """
    (function() {
        if (window._wlRecHooked) return;
        window._wlRecHooked = true;
        window._wlInputLog = [];
        function logEv(type, e) {
            var entry = {t: Date.now(), type: type};
            if (type.startsWith('key')) {
                entry.key = e.key || '';
                entry.code = e.code || '';
            } else {
                entry.btn = e.button;
                entry.x = e.clientX;
                entry.y = e.clientY;
            }
            window._wlInputLog.push(entry);
            if (window._wlInputLog.length > 500) window._wlInputLog.shift();
        }
        document.addEventListener('keydown', function(e){ logEv('keydown', e); }, true);
        document.addEventListener('keyup', function(e){ logEv('keyup', e); }, true);
        document.addEventListener('mousedown', function(e){ logEv('mousedown', e); }, true);
        document.addEventListener('mouseup', function(e){ logEv('mouseup', e); }, true);
    })();
    """

    # JS에서 입력 로그를 가져오고 버퍼 비우기
    REC_FLUSH_INPUT_JS = """
    (function() {
        var log = window._wlInputLog || [];
        window._wlInputLog = [];
        return JSON.stringify(log);
    })();
    """

    def _start_recording(self):
        """수집 시작"""
        os.makedirs(self._rec_output_dir, exist_ok=True)
        self._recording = True
        self._rec_count = 0
        self._rec_input_log = []

        # 입력 이벤트 캡처 JS 주입
        if not self._rec_input_injected:
            self.web_view.page().runJavaScript(self.REC_INPUT_HOOK_JS)
            self._rec_input_injected = True

        fps = app_settings.get('vision.capture_fps', 2)
        interval_ms = max(500, int(1000 / fps))

        self._rec_timer = QTimer(self)
        self._rec_timer.timeout.connect(self._rec_capture_frame)
        self._rec_timer.start(interval_ms)

        self.btn_rec.setText("REC ●")
        self.btn_rec.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                padding: 3px 8px;
                border-radius: 3px;
                font-size: 11px;
                font-weight: bold;
            }
        """)
        self.rec_count_label.setText("0장")
        print(f"[수집] 시작 - 저장: {self._rec_output_dir} (입력 기록 활성)")

    def _stop_recording(self):
        """수집 중지"""
        self._recording = False
        if self._rec_timer:
            self._rec_timer.stop()
            self._rec_timer = None

        self.btn_rec.setText("Rec")
        self.btn_rec.setStyleSheet("""
            QPushButton {
                background-color: #607D8B;
                color: white;
                padding: 3px 8px;
                border-radius: 3px;
                font-size: 11px;
            }
            QPushButton:hover { background-color: #546E7A; }
        """)
        self.rec_count_label.setText("")
        print(f"[수집] 중지 - 총 {self._rec_count}장 저장됨")

    def _rec_capture_frame(self):
        """현재 WebView 화면을 이미지로 저장 + 입력 로그 수집"""
        if not self._recording:
            return
        try:
            pixmap = self.web_view.grab()
            if pixmap.isNull() or pixmap.width() < 100:
                return
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            filename = f"frame_{timestamp}.jpg"
            filepath = os.path.join(self._rec_output_dir, filename)
            pixmap.save(filepath, "JPEG", 95)
            self._rec_count += 1
            self.rec_count_label.setText(f"{self._rec_count}장")

            # JS에서 입력 로그 가져오기 (비동기)
            import json
            def _on_input_log(result):
                if not result:
                    return
                try:
                    events = json.loads(result)
                    if events:
                        log_name = f"frame_{timestamp}.json"
                        log_path = os.path.join(self._rec_output_dir, log_name)
                        with open(log_path, 'w') as f:
                            json.dump(events, f)
                except Exception:
                    pass
            self.web_view.page().runJavaScript(self.REC_FLUSH_INPUT_JS, _on_input_log)

        except Exception as e:
            print(f"[수집] 캡처 오류: {e}")

    def resizeEvent(self, event):
        """오버레이 크기를 WebView에 맞춤"""
        super().resizeEvent(event)
        if hasattr(self, '_loading_overlay') and self._loading_overlay and not self._page_loaded:
            self._loading_overlay.setGeometry(self.web_view.geometry())
        if VISION_AVAILABLE and hasattr(self, '_vision_overlay'):
            self._vision_overlay.setGeometry(self.web_view.geometry())
        if hasattr(self, '_region_overlay') and self._region_overlay and self._region_overlay.isVisible():
            self._region_overlay.setGeometry(self.web_view.rect())

    def closeEvent(self, event):
        # GPU 플래그 정리 (정상 종료 — 크래시 아님)
        self._set_gpu_loading_flag(False)
        print("[LiveView] 정상 종료 — GPU 플래그 제거")

        # 마지막 창 크기 저장
        if app_settings.get('liveview.remember_resolution', True):
            size = self.size()
            app_settings.set('liveview.last_width', size.width(), False)
            app_settings.set('liveview.last_height', size.height(), False)
            app_settings.save()
            print(f"[LiveView] 창 크기 저장: {size.width()}x{size.height()}")

        # 재연결 방지 (닫는 중에 재연결 시도 안 함)
        self._max_reconnect = 0

        self._stop_game_mode()
        if self._recording:
            self._stop_recording()
        if self.vision_controller:
            self.vision_controller.cleanup()

        # 시그널 해제 (재연결 트리거 방지)
        try:
            self.web_view.page().titleChanged.disconnect(self._on_webrtc_title_changed)
        except Exception:
            pass
        try:
            self.web_view.loadFinished.disconnect(self._on_page_loaded)
        except Exception:
            pass
        try:
            self.aion2_page.renderProcessTerminated.disconnect(self._on_render_process_terminated)
        except Exception:
            pass

        # WebView 정리 (WebRTC 연결 해제 + Chromium 리소스 반환)
        try:
            self.web_view.stop()
            self.web_view.setUrl(QUrl("about:blank"))
            # 이벤트 처리: about:blank 전환이 시작되도록
            QApplication.processEvents()
            self.web_view.setParent(None)
            self.web_view.deleteLater()
            print("[LiveView] WebView 정리 완료")
        except Exception as e:
            print(f"[LiveView] WebView 정리 오류: {e}")
        self.hid.disconnect()
        super().closeEvent(event)


class VisionSettingsDialog(QDialog):
    """Vision(YOLO) 설정 다이얼로그"""

    def __init__(self, vision_controller, parent=None):
        super().__init__(parent)
        self._vc = vision_controller
        self.setWindowTitle("Vision (YOLO) 설정")
        self.setMinimumWidth(400)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 모델 경로
        group_model = QGroupBox("YOLO 모델")
        model_layout = QHBoxLayout(group_model)
        self.model_path_edit = QLineEdit(app_settings.get('vision.model_path', ''))
        self.model_path_edit.setPlaceholderText("모델 파일 경로 (.pt)")
        model_layout.addWidget(self.model_path_edit)
        btn_browse = QPushButton("찾기")
        btn_browse.clicked.connect(self._browse_model)
        model_layout.addWidget(btn_browse)
        btn_load = QPushButton("로드")
        btn_load.clicked.connect(self._load_model)
        model_layout.addWidget(btn_load)
        layout.addWidget(group_model)

        # 추론 설정
        group_infer = QGroupBox("추론 설정")
        infer_layout = QVBoxLayout(group_infer)

        # 신뢰도
        conf_row = QHBoxLayout()
        conf_row.addWidget(QLabel("신뢰도 임계값:"))
        self.conf_spin = QSpinBox()
        self.conf_spin.setRange(1, 99)
        self.conf_spin.setValue(int(app_settings.get('vision.confidence', 0.5) * 100))
        self.conf_spin.setSuffix("%")
        conf_row.addWidget(self.conf_spin)
        infer_layout.addLayout(conf_row)

        # 캡처 FPS
        fps_row = QHBoxLayout()
        fps_row.addWidget(QLabel("캡처 FPS:"))
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 30)
        self.fps_spin.setValue(app_settings.get('vision.capture_fps', 2))
        fps_row.addWidget(self.fps_spin)
        infer_layout.addLayout(fps_row)

        # 디바이스
        device_row = QHBoxLayout()
        device_row.addWidget(QLabel("추론 디바이스:"))
        self.device_combo = QComboBox()
        self.device_combo.addItems(["auto", "cpu", "cuda"])
        current_device = app_settings.get('vision.device', 'auto')
        idx = self.device_combo.findText(current_device)
        if idx >= 0:
            self.device_combo.setCurrentIndex(idx)
        device_row.addWidget(self.device_combo)
        infer_layout.addLayout(device_row)

        layout.addWidget(group_infer)

        # 기능 토글
        group_features = QGroupBox("기능")
        feat_layout = QVBoxLayout(group_features)

        from PyQt6.QtWidgets import QCheckBox
        self.chk_overlay = QCheckBox("오버레이 표시 (바운딩 박스)")
        self.chk_overlay.setChecked(app_settings.get('vision.overlay_enabled', True))
        feat_layout.addWidget(self.chk_overlay)

        self.chk_auto_action = QCheckBox("자동 HID 입력 (규칙 기반)")
        self.chk_auto_action.setChecked(app_settings.get('vision.auto_action_enabled', False))
        feat_layout.addWidget(self.chk_auto_action)

        self.chk_log = QCheckBox("감지 로깅")
        self.chk_log.setChecked(app_settings.get('vision.log_enabled', True))
        feat_layout.addWidget(self.chk_log)

        layout.addWidget(group_features)

        # 모델 정보
        if self._vc and self._vc._detector.is_model_loaded:
            names = self._vc.get_model_names()
            if names:
                group_info = QGroupBox(f"모델 클래스 ({len(names)}개)")
                info_layout = QVBoxLayout(group_info)
                classes_text = ", ".join(f"{v}" for v in names.values())
                lbl = QLabel(classes_text)
                lbl.setWordWrap(True)
                lbl.setStyleSheet("color: #aaa; font-size: 11px;")
                info_layout.addWidget(lbl)
                layout.addWidget(group_info)

        # 버튼
        btn_layout = QHBoxLayout()
        btn_save = QPushButton("저장")
        btn_save.clicked.connect(self._save_settings)
        btn_layout.addWidget(btn_save)
        btn_cancel = QPushButton("취소")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

    def _browse_model(self):
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "YOLO 모델 선택", "", "YOLO Model (*.pt *.onnx);;All Files (*)"
        )
        if path:
            self.model_path_edit.setText(path)

    def _load_model(self):
        path = self.model_path_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "Vision", "모델 경로를 입력해주세요.")
            return
        if self._vc:
            self._vc.load_model(path)
            app_settings.set('vision.model_path', path)

    def _save_settings(self):
        app_settings.set('vision.model_path', self.model_path_edit.text().strip())
        app_settings.set('vision.confidence', self.conf_spin.value() / 100.0)
        app_settings.set('vision.capture_fps', self.fps_spin.value())
        app_settings.set('vision.device', self.device_combo.currentText())
        app_settings.set('vision.overlay_enabled', self.chk_overlay.isChecked())
        app_settings.set('vision.auto_action_enabled', self.chk_auto_action.isChecked())
        app_settings.set('vision.log_enabled', self.chk_log.isChecked())

        # 실시간 적용
        if self._vc:
            self._vc.set_confidence(self.conf_spin.value() / 100.0)
            self._vc.set_fps(self.fps_spin.value())
            self._vc.set_overlay_enabled(self.chk_overlay.isChecked())
            self._vc.set_auto_action(self.chk_auto_action.isChecked())
            self._vc.set_log_enabled(self.chk_log.isChecked())

        self.accept()


class MainWindow(QMainWindow):
    """메인 애플리케이션 윈도우"""

    def __init__(self):
        super().__init__()

        self.manager = KVMManager()
        self._load_devices_from_source()

        self.status_thread: StatusUpdateThread = None
        self.current_device: KVMDevice = None
        self._live_control_device: str = None  # 1:1 제어 중인 장치 이름 (WebRTC 충돌 방지)
        self._initializing = True  # 초기화 중 플래그
        self._upload_progress = None
        self._upload_thread = None
        self._cloud_upload_thread = None

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
        from api_client import api_client
        title = "WellcomLAND"
        if api_client.user:
            name = api_client.user.get('display_name') or api_client.user.get('username', '')
            title = f"WellcomLAND - {name}"
        self.setWindowTitle(title)
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

        # 드래그 앤 드롭 (장치를 그룹 간 이동)
        self.device_tree.setDragEnabled(True)
        self.device_tree.setAcceptDrops(True)
        self.device_tree.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        self.device_tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        # 드롭 완료 후 DB 업데이트를 위해 원본 dropEvent 래핑
        self._orig_tree_dropEvent = self.device_tree.dropEvent
        self.device_tree.dropEvent = self._on_tree_drop_event

        layout.addWidget(self.device_tree)

        self.stats_label = QLabel("전체: 0 | 온라인: 0 | 오프라인: 0")
        layout.addWidget(self.stats_label)

        # ── 장치 기본정보 패널 ──
        info_group = QGroupBox("장치 정보")
        info_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 12px; }")
        info_layout = QVBoxLayout(info_group)
        info_layout.setContentsMargins(5, 10, 5, 5)
        info_layout.setSpacing(3)

        self.info_labels = {}
        for key, label in [("name", "이름"), ("ip", "IP 주소"), ("group", "그룹"),
                           ("status", "상태"), ("web_port", "웹 포트")]:
            row = QHBoxLayout()
            lbl = QLabel(f"{label}:")
            lbl.setFixedWidth(60)
            lbl.setStyleSheet("color: #888; font-size: 11px;")
            val = QLabel("-")
            val.setStyleSheet("font-size: 11px;")
            self.info_labels[key] = val
            row.addWidget(lbl)
            row.addWidget(val, 1)
            info_layout.addLayout(row)

        # 제어 버튼
        btn_layout = QHBoxLayout()
        self.btn_start_live = QPushButton("실시간 제어")
        self.btn_start_live.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50; color: white;
                font-size: 11px; font-weight: bold;
                padding: 5px 10px; border-radius: 4px;
            }
            QPushButton:hover { background-color: #45a049; }
            QPushButton:disabled { background-color: #ccc; }
        """)
        self.btn_start_live.setEnabled(False)
        self.btn_start_live.clicked.connect(self._on_start_live_control)
        btn_layout.addWidget(self.btn_start_live)

        self.btn_open_web = QPushButton("웹 열기")
        self.btn_open_web.setStyleSheet("""
            QPushButton {
                background-color: #2196F3; color: white;
                font-size: 11px; padding: 5px 10px; border-radius: 4px;
            }
            QPushButton:hover { background-color: #1976D2; }
            QPushButton:disabled { background-color: #ccc; }
        """)
        self.btn_open_web.setEnabled(False)
        self.btn_open_web.clicked.connect(self._on_open_web_browser)
        btn_layout.addWidget(self.btn_open_web)

        info_layout.addLayout(btn_layout)
        layout.addWidget(info_group)

        return panel

    def _create_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        self.tab_widget = QTabWidget()

        # 1. "전체 목록" 탭 (항상 첫 번째)
        self.grid_view_tab = GridViewTab(self.manager)
        self.grid_view_tab.device_selected.connect(self._on_grid_device_selected)
        self.grid_view_tab.device_double_clicked.connect(self._on_grid_device_double_clicked)
        self.grid_view_tab.device_right_clicked.connect(self._on_grid_device_right_clicked)
        self.tab_widget.addTab(self.grid_view_tab, "전체 목록")

        # 2. 그룹별 탭 (옆에 추가)
        self.group_grid_tabs: dict[str, GridViewTab] = {}
        self._build_group_tabs()

        # 탭 변경 시그널 연결
        self.tab_widget.currentChanged.connect(self._on_tab_changed)

        layout.addWidget(self.tab_widget)
        return panel

    def _collect_groups(self) -> dict:
        """현재 그룹 목록과 장치 수 수집"""
        groups = {}
        for device in self.manager.get_all_devices():
            group = device.info.group or 'default'
            groups[group] = groups.get(group, 0) + 1
        # DB에 등록된 빈 그룹도 포함
        try:
            db_groups = self.manager.get_groups()
            for g in db_groups:
                gn = g['name']
                if gn not in groups:
                    groups[gn] = 0
        except Exception:
            pass
        return groups

    def _build_group_tabs(self):
        """그룹별 탭 초기 생성 (메인 탭 옆에 추가)"""
        groups = self._collect_groups()
        for group_name in sorted(groups.keys(), key=lambda x: (x == 'default', x)):
            self._add_group_tab(group_name, groups[group_name])

    def _add_group_tab(self, group_name: str, device_count: int):
        """단일 그룹 탭을 메인 탭에 추가"""
        tab_label = f"{group_name} ({device_count})"
        group_grid = GridViewTab(self.manager)
        group_grid.device_selected.connect(self._on_grid_device_selected)
        group_grid.device_double_clicked.connect(self._on_grid_device_double_clicked)
        group_grid.device_right_clicked.connect(self._on_grid_device_right_clicked)
        group_grid._filter_group = group_name
        self.group_grid_tabs[group_name] = group_grid
        self.tab_widget.addTab(group_grid, tab_label)

    def refresh_group_tabs(self):
        """그룹 탭 새로고침 - 기존 탭 유지, 라벨 업데이트, 추가/제거만 처리"""
        try:
            groups = self._collect_groups()
            existing_names = set(self.group_grid_tabs.keys())
            needed_names = set(groups.keys())

            # 삭제할 그룹 탭
            for name in existing_names - needed_names:
                tab = self.group_grid_tabs.pop(name, None)
                if tab:
                    idx = self.tab_widget.indexOf(tab)
                    if idx >= 0:
                        self.tab_widget.removeTab(idx)
                    tab.cleanup()
                    tab.deleteLater()

            # 새로 추가할 그룹 탭
            for name in needed_names - existing_names:
                self._add_group_tab(name, groups.get(name, 0))

            # 기존 탭 라벨만 업데이트 (장치 수 반영)
            for name in needed_names & existing_names:
                tab = self.group_grid_tabs.get(name)
                if tab:
                    idx = self.tab_widget.indexOf(tab)
                    if idx >= 0:
                        self.tab_widget.setTabText(idx, f"{name} ({groups.get(name, 0)})")

            # 전체 탭 라벨 업데이트
            total = len(self.manager.get_all_devices())
            self.tab_widget.setTabText(0, f"전체 목록 ({total})")
        except Exception as e:
            print(f"[MainWindow] refresh_group_tabs 오류: {e}")

    def _create_device_control_tab(self) -> QWidget:
        """기기 제어 통합 탭 (실시간 제어 + 장치 정보 + 키보드/마우스 + USB 로그)"""
        widget = QWidget()
        main_layout = QVBoxLayout(widget)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(8)

        # === 상단: 선택된 장치 + 제어 버튼 ===
        top_layout = QHBoxLayout()

        self.live_device_label = QLabel("장치를 선택하세요")
        self.live_device_label.setStyleSheet("font-weight: bold; font-size: 15px; padding: 5px;")
        top_layout.addWidget(self.live_device_label, 1)

        self.btn_start_live = QPushButton("실시간 제어")
        self.btn_start_live.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50; color: white;
                font-size: 13px; font-weight: bold;
                padding: 8px 20px; border-radius: 5px;
            }
            QPushButton:hover { background-color: #45a049; }
            QPushButton:disabled { background-color: #ccc; }
        """)
        self.btn_start_live.setEnabled(False)
        self.btn_start_live.clicked.connect(self._on_start_live_control)
        top_layout.addWidget(self.btn_start_live)

        self.btn_open_web = QPushButton("웹 열기")
        self.btn_open_web.setStyleSheet("""
            QPushButton {
                background-color: #2196F3; color: white;
                font-size: 13px; padding: 8px 20px; border-radius: 5px;
            }
            QPushButton:hover { background-color: #1976D2; }
            QPushButton:disabled { background-color: #ccc; }
        """)
        self.btn_open_web.setEnabled(False)
        self.btn_open_web.clicked.connect(self._on_open_web_browser)
        top_layout.addWidget(self.btn_open_web)

        main_layout.addLayout(top_layout)

        # === 빠른 작업 버튼 ===
        quick_layout = QHBoxLayout()
        for text, handler in [("USB 재연결", self._on_reconnect_usb),
                               ("재부팅", self._on_reboot_device)]:
            btn = QPushButton(text)
            btn.setStyleSheet("padding: 6px 12px;")
            btn.clicked.connect(handler)
            quick_layout.addWidget(btn)
        main_layout.addLayout(quick_layout)

        # === 중앙: 장치 정보 + 키보드/마우스 (좌우 분할) ===
        center_splitter = QSplitter(Qt.Orientation.Horizontal)

        # 좌측: 장치 정보
        info_widget = QWidget()
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)

        info_label = QLabel("장치 정보")
        info_label.setStyleSheet("font-weight: bold; font-size: 13px; padding: 3px;")
        info_layout.addWidget(info_label)

        self.info_table = QTableWidget(8, 2)
        self.info_table.setHorizontalHeaderLabels(["항목", "값"])
        self.info_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.info_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.info_table.verticalHeader().setVisible(False)
        self.info_table.setMaximumHeight(260)

        for i, prop in enumerate(["이름", "IP 주소", "상태", "USB 상태", "버전", "가동시간", "온도", "메모리"]):
            self.info_table.setItem(i, 0, QTableWidgetItem(prop))
            self.info_table.setItem(i, 1, QTableWidgetItem("-"))

        info_layout.addWidget(self.info_table)
        info_layout.addStretch()
        center_splitter.addWidget(info_widget)

        # 우측: 키보드/마우스 제어
        self.control_panel = DeviceControlPanel()
        center_splitter.addWidget(self.control_panel)

        center_splitter.setSizes([350, 650])
        main_layout.addWidget(center_splitter, 1)

        # === 하단: USB 로그 ===
        log_group = QGroupBox("USB 로그")
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(5, 5, 5, 5)

        self.usb_log_text = QTextEdit()
        self.usb_log_text.setReadOnly(True)
        self.usb_log_text.setMaximumHeight(120)
        self.usb_log_text.setStyleSheet("font-family: 'Consolas', monospace; font-size: 11px;")
        log_layout.addWidget(self.usb_log_text)

        btn_refresh_log = QPushButton("로그 새로고침")
        btn_refresh_log.setFixedHeight(28)
        btn_refresh_log.clicked.connect(self._on_refresh_usb_log)
        log_layout.addWidget(btn_refresh_log)

        main_layout.addWidget(log_group)

        return widget

    def _on_tab_changed(self, index):
        """메인 탭 변경 시 호출 — 이전 탭 stop → 현재 탭 start

        KVM은 동시 1개 연결만 지원하므로:
        1) 이전 탭의 모든 WebView를 완전 중지 (WebRTC 해제)
        2) 약간의 지연 후 현재 탭 활성화 (WebRTC 해제 대기)
        """
        try:
            if hasattr(self, '_initializing') and self._initializing:
                return

            current_widget = self.tab_widget.widget(index)

            # 1. 모든 다른 GridViewTab 완전 중지 (WebRTC 연결 해제)
            all_tabs = [self.grid_view_tab] + list(self.group_grid_tabs.values())
            for tab in all_tabs:
                if tab is not current_widget and tab._is_visible:
                    tab.on_tab_deactivated()

            # 2. 현재 탭이 GridViewTab이면 지연 후 활성화
            #    (이전 탭의 WebRTC 해제가 완료될 시간 확보)
            if isinstance(current_widget, GridViewTab):
                QTimer.singleShot(300, current_widget.on_tab_activated)
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

        for text, handler in [("USB 재연결", self._on_reconnect_usb),
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

        for text, handler in [("전체 상태 새로고침", self._on_refresh_all_status)]:
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

        # 관리자 패널 (admin 로그인 시에만 표시)
        from api_client import api_client
        if api_client.is_admin:
            file_menu.addSeparator()
            admin_action = QAction("관리자 패널...", self)
            admin_action.triggered.connect(self._on_open_admin_panel)
            file_menu.addAction(admin_action)

        file_menu.addSeparator()
        exit_action = QAction("종료", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        device_menu = menubar.addMenu("장치")
        device_menu.addAction("설정", self._on_device_settings)

        tools_menu = menubar.addMenu("도구")
        tools_menu.addAction("자동 검색...", self._on_auto_discover)
        tools_menu.addSeparator()
        settings_action = QAction("환경 설정...", self)
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self._on_app_settings)
        tools_menu.addAction(settings_action)

        help_menu = menubar.addMenu("도움말")
        help_menu.addAction("WellcomLAND 정보", self._show_about)

    def _create_toolbar(self):
        pass  # 메뉴에 통합됨

    def _load_devices_from_source(self):
        """서버/로컬 DB에서 기기 목록 로드

        - 일반 사용자: 서버에서 할당된 기기만 표시
        - 관리자(admin): 로컬 DB + 서버 기기 병합
        - 서버 연결 실패: 로컬 DB 기기 표시

        추가: 원격 KVM 레지스트리에서 릴레이 접속 정보를 가져와
        직접 접근 불가한 KVM의 IP/포트를 릴레이 주소로 자동 치환.
        """
        try:
            from api_client import api_client
            if api_client.is_logged_in:
                if api_client.is_admin:
                    # 관리자: 로컬 DB 먼저 로드 + 서버 기기 병합
                    self.manager.load_devices_from_db()
                    local_count = len(self.manager.devices)
                    devices = api_client.get_my_devices()
                    if devices:
                        self.manager.merge_devices_from_server(devices)
                        print(f"[MainWindow] 관리자: 서버 {len(devices)}개 병합 (로컬 {local_count}개 유지)")
                else:
                    # 일반 사용자: 서버에서 할당된 기기만 표시
                    devices = api_client.get_my_devices()
                    if devices:
                        self.manager.load_devices_from_server(devices)
                        print(f"[MainWindow] 사용자: 할당된 {len(devices)}개 기기 로드")
                    else:
                        self.manager.devices.clear()
                        print("[MainWindow] 사용자: 할당된 기기 없음")

                # 원격 KVM 릴레이 정보로 접근 불가 기기의 IP/포트 자동 치환
                self._apply_relay_substitution(api_client)
                return
        except Exception as e:
            print(f"[MainWindow] 서버 기기 로드 실패, 로컬 DB만 사용: {e}")

        # 서버 연결 실패 시 로컬 DB 사용
        self.manager.load_devices_from_db()
        print(f"[MainWindow] 로컬 DB에서 {len(self.manager.devices)}개 기기 로드")

    def _apply_relay_substitution(self, api_client):
        """원격 KVM 레지스트리에서 릴레이 정보를 가져와
        직접 접근 불가한 KVM의 IP/포트를 Tailscale 릴레이 주소로 치환.

        관제 PC (KVM과 같은 서브넷)에서는 치환하지 않음.
        메인 PC (다른 서브넷)에서만 릴레이 IP:port로 변경.
        """
        try:
            remote_kvms = api_client.get_remote_kvm_list()
            if not remote_kvms:
                return

            # 내 로컬 서브넷 확인 (같은 서브넷이면 직접 접근 가능)
            import socket
            local_ips = set()
            try:
                hostname = socket.gethostname()
                for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                    local_ips.add(info[4][0])
            except Exception:
                pass
            local_subnets = set()
            for ip in local_ips:
                parts = ip.split('.')
                if len(parts) == 4:
                    local_subnets.add(f"{parts[0]}.{parts[1]}.{parts[2]}")

            # KVM 로컬 IP → 릴레이 정보 매핑 생성
            relay_map = {}  # kvm_local_ip → {relay_ip, relay_port, udp_relay_port}
            for rkvm in remote_kvms:
                if not rkvm.get('is_online'):
                    continue
                local_ip = rkvm.get('kvm_local_ip', '')
                relay_ip = rkvm.get('relay_ip', '') or rkvm.get('relay_zt_ip', '')
                relay_port = rkvm.get('relay_port')
                udp_port = rkvm.get('udp_relay_port')
                if local_ip and relay_ip and relay_port:
                    relay_map[local_ip] = {
                        'relay_ip': relay_ip,
                        'relay_port': relay_port,
                        'udp_relay_port': udp_port,
                        'kvm_name': rkvm.get('kvm_name', ''),
                    }

            if not relay_map:
                return

            # 각 디바이스에 대해: 내 서브넷이 아니면 릴레이 IP로 치환
            substituted = 0
            for name, device in self.manager.devices.items():
                orig_ip = device.info.ip
                parts = orig_ip.split('.')
                if len(parts) != 4:
                    continue

                device_subnet = f"{parts[0]}.{parts[1]}.{parts[2]}"

                # 이미 Tailscale IP면 스킵
                if orig_ip.startswith('100.'):
                    continue

                # 내 로컬 서브넷이면 직접 접근 가능 → 스킵
                if device_subnet in local_subnets:
                    continue

                # 릴레이 정보가 있으면 치환
                if orig_ip in relay_map:
                    info = relay_map[orig_ip]
                    device.info.ip = info['relay_ip']
                    device.info.web_port = info['relay_port']
                    # UDP 릴레이 포트 정보 저장 (ICE 패치에서 사용)
                    device.info._udp_relay_port = info.get('udp_relay_port')
                    device.info._kvm_local_ip = orig_ip  # 원본 IP 보존
                    substituted += 1
                    print(f"[RelaySubst] {name}: {orig_ip}:80 → {info['relay_ip']}:{info['relay_port']}"
                          f" (UDP:{info.get('udp_relay_port')})")

            if substituted:
                print(f"[RelaySubst] {substituted}개 기기 릴레이 IP 치환 완료")

        except Exception as e:
            print(f"[RelaySubst] 릴레이 치환 실패 (무시): {e}")
            import traceback
            traceback.print_exc()

    def _create_statusbar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("준비됨")

        # 버전 정보 (상태바 우측 고정)
        from version import __version__, __app_name__
        from api_client import api_client
        user_info = ""
        if api_client.user:
            name = api_client.user.get('display_name') or api_client.user.get('username', '')
            role = "관리자" if api_client.is_admin else "사용자"
            user_info = f"  |  {name} ({role})"
        version_label = QLabel(f"{__app_name__} v{__version__}{user_info}")
        version_label.setStyleSheet("color: #888; padding-right: 10px; font-size: 12px; font-weight: bold;")
        self.status_bar.addPermanentWidget(version_label)

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
            group = device.info.group or 'default'
            if group not in groups:
                groups[group] = []
            groups[group].append(device)

        item_to_select = None

        # DB에 등록된 그룹 중 장치가 없는 빈 그룹도 표시
        try:
            db_groups = self.manager.get_groups()
            for g in db_groups:
                gn = g['name']
                if gn not in groups:
                    groups[gn] = []
        except Exception:
            pass

        for group_name, devices in sorted(groups.items(), key=lambda x: (x[0] != 'default', x[0])):
            group_item = QTreeWidgetItem([group_name, f"({len(devices)}개)"])
            # 그룹은 드래그 불가, 드롭 수신만 가능
            group_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDropEnabled
            )
            self.device_tree.addTopLevelItem(group_item)

            # 확장 상태 복원 (첫 로드시 또는 이전에 확장되어 있었던 경우)
            if not expanded_groups or group_name in expanded_groups:
                group_item.setExpanded(True)

            for device in devices:
                status_text = "온라인" if device.status == DeviceStatus.ONLINE else "오프라인"
                device_item = QTreeWidgetItem([device.name, status_text])
                device_item.setData(0, Qt.ItemDataRole.UserRole, device.name)
                # 장치는 드래그 가능, 드롭 수신 불가
                device_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
                )
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

        # 그룹 탭 갱신
        if hasattr(self, 'group_grid_tabs'):
            self.refresh_group_tabs()

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
        try:
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
            if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
                self.grid_view_tab.update_device_status()
        except Exception as e:
            print(f"[MainWindow] 상태 업데이트 처리 오류: {e}")

    def _on_grid_device_selected(self, device: KVMDevice):
        """그리드 뷰에서 장치 클릭 - 선택만"""
        self.current_device = device
        self._update_device_info()

    def _on_grid_device_double_clicked(self, device: KVMDevice):
        """그리드 뷰에서 장치 더블클릭 - 실시간 제어 창 열기"""
        self.current_device = device
        self._on_start_live_control()

    def _on_grid_device_right_clicked(self, device, pos):
        """그리드 뷰에서 장치 우클릭 - 컨텍스트 메뉴"""
        self.current_device = device
        self._update_device_info()

        menu = QMenu(self)
        menu.addAction("실시간 제어", self._on_start_live_control)
        menu.addAction("브라우저에서 열기", self._on_open_web_browser)
        menu.addAction("파일 전송", self._on_file_transfer)
        menu.addSeparator()

        # 그룹 이동 서브메뉴
        move_menu = menu.addMenu("그룹 이동")
        groups = self.manager.get_groups()
        all_group_names = set()
        for g in groups:
            all_group_names.add(g['name'])
        for d in self.manager.get_all_devices():
            gn = d.info.group or 'default'
            all_group_names.add(gn)
        current_group = device.info.group if device else ''
        for gn in sorted(all_group_names):
            action = move_menu.addAction(gn)
            if gn == current_group:
                action.setEnabled(False)
            else:
                action.triggered.connect(lambda checked, g=gn: self._on_move_device_to_group(g))

        menu.addAction("이름 변경", self._on_rename_device)
        menu.addAction("설정", self._on_device_settings)
        menu.addSeparator()
        # 우클릭한 장치 참조를 직접 전달 (self.current_device 경쟁 조건 방지)
        _ctx_device = device
        menu.addAction("삭제", lambda: self._on_delete_device(_ctx_device))
        menu.exec(pos)

    def _on_device_selected(self, item: QTreeWidgetItem, column: int):
        device_name = item.data(0, Qt.ItemDataRole.UserRole)
        if device_name:
            self.current_device = self.manager.get_device(device_name)
            self._update_device_info()

    def _on_device_double_clicked(self, item: QTreeWidgetItem, column: int):
        device_name = item.data(0, Qt.ItemDataRole.UserRole)
        if device_name:
            self.current_device = self.manager.get_device(device_name)
            self._on_start_live_control()

    def _update_device_info(self):
        """왼쪽 패널 장치 기본정보 업데이트"""
        if not self.current_device:
            return
        device = self.current_device
        self.info_labels["name"].setText(device.name)
        self.info_labels["ip"].setText(device.ip)
        self.info_labels["group"].setText(device.info.group or "default")
        status_text = "🟢 온라인" if device.status == DeviceStatus.ONLINE else "🔴 오프라인"
        self.info_labels["status"].setText(status_text)
        self.info_labels["web_port"].setText(str(device.info.web_port or 80))
        self.btn_start_live.setEnabled(True)
        self.btn_open_web.setEnabled(True)

    def _clear_device_info(self):
        """장치 삭제 후 왼쪽 패널 초기화"""
        try:
            for key in self.info_labels:
                self.info_labels[key].setText("-")
            self.btn_start_live.setEnabled(False)
            self.btn_open_web.setEnabled(False)
        except Exception as e:
            print(f"[MainWindow] 장치 정보 초기화 오류: {e}")

    def _on_device_context_menu(self, pos):
        item = self.device_tree.itemAt(pos)
        menu = QMenu()

        if not item:
            # 빈 영역 우클릭 → 그룹 추가만
            menu.addAction("그룹 추가", self._on_add_group)
            menu.exec(self.device_tree.mapToGlobal(pos))
            return

        device_name = item.data(0, Qt.ItemDataRole.UserRole)

        if not device_name:
            # 그룹 항목 우클릭
            group_name = item.text(0)
            menu.addAction("그룹 추가", self._on_add_group)
            if group_name != 'default':
                menu.addAction("그룹 이름 변경", lambda: self._on_rename_group(item))
                menu.addAction("그룹 삭제", lambda: self._on_delete_group(group_name))
        else:
            # 장치 항목 우클릭 — 우클릭한 장치를 current_device로 설정
            self.current_device = self.manager.get_device(device_name)
            self._update_device_info()

            menu.addAction("실시간 제어", self._on_start_live_control)
            menu.addAction("브라우저에서 열기", self._on_open_web_browser)
            menu.addAction("파일 전송", self._on_file_transfer)
            menu.addSeparator()

            # 그룹 이동 서브메뉴
            move_menu = menu.addMenu("그룹 이동")
            groups = self.manager.get_groups()
            # DB 그룹 + 현재 사용중인 그룹 합치기
            all_group_names = set()
            for g in groups:
                all_group_names.add(g['name'])
            for d in self.manager.get_all_devices():
                gn = d.info.group or 'default'
                all_group_names.add(gn)
            current_group = self.current_device.info.group if self.current_device else ''
            for gn in sorted(all_group_names):
                action = move_menu.addAction(gn)
                if gn == current_group:
                    action.setEnabled(False)  # 현재 그룹은 비활성
                else:
                    action.triggered.connect(lambda checked, g=gn: self._on_move_device_to_group(g))

            menu.addAction("이름 변경", self._on_rename_device)
            menu.addAction("설정", self._on_device_settings)
            menu.addSeparator()
            # 우클릭한 장치 참조를 직접 전달 (self.current_device 경쟁 조건 방지)
            _ctx_device = self.current_device
            menu.addAction("삭제", lambda: self._on_delete_device(_ctx_device))

        menu.exec(self.device_tree.mapToGlobal(pos))

    # ===== 그룹 관리 =====

    def _on_add_group(self):
        """그룹 추가"""
        name, ok = QInputDialog.getText(self, "그룹 추가", "새 그룹 이름:")
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            self.manager.add_group(name)
            self._load_device_list()
            self.status_bar.showMessage(f"그룹 '{name}' 추가됨")
        except Exception as e:
            QMessageBox.warning(self, "오류", f"그룹 추가 실패: {e}")

    def _on_rename_group(self, item):
        """그룹 이름 변경"""
        old_name = item.text(0)
        new_name, ok = QInputDialog.getText(
            self, "그룹 이름 변경",
            f"'{old_name}' 의 새 이름:",
            QLineEdit.EchoMode.Normal,
            old_name
        )
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        new_name = new_name.strip()

        try:
            # 1) 새 그룹 추가
            try:
                self.manager.add_group(new_name)
            except Exception:
                pass

            # 2) 해당 그룹의 모든 장치 → 새 그룹으로 이동
            for device in self.manager.get_all_devices():
                if device.info.group == old_name:
                    self.manager.move_device_to_group(device.name, new_name)

            # 3) 이전 그룹 삭제 (장치는 이미 이동했으므로 안전)
            self.manager.db.delete_group(old_name)

            self._load_device_list()
            if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
                self.grid_view_tab.load_devices()
            self.status_bar.showMessage(f"그룹 이름 변경: {old_name} → {new_name}")
        except Exception as e:
            QMessageBox.warning(self, "오류", f"그룹 이름 변경 실패: {e}")

    def _on_delete_group(self, group_name: str):
        """그룹 삭제 (장치가 있으면 차단)"""
        device_count = len(self.manager.get_devices_by_group(group_name))

        if device_count > 0:
            QMessageBox.warning(
                self, "그룹 삭제 불가",
                f"'{group_name}' 그룹에 {device_count}개 장치가 있습니다.\n"
                f"장치를 다른 그룹으로 이동한 후 삭제해주세요.\n\n"
                f"(장치 우클릭 → '그룹 이동' 또는 드래그 앤 드롭)"
            )
            return

        reply = QMessageBox.question(
            self, "그룹 삭제",
            f"'{group_name}' 그룹을 삭제하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self.manager.delete_group(group_name)
            self._load_device_list()
            if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
                self.grid_view_tab.load_devices()
            self.status_bar.showMessage(f"그룹 '{group_name}' 삭제됨")
        except Exception as e:
            QMessageBox.warning(self, "오류", f"그룹 삭제 실패: {e}")

    def _on_move_device_to_group(self, group_name: str):
        """장치를 다른 그룹으로 이동 (우클릭 메뉴)"""
        if not self.current_device:
            return
        self.manager.move_device_to_group(self.current_device.name, group_name)
        self._load_device_list()
        if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
            self.grid_view_tab.load_devices()
        self.status_bar.showMessage(
            f"'{self.current_device.name}' → '{group_name}' 그룹으로 이동"
        )

    def _on_tree_drop_event(self, event):
        """드래그 앤 드롭으로 장치 그룹 이동"""
        # 드래그 중인 아이템 정보 저장
        dragged_item = self.device_tree.currentItem()
        if not dragged_item:
            event.ignore()
            return

        device_name = dragged_item.data(0, Qt.ItemDataRole.UserRole)
        if not device_name:
            # 그룹 아이템은 드래그 금지
            event.ignore()
            return

        # 드롭 대상 아이템
        target_item = self.device_tree.itemAt(event.position().toPoint())
        if not target_item:
            event.ignore()
            return

        # 대상이 그룹인지 확인 (UserRole 데이터가 없으면 그룹)
        target_device = target_item.data(0, Qt.ItemDataRole.UserRole)
        if target_device:
            # 장치 위에 드롭 → 그 장치의 부모(그룹)으로 이동
            parent = target_item.parent()
            if parent:
                target_group = parent.text(0)
            else:
                event.ignore()
                return
        else:
            # 그룹 위에 드롭
            target_group = target_item.text(0)

        # 현재 그룹과 같으면 무시
        device = self.manager.get_device(device_name)
        if not device or device.info.group == target_group:
            event.ignore()
            return

        # DB + 메모리 업데이트
        self.manager.move_device_to_group(device_name, target_group)

        # 기본 dropEvent 호출하지 않고 직접 리로드 (트리 구조 일관성 유지)
        event.ignore()
        self._load_device_list()
        if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
            self.grid_view_tab.load_devices()
        self.status_bar.showMessage(f"'{device_name}' → '{target_group}' 그룹으로 이동")

    # ===== 장치 관리 =====

    def _on_rename_device(self):
        """장치 이름 변경"""
        if not self.current_device:
            return

        old_name = self.current_device.name
        new_name, ok = QInputDialog.getText(
            self, "이름 변경",
            f"'{old_name}' 의 새 이름을 입력하세요:",
            QLineEdit.EchoMode.Normal,
            old_name
        )

        if not ok or not new_name.strip():
            return

        new_name = new_name.strip()
        if new_name == old_name:
            return

        # 이름 변경 실행
        if self.manager.rename_device(old_name, new_name):
            # 장치 목록 새로고침
            self._load_device_list()
            # 그리드 뷰 새로고침 (이름 라벨 업데이트)
            if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
                self.grid_view_tab.load_devices()
            self.status_bar.showMessage(f"이름 변경: {old_name} → {new_name}")
        else:
            QMessageBox.warning(self, "이름 변경 실패",
                                f"'{new_name}' 이름이 이미 존재하거나 변경에 실패했습니다.")

    def _on_start_live_control(self):
        if not self.current_device:
            QMessageBox.warning(self, "경고", "장치를 먼저 선택해주세요.")
            return

        # 1:1 제어 시작 전: 모든 썸네일 미리보기 중지 (GPU 부하 경감)
        # 16개 WebEngine 썸네일 + 1개 LiveView = GPU 과부하 → 크래시 원인
        self._live_control_device = self.current_device.name
        self._stop_all_previews_for_liveview()

        # 다이얼로그 생성 (URL은 __init__에서 로드)
        try:
            dialog = LiveViewDialog(self.current_device, self)
            dialog.exec()
        except Exception as e:
            print(f"[LiveView] 크래시: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "LiveView 오류", f"LiveView 실행 중 오류:\n{e}")
            return
        finally:
            # WebView deleteLater() 완료 대기 (이전 WebEngine 정리)
            # 두 번째 LiveView 열 때 이전 WebEngine과 충돌 방지
            QApplication.processEvents()

        # 1:1 제어 종료 — 플래그 해제 + 메인 윈도우 활성화
        self._live_control_device = None
        self.activateWindow()
        self.raise_()

        # 부분제어로 닫힌 경우 → 미리보기 재시작 하지 않음 (탭 전환에서 처리)
        if getattr(dialog, '_partial_control_closing', False):
            return

        # 1:1 제어 종료 후: 모든 썸네일 미리보기 재시작
        self._resume_all_previews_after_liveview()

    def _apply_partial_crop(self, group: str, region: tuple):
        """부분제어 — 해당 그룹 탭으로 전환하고 크롭 적용

        1) 모든 탭의 WebView를 완전 중지 (KVM 단일 스트림 해제)
        2) 크롭 영역 저장
        3) 대상 탭으로 전환 → 새로 start_capture → _on_load_finished → 크롭 자동 적용
        """
        print(f"[부분제어] _apply_partial_crop 시작: group={group}, region={region}")

        # 해당 그룹 탭 찾기
        target_tab = self.group_grid_tabs.get(group)
        if not target_tab:
            target_tab = self.grid_view_tab
            print(f"[부분제어] 그룹 '{group}' 탭 없음 → 전체 목록 탭 사용")
        else:
            print(f"[부분제어] 그룹 '{group}' 탭 찾음")

        # 1. 모든 탭의 WebView 완전 중지 (WebRTC 해제)
        all_tabs = [self.grid_view_tab] + list(self.group_grid_tabs.values())
        stopped = 0
        for tab in all_tabs:
            if tab._is_visible:
                tab.on_tab_deactivated()
                stopped += 1
        print(f"[부분제어] {stopped}개 탭 중지 완료")

        # 2. 크롭 영역 저장 (새 썸네일 생성 시 자동 적용)
        target_tab._crop_region = region
        target_tab._update_title_for_crop(region)
        print(f"[부분제어] 크롭 영역 저장: {region}")

        # 3. 대상 탭으로 전환
        idx = self.tab_widget.indexOf(target_tab)
        if idx >= 0:
            current_idx = self.tab_widget.currentIndex()
            print(f"[부분제어] 탭 전환: current={current_idx} → target={idx}")
            if current_idx == idx:
                # 이미 같은 탭 — currentChanged가 발생하지 않으므로 수동 활성화
                print("[부분제어] 같은 탭 — 수동 on_tab_activated (300ms)")
                QTimer.singleShot(300, target_tab.on_tab_activated)
            else:
                # 다른 탭 — setCurrentIndex → _on_tab_changed에서 처리
                print("[부분제어] 다른 탭 — setCurrentIndex")
                self.tab_widget.setCurrentIndex(idx)
        else:
            print(f"[부분제어] 경고: target_tab의 인덱스를 찾을 수 없음")

    # JavaScript: WebRTC 미디어 트랙 일시정지 (GPU 부하 감소)
    _PAUSE_WEBRTC_JS = """
    (function() {
        // 모든 video 요소의 srcObject 트랙 중지
        document.querySelectorAll('video').forEach(function(v) {
            if (v.srcObject) {
                v.srcObject.getTracks().forEach(function(t) { t.enabled = false; });
                v.pause();
            }
        });
        return true;
    })();
    """

    # JavaScript: WebRTC 미디어 트랙 재개
    _RESUME_WEBRTC_JS = """
    (function() {
        document.querySelectorAll('video').forEach(function(v) {
            if (v.srcObject) {
                v.srcObject.getTracks().forEach(function(t) { t.enabled = true; });
                v.play().catch(function(){});
            }
        });
        return true;
    })();
    """

    def _stop_all_previews_for_liveview(self):
        """1:1 제어 시작 전 모든 썸네일 미리보기 일시정지

        about:blank 전환 대신 pause_capture(hide) + WebRTC 트랙 비활성화.
        about:blank로 전환하면 Chromium 렌더 프로세스 정리 중
        새 WebEngine 생성 시 access violation 발생하므로 안전한 방법 사용.
        """
        all_tabs = []
        if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
            all_tabs.append(self.grid_view_tab)
        if hasattr(self, 'group_grid_tabs'):
            all_tabs.extend(self.group_grid_tabs.values())

        paused = 0
        for tab in all_tabs:
            for thumb in tab.thumbnails:
                try:
                    thumb.pause_capture()
                    # WebRTC 비디오 트랙 비활성화 (GPU 디코딩 중지)
                    if thumb._webview:
                        thumb._webview.page().runJavaScript(self._PAUSE_WEBRTC_JS)
                    paused += 1
                except Exception:
                    pass
        print(f"[MainWindow] 1:1 제어 시작 — 모든 썸네일 일시정지 ({paused}개, WebRTC 트랙 비활성화)")

    def _resume_all_previews_after_liveview(self):
        """1:1 제어 종료 후 활성 탭의 썸네일 미리보기 재시작"""
        all_tabs = []
        if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
            all_tabs.append(self.grid_view_tab)
        if hasattr(self, 'group_grid_tabs'):
            all_tabs.extend(self.group_grid_tabs.values())

        resumed = 0
        for tab in all_tabs:
            if tab._is_visible and tab._live_preview_enabled:
                for thumb in tab.thumbnails:
                    try:
                        # WebRTC 트랙 재활성화 + WebView 표시
                        if thumb._webview and thumb._is_paused:
                            thumb._webview.page().runJavaScript(self._RESUME_WEBRTC_JS)
                        thumb.resume_capture()
                        resumed += 1
                    except Exception:
                        pass
        print(f"[MainWindow] 1:1 제어 종료 — 썸네일 재개 ({resumed}개)")

    def _stop_device_preview(self, device: KVMDevice):
        """특정 장치의 미리보기 중지 (전체 탭 + 그룹 탭 모두 처리)"""
        all_tabs = []
        if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
            all_tabs.append(self.grid_view_tab)
        if hasattr(self, 'group_grid_tabs'):
            all_tabs.extend(self.group_grid_tabs.values())

        for tab in all_tabs:
            for thumb in tab.thumbnails:
                if thumb.device.name == device.name:
                    thumb.stop_capture()
                    break

    def _restart_device_preview(self, device: KVMDevice):
        """특정 장치의 미리보기 재시작 (전체 탭 + 그룹 탭 모두 처리)"""
        # 모든 탭에서 해당 장치의 썸네일을 찾아 재시작
        all_tabs = []
        if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
            all_tabs.append(self.grid_view_tab)
        if hasattr(self, 'group_grid_tabs'):
            all_tabs.extend(self.group_grid_tabs.values())

        restarted = False
        for tab in all_tabs:
            if tab._is_visible and tab._live_preview_enabled:
                for thumb in tab.thumbnails:
                    if thumb.device.name == device.name:
                        # 약간의 지연 후 재시작 (WebRTC 연결 정리 대기)
                        QTimer.singleShot(500, thumb.start_capture)
                        restarted = True
                        break
        if restarted:
            print(f"[MainWindow] 미리보기 재시작 예약: {device.name}")
        else:
            print(f"[MainWindow] 미리보기 재시작 건너뜀 (활성 탭 없음): {device.name}")

    def _on_open_web_browser(self):
        if not self.current_device:
            return
        web_port = getattr(self.current_device.info, 'web_port', 80)
        QDesktopServices.openUrl(QUrl(f"http://{self.current_device.ip}:{web_port}"))

    def _on_file_transfer(self):
        """파일 전송: SFTP(KVM) 또는 클라우드 업로드 선택"""
        if not self.current_device:
            return

        from api_client import api_client

        methods = ["KVM 직접 전송 (SFTP)"]
        if api_client.is_logged_in:
            try:
                quota_info = api_client.get_quota()
                if quota_info.get('quota') != 0:
                    methods.append("클라우드 업로드")
            except Exception:
                methods.append("클라우드 업로드")

        if len(methods) == 1:
            method = methods[0]
        else:
            method, ok = QInputDialog.getItem(
                self, "파일 전송", "전송 방식 선택:", methods, 0, False
            )
            if not ok:
                return

        from PyQt6.QtWidgets import QFileDialog, QProgressDialog
        path, _ = QFileDialog.getOpenFileName(self, "전송할 파일 선택", "", "All Files (*)")
        if not path:
            return

        import os
        filename = os.path.basename(path)

        if method == "클라우드 업로드":
            # 쿼타 사전 체크
            try:
                qi = api_client.get_quota()
                q = qi.get('quota')
                file_size = os.path.getsize(path)
                if q == 0:
                    QMessageBox.warning(self, "클라우드 업로드", "클라우드 저장소 접근 권한이 없습니다.")
                    return
                if q is not None:
                    remaining = qi.get('remaining', 0)
                    if file_size > remaining:
                        QMessageBox.warning(
                            self, "클라우드 업로드",
                            f"저장 용량이 부족합니다.\n"
                            f"파일 크기: {file_size // (1024*1024)}MB\n"
                            f"남은 용량: {remaining // (1024*1024)}MB"
                        )
                        return
            except Exception:
                pass  # 서버에서 최종 체크

            # 클라우드 업로드
            self._upload_progress = QProgressDialog(f"{filename}\n클라우드 업로드 중...", None, 0, 0, self)
            self._upload_progress.setWindowTitle("클라우드 업로드")
            self._upload_progress.setMinimumWidth(400)
            self._upload_progress.setModal(True)
            self._upload_progress.setAutoClose(False)
            self._upload_progress.setAutoReset(False)
            self._upload_progress.show()

            self._cloud_upload_thread = CloudUploadThread(path)
            self._cloud_upload_thread.finished_ok.connect(self._on_cloud_upload_done)
            self._cloud_upload_thread.finished_err.connect(self._on_cloud_upload_error)
            self._cloud_upload_thread.start()
        else:
            # 기존 SFTP 전송
            remote_path = f"/tmp/{filename}"
            self._upload_progress = QProgressDialog(f"{filename}\nSSH 연결 중...", None, 0, 100, self)
            self._upload_progress.setWindowTitle(f"파일 전송 - {self.current_device.name}")
            self._upload_progress.setMinimumWidth(400)
            self._upload_progress.setModal(True)
            self._upload_progress.setAutoClose(False)
            self._upload_progress.setAutoReset(False)
            self._upload_progress.setValue(0)
            self._upload_progress.show()

            self._upload_thread = SFTPUploadThread(self.current_device, path, remote_path)
            self._upload_thread.progress.connect(self._on_upload_progress)
            self._upload_thread.finished_ok.connect(self._on_upload_done)
            self._upload_thread.finished_err.connect(self._on_upload_error)
            self._upload_thread.start()

    def _on_upload_progress(self, pct, txt):
        try:
            if self._upload_progress and self._upload_progress.isVisible():
                self._upload_progress.setValue(pct)
                self._upload_progress.setLabelText(txt)
        except Exception:
            pass

    def _on_upload_done(self, msg):
        try:
            if self._upload_progress:
                self._upload_progress.close()
                self._upload_progress = None
        except Exception:
            pass
        QMessageBox.information(self, "전송 완료", msg)

    def _on_upload_error(self, msg):
        try:
            if self._upload_progress:
                self._upload_progress.close()
                self._upload_progress = None
        except Exception:
            pass
        QMessageBox.warning(self, "전송 실패", msg)

    def _on_cloud_upload_done(self, msg):
        try:
            if self._upload_progress:
                self._upload_progress.close()
                self._upload_progress = None
        except Exception:
            pass
        QMessageBox.information(self, "클라우드 업로드", msg)

    def _on_cloud_upload_error(self, msg):
        try:
            if self._upload_progress:
                self._upload_progress.close()
                self._upload_progress = None
        except Exception:
            pass
        QMessageBox.warning(self, "클라우드 업로드 실패", f"업로드 실패:\n{msg}")

    def _on_add_device(self):
        dialog = AddDeviceDialog(self)
        if dialog.exec():
            data = dialog.get_data()
            try:
                self.manager.add_device(**data)
                self._load_device_list()
                if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
                    self.grid_view_tab.load_devices()
                self.status_bar.showMessage(f"장치 '{data['name']}' 추가됨")
            except Exception as e:
                QMessageBox.critical(self, "오류", f"장치 추가 실패: {e}")

    def _on_auto_discover(self):
        """자동 검색 다이얼로그 열기"""
        # 기존 장치 IP 및 이름 목록
        existing_ips = [d.ip for d in self.manager.get_all_devices()]
        existing_names = set(d.name for d in self.manager.get_all_devices())

        dialog = AutoDiscoveryDialog(existing_ips, self)
        if dialog.exec():
            selected = dialog.get_selected_devices()
            if not selected:
                return

            added_count = 0
            skipped_count = 0

            for device in selected:
                # 이미 존재하는지 확인 (IP 또는 이름)
                if device.ip in existing_ips:
                    skipped_count += 1
                    continue

                # 이름 중복 시 자동으로 번호 부여
                name = device.name
                if name in existing_names:
                    suffix = 2
                    while f"{name}_{suffix}" in existing_names:
                        suffix += 1
                    name = f"{name}_{suffix}"

                try:
                    self.manager.add_device(
                        name=name,
                        ip=device.ip,
                        port=22,  # SSH 기본 포트
                        web_port=device.port,
                        username="root",
                        password="luckfox",
                        group="auto_discovery"
                    )
                    added_count += 1
                    existing_ips.append(device.ip)
                    existing_names.add(name)
                except Exception as e:
                    print(f"장치 추가 실패 ({device.ip}): {e}")

            # UI 새로고침
            self._load_device_list()
            if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
                self.grid_view_tab.load_devices()

            # 결과 메시지
            msg = f"{added_count}개 장치 추가됨"
            if skipped_count > 0:
                msg += f" (중복 {skipped_count}개 제외)"
            self.status_bar.showMessage(msg)

            if added_count > 0:
                QMessageBox.information(self, "자동 검색 완료", msg)

    def _on_delete_device(self, target_device=None):
        """장치 삭제 — target_device를 직접 전달받거나, 없으면 current_device 사용"""
        device = target_device or self.current_device
        if not device:
            return
        try:
            if QMessageBox.question(self, "삭제 확인",
                                     f"'{device.name}' ({device.ip}) 삭제?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                device_name = device.name

                # 서버에서도 삭제 (admin인 경우)
                try:
                    from api_client import api_client
                    if api_client.is_logged_in and api_client.is_admin:
                        devices = api_client.admin_get_all_devices()
                        for d in devices:
                            if d.get('name') == device_name:
                                api_client.admin_delete_device(d['id'])
                                print(f"[Delete] 서버에서 삭제: {device_name}")
                                break
                except Exception as e:
                    print(f"[Delete] 서버 삭제 실패 (로컬만 삭제): {e}")

                self.manager.remove_device(device_name)
                self.current_device = None
                self._load_device_list()
                if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
                    try:
                        self.grid_view_tab.load_devices()
                    except Exception as e:
                        print(f"[Delete] grid_view 갱신 실패: {e}")
                self._clear_device_info()
                self.status_bar.showMessage(f"'{device_name}' 삭제됨")
        except Exception as e:
            print(f"[MainWindow] 장치 삭제 오류: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, "오류", f"장치 삭제 중 오류 발생:\n{e}")

    def _on_device_settings(self):
        if not self.current_device:
            return
        try:
            DeviceSettingsDialog(self.current_device, self).exec()
        except Exception as e:
            print(f"[MainWindow] 장치 설정 오류: {e}")
            QMessageBox.warning(self, "오류", f"장치 설정 열기 오류: {e}")

    def _on_connect_device(self):
        if not self.current_device:
            return
        try:
            self.status_bar.showMessage(f"{self.current_device.name} SSH 연결 중...")
            if self.current_device.connect():
                self.status_bar.showMessage(f"{self.current_device.name} SSH 연결됨")
            else:
                self.status_bar.showMessage(f"{self.current_device.name} SSH 연결 실패")
            self._load_device_list()
            self._update_device_info()
        except Exception as e:
            print(f"[MainWindow] SSH 연결 오류: {e}")
            self.status_bar.showMessage(f"SSH 연결 오류: {e}")

    def _on_disconnect_device(self):
        if not self.current_device:
            return
        try:
            device_name = self.current_device.name
            self.current_device.disconnect()
            self._load_device_list()
            self._update_device_info()
            self.status_bar.showMessage(f"{device_name} SSH 해제됨")
        except Exception as e:
            print(f"[MainWindow] SSH 해제 오류: {e}")
            self.status_bar.showMessage(f"SSH 해제 오류: {e}")

    def _on_reboot_device(self):
        if not self.current_device:
            return
        if QMessageBox.question(self, "재부팅 확인", f"'{self.current_device.name}' 재부팅?",
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            try:
                if not self.current_device.is_connected():
                    self.current_device.connect()
                self.current_device.reboot()
                self.status_bar.showMessage(f"{self.current_device.name} 재부팅 중...")
            except Exception as e:
                print(f"[MainWindow] 재부팅 오류: {e}")
                self.status_bar.showMessage(f"재부팅 오류: {e}")

    def _on_reconnect_usb(self):
        if not self.current_device:
            return
        try:
            if not self.current_device.is_connected():
                self.current_device.connect()
            self.current_device.reconnect_usb()
            self.status_bar.showMessage(f"{self.current_device.name} USB 재연결됨")
        except Exception as e:
            print(f"[MainWindow] USB 재연결 오류: {e}")
            self.status_bar.showMessage(f"USB 재연결 오류: {e}")

    def _on_refresh_usb_log(self):
        if not self.current_device or not hasattr(self, 'usb_log_text'):
            return
        try:
            if not self.current_device.is_connected():
                self.current_device.connect()
            self.usb_log_text.setText(self.current_device.get_dmesg_usb(50))
        except Exception as e:
            print(f"[MainWindow] USB 로그 조회 오류: {e}")

    def _on_connect_all(self):
        try:
            self.status_bar.showMessage("전체 SSH 연결 중...")
            results = self.manager.connect_all()
            success = sum(1 for v in results.values() if v)
            self.status_bar.showMessage(f"{success}/{len(results)}개 SSH 연결됨")
            self._load_device_list()
        except Exception as e:
            print(f"[MainWindow] 전체 연결 오류: {e}")
            self.status_bar.showMessage(f"전체 연결 오류: {e}")

    def _on_disconnect_all(self):
        try:
            self.manager.disconnect_all()
            self._load_device_list()
            self.status_bar.showMessage("전체 SSH 해제됨")
        except Exception as e:
            print(f"[MainWindow] 전체 해제 오류: {e}")
            self.status_bar.showMessage(f"전체 해제 오류: {e}")

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

    def _on_open_admin_panel(self):
        """관리자 패널 다이얼로그 열기"""
        dialog = QDialog(self)
        dialog.setWindowTitle("관리자 패널")
        dialog.setMinimumSize(900, 600)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        admin_panel = AdminPanel()
        # 기기 변경 시 메인 윈도우 UI 갱신
        admin_panel.device_changed.connect(self._on_admin_device_changed)
        layout.addWidget(admin_panel)
        dialog.exec()

    def _on_admin_device_changed(self):
        """관리자 패널에서 기기 변경 시 메인 UI 갱신"""
        # 서버에서 최신 기기 목록 다시 로드
        self._load_devices_from_source()
        self._load_device_list()
        if hasattr(self, 'grid_view_tab') and self.grid_view_tab:
            self.grid_view_tab.load_devices()

    def _on_app_settings(self):
        """환경 설정 다이얼로그 열기"""
        dialog = AppSettingsDialog(self)
        dialog.exec()

    def _show_about(self):
        from version import __version__
        QMessageBox.about(self, "WellcomLAND 정보",
                          f"<h2>WellcomLAND</h2><p>버전 {__version__}</p>"
                          "<p>다중 KVM 장치 관리 솔루션</p>"
                          "<hr><p><b>기본 단축키:</b></p>"
                          "<p>• <b>더블클릭</b> — 1:1 실시간 제어</p>"
                          "<p>• <b>우클릭</b> — 장치 컨텍스트 메뉴</p>"
                          "<p>• <b>Ctrl+Space</b> — 한/영 전환</p>"
                          "<p>• <b>Alt+3</b> — 상단 바 토글</p>"
                          "<p>• <b>F11</b> — 전체 화면</p>"
                          "<hr><p><small>WellcomLAND by Wellcom LLC</small></p>")

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
