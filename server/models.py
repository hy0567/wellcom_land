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


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    display_name: Optional[str] = None
    is_active: bool
    created_at: Optional[str] = None
    last_login: Optional[str] = None


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
