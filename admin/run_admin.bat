@echo off
title HT Proxy - Admin Panel
echo ================================================
echo   HT Proxy - Admin Panel
echo   http://localhost:5000
echo   (Backend API: http://127.0.0.1:8000)
echo ================================================
echo.
cd /d "%~dp0"
python app.py
pause
