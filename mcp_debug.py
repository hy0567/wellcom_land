"""
WellcomLAND MCP 디버그 서버
- WellcomLAND 내부 상태를 Claude Code에서 실시간 조회 가능
- SSE transport로 localhost에서 실행
- 개발/디버깅 전용 (빌드 배포에 포함하지 않음)

사용법:
  1. pip install "mcp[cli]"
  2. WellcomLAND 실행 (자동으로 MCP 서버 시작)
  3. Claude Code에서 MCP 도구 사용
"""

import sys
import json
import time
import threading
import collections
import asyncio
import base64
from io import BytesIO
from typing import Optional

from mcp.server.fastmcp import FastMCP

# ─── 전역 상태 ───────────────────────────────────────
_main_window = None  # MainWindow 참조 (main.py에서 설정)
_log_buffer = collections.deque(maxlen=1000)  # 최근 1000줄 로그
_server_thread = None

# ─── stdout 캡처 ─────────────────────────────────────
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

    # 기타 file-like 속성 위임
    def __getattr__(self, name):
        return getattr(self.original, name)


def install_log_capture():
    """stdout/stderr 캡처 시작 (main.py에서 호출)"""
    if not isinstance(sys.stdout, _LogTee):
        sys.stdout = _LogTee(sys.stdout)
    if not isinstance(sys.stderr, _LogTee):
        sys.stderr = _LogTee(sys.stderr)


# ─── Qt 메인 스레드에서 안전하게 실행 ─────────────────
class _MainThreadInvoker(threading.Thread):
    """Qt 시그널 없이 메인 스레드 큐를 폴링하여 실행하는 헬퍼"""
    pass


def _run_on_main_thread(func):
    """Qt 메인 스레드에서 함수 실행하고 결과 반환 (thread-safe)"""
    if _main_window is None:
        _write_log("[MCP] _run_on_main_thread: _main_window is None")
        return None

    result_holder = [None]
    error_holder = [None]
    event = threading.Event()

    def wrapper():
        try:
            result_holder[0] = func()
        except Exception as e:
            error_holder[0] = str(e)
            _write_log(f"[MCP] _run_on_main_thread wrapper error: {e}")
        finally:
            event.set()

    try:
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, wrapper)
        waited = event.wait(timeout=5.0)  # 최대 5초 대기

        if not waited:
            _write_log("[MCP] _run_on_main_thread: TIMEOUT (5s) — wrapper never executed")
            # Timeout 시 직접 실행 시도 (메인 스레드 아니지만 read-only라면 OK)
            try:
                result_holder[0] = func()
                _write_log("[MCP] _run_on_main_thread: fallback direct call succeeded")
            except Exception as e2:
                _write_log(f"[MCP] _run_on_main_thread: fallback also failed: {e2}")
                return f"Error: timeout + fallback failed: {e2}"

        if error_holder[0]:
            return f"Error: {error_holder[0]}"
        return result_holder[0]
    except Exception as e:
        _write_log(f"[MCP] _run_on_main_thread exception: {e}")
        return f"Error: {e}"


# ─── MCP 서버 정의 ────────────────────────────────────
mcp = FastMCP("WellcomLAND Debug", host="127.0.0.1", port=5111)


@mcp.tool()
def list_devices() -> str:
    """모든 KVM 장치 목록과 상태를 반환합니다.

    각 장치의 이름, IP, 포트, 온라인/오프라인 상태, 그룹 정보를 포함합니다.
    """
    def _get():
        if not _main_window or not hasattr(_main_window, 'manager'):
            return json.dumps({"error": "MainWindow not available"})

        devices = _main_window.manager.get_all_devices()
        result = []
        for d in devices:
            result.append({
                "name": d.name,
                "ip": d.ip,
                "web_port": getattr(d.info, 'web_port', 80),
                "status": str(d.status).split('.')[-1] if hasattr(d.status, 'name') else str(d.status),
                "group": getattr(d.info, 'group', 'default') or 'default',
            })
        return json.dumps(result, ensure_ascii=False, indent=2)

    return _run_on_main_thread(_get) or '[]'


