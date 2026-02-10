"""
WellcomLAND API 서버
FastAPI + MySQL + JWT 인증
"""
import os
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse as FastAPIFileResponse

from auth import (
    hash_password, verify_password, create_token,
    get_current_user, require_admin,
)
from database import get_db
from config import UPLOAD_DIR, MAX_FILE_SIZE, TAILSCALE_AUTHKEY, TAILSCALE_API_TOKEN, TAILSCALE_TAILNET
from models import (
    LoginRequest, LoginResponse, UserInfo,
    UserCreate, UserUpdate, UserResponse,
    DeviceCreate, DeviceUpdate, DeviceResponse,
    GroupCreate, GroupResponse,
    DeviceAssign,
    FileResponse, QuotaResponse,
)

app = FastAPI(title="WellcomLAND API", version="1.9.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_cloud_used(cur, user_id: int) -> int:
    """사용자의 클라우드 사용량 계산 (bytes)"""
    cur.execute(
        "SELECT COALESCE(SUM(size), 0) AS used FROM files WHERE user_id = %s",
        (user_id,),
    )
    return cur.fetchone()["used"]


# ===========================================================
# 시작 시 admin 비밀번호 해싱 (최초 1회)
# ===========================================================
@app.on_event("startup")
def startup_init():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, password FROM users WHERE username = 'admin'")
            admin = cur.fetchone()
            if admin and not admin["password"].startswith("$2b$"):
                hashed = hash_password("admin")
                cur.execute("UPDATE users SET password = %s WHERE id = %s", (hashed, admin["id"]))
                print("[Init] admin 비밀번호 bcrypt 해싱 완료 (초기 비밀번호: admin)")

            # files 테이블 자동 생성
            cur.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    filename VARCHAR(255) NOT NULL,
                    stored_name VARCHAR(255) NOT NULL,
                    size BIGINT NOT NULL,
                    uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            print("[Init] files 테이블 확인 완료")

            # cloud_quota 컬럼 자동 마이그레이션
            try:
                cur.execute("ALTER TABLE users ADD COLUMN cloud_quota BIGINT DEFAULT 0")
                print("[Init] users.cloud_quota 컬럼 추가 완료")
            except Exception:
                pass  # 이미 존재

            # admin 사용자에게 무제한 쿼타 설정
            cur.execute("UPDATE users SET cloud_quota = NULL WHERE role = 'admin' AND cloud_quota = 0")

    # 업로드 디렉토리 생성
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    print(f"[Init] 업로드 디렉토리: {UPLOAD_DIR}")


# ===========================================================
# Auth
# ===========================================================
@app.post("/api/auth/login", response_model=LoginResponse)
def login(req: LoginRequest):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, password, role, display_name, is_active FROM users WHERE username = %s",
                (req.username,),
            )
            user = cur.fetchone()

    if not user:
        raise HTTPException(status_code=401, detail="사용자를 찾을 수 없습니다")
    if not user["is_active"]:
        raise HTTPException(status_code=401, detail="비활성화된 계정입니다")
    if not verify_password(req.password, user["password"]):
        raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다")

    # 마지막 로그인 시간 업데이트
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET last_login = %s WHERE id = %s",
                (datetime.now(timezone.utc), user["id"]),
            )

    token = create_token(user["id"], user["username"], user["role"])
    return LoginResponse(
        token=token,
        user=UserInfo(
            id=user["id"],
            username=user["username"],
            role=user["role"],
            display_name=user["display_name"],
        ),
    )


@app.get("/api/auth/me", response_model=UserInfo)
def get_me(user: dict = Depends(get_current_user)):
    return UserInfo(
        id=user["id"],
        username=user["username"],
        role=user["role"],
        display_name=user["display_name"],
    )


