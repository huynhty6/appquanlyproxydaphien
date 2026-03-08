from fastapi import APIRouter, Depends

from auth import get_current_user
from firebase_app import get_db
from schemas import SettingsResponse, SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["Settings"])

DEFAULTS = {
    "language": "vi",
    "timezone": "Asia/Ho_Chi_Minh",
    "default_protocol": "HTTP",
    "pppoe_username": "",
    "pppoe_password": "",
    "two_factor_enabled": False,
    "ip_whitelist_enabled": False,
}


def _get_or_create(user_id: str) -> dict:
    db = get_db()
    doc_ref = db.collection("settings").document(user_id)
    doc = doc_ref.get()
    if not doc.exists:
        doc_ref.set(DEFAULTS)
        return DEFAULTS.copy()
    return doc.to_dict()


@router.get("", response_model=SettingsResponse)
async def get_settings(user: dict = Depends(get_current_user)):
    data = _get_or_create(user["id"])
    return SettingsResponse(**data)


@router.put("", response_model=SettingsResponse)
async def update_settings(
    req: SettingsUpdate,
    user: dict = Depends(get_current_user),
):
    db = get_db()
    _get_or_create(user["id"])
    updates = req.model_dump(exclude_unset=True)
    if updates:
        db.collection("settings").document(user["id"]).update(updates)
    data = db.collection("settings").document(user["id"]).get().to_dict()
    return SettingsResponse(**data)


@router.post("/reset", response_model=SettingsResponse)
async def reset_settings(user: dict = Depends(get_current_user)):
    db = get_db()
    db.collection("settings").document(user["id"]).set(DEFAULTS)
    return SettingsResponse(**DEFAULTS)