@mcp.tool()
def get_thumbnail_states() -> str:
    """모든 GridViewTab의 썸네일 WebView 상태를 상세 반환합니다.

    각 썸네일의 _is_active, _is_paused, _stream_status, _crop_region,
    WebView 존재 여부, URL 등을 포함합니다.
    """
    def _get():
        if not _main_window:
            return json.dumps({"error": "MainWindow not available"})

        result = {}

        # 전체 목록 탭
        if hasattr(_main_window, 'grid_view_tab'):
            tab = _main_window.grid_view_tab
            tab_info = {
                "tab_name": "전체 목록",
                "is_visible": tab._is_visible,
                "load_in_progress": tab._load_in_progress,
                "crop_region": tab._crop_region,
                "thumbnail_count": len(tab.thumbnails),
                "thumbnails": []
            }
            for thumb in tab.thumbnails:
                t = {
                    "device": thumb.device.name,
                    "is_active": thumb._is_active,
                    "is_paused": thumb._is_paused,
                    "stream_status": thumb._stream_status,
                    "crop_region": thumb._crop_region,
                    "has_webview": thumb._webview is not None,
                    "use_preview": thumb._use_preview,
                }
                if thumb._webview:
                    try:
                        t["url"] = thumb._webview.url().toString()
                    except Exception:
                        t["url"] = "N/A"
                tab_info["thumbnails"].append(t)
            result["전체 목록"] = tab_info

        # 그룹 탭들
        if hasattr(_main_window, 'group_grid_tabs'):
            for group_name, tab in _main_window.group_grid_tabs.items():
                tab_info = {
                    "tab_name": group_name,
                    "is_visible": tab._is_visible,
                    "load_in_progress": tab._load_in_progress,
                    "crop_region": tab._crop_region,
                    "thumbnail_count": len(tab.thumbnails),
                    "thumbnails": []
                }
                for thumb in tab.thumbnails:
                    t = {
                        "device": thumb.device.name,
                        "is_active": thumb._is_active,
                        "is_paused": thumb._is_paused,
                        "stream_status": thumb._stream_status,
                        "crop_region": thumb._crop_region,
                        "has_webview": thumb._webview is not None,
                        "use_preview": thumb._use_preview,
                    }
                    if thumb._webview:
                        try:
                            t["url"] = thumb._webview.url().toString()
                        except Exception:
                            t["url"] = "N/A"
                    tab_info["thumbnails"].append(t)
                result[group_name] = tab_info

        return json.dumps(result, ensure_ascii=False, indent=2)

    return _run_on_main_thread(_get) or '{}'


@mcp.tool()
def get_tab_info() -> str:
    """현재 탭 위젯의 상태를 반환합니다.

    현재 활성 탭 인덱스, 각 탭의 이름, 타입, 가시성 등을 포함합니다.
    """
    def _get():
        if not _main_window or not hasattr(_main_window, 'tab_widget'):
            return json.dumps({"error": "MainWindow not available"})

        tw = _main_window.tab_widget
        result = {
            "current_index": tw.currentIndex(),
            "current_tab_text": tw.tabText(tw.currentIndex()),
            "tab_count": tw.count(),
            "tabs": []
        }

        for i in range(tw.count()):
            widget = tw.widget(i)
            tab_data = {
                "index": i,
                "text": tw.tabText(i),
                "type": type(widget).__name__,
                "is_current": i == tw.currentIndex(),
            }

            # GridViewTab 추가 정보
            if hasattr(widget, 'thumbnails'):
                tab_data["thumbnail_count"] = len(widget.thumbnails)
                tab_data["is_visible"] = widget._is_visible
                tab_data["load_in_progress"] = widget._load_in_progress
                tab_data["crop_region"] = widget._crop_region
                tab_data["live_preview_enabled"] = widget._live_preview_enabled

                # 상태 요약
                active = sum(1 for t in widget.thumbnails if t._is_active)
                connected = sum(1 for t in widget.thumbnails if t._stream_status == "connected")
                tab_data["active_count"] = active
                tab_data["connected_count"] = connected

            result["tabs"].append(tab_data)

        return json.dumps(result, ensure_ascii=False, indent=2)

    return _run_on_main_thread(_get) or '{}'