# ===========================================================
# Devices (로그인 사용자 → 자기 기기만)
# ===========================================================
@app.get("/api/devices", response_model=list[DeviceResponse])
def get_my_devices(user: dict = Depends(get_current_user)):
    with get_db() as conn:
        with conn.cursor() as cur:
            if user["role"] == "admin":
                # admin은 전체 기기 조회
                cur.execute("""
                    SELECT d.*, g.name AS group_name
                    FROM devices d
                    LEFT JOIN device_groups g ON d.group_id = g.id
                    WHERE d.is_active = TRUE
                    ORDER BY d.name
                """)
            else:
                # 일반 사용자는 할당된 기기만
                cur.execute("""
                    SELECT d.*, g.name AS group_name
                    FROM devices d
                    JOIN user_devices ud ON d.id = ud.device_id
                    LEFT JOIN device_groups g ON d.group_id = g.id
                    WHERE ud.user_id = %s AND d.is_active = TRUE
                    ORDER BY d.name
                """, (user["id"],))
            devices = cur.fetchall()
    return [DeviceResponse(**d) for d in devices]


# ===========================================================
# Admin: 기기 관리
# ===========================================================
@app.get("/api/admin/devices", response_model=list[DeviceResponse])
def admin_get_all_devices(user: dict = Depends(require_admin)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT d.*, g.name AS group_name
                FROM devices d
                LEFT JOIN device_groups g ON d.group_id = g.id
                ORDER BY d.name
            """)
            devices = cur.fetchall()
    return [DeviceResponse(**d) for d in devices]


@app.post("/api/admin/devices", response_model=DeviceResponse)
def admin_create_device(req: DeviceCreate, user: dict = Depends(require_admin)):
    with get_db() as conn:
        with conn.cursor() as cur:
            # 중복 체크 (이름 또는 IP)
            cur.execute(
                "SELECT id FROM devices WHERE name = %s OR ip = %s",
                (req.name, req.ip),
            )
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="이미 존재하는 기기입니다 (이름 또는 IP 중복)")

            cur.execute(
                """INSERT INTO devices (name, ip, port, web_port, username, password, group_id, description)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (req.name, req.ip, req.port, req.web_port, req.username, req.password, req.group_id, req.description),
            )
            device_id = cur.lastrowid
            cur.execute("""
                SELECT d.*, g.name AS group_name
                FROM devices d
                LEFT JOIN device_groups g ON d.group_id = g.id
                WHERE d.id = %s
            """, (device_id,))
            device = cur.fetchone()
    return DeviceResponse(**device)


@app.put("/api/admin/devices/{device_id}", response_model=DeviceResponse)
def admin_update_device(device_id: int, req: DeviceUpdate, user: dict = Depends(require_admin)):
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="수정할 항목이 없습니다")

    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [device_id]

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE devices SET {set_clause} WHERE id = %s", values)
            cur.execute("""
                SELECT d.*, g.name AS group_name
                FROM devices d
                LEFT JOIN device_groups g ON d.group_id = g.id
                WHERE d.id = %s
            """, (device_id,))
            device = cur.fetchone()
    if not device:
        raise HTTPException(status_code=404, detail="기기를 찾을 수 없습니다")
    return DeviceResponse(**device)


