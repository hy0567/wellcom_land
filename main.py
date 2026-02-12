"""
WellcomLAND - 다중 KVM 장치 관리 솔루션
Luckfox PicoKVM 기반
"""

import sys
import os
import faulthandler

# 프로젝트 루트 경로 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from version import __version__, __app_name__, __github_repo__

# ── 로그 리다이렉트 (EXE 환경에서 stdout/stderr → 파일) ──
_app_dir = os.path.dirname(os.path.abspath(__file__)) if not getattr(sys, 'frozen', False) else os.path.dirname(sys.executable)
_log_dir = os.path.join(_app_dir, "logs")
os.makedirs(_log_dir, exist_ok=True)

# stdout/stderr를 파일로 복제 (콘솔 출력도 유지)
class _LogTee:
    """stdout/stderr를 파일과 원래 스트림에 동시 출력"""
    def __init__(self, log_path, orig_stream):
        self._file = open(log_path, 'a', encoding='utf-8', buffering=1)  # line-buffered
        self._orig = orig_stream
    def write(self, data):
        try:
            self._file.write(data)
            self._file.flush()  # 크래시 대비 즉시 flush
        except Exception:
            pass
        if self._orig:
            try:
                self._orig.write(data)
            except Exception:
                pass
    def flush(self):
        try:
            self._file.flush()
        except Exception:
            pass
        if self._orig:
            try:
                self._orig.flush()
            except Exception:
                pass

_log_path = os.path.join(_log_dir, "app.log")
# 로그 파일 크기 제한 (1MB 초과 시 초기화)
try:
    if os.path.exists(_log_path) and os.path.getsize(_log_path) > 1_000_000:
        os.remove(_log_path)
except Exception:
    pass

sys.stdout = _LogTee(_log_path, sys.stdout if sys.stdout else None)
sys.stderr = _LogTee(_log_path, sys.stderr if sys.stderr else None)

# Python logging 모듈의 StreamHandler 패치
# EXE 환경에서 원래 stderr가 None이면 logging.StreamHandler.stream도 None이 되어
# "AttributeError: 'NoneType' object has no attribute 'write'" 에러가 대량 발생
import logging as _logging
for _handler in _logging.root.handlers[:]:
    if isinstance(_handler, _logging.StreamHandler) and not _handler.stream:
        _handler.stream = sys.stderr
# lastResort 핸들러도 패치
if hasattr(_logging, 'lastResort') and _logging.lastResort:
    if isinstance(_logging.lastResort, _logging.StreamHandler) and not _logging.lastResort.stream:
        _logging.lastResort.stream = sys.stderr
# 새로 생성되는 StreamHandler를 위해 기본 stream을 sys.stderr로 보장
_orig_stream_handler_init = _logging.StreamHandler.__init__
def _patched_stream_handler_init(self, stream=None):
    _orig_stream_handler_init(self, stream if stream else sys.stderr)
_logging.StreamHandler.__init__ = _patched_stream_handler_init

# faulthandler: segfault/abort 시 스택트레이스를 파일에 기록
_fault_path = os.path.join(_log_dir, "fault.log")
_fault_file = open(_fault_path, 'a', encoding='utf-8')
faulthandler.enable(file=_fault_file)
print(f"[Log] 로그 파일: {_log_path}")
print(f"[Log] Fault 로그: {_fault_path}")

# ── GPU / Chromium 설정 ──
# EXE(frozen) 환경에서 Chromium GPU 서브프로세스가 DLL 경로 문제로 크래시 발생
# --in-process-gpu: GPU를 별도 프로세스 대신 메인 프로세스에서 실행 (frozen 호환)
# 주의: --disable-gpu-compositing 사용 금지! WebRTC 비디오 합성을 차단하여 검은화면 발생
_is_frozen = getattr(sys, 'frozen', False)

# GPU 크래시 플래그 확인
_gpu_crash_flag = os.path.join(_app_dir, "data", ".gpu_crash")
_had_gpu_crash = os.path.exists(_gpu_crash_flag)

# settings.json의 graphics.software_rendering 설정 확인
_force_software = False
_settings_path = os.path.join(_app_dir, "data", "settings.json")
try:
    if os.path.exists(_settings_path):
        import json as _json
        with open(_settings_path, 'r', encoding='utf-8') as _f:
            _settings_data = _json.load(_f)
        if _settings_data.get('graphics', {}).get('software_rendering', False):
            _force_software = True
except Exception:
    pass

# Chromium 플래그 구성
_chromium_flags = ['--autoplay-policy=no-user-gesture-required']

