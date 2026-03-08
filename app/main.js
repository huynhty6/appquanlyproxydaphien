const { app, BrowserWindow, screen, shell, ipcMain, net } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
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
      preload: path.join(__dirname, 'preload.js'),
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

// ─── Auto-update check via GitHub Releases ──────────────────────────────────
function compareVersions(v1, v2) {
  const parts1 = v1.replace(/^v/, '').split('.').map(Number);
  const parts2 = v2.replace(/^v/, '').split('.').map(Number);
  for (let i = 0; i < Math.max(parts1.length, parts2.length); i++) {
    const a = parts1[i] || 0;
    const b = parts2[i] || 0;
    if (a > b) return 1;
    if (a < b) return -1;
  }
  return 0;
}

ipcMain.handle('check-for-update', async () => {
  try {
    const { net } = require('electron');
    const currentVersion = require('../package.json').version;
    const data = await new Promise((resolve, reject) => {
      const req = net.request('https://api.github.com/repos/huynhty6/appquanlyproxydaphien/releases/latest');
      req.setHeader('User-Agent', 'HT-Proxy-Desktop');
      req.setHeader('Authorization', 'token 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');
      let body = '';
      req.on('response', (res) => {
        res.on('data', (chunk) => { body += chunk.toString(); });
        res.on('end', () => {
          try { resolve(JSON.parse(body)); }
          catch (e) { reject(e); }
        });
      });
      req.on('error', reject);
      req.end();
    });

    const latestVersion = data.tag_name; // e.g. "v2.7.0"
    if (!latestVersion) {
      return { hasUpdate: false, error: 'No releases found' };
    }
    if (compareVersions(latestVersion, currentVersion) > 0) {
      // Tìm file .exe trong assets
      const exeAsset = (data.assets || []).find(a => a.name.endsWith('.exe'));
      return {
        hasUpdate: true,
        version: latestVersion,
        body: data.body || '',
        downloadUrl: exeAsset ? exeAsset.browser_download_url : null,
        assetUrl: exeAsset ? exeAsset.url : null,
        assetName: exeAsset ? exeAsset.name : null,
        assetSize: exeAsset ? exeAsset.size : 0,
        releaseUrl: data.html_url, // fallback link for manual download
      };
    }
    return { hasUpdate: false };
  } catch (err) {
    console.error('[Update Check]', err.message);
    return { hasUpdate: false, error: err.message };
  }
});

ipcMain.handle('open-download-link', (event, url) => {
  if (url && (url.startsWith('https://') || url.startsWith('http://'))) {
    shell.openExternal(url);
  }
});

// ─── Download update .exe from GitHub Release asset ──────────────────────────
let downloadedInstallerPath = null;

ipcMain.handle('download-update', async (event, downloadUrl, assetName) => {
  const GITHUB_TOKEN = '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';
  const destPath = path.join(os.tmpdir(), assetName || 'HT-Proxy-Setup.exe');
  const isApiUrl = downloadUrl.includes('api.github.com');

  return new Promise((resolve, reject) => {
    const req = net.request({ url: downloadUrl, redirect: 'manual' });
    req.setHeader('User-Agent', 'HT-Proxy-Desktop');
    req.setHeader('Authorization', `token ${GITHUB_TOKEN}`);
    if (isApiUrl) req.setHeader('Accept', 'application/octet-stream');

    req.on('response', (response) => {
      // GitHub redirects to S3 for asset download — follow without auth
      if (response.statusCode >= 300 && response.statusCode < 400 && response.headers.location) {
        const redirectUrl = Array.isArray(response.headers.location)
          ? response.headers.location[0]
          : response.headers.location;
        const req2 = net.request(redirectUrl);
        req2.setHeader('User-Agent', 'HT-Proxy-Desktop');
        req2.on('response', (res2) => {
          handleDownloadResponse(res2, destPath, event.sender, resolve, reject);
        });
        req2.on('error', (err) => reject(err.message));
        req2.end();
        return;
      }
      handleDownloadResponse(response, destPath, event.sender, resolve, reject);
    });

    req.on('error', (err) => reject(err.message));
    req.end();
  });
});

function handleDownloadResponse(response, destPath, sender, resolve, reject) {
  if (response.statusCode !== 200) {
    let errorBody = '';
    response.on('data', (chunk) => { errorBody += chunk.toString(); });
    response.on('end', () => reject(`HTTP ${response.statusCode}: ${errorBody.substring(0, 200)}`));
    return;
  }

  const contentLength = parseInt(
    (Array.isArray(response.headers['content-length'])
      ? response.headers['content-length'][0]
      : response.headers['content-length']) || '0',
    10
  );

  const fileStream = fs.createWriteStream(destPath);
  let downloaded = 0;

  response.on('data', (chunk) => {
    fileStream.write(chunk);
    downloaded += chunk.length;
    if (contentLength > 0) {
      const percent = Math.round((downloaded / contentLength) * 100);
      try { sender.send('download-progress', percent, downloaded, contentLength); } catch (_) {}
    }
  });

  response.on('end', () => {
    fileStream.end(() => {
      downloadedInstallerPath = destPath;
      resolve({ success: true, path: destPath });
    });
  });

  response.on('error', (err) => {
    fileStream.end();
    try { fs.unlinkSync(destPath); } catch (_) {}
    reject(err.message);
  });
}

// ─── Install update: run NSIS installer silently and quit ────────────────────
ipcMain.handle('install-update', async () => {
  if (!downloadedInstallerPath || !fs.existsSync(downloadedInstallerPath)) {
    return { success: false, error: 'Installer not found' };
  }

  // Spawn NSIS installer with /S (silent) flag, detached so it survives app quit
  spawn(downloadedInstallerPath, ['/S'], {
    detached: true,
    stdio: 'ignore',
  }).unref();

  // Give installer a moment to start, then quit
  setTimeout(() => {
    app.quit();
  }, 500);

  return { success: true };
});

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