@mcp.tool()
def get_app_logs(last_n: int = 100) -> str:
    """최근 앱 로그를 반환합니다.

    Args:
        last_n: 반환할 최근 로그 줄 수 (기본 100, 최대 1000)
    """
    n = min(max(1, last_n), 1000)
    logs = list(_log_buffer)
    recent = logs[-n:] if len(logs) > n else logs
    return '\n'.join(recent) if recent else '(로그 없음)'


@mcp.tool()
def take_screenshot() -> str:
    """MainWindow의 스크린샷을 찍어 base64 PNG로 반환합니다.

    반환값은 data:image/png;base64,... 형식의 문자열입니다.
    """
    def _get():
        if not _main_window:
            return "Error: MainWindow not available"

        try:
            from PyQt6.QtCore import QBuffer, QIODevice
            from PyQt6.QtWidgets import QApplication

            screen = QApplication.primaryScreen()
            if not screen:
                return "Error: No screen available"

            # MainWindow 영역만 캡처
            geometry = _main_window.geometry()
            pixmap = screen.grabWindow(0,
                                       geometry.x(), geometry.y(),
                                       geometry.width(), geometry.height())

            # PNG → base64
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            pixmap.save(buffer, "PNG")
            png_data = buffer.data().data()
            b64 = base64.b64encode(png_data).decode('ascii')

            return f"data:image/png;base64,{b64}"
        except Exception as e:
            return f"Error: {e}"

    return _run_on_main_thread(_get) or 'Error: timeout'


@mcp.tool()
def force_start_capture(device_name: str) -> str:
    """특정 장치의 썸네일 캡처를 강제로 시작합니다.

    Args:
        device_name: KVM 장치 이름
    """
    def _do():
        if not _main_window:
            return "Error: MainWindow not available"

        # 모든 탭에서 해당 장치 찾기
        all_tabs = [_main_window.grid_view_tab] + list(_main_window.group_grid_tabs.values())
        found = False
        for tab in all_tabs:
            for thumb in tab.thumbnails:
                if thumb.device.name == device_name:
                    thumb.start_capture()
                    found = True

        return f"OK: {device_name} 캡처 시작" if found else f"Error: '{device_name}' 장치를 찾을 수 없음"

    return _run_on_main_thread(_do) or 'Error: timeout'


@mcp.tool()
def force_stop_capture(device_name: str) -> str:
    """특정 장치의 썸네일 캡처를 강제로 중지합니다.

    Args:
        device_name: KVM 장치 이름
    """
    def _do():
        if not _main_window:
            return "Error: MainWindow not available"

        all_tabs = [_main_window.grid_view_tab] + list(_main_window.group_grid_tabs.values())
        found = False
        for tab in all_tabs:
            for thumb in tab.thumbnails:
                if thumb.device.name == device_name:
                    thumb.stop_capture()
                    found = True

        return f"OK: {device_name} 캡처 중지" if found else f"Error: '{device_name}' 장치를 찾을 수 없음"

    return _run_on_main_thread(_do) or 'Error: timeout'


@mcp.tool()
def apply_crop(tab_name: str, x: float, y: float, w: float, h: float) -> str:
    """특정 탭에 크롭 영역을 적용합니다.

    Args:
        tab_name: 탭 이름 ("전체 목록" 또는 그룹 이름)
        x: 크롭 시작 X (0.0~1.0)
        y: 크롭 시작 Y (0.0~1.0)
        w: 크롭 너비 (0.0~1.0)
        h: 크롭 높이 (0.0~1.0)
    """
    def _do():
        if not _main_window:
            return "Error: MainWindow not available"

        region = (x, y, w, h)

        if tab_name == "전체 목록":
            tab = _main_window.grid_view_tab
        else:
            tab = _main_window.group_grid_tabs.get(tab_name)

        if not tab:
            return f"Error: '{tab_name}' 탭을 찾을 수 없음"

        tab._crop_region = region
        for thumb in tab.thumbnails:
            if thumb._is_active and thumb._webview:
                thumb._crop_region = region
                thumb._poll_and_inject_crop(0)

        return f"OK: '{tab_name}' 탭에 크롭 적용 ({x:.2f}, {y:.2f}, {w:.2f}, {h:.2f})"

    return _run_on_main_thread(_do) or 'Error: timeout'


