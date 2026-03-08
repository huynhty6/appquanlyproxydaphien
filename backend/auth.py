"""Firebase Auth utilities - verify ID token."""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from firebase_app import get_db, get_firebase_auth

security = HTTPBearer(auto_error=False)


async def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
    """Verify Firebase ID token, return user from Firestore."""
    if not creds or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Chua dang nhap",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        decoded = get_firebase_auth().verify_id_token(creds.credentials)
        user_id = decoded.get("uid")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token khong hop le")
    except HTTPException:
        raise
    except Exception as e:
        err_msg = str(e).lower()
        if "expired" in err_msg or "exp" in err_msg:
            raise HTTPException(status_code=401, detail="Token het han, vui long dang nhap lai")
        if "project" in err_msg or "audience" in err_msg:
            raise HTTPException(status_code=401, detail="Token sai project, kiem tra cau hinh Firebase")
        raise HTTPException(status_code=401, detail="Token khong hop le hoac da het han")

    db = get_db()
    user_doc = db.collection("users").document(user_id).get()
    if not user_doc.exists:
        raise HTTPException(status_code=401, detail="User khong ton tai")
    user_data = user_doc.to_dict()
    user_data["id"] = user_doc.id
    if not user_data.get("is_active", True):
        raise HTTPException(status_code=403, detail="Tai khoan da bi khoa")
    return user_data


async def get_admin_user(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ban khong co quyen admin",
        )
    return user
