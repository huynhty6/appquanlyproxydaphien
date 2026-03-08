const { app, BrowserWindow, screen, shell } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

let backendProcess = null;

// ─── Khởi động backend.exe ngầm (không có console) ───────────────────────────
function startBackend() {
  // Trong production build: backend.exe nằm trong resources/backend/
  // Khi dev: chạy trực tiếp python
  let backendExe;

  if (app.isPackaged) {
    backendExe = path.join(process.resourcesPath, 'backend', 'backend.exe');
  } else {
    // Dev mode: dùng uvicorn trực tiếp
    return null;
  }

  const proc = spawn(backendExe, [], {
    cwd: path.dirname(backendExe),
    windowsHide: true,      // ẨN console hoàn toàn
    detached: false,
    stdio: 'ignore',        // Bỏ qua stdout/stderr
  });

  proc.on('error', (err) => {
    console.error('[Backend] Không thể khởi động:', err.message);
  });

  return proc;
}

// ─── Chờ backend sẵn sàng (port 8000) ────────────────────────────────────────
function waitForBackend(retries = 30, delay = 500) {
  return new Promise((resolve) => {
    const { net } = require('electron');
    const tryConnect = (attempt) => {
      if (attempt >= retries) {
        resolve(); // Hết thử, mở app dù sao
        return;
      }
      const req = net.request('http://127.0.0.1:8000/health');
      req.on('response', () => resolve());
      req.on('error', () => setTimeout(() => tryConnect(attempt + 1), delay));
      req.end();
    };
    tryConnect(0);
  });
}

// ─── Tạo cửa sổ chính ────────────────────────────────────────────────────────
function createWindow() {
  const { width: screenW, height: screenH } = screen.getPrimaryDisplay().workAreaSize;
  const w = Math.floor(screenW * 0.8);
  const h = Math.floor(screenH * 0.8);

  const win = new BrowserWindow({
    width: w,
    height: h,
    minWidth: 900,
    minHeight: 600,
    x: Math.floor((screenW - w) / 2),
    y: Math.floor((screenH - h) / 2),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
    icon: path.join(__dirname, 'icon.png'),
    title: 'HT Proxy - v2.6.0 Professional',
    show: false, // Ẩn cho đến khi backend sẵn sàng
  });

  win.loadFile(path.join(__dirname, 'index.html'));
  win.removeMenu();

  // Hiện cửa sổ sau khi backend ready
  win.once('ready-to-show', () => win.show());

  // Mở tất cả link http/https bằng trình duyệt bên ngoài (bao gồm iframe)
  function setupExternalLinks(webContents) {
    webContents.setWindowOpenHandler(({ url }) => {
      if (url.startsWith('http://') || url.startsWith('https://')) {
        shell.openExternal(url);
      }
      return { action: 'deny' };
    });

    webContents.on('will-navigate', (event, url) => {
      if ((url.startsWith('http://') || url.startsWith('https://')) && !url.includes('localhost')) {
        event.preventDefault();
        shell.openExternal(url);
      }
    });
  }

  setupExternalLinks(win.webContents);

  // Bắt tất cả iframe/frame con
  win.webContents.on('did-attach-webview', (event, wc) => {
    setupExternalLinks(wc);
  });

  win.webContents.on('frame-created', (event, { frame }) => {
    frame.once('dom-ready', () => {
      if (frame.url && (frame.url.startsWith('http://') || frame.url.startsWith('https://')) && !frame.url.includes('localhost')) {
        shell.openExternal(frame.url);
      }
    });
  });

  return win;
}

// ─── App lifecycle ────────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  // Khởi động backend trước
  backendProcess = startBackend();

  // Chờ backend sẵn sàng (nếu packaged)
  if (app.isPackaged && backendProcess) {
    await waitForBackend();
  }

  createWindow();
});

app.on('window-all-closed', () => {
  // Kill backend khi đóng app
  if (backendProcess) {
    try {
      process.kill(backendProcess.pid);
    } catch (_) { }
    backendProcess = null;
  }
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

// Đảm bảo kill backend khi app crash hoặc thoát bất thường
app.on('before-quit', () => {
  if (backendProcess) {
    try {
      process.kill(backendProcess.pid);
    } catch (_) { }
  }
});