@mcp.tool()
def clear_crop(tab_name: str) -> str:
    """특정 탭의 크롭을 해제합니다.

    Args:
        tab_name: 탭 이름 ("전체 목록" 또는 그룹 이름)
    """
    def _do():
        if not _main_window:
            return "Error: MainWindow not available"

        if tab_name == "전체 목록":
            tab = _main_window.grid_view_tab
        else:
            tab = _main_window.group_grid_tabs.get(tab_name)

        if not tab:
            return f"Error: '{tab_name}' 탭을 찾을 수 없음"

        tab._crop_region = None
        for thumb in tab.thumbnails:
            thumb._crop_region = None
            if thumb._webview:
                thumb._clear_crop_css()

        return f"OK: '{tab_name}' 탭 크롭 해제"

    return _run_on_main_thread(_do) or 'Error: timeout'


@mcp.tool()
def run_js_on_thumbnail(device_name: str, js_code: str) -> str:
    """특정 장치의 썸네일 WebView에서 JavaScript를 실행합니다.

    Args:
        device_name: KVM 장치 이름
        js_code: 실행할 JavaScript 코드
    """
    result_holder = [None]
    event = threading.Event()

    def _do():
        if not _main_window:
            result_holder[0] = "Error: MainWindow not available"
            event.set()
            return

        all_tabs = [_main_window.grid_view_tab] + list(_main_window.group_grid_tabs.values())
        for tab in all_tabs:
            for thumb in tab.thumbnails:
                if thumb.device.name == device_name and thumb._webview:
                    def on_result(val):
                        result_holder[0] = json.dumps(val, ensure_ascii=False, default=str)
                        event.set()

                    thumb._webview.page().runJavaScript(js_code, on_result)
                    return

        result_holder[0] = f"Error: '{device_name}' 장치의 WebView를 찾을 수 없음"
        event.set()

    from PyQt6.QtCore import QTimer
    QTimer.singleShot(0, _do)
    event.wait(timeout=10.0)

    return result_holder[0] or 'Error: timeout'


@mcp.tool()
def get_device_detail(device_name: str) -> str:
    """특정 장치의 상세 정보를 반환합니다.

    Args:
        device_name: KVM 장치 이름
    """
    def _get():
        if not _main_window or not hasattr(_main_window, 'manager'):
            return json.dumps({"error": "MainWindow not available"})

        devices = _main_window.manager.get_all_devices()
        for d in devices:
            if d.name == device_name:
                info = {
                    "name": d.name,
                    "ip": d.ip,
                    "status": str(d.status).split('.')[-1] if hasattr(d.status, 'name') else str(d.status),
                    "web_port": getattr(d.info, 'web_port', 80),
                    "ssh_port": getattr(d.info, 'ssh_port', 22),
                    "ssh_user": getattr(d.info, 'ssh_user', ''),
                    "group": getattr(d.info, 'group', 'default') or 'default',
                    "model": getattr(d.info, 'model', ''),
                    "firmware": getattr(d.info, 'firmware_version', ''),
                }
                return json.dumps(info, ensure_ascii=False, indent=2)

        return json.dumps({"error": f"'{device_name}' not found"})

    return _run_on_main_thread(_get) or '{}'


# ─── 서버 시작/중지 ──────────────────────────────────

