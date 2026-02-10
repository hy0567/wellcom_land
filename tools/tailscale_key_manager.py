"""
Tailscale AuthKey 자동 갱신 관리 도구

기능:
  1. 현재 authkey 상태 확인
  2. 만료 임박 시 새 authkey 자동 생성
  3. WellcomLAND 서버 DB에 새 authkey 자동 반영
  4. installer.iss 자동 업데이트 (선택)

사용법:
  python tailscale_key_manager.py              # 상태 확인 + 자동 갱신
  python tailscale_key_manager.py --force      # 강제 새 키 생성
  python tailscale_key_manager.py --status     # 상태만 확인
  python tailscale_key_manager.py --renew-days 60  # 60일 이내 만료 시 갱신

스케줄링 (Windows 작업 스케줄러):
  매주 1회 실행 권장
  schtasks /create /tn "TailscaleKeyRenew" /tr "python tailscale_key_manager.py" /sc weekly /d MON /st 09:00
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# ============================================================
# 설정
# ============================================================
TAILSCALE_API_TOKEN = "tskey-api-kTCQDpaFUc11CNTRL-6wCCgT14oTWuBsEi4eeoTWCwZ4nztF8L"
TAILSCALE_TAILNET = "-"  # "-" = 기본 tailnet
TAILSCALE_API_BASE = "https://api.tailscale.com/api/v2"

# WellcomLAND 서버 (authkey를 DB에 반영할 때 사용)
WELLCOMLAND_API = "http://log.wellcomll.org:8000"
WELLCOMLAND_ADMIN_USER = "admin"
WELLCOMLAND_ADMIN_PASS = "admin"

# installer.iss 경로 (자동 업데이트용)
INSTALLER_ISS_PATH = Path(__file__).parent.parent / "build" / "installer.iss"

# 갱신 기준 (만료 N일 이내면 새 키 생성)
DEFAULT_RENEW_DAYS = 30


def log(msg: str):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        print(f"[{timestamp}] {msg}")
    except UnicodeEncodeError:
        # Windows cp949 인코딩 문제 우회
        safe = msg.encode('ascii', errors='replace').decode('ascii')
        print(f"[{timestamp}] {safe}")


# ============================================================
# Tailscale API
# ============================================================
def get_headers():
    return {"Authorization": f"Bearer {TAILSCALE_API_TOKEN}"}


def list_auth_keys() -> list[dict]:
    """현재 authkey 목록 조회"""
    resp = requests.get(
        f"{TAILSCALE_API_BASE}/tailnet/{TAILSCALE_TAILNET}/keys",
        headers=get_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("keys", [])


def get_reusable_keys(keys: list[dict]) -> list[dict]:
    """Reusable authkey만 필터"""
    result = []
    for k in keys:
        caps = k.get("capabilities", {})
        if caps.get("devices", {}).get("create", {}).get("reusable", False):
            result.append(k)
    return result


def parse_expiry(expires_str: str) -> datetime | None:
    """ISO 8601 만료일 파싱"""
    if not expires_str:
        return None
    try:
        return datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
    except Exception:
        return None


def create_new_authkey(description: str = "WellcomLAND-auto") -> dict:
    """새 Reusable authkey 생성 (90일)"""
    resp = requests.post(
        f"{TAILSCALE_API_BASE}/tailnet/{TAILSCALE_TAILNET}/keys",
        headers=get_headers(),
        json={
            "capabilities": {
                "devices": {
                    "create": {
                        "reusable": True,
                        "ephemeral": False,
                    }
                }
            },
            "expirySeconds": 7776000,  # 90일
            "description": description,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def delete_key(key_id: str):
    """authkey 삭제"""
    resp = requests.delete(
        f"{TAILSCALE_API_BASE}/tailnet/{TAILSCALE_TAILNET}/keys/{key_id}",
        headers=get_headers(),
        timeout=15,
    )
    resp.raise_for_status()


# ============================================================
# WellcomLAND 서버 연동
# ============================================================
def update_server_authkey(new_authkey: str) -> bool:
    """WellcomLAND 서버 DB에 새 authkey 반영"""
    try:
        # 로그인
        login_resp = requests.post(
            f"{WELLCOMLAND_API}/api/auth/login",
            json={"username": WELLCOMLAND_ADMIN_USER, "password": WELLCOMLAND_ADMIN_PASS},
            timeout=10,
        )
        if login_resp.status_code != 200:
            log(f"[WARN] 서버 로그인 실패: {login_resp.status_code}")
            return False

        token = login_resp.json().get("token", "")

        # authkey 업데이트
        update_resp = requests.put(
            f"{WELLCOMLAND_API}/api/admin/tailscale/authkey",
            headers={"Authorization": f"Bearer {token}"},
            json={"authkey": new_authkey},
            timeout=10,
        )
        if update_resp.status_code == 200:
            log("[OK] 서버 DB authkey 업데이트 완료")
            return True
        elif update_resp.status_code == 404:
            log("[WARN] 서버에 /api/admin/tailscale/authkey 엔드포인트 없음 (v1.9.0 필요)")
            return False
        else:
            log(f"[WARN] 서버 authkey 업데이트 실패: {update_resp.status_code}")
            return False
    except Exception as e:
        log(f"[WARN] 서버 연결 실패: {e}")
        return False


# ============================================================
# installer.iss 업데이트
# ============================================================
def update_installer_iss(new_authkey: str) -> bool:
    """installer.iss의 TailscaleAuthKey 값 업데이트"""
    if not INSTALLER_ISS_PATH.exists():
        log(f"[WARN] installer.iss 없음: {INSTALLER_ISS_PATH}")
        return False

    content = INSTALLER_ISS_PATH.read_text(encoding="utf-8")
    pattern = r'(#define TailscaleAuthKey\s+")[^"]*(")'
    new_content, count = re.subn(pattern, rf'\g<1>{new_authkey}\2', content)

    if count == 0:
        log("[WARN] installer.iss에 TailscaleAuthKey 정의를 찾을 수 없음")
        return False

    INSTALLER_ISS_PATH.write_text(new_content, encoding="utf-8")
    log(f"[OK] installer.iss 업데이트 완료: {INSTALLER_ISS_PATH}")
    return True


# ============================================================
# 메인 로직
# ============================================================
def check_and_renew(renew_days: int = DEFAULT_RENEW_DAYS, force: bool = False, status_only: bool = False):
    """authkey 상태 확인 및 자동 갱신"""
    log("=" * 50)
    log("Tailscale AuthKey 관리 도구")
    log("=" * 50)

    # 1. 현재 키 조회
    log("키 목록 조회 중...")
    try:
        all_keys = list_auth_keys()
    except requests.RequestException as e:
        log(f"[FAIL] API 호출 실패: {e}")
        sys.exit(1)

    reusable_keys = get_reusable_keys(all_keys)
    now = datetime.now(timezone.utc)

    log(f"전체 키: {len(all_keys)}개, Reusable: {len(reusable_keys)}개")
    log("")

    # 2. 키 상태 출력
    best_key = None
    best_remaining = -1

    for k in all_keys:
        key_id = k.get("id", "?")
        desc = k.get("description", "")
        expires = k.get("expires", "")
        reusable = k.get("capabilities", {}).get("devices", {}).get("create", {}).get("reusable", False)
        key_value = k.get("key", "")  # 보통 생성 직후만 노출

        exp_dt = parse_expiry(expires)
        if exp_dt:
            remaining = (exp_dt - now).days
            exp_str = f"{remaining}일 남음 ({exp_dt.strftime('%Y-%m-%d')})"
        else:
            remaining = -1
            exp_str = "만료일 없음"

        tag = "[R]" if reusable else "[S]"
        log(f"  {tag} [{key_id[:12]}...] {desc or '(설명없음)'}")
        log(f"     Reusable: {reusable}, 만료: {exp_str}")

        if reusable and remaining > best_remaining:
            best_key = k
            best_remaining = remaining

    log("")

    if status_only:
        if best_key:
            log(f"[OK] 최적 Reusable Key: 만료까지 {best_remaining}일")
        else:
            log("[FAIL] Reusable Key 없음!")
        return

    # 3. 갱신 필요 여부 판단
    needs_renew = force
    if not force:
        if best_remaining < 0:
            log("Reusable Key 없음 → 새 키 생성 필요")
            needs_renew = True
        elif best_remaining <= renew_days:
            log(f"만료 임박 ({best_remaining}일 남음, 기준: {renew_days}일) → 갱신 필요")
            needs_renew = True
        else:
            log(f"[OK] 키 유효 (만료까지 {best_remaining}일, 기준: {renew_days}일 이내 시 갱신)")
            return

    if not needs_renew:
        return

    # 4. 새 키 생성
    log("")
    log("새 Reusable AuthKey 생성 중...")
    try:
        new_key_data = create_new_authkey()
        new_authkey = new_key_data.get("key", "")
        new_expires = new_key_data.get("expires", "")

        if not new_authkey:
            log("[FAIL] 응답에 key 없음")
            sys.exit(1)

        exp_dt = parse_expiry(new_expires)
        exp_str = exp_dt.strftime("%Y-%m-%d") if exp_dt else "?"
        log(f"[OK] 새 키 생성 완료!")
        log(f"  키: {new_authkey[:30]}...")
        log(f"  만료: {exp_str}")
    except requests.RequestException as e:
        log(f"[FAIL] 키 생성 실패: {e}")
        sys.exit(1)

    # 5. WellcomLAND 서버 DB 업데이트
    log("")
    log("WellcomLAND 서버 DB 업데이트 중...")
    update_server_authkey(new_authkey)

    # 6. installer.iss 업데이트
    log("")
    log("installer.iss 업데이트 중...")
    update_installer_iss(new_authkey)

    # 7. 요약
    log("")
    log("=" * 50)
    log("완료! 새 AuthKey가 적용되었습니다.")
    log(f"  키: {new_authkey[:30]}...")
    log(f"  만료: {exp_str}")
    log("")
    log("다음 단계:")
    log("  1. installer.iss가 업데이트 → PyInstaller + Inno Setup 재빌드 필요")
    log("  2. 서버 DB 업데이트 성공 시 → 클라이언트 자동 반영")
    log("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="Tailscale AuthKey 자동 갱신 관리 도구")
    parser.add_argument("--status", action="store_true", help="상태만 확인 (갱신 안 함)")
    parser.add_argument("--force", action="store_true", help="강제 새 키 생성")
    parser.add_argument("--renew-days", type=int, default=DEFAULT_RENEW_DAYS,
                        help=f"만료 N일 이내 시 갱신 (기본: {DEFAULT_RENEW_DAYS})")
    args = parser.parse_args()

    check_and_renew(
        renew_days=args.renew_days,
        force=args.force,
        status_only=args.status,
    )


if __name__ == "__main__":
    main()
