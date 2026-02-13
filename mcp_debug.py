"""
WellcomLAND 원격 디버그 서버 (내장 HTTP)
- 외부 패키지 의존 없음 (순수 Python stdlib만 사용)
- Tailscale IP로 원격 접근 가능 (0.0.0.0 바인딩)
- 실시간 로그/상태/스레드/GPU 정보 조회

사용법:
  1. WellcomLAND 실행 (자동으로 디버그 서버 시작)
  2. 브라우저/curl: http://<Tailscale_IP>:5111/
  3. curl http://<IP>:5111/api/logs?n=200
  4. curl http://<IP>:5111/api/devices
  5. curl http://<IP>:5111/api/status
"""

import sys
import os
import json
import time
import threading
import collections
import traceback
import socket
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ─── 전역 상태 ───────────────────────────────────────
_main_window = None
_log_buffer = collections.deque(maxlen=2000)  # 최근 2000줄 로그
_server_thread = None
_start_time = time.time()


# ─── stdout/stderr 캡처 ──────────────────────────────
class _LogTee:
    """stdout을 원래 출력 + 링 버퍼에 동시 기록"""
    def __init__(self, original):
        self.original = original

    def write(self, text):
        try:
            self.original.write(text)
        except Exception:
            pass
        if text and text.strip():
            ts = time.strftime('%H:%M:%S')
            _log_buffer.append(f"[{ts}] {text.rstrip()}")

    def flush(self):
        try:
            self.original.flush()
        except Exception:
            pass

    def isatty(self):
        return False

    def __getattr__(self, name):
        return getattr(self.original, name)


def install_log_capture():
    """stdout/stderr 캡처 시작"""
    if not isinstance(sys.stdout, _LogTee):
        sys.stdout = _LogTee(sys.stdout)
    if not isinstance(sys.stderr, _LogTee):
        sys.stderr = _LogTee(sys.stderr)


# ─── Qt 메인 스레드 안전 실행 ─────────────────────────
def _run_on_main_thread(func, timeout=5.0):
    """Qt 메인 스레드에서 함수 실행 (thread-safe)"""
    if _main_window is None:
        return None

    result_holder = [None]
    error_holder = [None]
    event = threading.Event()

    def wrapper():
        try:
            result_holder[0] = func()
        except Exception as e:
            error_holder[0] = str(e)
        finally:
            event.set()

    try:
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, wrapper)
        waited = event.wait(timeout=timeout)

        if not waited:
            # 타임아웃 시 직접 실행 (read-only 함수면 안전)
            try:
                result_holder[0] = func()
            except Exception as e2:
                return {"error": f"timeout + fallback failed: {e2}"}

        if error_holder[0]:
            return {"error": error_holder[0]}
        return result_holder[0]
    except Exception as e:
        return {"error": str(e)}


# ─── 데이터 수집 함수들 ──────────────────────────────

def _get_devices():
    """장치 목록"""
    def _get():
        if not _main_window or not hasattr(_main_window, 'manager'):
            return []
        devices = _main_window.manager.get_all_devices()
        return [{
            "name": d.name,
            "ip": d.ip,
            "web_port": getattr(d.info, 'web_port', 80),
            "port": getattr(d.info, 'port', 22),
            "status": d.status.name if hasattr(d.status, 'name') else str(d.status),
            "group": getattr(d.info, 'group', 'default') or 'default',
            "is_relay": d.ip.startswith('100.'),
            "kvm_local_ip": getattr(d.info, '_kvm_local_ip', None),
        } for d in devices]
    return _run_on_main_thread(_get) or []


