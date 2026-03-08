"""Admin API: quản lý user, license key, login history. Không cần auth (chỉ chạy local)."""

import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Query, Body
from firebase_app import get_db, get_firebase_auth
from schemas import (
    AdminUserResponse, AdminUserCreate, AdminUserUpdate,
    AdminLicenseKeyCreate,
)

router = APIRouter(prefix="/api/admin", tags=["Admin"])


# ──────────── App Config (Firestore: app_config/global) ────────────

@router.get("/app-config")
async def get_app_config():
    db = get_db()
    doc = db.collection("app_config").document("global").get()
    if doc.exists:
        return doc.to_dict()
    return {"api_server_url": "", "api_server_note": ""}


@router.put("/app-config")
async def update_app_config(request_data: dict = Body(...)):
    db = get_db()
    allowed = {"api_server_url", "api_server_note", "mikrotik_api_port"}
    data = {k: v for k, v in request_data.items() if k in allowed}
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    db.collection("app_config").document("global").set(data, merge=True)
    return {"ok": True, **data}


def _to_str(v):
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat() if callable(getattr(v, "isoformat")) else str(v)
    if hasattr(v, "timestamp"):
        return datetime.fromtimestamp(v.timestamp(), tz=timezone.utc).isoformat()
    return str(v) if v else None


def _user_to_response(doc_id: str, data: dict, last_login: dict | None = None) -> dict:
    mk = data.get("mikrotik", {}) or {}
    login_at = last_login.get("login_at") if last_login else None
    return {
        "id": doc_id,
        "full_name": data.get("full_name", ""),
        "email": data.get("email", ""),
        "phone": data.get("phone", ""),
        "country": data.get("country", ""),
        "license_key": data.get("license_key", ""),
        "license_expires": _to_str(data.get("license_expires")),
        "license_tier": data.get("license_tier", "personal"),
        "is_active": data.get("is_active", True),
        "role": data.get("role", "user"),
        "created_at": _to_str(data.get("created_at")),
        "mikrotik_host": mk.get("host", ""),
        "mikrotik_username": mk.get("username", ""),
        "mikrotik_password": mk.get("password", ""),
        "mikrotik_identity": mk.get("identity", ""),
        "mikrotik_version": mk.get("version", ""),
        "mikrotik_uptime": mk.get("uptime", ""),
        "mikrotik_connected": bool(mk.get("is_connected", False)),
        "mikrotik_connected_at": _to_str(mk.get("connected_at")),
        "last_login_at": _to_str(login_at),
        "last_login_ip": last_login.get("ip_address", "") if last_login else "",
        "last_login_location": last_login.get("location", "") if last_login else "",
    }


def _get_last_login(db, user_id: str) -> dict | None:
    try:
        docs = (
            db.collection("login_history")
            .where("user_id", "==", user_id)
            .order_by("login_at", direction="DESCENDING")
            .limit(1)
            .get()
        )
        if docs:
            return docs[0].to_dict()
    except Exception:
        try:
            docs = (
                db.collection("login_history")
                .where("user_id", "==", user_id)
                .limit(5)
                .get()
            )
            if docs:
                items = sorted(docs, key=lambda d: str(d.to_dict().get("login_at", "")), reverse=True)
                return items[0].to_dict()
        except Exception:
            pass
    return None


def _sync_auth_to_firestore(db):
    """Đồng bộ Firebase Auth users sang Firestore (tạo doc cho user chưa có)."""
    auth = get_firebase_auth()
    try:
        page = auth.list_users()
        while page:
            for u in page.users:
                doc = db.collection("users").document(u.uid).get()
                if not doc.exists:
                    # Dùng set(merge=True) để KHÔNG ghi đè các field đã có
                    db.collection("users").document(u.uid).set({
                        "full_name": u.display_name or (u.email or "").split("@")[0],
                        "email": u.email or "",
                        "phone": "",
                        "country": "",
                        "avatar_url": "",
                        "license_key": "",
                        "license_expires": None,
                        "license_tier": "personal",
                        "is_active": True,
                        "role": "user",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }, merge=True)
                else:
                    # Patch user cũ nếu thiếu field license_tier
                    data = doc.to_dict()
                    if "license_tier" not in data:
                        tier = "personal"
                        # Tìm tier từ license_keys nếu user đang có key
                        lk = data.get("license_key", "")
                        if lk:
                            try:
                                ks = db.collection("license_keys").where("key", "==", lk).limit(1).get()
                                if ks:
                                    tier = (ks[0].to_dict().get("tier") or "personal").lower()
                            except Exception:
                                pass
                        db.collection("users").document(u.uid).update({"license_tier": tier})
            page = page.get_next_page()
    except Exception:
        pass



