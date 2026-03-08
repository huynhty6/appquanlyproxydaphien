"""
HT Proxy - Admin Panel (Standalone Web)
Chạy: python app.py
Truy cập: http://localhost:5000
Backend API phải đang chạy tại http://127.0.0.1:8000
"""

import os
import webbrowser
from threading import Timer

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

app = FastAPI(title="HT Proxy Admin Panel")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
APP_STATIC_DIR = os.path.join(BASE_DIR, "app")

if os.path.isdir(APP_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=APP_STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def admin_page():
    html_path = os.path.join(TEMPLATES_DIR, "admin.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/health")
def health():
    return {"status": "ok", "app": "HT Proxy Admin Panel"}


def open_browser():
    webbrowser.open("http://localhost:5000")


if __name__ == "__main__":
    print("=" * 50)
    print("  HT Proxy - Admin Panel")
    print("  http://localhost:5000")
    print("  (Backend API: http://127.0.0.1:8000)")
    print("=" * 50)
    Timer(1.5, open_browser).start()
    uvicorn.run(app, host="0.0.0.0", port=5000)