def _get_app_status():
    """앱 전체 상태"""
    from version import __version__
    uptime = time.time() - _start_time

    def _get():
        info = {
            "version": __version__,
            "uptime_seconds": int(uptime),
            "uptime_human": f"{int(uptime//3600)}h {int((uptime%3600)//60)}m {int(uptime%60)}s",
            "frozen": getattr(sys, 'frozen', False),
            "python": sys.version.split()[0],
            "pid": os.getpid(),
            "log_buffer_size": len(_log_buffer),
        }

        # 메모리 사용량
        try:
            import resource
            info["memory_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        except ImportError:
            try:
                # Windows
                import ctypes
                from ctypes import wintypes
                class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
                pmc = PROCESS_MEMORY_COUNTERS()
                pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                handle = ctypes.windll.kernel32.GetCurrentProcess()
                if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
                    info["memory_mb"] = round(pmc.WorkingSetSize / (1024 * 1024), 1)
                    info["peak_memory_mb"] = round(pmc.PeakWorkingSetSize / (1024 * 1024), 1)
            except Exception:
                pass

        if _main_window:
            # LiveView 상태
            info["live_control_device"] = getattr(_main_window, '_live_control_device', None)
            info["live_dialog_open"] = getattr(_main_window, '_live_dialog', None) is not None

            # StatusThread 상태
            st = getattr(_main_window, 'status_thread', None)
            if st:
                info["status_thread"] = {
                    "running": getattr(st, 'running', None),
                    "paused": getattr(st, '_paused', None),
                    "alive": st.isRunning() if hasattr(st, 'isRunning') else None,
                }

            # 장치 수
            if hasattr(_main_window, 'manager'):
                info["device_count"] = len(_main_window.manager.get_all_devices())

        return info

    return _run_on_main_thread(_get) or {"version": __version__, "error": "MainWindow not ready"}


def _get_threads():
    """활성 스레드 목록"""
    threads = []
    for t in threading.enumerate():
        threads.append({
            "name": t.name,
            "daemon": t.daemon,
            "alive": t.is_alive(),
            "ident": t.ident,
        })
    return threads


def _get_thumbnails():
    """썸네일 상태"""
    def _get():
        if not _main_window:
            return {}

        result = {}
        tabs = []
        if hasattr(_main_window, 'grid_view_tab') and _main_window.grid_view_tab:
            tabs.append(("전체 목록", _main_window.grid_view_tab))
        if hasattr(_main_window, 'group_grid_tabs'):
            for gname, gtab in _main_window.group_grid_tabs.items():
                tabs.append((gname, gtab))

        for tab_name, tab in tabs:
            tab_info = {
                "is_visible": getattr(tab, '_is_visible', None),
                "load_in_progress": getattr(tab, '_load_in_progress', None),
                "crop_region": getattr(tab, '_crop_region', None),
                "preview_enabled": getattr(tab, '_live_preview_enabled', None),
                "thumbnails": [],
            }
            for thumb in getattr(tab, 'thumbnails', []):
                t = {
                    "device": thumb.device.name,
                    "ip": thumb.device.ip,
                    "active": getattr(thumb, '_is_active', None),
                    "stream": getattr(thumb, '_stream_status', None),
                    "has_webview": getattr(thumb, '_webview', None) is not None,
                    "crop": getattr(thumb, '_crop_region', None),
                }
                if thumb._webview:
                    try:
                        t["url"] = thumb._webview.url().toString()
                    except Exception:
                        pass
                tab_info["thumbnails"].append(t)
            result[tab_name] = tab_info
        return result

    return _run_on_main_thread(_get) or {}


def _get_gpu_info():
    """GPU 관련 설정/상태"""
    info = {
        "frozen": getattr(sys, 'frozen', False),
        "chromium_flags": os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', ''),
    }
    # GPU 크래시 플래그
    try:
        from config import DATA_DIR
        flag_path = os.path.join(DATA_DIR, ".gpu_crash")
        info["gpu_crash_flag"] = os.path.exists(flag_path)
        if os.path.exists(flag_path):
            with open(flag_path, 'r') as f:
                info["gpu_crash_content"] = f.read().strip()
    except Exception:
        pass
    # 소프트웨어 렌더링 설정
    info["software_opengl"] = bool(os.environ.get('QT_OPENGL', ''))
    info["angle_platform"] = os.environ.get('QT_OPENGL_ANGLE_PLATFORM', '')
    return info


def _get_relay_info():
    """릴레이 상태"""
    def _get():
        result = {"relays": []}
        try:
            from main import _kvm_relay
            if _kvm_relay:
                for name, proxy in getattr(_kvm_relay, '_tcp_proxies', {}).items():
                    result["relays"].append({
                        "name": name,
                        "type": "TCP",
                        "listen_port": getattr(proxy, '_listen_port', None),
                        "target": f"{getattr(proxy, '_target_host', '?')}:{getattr(proxy, '_target_port', '?')}",
                        "running": getattr(proxy, '_running', None),
                    })
                for name, relay in getattr(_kvm_relay, '_udp_relays', {}).items():
                    result["relays"].append({
                        "name": name,
                        "type": "UDP",
                        "listen_port": getattr(relay, '_listen_port', None),
                        "running": getattr(relay, '_running', None),
                    })
                result["heartbeat_running"] = getattr(_kvm_relay, '_running', None)
        except Exception as e:
            result["error"] = str(e)
        return result

    return _run_on_main_thread(_get) or {"relays": []}


def _get_network_info():
    """네트워크 정보"""
    info = {"interfaces": []}
    try:
        hostname = socket.gethostname()
        info["hostname"] = hostname
        for ai in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = ai[4][0]
            info["interfaces"].append({
                "ip": ip,
                "is_tailscale": ip.startswith('100.'),
                "is_lan": ip.startswith('192.168.') or ip.startswith('10.'),
            })
    except Exception as e:
        info["error"] = str(e)
    return info


def _read_log_file(filename='app.log', last_n=200):
    """로그 파일 직접 읽기"""
    try:
        base_dir = os.environ.get('WELLCOMLAND_BASE_DIR', '.')
        log_path = os.path.join(base_dir, 'logs', filename)
        if not os.path.exists(log_path):
            return f"(파일 없음: {log_path})"
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        return ''.join(lines[-last_n:])
    except Exception as e:
        return f"Error: {e}"


def _read_fault_log():
    """fault.log 읽기 (크래시 로그)"""
    return _read_log_file('fault.log', last_n=100)


# ─── LiveView JavaScript 실행 ──────────────────────
_js_result = {"value": None, "error": None, "done": False}


def _run_js_on_liveview(code, timeout=8.0):
    """LiveView WebView에서 JavaScript 실행 후 결과 반환"""
    global _js_result
    _js_result = {"value": None, "error": None, "done": False}

    def _exec():
        try:
            dialog = getattr(_main_window, '_live_dialog', None)
            if not dialog:
                _js_result["error"] = "LiveView dialog not open"
                _js_result["done"] = True
                return
            page = getattr(dialog, 'aion2_page', None)
            if not page:
                _js_result["error"] = "No aion2_page"
                _js_result["done"] = True
                return

            def _callback(result):
                _js_result["value"] = result
                _js_result["done"] = True

            page.runJavaScript(code, _callback)
        except Exception as e:
            _js_result["error"] = str(e)
            _js_result["done"] = True

    # Qt 메인 스레드에서 실행
    try:
        from PyQt6.QtCore import QTimer
        event = threading.Event()

        def _on_main():
            _exec()
            # runJavaScript는 비동기 — 별도로 완료 대기
            def _check():
                if _js_result["done"]:
                    event.set()
                else:
                    QTimer.singleShot(100, _check)
            QTimer.singleShot(100, _check)

        QTimer.singleShot(0, _on_main)
        event.wait(timeout=timeout)
        if not _js_result["done"]:
            return {"error": "timeout", "code": code}
        return _js_result
    except Exception as e:
        return {"error": str(e), "code": code}


# ─── HTTP 핸들러 ────────────────────────────────────
class DebugHandler(BaseHTTPRequestHandler):
    """경량 REST API 핸들러"""

    def log_message(self, format, *args):
        """HTTP 로그 억제 (너무 많으면 성능 저하)"""
        pass

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        body = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        self.wfile.write(body.encode('utf-8'))

    def _send_text(self, text, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(text.encode('utf-8'))

    def _send_html(self, html, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        params = parse_qs(parsed.query)

        try:
            # 대시보드
            if path == '' or path == '/':
                self._send_html(self._dashboard_html())

            # === API 엔드포인트 ===
            elif path == '/api/status':
                self._send_json(_get_app_status())

            elif path == '/api/devices':
                self._send_json(_get_devices())

            elif path == '/api/threads':
                self._send_json(_get_threads())

            elif path == '/api/thumbnails':
                self._send_json(_get_thumbnails())

            elif path == '/api/gpu':
                self._send_json(_get_gpu_info())

            elif path == '/api/relay':
                self._send_json(_get_relay_info())

            elif path == '/api/network':
                self._send_json(_get_network_info())

            elif path == '/api/logs':
                n = int(params.get('n', ['200'])[0])
                logs = list(_log_buffer)
                recent = logs[-n:] if len(logs) > n else logs
                self._send_text('\n'.join(recent) if recent else '(로그 없음)')

            elif path == '/api/logs/file':
                n = int(params.get('n', ['200'])[0])
                filename = params.get('f', ['app.log'])[0]
                # 보안: 파일명에 경로 구분자 금지
                if '/' in filename or '\\' in filename or '..' in filename:
                    self._send_json({"error": "invalid filename"}, 400)
                    return
                self._send_text(_read_log_file(filename, n))

            elif path == '/api/logs/fault':
                self._send_text(_read_fault_log())

            elif path == '/api/js':
                # LiveView WebView에서 JavaScript 실행
                code = params.get('code', [''])[0]
                if not code:
                    self._send_json({"error": "missing 'code' parameter"}, 400)
                    return
                result = _run_js_on_liveview(code)
                self._send_json(result)

            elif path == '/api/webrtc_diag':
                # WebRTC 종합 진단 — LiveView에서 자동 실행
                diag_js = """
(function() {
    var result = {};

    // 1. video 엘리먼트
    var videos = document.querySelectorAll('video');
    result.video_count = videos.length;
    result.videos = [];
    videos.forEach(function(v, i) {
        result.videos.push({
            index: i,
            readyState: v.readyState,
            paused: v.paused,
            width: v.videoWidth,
            height: v.videoHeight,
            srcObj: !!v.srcObject,
            src: v.src || '',
            currentTime: v.currentTime,
            networkState: v.networkState
        });
    });

    // 2. RTCPeerConnection 존재 여부
    result.rtc_available = typeof RTCPeerConnection !== 'undefined';
    result.rtc_count = window.__rtc_count || 0;

    // 3. WebSocket 상태
    result.ws_count = window.__ws_count || 0;

    // 4. 페이지 에러
    result.page_errors = window.__page_errors || [];

    // 5. DOM 주요 요소
    var root = document.getElementById('root');
    result.root_html_length = root ? root.innerHTML.length : 0;
    result.root_children = root ? root.children.length : 0;

    // 6. React 앱 상태 탐색
    result.body_text_preview = document.body.innerText.substring(0, 500);

    // 7. navigator.mediaDevices
    result.mediaDevices_available = !!navigator.mediaDevices;
    result.getUserMedia_available = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    result.getDisplayMedia_available = !!(navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia);

    // 8. console 에러 캡처 (이미 오버라이드된 경우)
    result.console_errors = window.__console_errors || [];

    return JSON.stringify(result);
})()
"""
                result = _run_js_on_liveview(diag_js)
                # 결과가 JSON 문자열이면 파싱
                if result.get("value") and isinstance(result["value"], str):
                    try:
                        result["parsed"] = json.loads(result["value"])
                    except Exception:
                        pass
                self._send_json(result)

            elif path == '/api/js_inject_monitors':
                # WebRTC/WebSocket/에러 모니터 삽입
                inject_js = """
(function() {
    // 이미 삽입됐으면 스킵
    if (window.__debug_monitors_installed) return 'already installed';

    window.__rtc_count = 0;
    window.__ws_count = 0;
    window.__page_errors = [];
    window.__console_errors = [];
    window.__rtc_instances = [];
    window.__ws_instances = [];

    // RTCPeerConnection 가로채기
    var _OrigRTC = window.RTCPeerConnection;
    window.RTCPeerConnection = function() {
        window.__rtc_count++;
        var pc = new _OrigRTC(...arguments);
        window.__rtc_instances.push({
            created: new Date().toISOString(),
            config: arguments[0]
        });
        console.log('[DEBUG] RTCPeerConnection created #' + window.__rtc_count);
        var origSetRemote = pc.setRemoteDescription.bind(pc);
        pc.setRemoteDescription = function(desc) {
            console.log('[DEBUG] setRemoteDescription type=' + desc.type);
            return origSetRemote(desc);
        };
        pc.addEventListener('iceconnectionstatechange', function() {
            console.log('[DEBUG] ICE state: ' + pc.iceConnectionState);
        });
        pc.addEventListener('connectionstatechange', function() {
            console.log('[DEBUG] Connection state: ' + pc.connectionState);
        });
        pc.addEventListener('track', function(e) {
            console.log('[DEBUG] Track received: ' + e.track.kind);
        });
        return pc;
    };
    window.RTCPeerConnection.prototype = _OrigRTC.prototype;

    // WebSocket 가로채기
    var _OrigWS = window.WebSocket;
    window.WebSocket = function(url, protocols) {
        window.__ws_count++;
        console.log('[DEBUG] WebSocket #' + window.__ws_count + ' → ' + url);
        var ws = protocols ? new _OrigWS(url, protocols) : new _OrigWS(url);
        window.__ws_instances.push({url: url, created: new Date().toISOString()});
        ws.addEventListener('open', function() { console.log('[DEBUG] WS open: ' + url); });
        ws.addEventListener('error', function(e) { console.log('[DEBUG] WS error: ' + url); });
        ws.addEventListener('close', function(e) { console.log('[DEBUG] WS close: ' + url + ' code=' + e.code); });
        return ws;
    };
    window.WebSocket.prototype = _OrigWS.prototype;
    // Copy static properties
    window.WebSocket.CONNECTING = _OrigWS.CONNECTING;
    window.WebSocket.OPEN = _OrigWS.OPEN;
    window.WebSocket.CLOSING = _OrigWS.CLOSING;
    window.WebSocket.CLOSED = _OrigWS.CLOSED;

    // 에러 캡처
    window.addEventListener('error', function(e) {
        window.__page_errors.push({
            message: e.message,
            filename: e.filename,
            lineno: e.lineno,
            time: new Date().toISOString()
        });
    });

    // console.error 캡처
    var _origError = console.error;
    console.error = function() {
        window.__console_errors.push({
            args: Array.from(arguments).map(String),
            time: new Date().toISOString()
        });
        _origError.apply(console, arguments);
    };

    window.__debug_monitors_installed = true;
    return 'monitors installed';
})()
"""
                result = _run_js_on_liveview(inject_js)
                self._send_json(result)

            elif path == '/api/all':
                # 전체 상태 한 번에 가져오기
                self._send_json({
                    "status": _get_app_status(),
                    "devices": _get_devices(),
                    "threads": _get_threads(),
                    "gpu": _get_gpu_info(),
                    "relay": _get_relay_info(),
                    "network": _get_network_info(),
                })

            else:
                self._send_json({"error": "not found", "endpoints": [
                    "/", "/api/status", "/api/devices", "/api/threads",
                    "/api/thumbnails", "/api/gpu", "/api/relay", "/api/network",
                    "/api/logs?n=200", "/api/logs/file?f=app.log&n=200",
                    "/api/logs/fault", "/api/all",
                    "/api/js?code=...", "/api/webrtc_diag",
                    "/api/js_inject_monitors",
                ]}, 404)

        except Exception as e:
            self._send_json({"error": str(e), "traceback": traceback.format_exc()}, 500)

    def _dashboard_html(self):
        """간단한 대시보드 HTML"""
        from version import __version__
        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>WellcomLAND Debug</title>
<style>
body {{ font-family: monospace; background: #1a1a2e; color: #e0e0e0; margin: 20px; }}
h1 {{ color: #4CAF50; }}
a {{ color: #64B5F6; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.section {{ background: #16213e; padding: 15px; margin: 10px 0; border-radius: 8px; }}
.endpoint {{ margin: 5px 0; }}
pre {{ background: #0f3460; padding: 10px; border-radius: 5px; overflow-x: auto; }}
#status {{ margin: 20px 0; }}
</style>
</head><body>
<h1>WellcomLAND Debug Server v{__version__}</h1>

<div class="section">
<h2>API Endpoints</h2>
<div class="endpoint"><a href="/api/status">/api/status</a> — 앱 상태 (버전, uptime, LiveView, StatusThread)</div>
<div class="endpoint"><a href="/api/devices">/api/devices</a> — KVM 장치 목록</div>
<div class="endpoint"><a href="/api/threads">/api/threads</a> — 활성 스레드 목록</div>
<div class="endpoint"><a href="/api/thumbnails">/api/thumbnails</a> — 썸네일 WebView 상태</div>
<div class="endpoint"><a href="/api/gpu">/api/gpu</a> — GPU 설정/크래시 정보</div>
<div class="endpoint"><a href="/api/relay">/api/relay</a> — 릴레이 프록시 상태</div>
<div class="endpoint"><a href="/api/network">/api/network</a> — 네트워크 정보</div>
<div class="endpoint"><a href="/api/logs?n=200">/api/logs?n=200</a> — 최근 로그 (메모리 버퍼)</div>
<div class="endpoint"><a href="/api/logs/file?f=app.log&n=200">/api/logs/file</a> — 로그 파일 직접 읽기</div>
<div class="endpoint"><a href="/api/logs/fault">/api/logs/fault</a> — 크래시 로그 (fault.log)</div>
<div class="endpoint"><a href="/api/all">/api/all</a> — 전체 상태 한 번에</div>
</div>

<div class="section" id="status">
<h2>Live Status</h2>
<pre id="status-data">로딩 중...</pre>
</div>

<script>
async function refresh() {{
    try {{
        const r = await fetch('/api/status');
        const d = await r.json();
        document.getElementById('status-data').textContent = JSON.stringify(d, null, 2);
    }} catch(e) {{
        document.getElementById('status-data').textContent = 'Error: ' + e;
    }}
}}
refresh();
setInterval(refresh, 5000);
</script>
</body></html>"""


# ─── 서버 시작 ──────────────────────────────────────

def start_server(main_window, port=5111):
    """디버그 HTTP 서버를 별도 스레드에서 시작

    Args:
        main_window: MainWindow 인스턴스
        port: HTTP 포트 (기본 5111)
    """
    global _main_window, _server_thread
    _main_window = main_window

    def _run():
        try:
            # 0.0.0.0 바인딩 → Tailscale IP로 원격 접근 가능
            server = HTTPServer(('0.0.0.0', port), DebugHandler)
            server.timeout = 1.0
            print(f"[Debug] 디버그 서버 시작: http://0.0.0.0:{port}/")

            while True:
                server.handle_request()
        except Exception as e:
            print(f"[Debug] 서버 오류: {e}")
            traceback.print_exc()

    _server_thread = threading.Thread(target=_run, daemon=True, name="DebugHTTP")
    _server_thread.start()

    # Tailscale IP 출력
    try:
        hostname = socket.gethostname()
        for ai in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = ai[4][0]
            if ip.startswith('100.'):
                print(f"[Debug] Tailscale 접근: http://{ip}:{port}/")
                break
    except Exception:
        pass

    return _server_thread
