"""
HT Proxy - Backend Launcher (no-console)
Entry point for PyInstaller bundle.
"""
import sys
import os

# Khi chạy từ PyInstaller bundle, thêm path của bundle vào sys.path
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
    # Chuyển working dir về thư mục chứa exe để .env và db files tìm được
    os.chdir(os.path.dirname(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Thêm BASE_DIR vào path để import các module
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        log_level="error",    # Không log ra console
        access_log=False,     # Tắt access log
    )