if _is_frozen:
    # EXE 환경: --in-process-gpu가 GPU 서브프로세스 DLL 문제를 완전히 해결
    # 따라서 SwiftShader 폴백이 불필요 (오히려 다수 WebRTC 스트림에서 Abort 유발)
    _chromium_flags.append('--in-process-gpu')
    _chromium_flags.append('--disable-gpu-shader-disk-cache')
    _chromium_flags.append('--disable-gpu-program-cache')
    print(f"[GPU] frozen 환경 — --in-process-gpu (하드웨어 GPU 사용)")

    # frozen 환경에서는 gpu_crash 플래그 무조건 삭제 (SwiftShader 폴백 비활성화)
    # --in-process-gpu가 근본 원인을 해결하므로 소프트웨어 렌더링 불필요
    if _had_gpu_crash:
        _flag_reason = "플래그 존재"
        try:
            with open(_gpu_crash_flag, 'r') as _f:
                _flag_reason = _f.read().strip()
        except Exception:
            pass
        print(f"[GPU] 이전 크래시 플래그 감지 (무시): {_flag_reason}")
        try:
            os.remove(_gpu_crash_flag)
            print(f"[GPU] 크래시 플래그 삭제 (frozen 환경은 --in-process-gpu로 보호)")
        except Exception:
            pass
        _had_gpu_crash = False  # SwiftShader 적용 방지

elif _had_gpu_crash:
    # 개발환경 (non-frozen): 기존 SwiftShader 폴백 유지
    _flag_reason = "플래그 존재"
    try:
        with open(_gpu_crash_flag, 'r') as _f:
            _flag_reason = _f.read().strip()
    except Exception:
        pass
    print(f"[GPU] 이전 크래시 감지 — 이유: {_flag_reason}")
    _chromium_flags.append('--use-gl=angle')
    _chromium_flags.append('--use-angle=swiftshader')
    # 크래시 플래그 삭제 (1회 적용)
    try:
        with open(_gpu_crash_flag, 'r') as _f:
            if 'manual=True' not in _f.read():
                os.remove(_gpu_crash_flag)
                print(f"[GPU] 크래시 플래그 삭제 (1회 적용)")
    except Exception:
        pass

if _force_software:
    print(f"[GPU] 설정에서 소프트웨어 렌더링 강제")
    if '--use-gl=angle' not in ' '.join(_chromium_flags):
        _chromium_flags.append('--use-gl=angle')
        _chromium_flags.append('--use-angle=swiftshader')

os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = ' '.join(_chromium_flags)

# 중요: QtWebEngineWidgets는 QApplication 생성 전에 임포트해야 함
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon

from ui import MainWindow
from ui.login_dialog import LoginDialog
from config import WINDOW_TITLE, ICON_PATH


def check_for_updates(app: QApplication) -> bool:
    """시작 시 업데이트 확인. True=정상진행, False=재시작필요"""
    try:
        from pathlib import Path
        from config import settings, BASE_DIR
        from updater import UpdateChecker
        from updater.update_dialog import UpdateNotifyDialog, UpdateDialog

        if not settings.get('update.auto_check', True):
            return True

        token = settings.get('update.github_token', '')
        checker = UpdateChecker(Path(BASE_DIR), __github_repo__, token or None,
                                running_version=__version__)

        has_update, release_info = checker.check_update()
        if not has_update or not release_info:
            return True

        # 알림 (버전만 표시)
        if UpdateNotifyDialog(checker.get_current_version(), release_info).exec() == 0:
            return True

        # 업데이트 진행
        dlg = UpdateDialog(release_info)
        dlg.start_update(checker)
        dlg.exec()

        if dlg.is_success:
            _restart_application()
            return False

        return True
    except Exception:
        return True


def _restart_application():
    """프로그램 재시작"""
    import subprocess

    # 1) 런처가 설정한 EXE 경로 (가장 신뢰)
    exe_path = os.environ.get('WELLCOMLAND_EXE_PATH')
    if exe_path and os.path.exists(exe_path):
        print(f"[Restart] EXE 경로: {exe_path}")
        subprocess.Popen([exe_path])
        sys.exit(0)

    # 2) 설치 디렉터리 기준 EXE
    base_dir = os.environ.get('WELLCOMLAND_BASE_DIR')
    if base_dir:
        candidate = os.path.join(base_dir, 'WellcomLAND.exe')
        if os.path.exists(candidate):
            print(f"[Restart] BASE_DIR 기준: {candidate}")
            subprocess.Popen([candidate])
            sys.exit(0)

    # 3) Fallback
    if getattr(sys, 'frozen', False):
        subprocess.Popen([sys.executable])
    else:
        subprocess.Popen([sys.executable] + sys.argv)
    sys.exit(0)


