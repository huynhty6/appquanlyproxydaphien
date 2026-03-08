import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from firebase_app import get_db
from schemas import ApiKeyResponse, ApiKeyCreate

router = APIRouter(prefix="/api/api-keys", tags=["API Keys"])


@router.get("", response_model=list[ApiKeyResponse])
async def list_api_keys(user: dict = Depends(get_current_user)):
    db = get_db()
    docs = (
        db.collection("api_keys")
        .where("user_id", "==", user["id"])
        .order_by("created_at", direction="DESCENDING")
        .get()
    )
    results = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        results.append(ApiKeyResponse(**data))
    return results


@router.post("", response_model=ApiKeyResponse)
async def create_api_key(
    req: ApiKeyCreate,
    user: dict = Depends(get_current_user),
):
    db = get_db()
    key = f"htpx_{secrets.token_urlsafe(32)}"
    now = datetime.now(timezone.utc).isoformat()
    data = {
        "name": req.name,
        "key": key,
        "status": "active",
        "used_requests": 0,
        "limit_requests": req.limit_requests,
        "user_id": user["id"],
        "created_at": now,
    }
    _, doc_ref = db.collection("api_keys").add(data)
    data["id"] = doc_ref.id
    return ApiKeyResponse(**data)


@router.put("/{key_id}")
async def toggle_api_key(
    key_id: str,
    user: dict = Depends(get_current_user),
):
    db = get_db()
    doc_ref = db.collection("api_keys").document(key_id)
    doc = doc_ref.get()
    if not doc.exists or doc.to_dict().get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="API key khong ton tai")
    current = doc.to_dict().get("status", "active")
    new_status = "paused" if current == "active" else "active"
    doc_ref.update({"status": new_status})
    return {"status": new_status}


@router.delete("/{key_id}")
async def delete_api_key(
    key_id: str,
    user: dict = Depends(get_current_user),
):
    db = get_db()
    doc_ref = db.collection("api_keys").document(key_id)
    doc = doc_ref.get()
    if not doc.exists or doc.to_dict().get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="API key khong ton tai")
    doc_ref.delete()
    return {"status": "ok"}
