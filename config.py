"""
WellcomLAND 설정
"""

import sys
import os
import json
from typing import Any, Optional


def _get_base_dir():
    """실행 환경에 따른 기본 디렉터리 결정"""
    if getattr(sys, 'frozen', False):
        # PyInstaller EXE: WellcomLAND.exe가 있는 디렉터리
        return os.path.dirname(sys.executable)
    else:
        # 개발환경: ipkvm/ 디렉터리
        return os.path.dirname(os.path.abspath(__file__))


# 기본 경로
BASE_DIR = _get_base_dir()
DATA_DIR = os.path.join(BASE_DIR, "data")

# 데이터베이스
DB_PATH = os.path.join(DATA_DIR, "kvm_devices.db")
CONFIG_PATH = os.path.join(DATA_DIR, "settings.json")

# 로그 / 백업 (EXE 환경용)
LOG_DIR = os.path.join(BASE_DIR, "logs")

# 아이콘 경로
def _get_icon_path():
    """아이콘 파일 경로 (EXE/개발 환경 자동 감지)"""
    if getattr(sys, 'frozen', False):
        # EXE: _internal/assets/wellcom.ico
        return os.path.join(sys._MEIPASS, "assets", "wellcom.ico")
    else:
        # 개발: build/wellcom.ico 또는 wellcom.ico
        for p in [
            os.path.join(BASE_DIR, "build", "wellcom.ico"),
            os.path.join(BASE_DIR, "wellcom.ico"),
        ]:
            if os.path.exists(p):
                return p
    return ""

ICON_PATH = _get_icon_path()
BACKUP_DIR = os.path.join(BASE_DIR, "backup")

# 기본 KVM 설정
DEFAULT_SSH_PORT = 22
DEFAULT_SSH_USER = "root"
DEFAULT_SSH_PASSWORD = "luckfox"
DEFAULT_WEB_PORT = 80

# 연결 설정
SSH_TIMEOUT = 10
SSH_KEEPALIVE_INTERVAL = 30

# 모니터링
MONITOR_INTERVAL = 5000  # ms
STATUS_CHECK_INTERVAL = 3000  # ms

# UI 설정
WINDOW_TITLE = "WellcomLAND"
WINDOW_MIN_WIDTH = 1400
WINDOW_MIN_HEIGHT = 900

# 아이온2 모드 기본값
DEFAULT_AION2_SENSITIVITY = 1.0
DEFAULT_AION2_IMMEDIATE_MODE = True


class Settings:
    """애플리케이션 설정 관리"""

    _instance: Optional['Settings'] = None
    _defaults = {
        'window': {
            'width': WINDOW_MIN_WIDTH,
            'height': WINDOW_MIN_HEIGHT,
            'x': 100,
            'y': 100,
            'maximized': False
        },
        'discovery': {
            'ports': [80, 8080],
            'timeout': 1.5,
            'auto_scan_on_start': False
        },
        'aion2': {
            'sensitivity': DEFAULT_AION2_SENSITIVITY,
            'immediate_mode': DEFAULT_AION2_IMMEDIATE_MODE
        },
        'ssh': {
            'default_user': DEFAULT_SSH_USER,
            'default_password': DEFAULT_SSH_PASSWORD,
            'default_port': DEFAULT_SSH_PORT,
            'timeout': SSH_TIMEOUT
        },
        'grid_view': {
            'thumbnail_refresh_interval': 30000,  # ms
            'columns': 0  # 0 = 자동
        },
        'general': {
            'theme': 'fusion',
            'language': 'ko',
            'start_minimized': False,
            'confirm_delete': True
        },
        'update': {
            'github_token': '',
            'auto_check': True,
            'skip_version': ''
        }
    }

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._data = {}
            cls._instance._load()
        return cls._instance

    def _load(self):
        """설정 파일 로드"""
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    self._data = json.load(f)
        except Exception as e:
            print(f"설정 로드 실패: {e}")
            self._data = {}

    def save(self):
        """설정 파일 저장"""
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"설정 저장 실패: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        """
        설정 값 가져오기 (점 표기법 지원)
        예: settings.get('aion2.sensitivity')
        """
        keys = key.split('.')
        value = self._data

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                # 기본값에서 찾기
                default_val = self._defaults
                for dk in keys:
                    if isinstance(default_val, dict) and dk in default_val:
                        default_val = default_val[dk]
                    else:
                        return default
                return default_val

        return value

    def set(self, key: str, value: Any, auto_save: bool = True):
        """
        설정 값 저장 (점 표기법 지원)
        예: settings.set('aion2.sensitivity', 1.5)
        """
        keys = key.split('.')
        data = self._data

        for k in keys[:-1]:
            if k not in data:
                data[k] = {}
            data = data[k]

        data[keys[-1]] = value

        if auto_save:
            self.save()

    def reset(self, key: Optional[str] = None):
        """설정 초기화"""
        if key:
            keys = key.split('.')
            default_val = self._defaults
            for k in keys:
                if isinstance(default_val, dict) and k in default_val:
                    default_val = default_val[k]
                else:
                    return
            self.set(key, default_val)
        else:
            self._data = {}
            self.save()


# 싱글톤 인스턴스
settings = Settings()