@app.delete("/api/admin/devices/{device_id}")
def admin_delete_device(device_id: int, user: dict = Depends(require_admin)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM devices WHERE id = %s", (device_id,))
    return {"message": "삭제되었습니다"}


# ===========================================================
# Admin: 사용자 관리
# ===========================================================
@app.get("/api/admin/users", response_model=list[UserResponse])
def admin_get_users(user: dict = Depends(require_admin)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT u.id, u.username, u.role, u.display_name, u.is_active,
                       u.created_at, u.last_login, u.cloud_quota,
                       COALESCE((SELECT SUM(f.size) FROM files f WHERE f.user_id = u.id), 0) AS cloud_used
                FROM users u ORDER BY u.id
            """)
            users = cur.fetchall()
    result = []
    for u in users:
        result.append(UserResponse(
            id=u["id"],
            username=u["username"],
            role=u["role"],
            display_name=u["display_name"],
            is_active=u["is_active"],
            created_at=str(u["created_at"]) if u["created_at"] else None,
            last_login=str(u["last_login"]) if u["last_login"] else None,
            cloud_quota=u["cloud_quota"],
            cloud_used=u["cloud_used"],
        ))
    return result


@app.post("/api/admin/users", response_model=UserResponse)
def admin_create_user(req: UserCreate, user: dict = Depends(require_admin)):
    hashed = hash_password(req.password)
    # -1 → NULL (무제한)
    quota_val = None if req.cloud_quota == -1 else (req.cloud_quota or 0)
    with get_db() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO users (username, password, role, display_name, cloud_quota) VALUES (%s, %s, %s, %s, %s)",
                    (req.username, hashed, req.role, req.display_name, quota_val),
                )
            except Exception:
                raise HTTPException(status_code=409, detail="이미 존재하는 사용자입니다")
            user_id = cur.lastrowid
            cur.execute("""
                SELECT u.*, COALESCE((SELECT SUM(f.size) FROM files f WHERE f.user_id = u.id), 0) AS cloud_used
                FROM users u WHERE u.id = %s
            """, (user_id,))
            new_user = cur.fetchone()
    return UserResponse(
        id=new_user["id"],
        username=new_user["username"],
        role=new_user["role"],
        display_name=new_user["display_name"],
        is_active=new_user["is_active"],
        created_at=str(new_user["created_at"]) if new_user["created_at"] else None,
        last_login=None,
        cloud_quota=new_user["cloud_quota"],
        cloud_used=new_user["cloud_used"],
    )


@app.put("/api/admin/users/{user_id}", response_model=UserResponse)
def admin_update_user(user_id: int, req: UserUpdate, admin: dict = Depends(require_admin)):
    updates = {}
    if req.display_name is not None:
        updates["display_name"] = req.display_name
    if req.role is not None:
        updates["role"] = req.role
    if req.is_active is not None:
        updates["is_active"] = req.is_active
    if req.password is not None:
        updates["password"] = hash_password(req.password)
    if req.cloud_quota is not None:
        # -1 → NULL (무제한), 0 → 비활성, >0 → 제한
        updates["cloud_quota"] = None if req.cloud_quota == -1 else req.cloud_quota

    if not updates:
        raise HTTPException(status_code=400, detail="수정할 항목이 없습니다")

    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [user_id]

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE users SET {set_clause} WHERE id = %s", values)
            cur.execute("""
                SELECT u.*, COALESCE((SELECT SUM(f.size) FROM files f WHERE f.user_id = u.id), 0) AS cloud_used
                FROM users u WHERE u.id = %s
            """, (user_id,))
            user = cur.fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    return UserResponse(
        id=user["id"],
        username=user["username"],
        role=user["role"],
        display_name=user["display_name"],
        is_active=user["is_active"],
        created_at=str(user["created_at"]) if user["created_at"] else None,
        last_login=str(user["last_login"]) if user["last_login"] else None,
        cloud_quota=user["cloud_quota"],
        cloud_used=user["cloud_used"],
    )


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, admin: dict = Depends(require_admin)):
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="자신의 계정은 삭제할 수 없습니다")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    return {"message": "삭제되었습니다"}


# ===========================================================
# Admin: 기기 할당
# ===========================================================
@app.get("/api/admin/users/{user_id}/devices")
def admin_get_user_devices(user_id: int, admin: dict = Depends(require_admin)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT d.id, d.name, d.ip, ud.permission
                FROM user_devices ud
                JOIN devices d ON ud.device_id = d.id
                WHERE ud.user_id = %s
                ORDER BY d.name
            """, (user_id,))
            return cur.fetchall()


@app.post("/api/admin/users/{user_id}/devices")
def admin_assign_devices(user_id: int, req: DeviceAssign, admin: dict = Depends(require_admin)):
    with get_db() as conn:
        with conn.cursor() as cur:
            # 기존 할당 제거 후 재할당
            cur.execute("DELETE FROM user_devices WHERE user_id = %s", (user_id,))
            for device_id in req.device_ids:
                cur.execute(
                    "INSERT INTO user_devices (user_id, device_id, permission) VALUES (%s, %s, %s)",
                    (user_id, device_id, req.permission),
                )
    return {"message": f"{len(req.device_ids)}개 기기가 할당되었습니다"}


# ===========================================================
# Admin: 그룹 관리
# ===========================================================
@app.get("/api/admin/groups", response_model=list[GroupResponse])
def admin_get_groups(user: dict = Depends(require_admin)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, description FROM device_groups ORDER BY name")
            return [GroupResponse(**g) for g in cur.fetchall()]


@app.post("/api/admin/groups", response_model=GroupResponse)
def admin_create_group(req: GroupCreate, user: dict = Depends(require_admin)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO device_groups (name, description) VALUES (%s, %s)", (req.name, req.description))
            return GroupResponse(id=cur.lastrowid, name=req.name, description=req.description)


# ===========================================================
# Files (클라우드 드라이브)
# ===========================================================
@app.post("/api/files/upload", response_model=FileResponse)
async def upload_file(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    """파일 업로드 (사용자별 폴더)"""
    # 파일 크기 체크
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail=f"파일 크기 제한 초과 ({MAX_FILE_SIZE // (1024*1024)}MB)")

    # 쿼타 체크
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT cloud_quota FROM users WHERE id = %s", (user["id"],))
            user_row = cur.fetchone()
            quota = user_row["cloud_quota"] if user_row else 0

            if quota == 0:
                raise HTTPException(status_code=403, detail="클라우드 저장소 접근 권한이 없습니다")

            if quota is not None:  # None = 무제한
                used = _get_cloud_used(cur, user["id"])
                if used + len(content) > quota:
                    remaining_mb = max(0, (quota - used)) // (1024 * 1024)
                    raise HTTPException(
                        status_code=413,
                        detail=f"클라우드 저장 용량 초과 (남은 용량: {remaining_mb}MB)"
                    )

    # 사용자별 디렉토리
    user_dir = os.path.join(UPLOAD_DIR, str(user["id"]))
    os.makedirs(user_dir, exist_ok=True)

    # 저장 (UUID + 원본 확장자)
    ext = os.path.splitext(file.filename)[1] if file.filename else ""
    stored_name = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(user_dir, stored_name)

    with open(file_path, "wb") as f:
        f.write(content)

    # DB 저장
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO files (user_id, filename, stored_name, size) VALUES (%s, %s, %s, %s)",
                (user["id"], file.filename, stored_name, len(content)),
            )
            file_id = cur.lastrowid
            cur.execute("SELECT * FROM files WHERE id = %s", (file_id,))
            row = cur.fetchone()

    return FileResponse(
        id=row["id"],
        filename=row["filename"],
        size=row["size"],
        uploaded_at=str(row["uploaded_at"]) if row["uploaded_at"] else None,
    )


@app.get("/api/files", response_model=list[FileResponse])
def get_my_files(user: dict = Depends(get_current_user)):
    """내 파일 목록"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM files WHERE user_id = %s ORDER BY uploaded_at DESC",
                (user["id"],),
            )
            rows = cur.fetchall()
    return [
        FileResponse(
            id=r["id"],
            filename=r["filename"],
            size=r["size"],
            uploaded_at=str(r["uploaded_at"]) if r["uploaded_at"] else None,
        )
        for r in rows
    ]


@app.get("/api/files/{file_id}/download")
def download_file(file_id: int, user: dict = Depends(get_current_user)):
    """파일 다운로드"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM files WHERE id = %s AND user_id = %s",
                (file_id, user["id"]),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")

    file_path = os.path.join(UPLOAD_DIR, str(user["id"]), row["stored_name"])
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="파일이 서버에서 삭제되었습니다")

    return FastAPIFileResponse(
        path=file_path,
        filename=row["filename"],
        media_type="application/octet-stream",
    )


@app.delete("/api/files/{file_id}")
def delete_file(file_id: int, user: dict = Depends(get_current_user)):
    """파일 삭제"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM files WHERE id = %s AND user_id = %s",
                (file_id, user["id"]),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")

    # 실제 파일 삭제
    file_path = os.path.join(UPLOAD_DIR, str(user["id"]), row["stored_name"])
    if os.path.exists(file_path):
        os.remove(file_path)

    # DB 삭제
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM files WHERE id = %s", (file_id,))

    return {"message": "삭제되었습니다"}


@app.get("/api/files/quota", response_model=QuotaResponse)
def get_my_quota(user: dict = Depends(get_current_user)):
    """내 클라우드 쿼타 조회"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT cloud_quota FROM users WHERE id = %s", (user["id"],))
            row = cur.fetchone()
            quota = row["cloud_quota"] if row else 0
            used = _get_cloud_used(cur, user["id"])

    remaining = None if quota is None else max(0, quota - used)
    return QuotaResponse(quota=quota, used=used, remaining=remaining)


