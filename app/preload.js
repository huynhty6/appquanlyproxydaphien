const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('htUpdate', {
  check: () => ipcRenderer.invoke('check-for-update'),
  openDownloadLink: (url) => ipcRenderer.invoke('open-download-link', url),
  downloadUpdate: (assetUrl, assetName) => ipcRenderer.invoke('download-update', assetUrl, assetName),
  installUpdate: () => ipcRenderer.invoke('install-update'),
  onDownloadProgress: (callback) => {
    ipcRenderer.on('download-progress', (event, percent, downloaded, total) => {
      callback(percent, downloaded, total);
    });
  },
});
