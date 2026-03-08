"""
Quick API – Xoay IP qua link trình duyệt (SQLite + API key).

GET endpoints: dán link trình duyệt, auth bằng ?key=htpx_xxx
POST endpoints: app desktop gọi, auth bằng Firebase Bearer token
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth import get_current_user
from mikrotik_client import MikroTikClient
from quick_db import (
    check_rate_limit,
    create_api_key,
    delete_api_key,
    get_mikrotik_creds_by_key,
    increment_usage,
    list_api_keys,
    log_request,
    save_mikrotik_creds,
    toggle_api_key,
)

router = APIRouter(prefix="/quick", tags=["Quick API"])


# ─── Helpers ─────────────────────────────────────────────────


def _validate_key(key: str) -> dict:
    """Validate API key, check status + limits + rate. Trả creds dict."""
    data = get_mikrotik_creds_by_key(key)
    if not data:
        return {"error": True, "status": "error", "message": "API key khong hop le hoac chua dang ky MikroTik"}

    if data["key_status"] != "active":
        return {"error": True, "status": "error", "message": "API key da bi tam dung"}

    if data["used_requests"] >= data["limit_requests"]:
        return {"error": True, "status": "error", "message": "Da het gioi han request"}

    if not check_rate_limit(data["key_id"]):
        return {"error": True, "status": "error", "message": "Qua nhieu request, vui long doi 1 phut"}

    data["error"] = False
    return data


def _make_client(data: dict) -> MikroTikClient:
    return MikroTikClient(host=data["host"], username=data["username"], password=data["password"])


# ═══════════════════════════════════════════════════════════════
# GET endpoints – dán link trình duyệt (key auth)
# ═══════════════════════════════════════════════════════════════


@router.get("/rotate")
async def quick_rotate(
    key: str = Query(..., description="API key (htpx_xxx)"),
    interface: str = Query("pppoe-out2", description="Tên interface PPPoE"),
):
    """Xoay IP 1 interface – GET, dán link trình duyệt."""
    data = _validate_key(key)
    if data.get("error"):
        return data

    try:
        client = _make_client(data)
        result = client.rotate_single(interface)
    except Exception as e:
        log_request(data["key_id"], "rotate", interface, f"error: {e}")
        return {"status": "error", "message": str(e)}

    increment_usage(data["key_id"])
    log_request(data["key_id"], "rotate", interface, result.get("status", ""))
    return result


@router.get("/rotate-all")
async def quick_rotate_all(
    key: str = Query(..., description="API key (htpx_xxx)"),
):
    """Xoay IP tất cả PPPoE – GET, dán link trình duyệt."""
    data = _validate_key(key)
    if data.get("error"):
        return data

    try:
        client = _make_client(data)
        result = client.rotate_all()
    except Exception as e:
        log_request(data["key_id"], "rotate-all", "", f"error: {e}")
        return {"status": "error", "message": str(e)}

    increment_usage(data["key_id"])
    log_request(data["key_id"], "rotate-all", "", "ok")
    return result


@router.get("/status")
async def quick_status(
    key: str = Query(..., description="API key (htpx_xxx)"),
):
    """Xem trạng thái PPPoE – GET."""
    data = _validate_key(key)
    if data.get("error"):
        return data

    try:
        client = _make_client(data)
        status = client.get_pppoe_status()
    except Exception as e:
        log_request(data["key_id"], "status", "", f"error: {e}")
        return {"status": "error", "message": str(e)}

    log_request(data["key_id"], "status", "", "ok")
    return {"status": "ok", "total": len(status), "clients": status}


# ═══════════════════════════════════════════════════════════════
# POST endpoints – app desktop gọi (Firebase Bearer auth)
# ═══════════════════════════════════════════════════════════════


class RegisterMikroTikRequest(BaseModel):
    host: str
    username: str
    password: str
    label: str = ""


class CreateApiKeyRequest(BaseModel):
    name: str = ""
    limit: int = 100000


@router.post("/register-mikrotik")
async def register_mikrotik(body: RegisterMikroTikRequest, user: dict = Depends(get_current_user)):
    """Lưu MikroTik credentials vào SQLite (1 user = 1 router)."""
    result = save_mikrotik_creds(
        user_id=user["id"],
        host=body.host,
        username=body.username,
        password=body.password,
        label=body.label,
    )
    return {"status": "ok", "data": result}


@router.post("/api-keys")
async def create_key(body: CreateApiKeyRequest, user: dict = Depends(get_current_user)):
    """Tạo API key mới."""
    result = create_api_key(user_id=user["id"], name=body.name, limit=body.limit)
    return {"status": "ok", "data": result}


@router.get("/api-keys")
async def list_keys(user: dict = Depends(get_current_user)):
    """Danh sách API keys của user."""
    keys = list_api_keys(user["id"])
    return {"status": "ok", "data": keys}


@router.put("/api-keys/{key_id}")
async def toggle_key(key_id: int, user: dict = Depends(get_current_user)):
    """Toggle active ↔ paused."""
    result = toggle_api_key(key_id, user["id"])
    if not result:
        raise HTTPException(status_code=404, detail="Key khong ton tai")
    return {"status": "ok", "data": result}


@router.delete("/api-keys/{key_id}")
async def delete_key(key_id: int, user: dict = Depends(get_current_user)):
    """Xóa API key."""
    ok = delete_api_key(key_id, user["id"])
    if not ok:
        raise HTTPException(status_code=404, detail="Key khong ton tai")
    return {"status": "ok", "message": "Da xoa key"}