# ===========================================================
# Tailscale 네트워크 관리 (VPN 연결은 Tailscale이 자동 처리)
# ===========================================================

@app.on_event("startup")
def init_tailscale_config():
    """tailscale_config 테이블 생성 (authkey 등 저장)"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tailscale_config (
                    `key` VARCHAR(50) PRIMARY KEY,
                    `value` TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
            """)
            # 환경변수 TAILSCALE_AUTHKEY가 있으면 DB에 반영
            if TAILSCALE_AUTHKEY:
                cur.execute("""
                    INSERT INTO tailscale_config (`key`, `value`)
                    VALUES ('authkey', %s)
                    ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)
                """, (TAILSCALE_AUTHKEY,))
                print(f"[Init] Tailscale authkey 환경변수에서 로드됨")
            # API 토큰도 DB에 저장
            if TAILSCALE_API_TOKEN:
                cur.execute("""
                    INSERT INTO tailscale_config (`key`, `value`)
                    VALUES ('api_token', %s)
                    ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)
                """, (TAILSCALE_API_TOKEN,))
            print("[Init] tailscale_config 테이블 확인 완료")

    # API 토큰이 있으면 authkey 자동 갱신 확인
    _auto_refresh_tailscale_authkey()


def _auto_refresh_tailscale_authkey():
    """Tailscale API로 authkey 만료 확인 및 자동 갱신

    API 토큰이 DB에 있으면:
    1. 현재 authkey 목록 조회
    2. 만료 30일 이내이면 새 authkey 자동 생성
    3. DB에 새 authkey 저장
    """
    import requests as _req
    from datetime import timedelta

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT `value` FROM tailscale_config WHERE `key` = 'api_token'")
                row = cur.fetchone()
                if not row or not row["value"]:
                    return
                api_token = row["value"]

        # Tailscale API: authkey 목록 조회
        headers = {"Authorization": f"Bearer {api_token}"}
        tailnet = TAILSCALE_TAILNET

        resp = _req.get(
            f"https://api.tailscale.com/api/v2/tailnet/{tailnet}/keys",
            headers=headers, timeout=10
        )
        if resp.status_code != 200:
            print(f"[Tailscale API] authkey 목록 조회 실패: {resp.status_code}")
            return

        keys = resp.json().get("keys", [])
        now = datetime.now(timezone.utc)

        # Reusable authkey 중 유효한 것 찾기
        valid_key = None
        needs_refresh = True
        for k in keys:
            if not k.get("capabilities", {}).get("devices", {}).get("create", {}).get("reusable", False):
                continue
            expires = k.get("expires", "")
            if expires:
                try:
                    # ISO 8601 파싱 (표준 라이브러리만 사용)
                    # Tailscale API: "2026-05-11T06:23:45Z" 형식
                    exp_str = expires.replace("Z", "+00:00")
                    exp_dt = datetime.fromisoformat(exp_str)
                    remaining = (exp_dt - now).days
                    if remaining > 30:
                        needs_refresh = False
                        valid_key = k
                        print(f"[Tailscale API] 유효한 authkey 있음 (만료까지 {remaining}일)")
                    else:
                        print(f"[Tailscale API] authkey 만료 임박 ({remaining}일 남음) → 갱신 필요")
                except Exception:
                    pass

        if not needs_refresh:
            return

        # 새 authkey 생성
        print("[Tailscale API] 새 Reusable authkey 생성 중...")
        create_resp = _req.post(
            f"https://api.tailscale.com/api/v2/tailnet/{tailnet}/keys",
            headers=headers,
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
                "description": "WellcomLAND-auto",
            },
            timeout=10
        )

        if create_resp.status_code in (200, 201):
            new_key_data = create_resp.json()
            new_authkey = new_key_data.get("key", "")
            if new_authkey:
                # DB에 새 authkey 저장
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO tailscale_config (`key`, `value`)
                            VALUES ('authkey', %s)
                            ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)
                        """, (new_authkey,))
                print(f"[Tailscale API] 새 authkey 생성 및 DB 저장 완료")
            else:
                print("[Tailscale API] 응답에 key 없음")
        else:
            print(f"[Tailscale API] authkey 생성 실패: {create_resp.status_code} {create_resp.text}")

    except Exception as e:
        print(f"[Tailscale API] authkey 자동 갱신 오류 (무시): {e}")


@app.get("/api/tailscale/status")
def tailscale_status(user: dict = Depends(get_current_user)):
    """Tailscale 네트워크 상태 조회"""
    return {
        "vpn": "tailscale",
        "name": "WellcomLAND",
        "status": "Tailscale manages connections automatically",
    }


@app.get("/api/tailscale/authkey")
def get_tailscale_authkey(user: dict = Depends(get_current_user)):
    """Tailscale authkey 조회 (로그인된 사용자만)

    클라이언트가 이 키로 tailscale up --authkey=<key> 실행하여
    회사 공용 tailnet에 자동 참여.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT `value` FROM tailscale_config WHERE `key` = 'authkey'")
            row = cur.fetchone()

    if not row or not row["value"]:
        return {"authkey": "", "message": "authkey 미설정"}

    return {"authkey": row["value"]}