# ---------- Users ----------

@router.get("/users", response_model=list[AdminUserResponse])
async def list_users(
    search: str = Query("", description="Tim kiem theo email hoac ten"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    db = get_db()
    _sync_auth_to_firestore(db)
    docs = db.collection("users").get()
    results = []
    for doc in docs:
        data = doc.to_dict()
        if search:
            s = search.lower()
            if s not in data.get("email", "").lower() and s not in data.get("full_name", "").lower():
                continue
        last_login = _get_last_login(db, doc.id)
        results.append(_user_to_response(doc.id, data, last_login))
    results.sort(key=lambda u: u.get("created_at") or "", reverse=True)
    start = (page - 1) * limit
    return results[start:start + limit]


@router.get("/users/count")
async def users_count():
    db = get_db()
    docs = db.collection("users").get()
    total = len(docs)
    active = sum(1 for d in docs if d.to_dict().get("is_active", True))
    admins = sum(1 for d in docs if d.to_dict().get("role") == "admin")
    return {"total": total, "active": active, "inactive": total - active, "admins": admins}


@router.get("/users/{user_id}", response_model=AdminUserResponse)
async def get_user(user_id: str):
    db = get_db()
    doc = db.collection("users").document(user_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="User khong ton tai")
    last_login = _get_last_login(db, user_id)
    return _user_to_response(doc.id, doc.to_dict(), last_login)


@router.post("/users", response_model=AdminUserResponse)
async def create_user(req: AdminUserCreate):
    db = get_db()
    auth = get_firebase_auth()
    try:
        auth.get_user_by_email(req.email)
        raise HTTPException(status_code=400, detail="Email da ton tai")
    except auth.UserNotFoundError:
        pass

    fb_user = auth.create_user(
        email=req.email,
        password=req.password,
        display_name=req.full_name,
        email_verified=False,
    )
    uid = fb_user.uid
    user_data = {
        "full_name": req.full_name,
        "email": req.email,
        "phone": "",
        "country": "",
        "avatar_url": "",
        "license_key": "",
        "license_expires": None,
        "license_tier": "personal",   # Luôn có field này để tránh bị default sai
        "is_active": req.is_active,
        "role": req.role,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.collection("users").document(uid).set(user_data)
    return _user_to_response(uid, user_data)


@router.put("/users/{user_id}", response_model=AdminUserResponse)
async def update_user(user_id: str, req: AdminUserUpdate):
    db = get_db()
    doc_ref = db.collection("users").document(user_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="User khong ton tai")

    updates = req.model_dump(exclude_unset=True)
    if "password" in updates:
        new_pass = updates.pop("password")
        get_firebase_auth().update_user(user_id, password=new_pass)

    if updates:
        doc_ref.update(updates)

    refreshed = doc_ref.get().to_dict()
    last_login = _get_last_login(db, user_id)
    return _user_to_response(user_id, refreshed, last_login)


@router.delete("/users/{user_id}")
async def delete_user(user_id: str):
    db = get_db()
    doc = db.collection("users").document(user_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="User khong ton tai")
    try:
        get_firebase_auth().delete_user(user_id)
    except Exception:
        pass
    db.collection("users").document(user_id).delete()
    return {"status": "ok"}


@router.get("/users/{user_id}/login-history")
async def user_login_history(user_id: str, limit: int = Query(100, ge=1, le=500)):
    db = get_db()
    try:
        docs = (
            db.collection("login_history")
            .where("user_id", "==", user_id)
            .order_by("login_at", direction="DESCENDING")
            .limit(limit)
            .get()
        )
    except Exception:
        try:
            docs = list(
                db.collection("login_history")
                .where("user_id", "==", user_id)
                .limit(limit)
                .get()
            )
            docs.sort(key=lambda d: str(d.to_dict().get("login_at", "")), reverse=True)
        except Exception:
            docs = []
    return [
        {
            "device": d.to_dict().get("device", ""),
            "ip_address": d.to_dict().get("ip_address", ""),
            "location": d.to_dict().get("location", ""),
            "status": d.to_dict().get("status", ""),
            "login_at": _to_str(d.to_dict().get("login_at")),
        }
        for d in docs
    ]


@router.get("/users/{user_id}/mikrotik-history")
async def user_mikrotik_history(user_id: str, limit: int = Query(100, ge=1, le=500)):
    db = get_db()
    try:
        docs = (
            db.collection("mikrotik_history")
            .where("user_id", "==", user_id)
            .order_by("timestamp", direction="DESCENDING")
            .limit(limit)
            .get()
        )
    except Exception:
        try:
            docs = list(
                db.collection("mikrotik_history")
                .where("user_id", "==", user_id)
                .limit(limit)
                .get()
            )
            docs.sort(key=lambda d: str(d.to_dict().get("timestamp", "")), reverse=True)
        except Exception:
            docs = []
    return [
        {
            "action": d.to_dict().get("action", ""),
            "host": d.to_dict().get("host", ""),
            "identity": d.to_dict().get("identity", ""),
            "version": d.to_dict().get("version", ""),
            "uptime": d.to_dict().get("uptime", ""),
            "timestamp": _to_str(d.to_dict().get("timestamp")),
        }
        for d in docs
    ]


# ---------- License keys ----------

@router.get("/license-keys")
async def list_license_keys():
    db = get_db()
    try:
        docs = db.collection("license_keys").order_by("created_at", direction="DESCENDING").get()
    except Exception:
        docs = db.collection("license_keys").get()
    results = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        for k in ("created_at", "expires_at"):
            if k in data:
                data[k] = _to_str(data[k])
        results.append(data)
    return results


@router.post("/license-keys")
async def create_license_key(req: AdminLicenseKeyCreate):
    db = get_db()
    key = req.key or f"HTP-{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"
    now = datetime.now(timezone.utc)
    tier = (req.tier or "personal").lower()
    if tier not in ("personal", "business", "test"):
        tier = "personal"

    if tier == "test":
        if req.expires_at:
            expires = req.expires_at
        elif req.duration_days:
            expires = (now + timedelta(days=req.duration_days)).isoformat()
        else:
            expires = (now + timedelta(days=30)).isoformat()
    else:
        expires = None  # Personal & Business: vĩnh viễn

    key_data = {
        "key": key,
        "tier": tier,
        "expires_at": expires,
        "user_id": req.user_id or "",
        "is_used": False,
        "created_at": now.isoformat(),
        "created_by": "admin",
    }
    _, doc_ref = db.collection("license_keys").add(key_data)
    key_data["id"] = doc_ref.id

    if req.user_id:
        user_doc = db.collection("users").document(req.user_id).get()
        if user_doc.exists:
            db.collection("users").document(req.user_id).update({
                "license_key": key,
                "license_expires": expires,
                "license_tier": tier,
            })
            key_data["is_used"] = True
            db.collection("license_keys").document(doc_ref.id).update({"is_used": True})

    return key_data


@router.delete("/license-keys/{key_id}")
async def delete_license_key(key_id: str):
    db = get_db()
    doc = db.collection("license_keys").document(key_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Key khong ton tai")
    data = doc.to_dict()
    key_value = data.get("key", "")
    user_id = data.get("user_id", "")
    if user_id:
        user_doc = db.collection("users").document(user_id).get()
        if user_doc.exists and user_doc.to_dict().get("license_key") == key_value:
            db.collection("users").document(user_id).update({
                "license_key": "",
                "license_expires": None,
            })
    elif key_value:
        users = db.collection("users").where("license_key", "==", key_value).limit(1).get()
        for u in users:
            db.collection("users").document(u.id).update({
                "license_key": "",
                "license_expires": None,
            })
    db.collection("license_keys").document(key_id).delete()
    return {"status": "ok"}


# ---------- Stats ----------

@router.get("/stats")
async def admin_stats():
    db = get_db()
    _sync_auth_to_firestore(db)
    users = db.collection("users").get()
    keys = db.collection("license_keys").get()
    try:
        logins = db.collection("login_history").order_by("login_at", direction="DESCENDING").limit(50).get()
    except Exception:
        try:
            logins = list(db.collection("login_history").limit(50).get())
            logins.sort(key=lambda d: str(d.to_dict().get("login_at", "")), reverse=True)
        except Exception:
            logins = []

    total_users = len(users)
    active_users = sum(1 for u in users if u.to_dict().get("is_active", True))
    total_keys = len(keys)
    used_keys = sum(1 for k in keys if k.to_dict().get("is_used", False))
    users_with_mk = sum(1 for u in users if (u.to_dict().get("mikrotik") or {}).get("host"))

    return {
        "total_users": total_users,
        "active_users": active_users,
        "inactive_users": total_users - active_users,
        "total_license_keys": total_keys,
        "used_license_keys": used_keys,
        "unused_license_keys": total_keys - used_keys,
        "users_with_mikrotik": users_with_mk,
        "recent_logins": [
            {
                "user_id": d.to_dict().get("user_id", ""),
                "ip_address": d.to_dict().get("ip_address", ""),
                "location": d.to_dict().get("location", ""),
                "login_at": _to_str(d.to_dict().get("login_at")),
                "device": d.to_dict().get("device", ""),
            }
            for d in (logins[:10] if logins else [])
        ],
    }
