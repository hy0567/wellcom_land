"""
WellcomLAND - 다중 KVM 장치 관리 솔루션
Luckfox PicoKVM 기반
"""

import sys
import os

# 프로젝트 루트 경로 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from version import __version__, __app_name__, __github_repo__

# QtWebEngine 하드웨어 가속 및 입력 지연 최적화 (QApplication 생성 전에 설정 필요)
os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = (
    # GPU 가속
    '--enable-gpu-rasterization '
    '--enable-native-gpu-memory-buffers '
    '--enable-accelerated-video-decode '
    '--enable-accelerated-mjpeg-decode '
    '--disable-gpu-driver-bug-workarounds '
    '--ignore-gpu-blocklist '
    # WebRTC 최적화
    '--enable-webrtc-hw-decoding '
    '--enable-webrtc-hw-encoding '
    '--disable-webrtc-hw-vp8-encoding '
    # 입력 지연 최소화
    '--disable-frame-rate-limit '
    '--disable-gpu-vsync '
    # 렌더링 최적화
    '--enable-zero-copy '
    '--enable-features=VaapiVideoDecoder '
    '--canvas-oop-rasterization '
)

# 중요: QtWebEngineWidgets는 QApplication 생성 전에 임포트해야 함
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon

from ui import MainWindow
from ui.login_dialog import LoginDialog
from config import WINDOW_TITLE, ICON_PATH


def check_for_updates(app: QApplication) -> bool:
    """시작 시 업데이트 확인

    Returns:
        True: 정상 진행 (업데이트 없음 또는 스킵)
        False: 업데이트 완료 → 재시작 필요
    """
    try:
        from pathlib import Path
        from config import settings, BASE_DIR
        from updater import UpdateChecker
        from updater.update_dialog import UpdateNotifyDialog, UpdateDialog

        # 자동 업데이트 체크 비활성화 시 스킵
        if not settings.get('update.auto_check', True):
            return True

        token = settings.get('update.github_token', '')
        if not token:
            # Private repo는 토큰 없이 접근 불가
            print("[Update] GitHub 토큰이 설정되지 않았습니다. 업데이트 확인을 건너뜁니다.")
            return True

        base_dir = Path(BASE_DIR)
        checker = UpdateChecker(base_dir, __github_repo__, token)

        print(f"[Update] 현재 버전: v{checker.get_current_version()}")
        print(f"[Update] 업데이트 확인 중... ({__github_repo__})")

        has_update, release_info = checker.check_update()

        if not has_update or not release_info:
            print("[Update] 최신 버전입니다.")
            return True

        # 건너뛰기 버전 체크
        skip_version = settings.get('update.skip_version', '')
        if skip_version == release_info.version:
            print(f"[Update] v{release_info.version} 스킵 설정됨")
            return True

        # 업데이트 알림 다이얼로그
        notify = UpdateNotifyDialog(
            checker.get_current_version(), release_info
        )
        result = notify.exec()

        if result == 0:
            # "나중에" 선택
            print("[Update] 사용자가 업데이트를 건너뛰었습니다.")
            return True

        # 업데이트 진행 다이얼로그
        progress_dialog = UpdateDialog(release_info)
        progress_dialog.start_update(checker)
        progress_dialog.exec()

        if progress_dialog.is_success:
            print("[Update] 업데이트 완료 - 재시작...")
            _restart_application()
            return False

        return True

    except ImportError as e:
        print(f"[Update] updater 모듈 로드 실패 (무시): {e}")
        return True
    except Exception as e:
        print(f"[Update] 업데이트 확인 실패 (무시): {e}")
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


def main():
    print(f"[{__app_name__}] v{__version__} 시작")

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

    # OpenGL 소프트웨어 렌더링 비활성화 (하드웨어 가속 강제)
    app.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL, False)

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

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
