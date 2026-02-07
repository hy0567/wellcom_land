"""
Pydantic 모델 (요청/응답 스키마)
"""
from typing import Optional, List
from pydantic import BaseModel


# === Auth ===
class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user: "UserInfo"


class UserInfo(BaseModel):
    id: int
    username: str
    role: str
    display_name: Optional[str] = None


# === Users ===
class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "user"
    display_name: Optional[str] = None
    cloud_quota: Optional[int] = 0  # bytes. 0=비활성, -1=무제한, >0=제한


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None
    cloud_quota: Optional[int] = None  # None=변경없음, -1=무제한, 0=비활성


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    display_name: Optional[str] = None
    is_active: bool
    created_at: Optional[str] = None
    last_login: Optional[str] = None
    cloud_quota: Optional[int] = None  # None=무제한, 0=비활성
    cloud_used: int = 0  # 사용 중인 용량 (bytes)


# === Devices ===
class DeviceCreate(BaseModel):
    name: str
    ip: str
    port: int = 22
    web_port: int = 80
    username: str = "root"
    password: str = "luckfox"
    group_id: Optional[int] = None
    description: Optional[str] = None


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    ip: Optional[str] = None
    port: Optional[int] = None
    web_port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    group_id: Optional[int] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class DeviceResponse(BaseModel):
    id: int
    name: str
    ip: str
    port: int
    web_port: int
    username: str
    password: str
    group_id: Optional[int] = None
    group_name: Optional[str] = None
    description: Optional[str] = None
    is_active: bool


# === Groups ===
class GroupCreate(BaseModel):
    name: str
    description: Optional[str] = None


class GroupResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None


# === Device Assignment ===
class DeviceAssign(BaseModel):
    device_ids: List[int]
    permission: str = "control"


# === Files (Cloud Drive) ===
class FileResponse(BaseModel):
    id: int
    filename: str
    size: int
    uploaded_at: Optional[str] = None


class QuotaResponse(BaseModel):
    quota: Optional[int] = None  # None=무제한, 0=비활성
    used: int = 0
    remaining: Optional[int] = None  # None=무제한
