"""
WellcomLAND 런처
- PyInstaller EXE의 엔트리포인트
- app/ 폴더의 코드를 동적 로드하여 실행
- Pending update 처리 (파일 잠금 대응)
"""

import sys
import os
import shutil
import logging
from pathlib import Path

LAUNCHER_VERSION = "1.0.0"


def _get_base_dir() -> Path:
    """실행 파일 기준 디렉터리"""
    if getattr(sys, 'frozen', False):
        return Path(os.path.dirname(sys.executable))
    else:
        return Path(os.path.dirname(os.path.abspath(__file__)))


BASE_DIR = _get_base_dir()
APP_DIR = BASE_DIR / "app"
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
TEMP_DIR = BASE_DIR / "temp"


def setup_logging():
    """로그 설정"""
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(LOG_DIR / 'wellcomland.log', encoding='utf-8'),
            logging.StreamHandler()
        ]
    )


def apply_pending_update():
    """Pending 업데이트 적용 (프로그램 재시작 후 실행)

    업데이트 과정에서 파일 잠금으로 교체 실패 시,
    temp/pending_update.zip을 남겨두고 재시작 후 여기서 적용.
    """
    logger = logging.getLogger('Launcher')
    pending_flag = TEMP_DIR / "pending_update.flag"
    pending_zip = TEMP_DIR / "pending_update.zip"

    if not pending_flag.exists() or not pending_zip.exists():
        return

    logger.info("Pending 업데이트 발견 - 적용 중...")

    try:
        import zipfile

        # app/ 삭제 후 재생성
        if APP_DIR.exists():
            shutil.rmtree(APP_DIR, ignore_errors=True)
        APP_DIR.mkdir(exist_ok=True)

        # zip 해제
        with zipfile.ZipFile(pending_zip, 'r') as zf:
            zf.extractall(APP_DIR)

        logger.info("Pending 업데이트 적용 완료")

    except Exception as e:
        logger.error(f"Pending 업데이트 적용 실패: {e}")
    finally:
        # 정리
        try:
            pending_flag.unlink(missing_ok=True)
            pending_zip.unlink(missing_ok=True)
            if TEMP_DIR.exists() and not any(TEMP_DIR.iterdir()):
                TEMP_DIR.rmdir()
        except Exception:
            pass


def ensure_app_dir():
    """app/ 디렉터리 확인 및 최초 설정

    PyInstaller 빌드 시 _internal/app/ 에 코드가 포함됨.
    최초 실행 시 이를 {exe_dir}/app/ 로 복사.
    """
    logger = logging.getLogger('Launcher')

    if APP_DIR.exists() and any(APP_DIR.glob("main.py")):
        return  # 이미 존재

    logger.info("최초 실행 - app/ 디렉터리 초기화")
    APP_DIR.mkdir(exist_ok=True)

    # PyInstaller 내부에서 app 코드 찾기
    if getattr(sys, 'frozen', False):
        # _MEIPASS/app/ 에서 복사
        internal_app = Path(sys._MEIPASS) / "app"
        if internal_app.exists():
            logger.info(f"내부 app 복사: {internal_app} -> {APP_DIR}")
            shutil.copytree(internal_app, APP_DIR, dirs_exist_ok=True)
        else:
            logger.error("내부 app/ 디렉터리를 찾을 수 없습니다.")
    else:
        logger.info("개발환경 - app/ 디렉터리 생성 스킵")


def load_and_run_app():
    """app/ 폴더의 main 모듈을 로드하여 실행 (.py 또는 .pyc)"""
    logger = logging.getLogger('Launcher')

    # app/ 를 sys.path 최상위에 추가
    app_path = str(APP_DIR)
    if app_path not in sys.path:
        sys.path.insert(0, app_path)

    # 환경변수로 base_dir 전달 (config.py가 사용)
    os.environ['WELLCOMLAND_BASE_DIR'] = str(BASE_DIR)

    # .pyc 또는 .py 확인
    has_pyc = (APP_DIR / "main.pyc").exists()
    has_py = (APP_DIR / "main.py").exists()
    logger.info(f"앱 로드: {'main.pyc (바이트코드)' if has_pyc else 'main.py (소스)'}")

    if has_pyc and not has_py:
        # .pyc만 있는 경우: importlib로 직접 로드
        import importlib.util
        spec = importlib.util.spec_from_file_location("main", APP_DIR / "main.pyc")
        main_module = importlib.util.module_from_spec(spec)
        sys.modules['main'] = main_module
        spec.loader.exec_module(main_module)
    else:
        # .py가 있는 경우: 일반 import
        import importlib
        main_module = importlib.import_module('main')

    main_module.main()


def main():
    """런처 메인"""
    setup_logging()
    logger = logging.getLogger('Launcher')
    logger.info(f"WellcomLAND Launcher v{LAUNCHER_VERSION}")
    logger.info(f"Base: {BASE_DIR}")

    # 필요 디렉터리 생성
    DATA_DIR.mkdir(exist_ok=True)

    # Pending 업데이트 적용
    apply_pending_update()

    # app/ 디렉터리 확인
    ensure_app_dir()

    # 앱 실행
    try:
        load_and_run_app()
    except Exception as e:
        logger.error(f"앱 실행 실패: {e}")
        import traceback
        traceback.print_exc()

        # 긴급 복구: 최신 백업으로 롤백 시도
        backup_dir = BASE_DIR / "backup"
        if backup_dir.exists():
            backups = sorted(backup_dir.glob("app_v*.zip"),
                             key=lambda p: p.stat().st_mtime, reverse=True)
            if backups:
                logger.info(f"긴급 롤백 시도: {backups[0].name}")
                try:
                    import zipfile
                    if APP_DIR.exists():
                        shutil.rmtree(APP_DIR, ignore_errors=True)
                    APP_DIR.mkdir(exist_ok=True)
                    with zipfile.ZipFile(backups[0], 'r') as zf:
                        zf.extractall(APP_DIR)
                    logger.info("롤백 완료 - 앱 재실행")
                    load_and_run_app()
                except Exception as e2:
                    logger.error(f"롤백도 실패: {e2}")

        input("엔터를 눌러 종료...")


if __name__ == "__main__":
    main()
