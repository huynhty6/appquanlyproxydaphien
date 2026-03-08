"""Pydantic schemas."""

from typing import Optional
from pydantic import BaseModel, EmailStr


# ---------- Auth ----------

class RegisterRequest(BaseModel):
    full_name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str = ""


class UserResponse(BaseModel):
    id: str
    full_name: str
    email: str
    phone: str = ""
    country: str = ""
    avatar_url: str = ""
    license_key: str = ""
    is_active: bool = True
    role: str = "user"
    created_at: Optional[str] = None


# ---------- MikroTik connection (port ẩn, user chỉ nhập host/user/pass) ----------

class MikroTikConnectRequest(BaseModel):
    host: str
    username: str
    password: str


class MikroTikStatusResponse(BaseModel):
    connected: bool
    identity: str = ""
    version: str = ""
    uptime: str = ""
    cpu_load: str = ""
    error: str = ""


# ---------- Change Proxy Credentials ----------

class ChangeProxyCredsRequest(BaseModel):
    host: str
    username: str
    password: str
    envlist: str = ""
    container_id: str = ""
    mode: str = "manual"  # manual | random | clear
    proxy_login: str = ""
    proxy_password: str = ""


class ChangeAllProxyCredsRequest(BaseModel):
    host: str
    username: str
    password: str
    mode: str = "manual"  # manual | random | clear
    proxy_login: str = ""
    proxy_password: str = ""


# ---------- Proxy ----------

class ProxyResponse(BaseModel):
    id: str
    ip: str
    http_port: int = 10001
    socks_port: int = 20001
    username: str = ""
    password: str = ""
    status: str = "online"
    location: str = ""
    assigned_user: str = ""
    task: str = ""
    proxy_type: str = "HTTP"


class ProxyCreate(BaseModel):
    ip: str
    http_port: int = 10001
    socks_port: int = 20001
    username: str = ""
    password: str = ""
    location: str = ""
    proxy_type: str = "HTTP"


class ProxyAssignRequest(BaseModel):
    proxy_id: str
    assigned_user: str
    task: str = ""


# ---------- Rotation ----------

class RotationResponse(BaseModel):
    id: str
    proxy_id: str
    old_ip: str
    new_ip: str
    status: str
    rotated_at: Optional[str] = None


class RotateRequest(BaseModel):
    interface: str = "pppoe-out1"


# ---------- Settings ----------

class SettingsResponse(BaseModel):
    language: str = "vi"
    timezone: str = "Asia/Ho_Chi_Minh"
    default_protocol: str = "HTTP"
    pppoe_username: str = ""
    pppoe_password: str = ""
    two_factor_enabled: bool = False
    ip_whitelist_enabled: bool = False


class SettingsUpdate(BaseModel):
    language: str | None = None
    timezone: str | None = None
    default_protocol: str | None = None
    pppoe_username: str | None = None
    pppoe_password: str | None = None
    two_factor_enabled: bool | None = None
    ip_whitelist_enabled: bool | None = None


# ---------- API Key ----------

class ApiKeyResponse(BaseModel):
    id: str
    name: str
    key: str
    status: str = "active"
    used_requests: int = 0
    limit_requests: int = 10000
    created_at: Optional[str] = None


class ApiKeyCreate(BaseModel):
    name: str
    limit_requests: int = 10000


# ---------- Profile ----------

class ProfileUpdate(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    country: str | None = None


class LicenseActivate(BaseModel):
    license_key: str


# ---------- Dashboard ----------

class DashboardStats(BaseModel):
    total_proxy: int
    active_proxy: int
    offline_proxy: int


# ---------- Proxy Check ----------

class ProxyCheckRequest(BaseModel):
    proxy_ids: list[str] | None = None
    timeout_seconds: int = 10


class ProxyCheckResult(BaseModel):
    ip: str
    port: int
    status: str
    latency_ms: float
    country: str
    last_checked: str


# ---------- Admin ----------

class AdminUserResponse(BaseModel):
    id: str
    full_name: str
    email: str
    phone: str = ""
    country: str = ""
    license_key: str = ""
    license_expires: Optional[str] = None
    license_tier: str = "personal"  # personal | business | test
    is_active: bool = True
    role: str = "user"
    created_at: Optional[str] = None
    mikrotik_host: str = ""
    mikrotik_username: str = ""
    mikrotik_password: str = ""
    mikrotik_identity: str = ""
    mikrotik_version: str = ""
    mikrotik_uptime: str = ""
    mikrotik_connected: bool = False
    mikrotik_connected_at: Optional[str] = None
    last_login_at: Optional[str] = None
    last_login_ip: str = ""
    last_login_location: str = ""


class AdminUserCreate(BaseModel):
    full_name: str
    email: EmailStr
    password: str
    role: str = "user"
    is_active: bool = True


class AdminUserUpdate(BaseModel):
    full_name: str | None = None
    email: EmailStr | None = None
    password: str | None = None
    phone: str | None = None
    country: str | None = None
    role: str | None = None
    is_active: bool | None = None
    license_key: str | None = None
    license_expires: str | None = None
    license_tier: str | None = None  # personal | business | test


class AdminLicenseKeyCreate(BaseModel):
    key: str | None = None
    tier: str = "personal"  # personal | business | test
    duration_days: int | None = None  # chỉ dùng cho test
    expires_at: str | None = None  # ISO date, chỉ cho test
    user_id: str | None = None