def start_server(main_window, port: int = 5111):
    """MCP 디버그 서버를 별도 스레드에서 시작

    Args:
        main_window: MainWindow 인스턴스
        port: SSE 서버 포트 (기본 5111)
    """
    global _main_window, _server_thread
    _main_window = main_window

    # 포트 변경이 필요한 경우 settings 업데이트
    mcp.settings.port = port

    def _run():
        try:
            # PyInstaller EXE에서 누락되는 stdlib 모듈을 시스템 Python에서 강제 로드
            _patch_frozen_imports()
            mcp.run(transport="sse")
        except Exception as e:
            _write_log(f"[MCP] 서버 스레드 오류: {e}")
            import traceback
            _write_log(traceback.format_exc())

    _server_thread = threading.Thread(target=_run, daemon=True, name="MCP-Debug")
    _server_thread.start()

    # 스레드 시작 후 잠시 대기하여 즉시 크래시 감지
    import time as _t2
    _t2.sleep(1.0)
    if not _server_thread.is_alive():
        _write_log("[MCP] 경고: 서버 스레드가 1초 내에 종료됨!")
    else:
        _write_log(f"[MCP] 서버 스레드 정상 실행 중 (alive={_server_thread.is_alive()})")

    return _server_thread


def _write_log(msg):
    """MCP 내부 로그를 파일에 기록"""
    import os
    try:
        log_path = os.path.join(
            os.environ.get('WELLCOMLAND_BASE_DIR', '.'), 'logs', 'mcp_debug.log'
        )
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass
    print(msg)


def _patch_frozen_imports():
    """PyInstaller EXE에서 누락되는 stdlib 서브모듈을 시스템 Python에서 강제 로드.

    PyInstaller가 logging 패키지를 번들하지만 logging.config 등 일부
    서브모듈을 누락시킴. sys.path에 시스템 stdlib을 넣어도 frozen importer가
    logging 네임스페이스를 먼저 잡으므로, importlib로 직접 로드하여 등록.
    """
    import importlib, importlib.util, os, glob

    if not getattr(sys, 'frozen', False):
        return  # 개발 환경에서는 불필요

    # 시스템 Python stdlib 경로 수집
    stdlib_dirs = []
    for pattern in [
        os.path.expandvars(r'%LOCALAPPDATA%\Python\*\Lib'),
        os.path.expandvars(r'%LOCALAPPDATA%\Programs\Python\*\Lib'),
    ]:
        stdlib_dirs.extend(glob.glob(pattern))

    if not stdlib_dirs:
        return

    # 의존성 순서대로 나열 (handlers → config, config가 handlers에 의존)
    missing_modules = [
        ('logging.handlers', 'logging', 'handlers.py'),
        ('logging.config', 'logging', 'config.py'),
    ]

    def _load_one(mod_name, parent_pkg, filename):
        """하나의 모듈을 시스템 stdlib에서 로드하여 sys.modules에 등록"""
        if mod_name in sys.modules:
            return True
        for lib_dir in stdlib_dirs:
            candidate = os.path.join(lib_dir, parent_pkg, filename)
            if os.path.exists(candidate):
                try:
                    spec = importlib.util.spec_from_file_location(mod_name, candidate)
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        sys.modules[mod_name] = mod
                        spec.loader.exec_module(mod)
                        # 부모 패키지에 attribute 등록
                        parent = sys.modules.get(parent_pkg)
                        if parent:
                            setattr(parent, filename.replace('.py', ''), mod)
                        return True
                except Exception:
                    # 의존성 부족으로 실패 가능 — 나중에 재시도
                    if mod_name in sys.modules:
                        del sys.modules[mod_name]
        return False

    # 2-pass: 1차에서 실패하면 의존성이 채워진 후 2차 시도
    patched = []
    for pass_num in range(2):
        for mod_name, parent_pkg, filename in missing_modules:
            if mod_name not in [m for m, _, _ in missing_modules if m not in patched]:
                continue
            if _load_one(mod_name, parent_pkg, filename):
                if mod_name not in patched:
                    patched.append(mod_name)

    if patched:
        _write_log(f"[MCP] frozen 모듈 패치 완료: {patched}")
