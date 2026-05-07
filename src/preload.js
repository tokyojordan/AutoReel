// Preload script — runs in renderer context with Node access.
// Exposes a controlled API to the page via contextBridge.

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  saveMoments: (moments) => ipcRenderer.invoke('save-moments', moments),
  extractMedia: (url) => ipcRenderer.invoke('extract-media', url),
  autoPickMoments: (opts) => ipcRenderer.invoke('auto-pick-moments', opts),
  buildDeck: (opts) => ipcRenderer.invoke('build-deck', opts),
  revealFile: (path) => ipcRenderer.invoke('reveal-file', path),
  listMedia: () => ipcRenderer.invoke('list-media'),
  checkEnv: () => ipcRenderer.invoke('check-env'),
  fetchVideoMetadata: (url) => ipcRenderer.invoke('fetch-video-metadata', url),

  onExtractProgress: (cb) => ipcRenderer.on('extract-progress', (_e, ev) => cb(ev)),
  onAnalyzeProgress: (cb) => ipcRenderer.on('analyze-progress', (_e, ev) => cb(ev)),
  onBuildProgress: (cb) => ipcRenderer.on('build-progress', (_e, ev) => cb(ev)),
});
