@echo off
title HT Proxy - Build Script
chcp 65001 >nul

echo ================================================
echo   HT Proxy - Build Script
echo   Frontend (Electron) + Backend (PyInstaller)
echo ================================================
echo.

set "ROOT=%~dp0"
set "BACKEND_DIR=%ROOT%backend"
set "DIST_DIR=%ROOT%dist"

:: ── Bước 1: Build Backend với PyInstaller ────────────────────────────────────
echo [1/3] Dang build backend.exe (PyInstaller)...
cd /d "%BACKEND_DIR%"

:: Kiểm tra pyinstaller
where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [!] PyInstaller chua duoc cai. Dang cai...
    pip install pyinstaller
)

:: Xóa build cũ
if exist "dist\backend" rmdir /s /q "dist\backend"
if exist "build" rmdir /s /q "build"

:: Build (onedir để tránh antivirus flag, nhanh hơn onefile)
pyinstaller backend.spec --distpath dist --workpath build --noconfirm
if errorlevel 1 (
    echo [THAT BAI] Build backend.exe that bai!
    pause
    exit /b 1
)
echo [OK] backend.exe da build xong: backend\dist\backend\backend.exe
echo.

:: ── Bước 2: Build Electron App với electron-builder ──────────────────────────
echo [2/3] Dang build Electron app (electron-builder)...
cd /d "%ROOT%"

:: Kiểm tra node_modules
if not exist "node_modules" (
    echo [!] Chua co node_modules. Dang cai npm...
    npm install
)

npm run build
if errorlevel 1 (
    echo [THAT BAI] Build Electron that bai!
    pause
    exit /b 1
)
echo [OK] Electron app da build xong!
echo.

:: ── Bước 3: Kết quả ──────────────────────────────────────────────────────────
echo [3/3] Kiem tra output...
echo.
if exist "%DIST_DIR%" (
    echo [OK] File installer nam trong thu muc:
    echo      %DIST_DIR%
    echo.
    dir "%DIST_DIR%\*.exe" /b 2>nul
) else (
    echo [!] Khong tim thay thu muc dist
)

echo.
echo ================================================
echo   BUILD HOAN TAT!
echo   File .exe nam trong thu muc: dist\
echo ================================================
pause
