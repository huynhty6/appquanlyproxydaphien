import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "HT Proxy API"
    APP_VERSION: str = "2.4.0"

    # MikroTik RouterOS - port ẩn; timeout cao cho IP public
    MIKROTIK_PORT: int = 2601
    MIKROTIK_USE_SSL: bool = False
    MIKROTIK_PLAINTEXT_LOGIN: bool = True
    MIKROTIK_TIMEOUT: float = 30.0  # giây, cho kết nối qua internet (IP public)

    # JWT
    SECRET_KEY: str = "htproxy-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    # Firebase
    FIREBASE_KEY_PATH: str = os.path.join(os.path.dirname(__file__), "firebase-key.json")
    FIREBASE_PROJECT_ID: str = "proxyapp-8758c"

    # Quick API VPS
    QUICK_API_VPS_URL: str = "http://localhost:8000"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
