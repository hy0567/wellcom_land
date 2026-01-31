"""
WellcomLAND 빌드 스크립트
사용법: python build/build.py
출력: dist/WellcomLAND/WellcomLAND.exe
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent  # ipkvm/
BUILD_DIR = PROJECT_DIR / "build"
DIST_DIR = PROJECT_DIR / "dist"


def clean():
    """이전 빌드 결과물 정리"""
    print("=== 이전 빌드 정리 ===")
    for d in [DIST_DIR / "WellcomLAND", BUILD_DIR / "work"]:
        if d.exists():
            shutil.rmtree(d)
            print(f"  삭제: {d}")
    print("  정리 완료")


def build_exe():
    """PyInstaller로 EXE 빌드"""
    print("\n=== PyInstaller 빌드 시작 ===")
    spec_file = BUILD_DIR / "wellcomland.spec"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR / "work"),
        "--clean",
        str(spec_file)
    ]

    print(f"  명령: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(PROJECT_DIR))

    if result.returncode != 0:
        print("  빌드 실패!")
        sys.exit(1)

    print(f"  빌드 완료: {DIST_DIR / 'WellcomLAND'}")


def create_data_dir():
    """data/ 디렉터리 생성 (빈 상태)"""
    data_dir = DIST_DIR / "WellcomLAND" / "data"
    data_dir.mkdir(exist_ok=True)
    print(f"\n  data/ 디렉터리 생성: {data_dir}")


def verify_build():
    """빌드 결과물 검증"""
    print("\n=== 빌드 검증 ===")
    exe_path = DIST_DIR / "WellcomLAND" / "WellcomLAND.exe"

    checks = [
        (exe_path, "WellcomLAND.exe"),
    ]

    all_ok = True
    for path, name in checks:
        if path.exists():
            size_mb = path.stat().st_size / 1024 / 1024
            print(f"  OK: {name} ({size_mb:.1f} MB)")
        else:
            print(f"  FAIL: {name} 없음!")
            all_ok = False

    # 전체 크기
    total = sum(f.stat().st_size for f in (DIST_DIR / "WellcomLAND").rglob('*') if f.is_file())
    print(f"\n  전체 크기: {total / 1024 / 1024:.1f} MB")

    if all_ok:
        print("\n  빌드 검증 통과!")
    else:
        print("\n  빌드 검증 실패!")
        sys.exit(1)


def main():
    print(f"WellcomLAND 빌드 스크립트")
    print(f"프로젝트: {PROJECT_DIR}")
    print(f"출력: {DIST_DIR / 'WellcomLAND'}")
    print()

    clean()
    build_exe()
    create_data_dir()
    verify_build()

    print(f"\n완료! 실행: {DIST_DIR / 'WellcomLAND' / 'WellcomLAND.exe'}")


if __name__ == "__main__":
    main()
