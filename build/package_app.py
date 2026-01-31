"""
릴리스용 app.zip 패키징
사용법: python build/package_app.py
출력: dist/app.zip + dist/checksum.json

GitHub Releases에 app.zip을 업로드하면 자동 업데이트 가능.
"""

import os
import sys
import zipfile
import hashlib
import json
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent  # ipkvm/
OUTPUT_DIR = PROJECT_DIR / "dist"

# app.zip에 포함할 파일들 (data/ 는 절대 포함하지 않음)
APP_FILES = [
    'main.py',
    'config.py',
    'version.py',
    'core/__init__.py',
    'core/kvm_device.py',
    'core/kvm_manager.py',
    'core/database.py',
    'core/discovery.py',
    'core/hid_controller.py',
    'ui/__init__.py',
    'ui/main_window.py',
    'ui/device_control.py',
    'ui/dialogs.py',
    'updater/__init__.py',
    'updater/github_client.py',
    'updater/update_checker.py',
    'updater/update_dialog.py',
    'updater/file_manager.py',
]


def create_app_zip():
    """app.zip 생성"""
    OUTPUT_DIR.mkdir(exist_ok=True)
    zip_path = OUTPUT_DIR / "app.zip"

    print("=== app.zip 패키징 ===")

    # 버전 정보 읽기
    sys.path.insert(0, str(PROJECT_DIR))
    from version import __version__
    print(f"  버전: v{__version__}")

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for rel_path in APP_FILES:
            full_path = PROJECT_DIR / rel_path
            if not full_path.exists():
                print(f"  경고: 파일 없음 - {rel_path}")
                continue
            zf.write(full_path, rel_path)
            print(f"  추가: {rel_path}")

    # SHA256 체크섬
    sha256 = hashlib.sha256()
    with open(zip_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    checksum = sha256.hexdigest()

    print(f"\n  파일: {zip_path}")
    print(f"  크기: {zip_path.stat().st_size / 1024:.1f} KB")
    print(f"  SHA256: {checksum}")

    # checksum.json 생성
    checksum_data = {
        "version": __version__,
        "sha256": checksum,
        "size": zip_path.stat().st_size
    }
    checksum_path = OUTPUT_DIR / "checksum.json"
    with open(checksum_path, 'w', encoding='utf-8') as f:
        json.dump(checksum_data, f, indent=2)

    print(f"\n  checksum.json: {checksum_path}")
    print(f"\n릴리스 노트에 추가:")
    print(f"  SHA256: {checksum}")

    return checksum


if __name__ == "__main__":
    create_app_zip()