@app.put("/api/admin/tailscale/authkey")
def set_tailscale_authkey(data: dict, user: dict = Depends(require_admin)):
    """Tailscale authkey 설정 (admin 전용)

    Body: {"authkey": "tskey-auth-xxx"}

    Tailscale 관리 콘솔에서 생성한 Reusable authkey를 등록.
    모든 클라이언트가 이 키로 회사 tailnet에 자동 참여.
    """
    authkey = data.get("authkey", "").strip()
    if not authkey:
        raise HTTPException(status_code=400, detail="authkey 필수")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tailscale_config (`key`, `value`)
                VALUES ('authkey', %s)
                ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)
            """, (authkey,))

    return {"status": "ok", "message": "Tailscale authkey 설정 완료"}


# ===========================================================
# KVM 레지스트리 (원격 장치 공유)
# ===========================================================
@app.on_event("startup")
def init_kvm_registry():
    """kvm_registry 테이블 생성"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS kvm_registry (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    kvm_local_ip VARCHAR(45) NOT NULL,
                    kvm_port INT DEFAULT 80,
                    kvm_name VARCHAR(100) DEFAULT '',
                    relay_ip VARCHAR(45) NOT NULL COMMENT '관제PC의 Tailscale IP',
                    relay_port INT NOT NULL COMMENT '관제PC의 TCP 프록시 포트',
                    udp_relay_port INT DEFAULT NULL COMMENT 'WebRTC UDP 릴레이 포트',
                    owner_username VARCHAR(50) NOT NULL COMMENT '등록한 관제PC 사용자',
                    location VARCHAR(100) DEFAULT '' COMMENT '관제 위치명',
                    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_online BOOLEAN DEFAULT TRUE,
                    UNIQUE KEY uq_relay (relay_ip, relay_port)
                )
            """)
            # udp_relay_port 컬럼 추가 (기존 테이블 호환)
            try:
                cur.execute("""
                    ALTER TABLE kvm_registry ADD COLUMN udp_relay_port INT DEFAULT NULL
                    COMMENT 'WebRTC UDP 릴레이 포트' AFTER relay_port
                """)
                print("[Init] kvm_registry: udp_relay_port 컬럼 추가")
            except Exception:
                pass  # 이미 존재
            print("[Init] kvm_registry 테이블 확인 완료")


@app.post("/api/kvm/register")
def register_kvm(data: dict, user: dict = Depends(get_current_user)):
    """관제 PC가 발견한 KVM 장치를 서버에 등록

    Body: {
        "devices": [
            {
                "kvm_local_ip": "192.168.68.100",
                "kvm_port": 80,
                "kvm_name": "KVM-100",
                "relay_port": 18100
            }
        ],
        "relay_ip": "100.64.0.2",
        "location": "본사 관제실"
    }
    """
    devices = data.get("devices", [])
    relay_ip = data.get("relay_ip", "").strip() or data.get("relay_zt_ip", "").strip()
    location = data.get("location", "")

    if not relay_ip or not devices:
        raise HTTPException(status_code=400, detail="relay_ip와 devices 필수")

    registered = 0
    with get_db() as conn:
        with conn.cursor() as cur:
            for dev in devices:
                kvm_local_ip = dev.get("kvm_local_ip", "")
                kvm_port = dev.get("kvm_port", 80)
                kvm_name = dev.get("kvm_name", f"KVM-{kvm_local_ip.split('.')[-1]}")
                relay_port = dev.get("relay_port", 0)
                udp_relay_port = dev.get("udp_relay_port")

                if not kvm_local_ip or not relay_port:
                    continue

                # UPSERT: 이미 있으면 업데이트
                cur.execute("""
                    INSERT INTO kvm_registry
                        (kvm_local_ip, kvm_port, kvm_name, relay_ip, relay_port,
                         udp_relay_port, owner_username, location, last_seen, is_online)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), TRUE)
                    ON DUPLICATE KEY UPDATE
                        kvm_local_ip = VALUES(kvm_local_ip),
                        kvm_port = VALUES(kvm_port),
                        kvm_name = VALUES(kvm_name),
                        udp_relay_port = VALUES(udp_relay_port),
                        owner_username = VALUES(owner_username),
                        location = VALUES(location),
                        last_seen = NOW(),
                        is_online = TRUE
                """, (kvm_local_ip, kvm_port, kvm_name, relay_ip, relay_port,
                      udp_relay_port, user["username"], location))
                registered += 1

    return {"status": "ok", "registered": registered}


@app.get("/api/kvm/list")
def list_kvm_devices(user: dict = Depends(get_current_user)):
    """등록된 모든 원격 KVM 장치 목록 (Tailscale 경유 접근 정보 포함)"""
    with get_db() as conn:
        with conn.cursor() as cur:
            # 5분 이상 미갱신 → offline 처리
            cur.execute("""
                UPDATE kvm_registry SET is_online = FALSE
                WHERE last_seen < DATE_SUB(NOW(), INTERVAL 5 MINUTE)
            """)

            if user["role"] == "admin":
                cur.execute("SELECT * FROM kvm_registry ORDER BY location, kvm_name")
            else:
                cur.execute("""
                    SELECT * FROM kvm_registry
                    WHERE owner_username = %s
                    ORDER BY kvm_name
                """, (user["username"],))

            rows = cur.fetchall()

    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "kvm_name": r["kvm_name"],
            "kvm_local_ip": r["kvm_local_ip"],
            "kvm_port": r["kvm_port"],
            "relay_ip": r.get("relay_ip", r.get("relay_zt_ip", "")),
            "relay_port": r["relay_port"],
            "udp_relay_port": r.get("udp_relay_port"),
            "access_url": f"http://{r.get('relay_ip', r.get('relay_zt_ip', ''))}:{r['relay_port']}",
            "owner": r["owner_username"],
            "location": r["location"],
            "is_online": bool(r["is_online"]),
            "last_seen": str(r["last_seen"]) if r["last_seen"] else None,
        })

    return {"devices": result}


@app.post("/api/kvm/heartbeat")
def kvm_heartbeat(data: dict, user: dict = Depends(get_current_user)):
    """관제 PC가 주기적으로 온라인 상태 갱신

    Body: {"relay_ip": "100.64.0.2"}
    """
    relay_ip = data.get("relay_ip", "").strip() or data.get("relay_zt_ip", "").strip()
    if not relay_ip:
        raise HTTPException(status_code=400, detail="relay_ip 필수")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE kvm_registry SET last_seen = NOW(), is_online = TRUE
                WHERE relay_ip = %s AND owner_username = %s
            """, (relay_ip, user["username"]))

    return {"status": "ok"}


@app.delete("/api/kvm/{kvm_id}")
def delete_kvm(kvm_id: int, user: dict = Depends(require_admin)):
    """KVM 레지스트리에서 삭제 (admin)"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM kvm_registry WHERE id = %s", (kvm_id,))
    return {"message": "삭제되었습니다"}


# ===========================================================
# Version (공개)
# ===========================================================
@app.get("/api/version")
def get_version():
    return {"version": "1.9.0", "app_name": "WellcomLAND"}


# ===========================================================
# Health
# ===========================================================
@app.get("/api/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    from config import API_HOST, API_PORT
    uvicorn.run("main:app", host=API_HOST, port=API_PORT, reload=True)