def _setup_crash_handler():
    """전역 예외 핸들러 — 미처리 예외 로깅 및 크래시 방지"""
    import traceback
    _orig_excepthook = sys.excepthook

    def _crash_handler(exc_type, exc_value, exc_tb):
        try:
            msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
            print(f"[CRASH] 미처리 예외:\n{msg}")
            # 로그 파일에 기록
            log_dir = os.path.join(_app_dir, "logs")
            os.makedirs(log_dir, exist_ok=True)
            from datetime import datetime
            with open(os.path.join(log_dir, "crash.log"), 'a', encoding='utf-8') as f:
                f.write(f"\n[{datetime.now().isoformat()}] {msg}\n")
        except Exception:
            pass
        # 원래 핸들러 호출 (Qt 내부 예외 처리)
        _orig_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = _crash_handler


def main():
    _setup_crash_handler()
    from datetime import datetime
    print(f"\n{'='*60}")
    print(f"[{__app_name__}] v{__version__} 시작 — {datetime.now().isoformat()}")
    print(f"[System] Python={sys.version}, frozen={getattr(sys, 'frozen', False)}")
    print(f"[System] frozen={_is_frozen}, gpu_crash={_had_gpu_crash}, sw_force={_force_software}")
    print(f"[System] CHROMIUM_FLAGS={os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')}")
    print(f"{'='*60}")

    # GPU 크래시 플래그: 소프트웨어 모드로 시작한 경우
    if _had_gpu_crash or _force_software:
        print("[GPU] 소프트웨어 렌더링 모드 유지 (환경설정에서 해제 가능)")

    # High DPI 스케일링 활성화
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName(WINDOW_TITLE)
    app.setStyle("Fusion")

    # 애플리케이션 아이콘 설정 (윈도우 타이틀바 + 작업표시줄)
    if ICON_PATH and os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))
        print(f"[App] 아이콘 적용: {ICON_PATH}")

    # GPU 모드 설정
    if _had_gpu_crash or _force_software:
        app.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL, True)
        print("[GPU] 소프트웨어 OpenGL 활성화")
    # else: Qt 기본값 사용 (강제 GPU 비활성화하지 않음)

    # 네트워크 우선순위 자동 조정 (Tailscale/APIPA가 기본인 경우)
    try:
        from core.network_fixer import auto_fix_network
        auto_fix_network()
    except Exception as e:
        print(f"[App] 네트워크 자동 조정 건너뜀: {e}")

    # 업데이트 확인 (업데이트 적용 시 재시작)
    if not check_for_updates(app):
        return

    # 로그인 다이얼로그
    login_dialog = LoginDialog()
    if login_dialog.exec() == 0 or not login_dialog.logged_in:
        print("[App] 로그인 취소됨. 종료합니다.")
        sys.exit(0)

    # 메인 윈도우 생성 및 표시
    window = MainWindow()
    window.show()

    # KVM 릴레이 시작 (로컬 KVM을 Tailscale로 중계 + 서버 등록)
    try:
        from core.kvm_relay import KVMRelayManager
        from api_client import api_client
        _kvm_relay = KVMRelayManager()

        def _start_kvm_relay():
            """로그인 후 백그라운드에서 KVM 릴레이 시작"""
            import time
            time.sleep(3)  # UI 초기화 대기

            if not api_client.is_logged_in:
                return

            ts_ip = _kvm_relay.get_tailscale_ip()
            if not ts_ip:
                print("[Relay] Tailscale IP 없음 — 릴레이 건너뜀")
                return

            print(f"[Relay] Tailscale IP: {ts_ip}")

            from core.discovery import NetworkScanner
            lan_ip = NetworkScanner.get_local_ip()
            print(f"[Network] LAN IP: {lan_ip}")

            # 로컬 KVM 장치에 대해 TCP 프록시 시작 (Tailscale 릴레이 IP는 제외)
            manager = window.manager if hasattr(window, 'manager') else None
            relay_devices = []
            if manager:
                devices = manager.get_all_devices()
                for dev in devices:
                    kvm_ip = dev.ip
                    kvm_port = dev.info.web_port if hasattr(dev.info, 'web_port') else 80

                    # 이미 릴레이로 치환된 디바이스(Tailscale IP)는 스킵
                    # → 이 PC에서 직접 접근 가능한 로컬 KVM만 릴레이 생성
                    if kvm_ip.startswith('100.'):
                        print(f"[Relay] {dev.name} ({kvm_ip}) — 원격 릴레이 (스킵)")
                        continue

                    relay_port = _kvm_relay.start_relay(kvm_ip, kvm_port, dev.name)
                    if relay_port:
                        udp_port = _kvm_relay.get_udp_port(kvm_ip)
                        relay_devices.append({
                            "kvm_local_ip": kvm_ip,
                            "kvm_port": kvm_port,
                            "kvm_name": dev.name,
                            "relay_port": relay_port,
                            "udp_relay_port": udp_port,
                        })
                        udp_info = f" UDP:{udp_port}" if udp_port else ""
                        print(f"[Relay] {dev.name} ({kvm_ip}:{kvm_port}) → TCP:{relay_port}{udp_info}")

            # 관제 PC 판별: 릴레이할 로컬 KVM이 1개 이상이면 관제 PC
            is_control_pc = len(relay_devices) > 0
            if is_control_pc:
                print(f"[Relay] 관제 PC 모드 — {len(relay_devices)}개 로컬 KVM 릴레이 중")
                try:
                    if lan_ip and not lan_ip.startswith('100.'):
                        from core.network_fixer import auto_setup_tailscale_forwarding
                        auto_setup_tailscale_forwarding(ts_ip, lan_ip)
                except Exception as e:
                    print(f"[Relay] Tailscale 서브넷 라우팅 설정 실패 (무시): {e}")

                # 서버에 릴레이 KVM 등록
                try:
                    api_client.register_kvm_devices(relay_devices, ts_ip)
                    print(f"[Relay] 서버에 {len(relay_devices)}개 KVM 등록 완료")
                except Exception as e:
                    print(f"[Relay] 서버 등록 실패: {e}")

                # heartbeat 시작
                _kvm_relay.start_heartbeat(api_client, interval=120)
            else:
                print(f"[Relay] 클라이언트 PC 모드 — 로컬 KVM 없음, 서브넷 라우팅 생략")

        import threading
        threading.Thread(target=_start_kvm_relay, daemon=True).start()

    except Exception as e:
        print(f"[Relay] KVM 릴레이 초기화 실패 (무시): {e}")

    # MCP 디버그 서버 시작 (mcp 패키지 미설치 시 자동 비활성화)
    def _mcp_log(msg):
        """MCP 로그를 파일에 기록 (EXE에서 print가 안 보일 수 있으므로)"""
        print(msg)
        try:
            mcp_log_path = os.path.join(os.environ.get('WELLCOMLAND_BASE_DIR', '.'), 'logs', 'mcp_debug.log')
            os.makedirs(os.path.dirname(mcp_log_path), exist_ok=True)
            with open(mcp_log_path, 'a', encoding='utf-8') as f:
                import time as _t
                f.write(f"[{_t.strftime('%H:%M:%S')}] {msg}\n")
        except Exception:
            pass

    _mcp_log("[MCP] === MCP 서버 초기화 시작 ===")
    _mcp_log(f"[MCP] frozen={getattr(sys, 'frozen', False)}, python={sys.version}")

    try:
        # EXE 환경에서 시스템 Python 패키지 접근
        if getattr(sys, 'frozen', False):
            import glob
            added_paths = []
            for pattern in [
                os.path.expandvars(r'%LOCALAPPDATA%\Python\*\Lib\site-packages'),
                os.path.expandvars(r'%LOCALAPPDATA%\Programs\Python\*\Lib\site-packages'),
            ]:
                for sp in glob.glob(pattern):
                    if sp not in sys.path:
                        sys.path.insert(0, sp)
                        added_paths.append(sp)
                    # pywin32 네이티브 DLL 경로 (pywintypes 등)
                    for sub in ['pywin32_system32', 'win32', 'win32\\lib']:
                        dll_dir = os.path.join(sp, sub)
                        if os.path.isdir(dll_dir):
                            if dll_dir not in sys.path:
                                sys.path.insert(0, dll_dir)
                            os.environ['PATH'] = dll_dir + ';' + os.environ.get('PATH', '')
            for pattern in [
                os.path.expandvars(r'%LOCALAPPDATA%\Python\*\Lib'),
                os.path.expandvars(r'%LOCALAPPDATA%\Programs\Python\*\Lib'),
            ]:
                for sp in glob.glob(pattern):
                    if sp not in sys.path and os.path.isdir(sp):
                        sys.path.insert(0, sp)
                        added_paths.append(sp)
            _mcp_log(f"[MCP] 시스템 Python 경로 추가: {added_paths}")
        else:
            _mcp_log("[MCP] 개발 환경 (non-frozen)")

        _mcp_log("[MCP] mcp_debug 모듈 import 시도...")
        from mcp_debug import start_server, install_log_capture
        _mcp_log("[MCP] mcp_debug import 성공")

        install_log_capture()
        _mcp_log("[MCP] 로그 캡처 설치 완료")

        start_server(window, port=5111)
        _mcp_log("[MCP] 디버그 서버 시작: http://localhost:5111/sse")
    except ImportError as e:
        _mcp_log(f"[MCP] ImportError: {e}")
    except Exception as e:
        _mcp_log(f"[MCP] 시작 실패: {e}")
        import traceback
        _mcp_log(traceback.format_exc())

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
