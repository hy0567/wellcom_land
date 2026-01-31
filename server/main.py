"""
WellcomLAND API 서버
FastAPI + MySQL + JWT 인증
"""
from datetime import datetime, timezone
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from auth import (
    hash_password, verify_password, create_token,
    get_current_user, require_admin,
)
from database import get_db
from models import (
    LoginRequest, LoginResponse, UserInfo,
    UserCreate, UserUpdate, UserResponse,
    DeviceCreate, DeviceUpdate, DeviceResponse,
    GroupCreate, GroupResponse,
    DeviceAssign,
)

app = FastAPI(title="WellcomLAND API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
            cur.execute("SELECT id, username, role, display_name, is_active, created_at, last_login FROM users ORDER BY id")
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
        ))
    return result


@app.post("/api/admin/users", response_model=UserResponse)
def admin_create_user(req: UserCreate, user: dict = Depends(require_admin)):
    hashed = hash_password(req.password)
    with get_db() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO users (username, password, role, display_name) VALUES (%s, %s, %s, %s)",
                    (req.username, hashed, req.role, req.display_name),
                )
            except Exception:
                raise HTTPException(status_code=409, detail="이미 존재하는 사용자입니다")
            user_id = cur.lastrowid
            cur.execute("SELECT id, username, role, display_name, is_active, created_at, last_login FROM users WHERE id = %s", (user_id,))
            new_user = cur.fetchone()
    return UserResponse(
        id=new_user["id"],
        username=new_user["username"],
        role=new_user["role"],
        display_name=new_user["display_name"],
        is_active=new_user["is_active"],
        created_at=str(new_user["created_at"]) if new_user["created_at"] else None,
        last_login=None,
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

    if not updates:
        raise HTTPException(status_code=400, detail="수정할 항목이 없습니다")

    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [user_id]

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE users SET {set_clause} WHERE id = %s", values)
            cur.execute("SELECT id, username, role, display_name, is_active, created_at, last_login FROM users WHERE id = %s", (user_id,))
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
# Version (공개)
# ===========================================================
@app.get("/api/version")
def get_version():
    return {"version": "1.2.0", "app_name": "WellcomLAND"}


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
