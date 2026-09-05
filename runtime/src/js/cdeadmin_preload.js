/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

const { contextBridge, ipcRenderer } = require('electron/renderer');

contextBridge.exposeInMainWorld('electronUI', {
  focus: () => ipcRenderer.send('focus'),
  onMenuClick: (callback) => ipcRenderer.on('menu-click', (_event, details) => callback(details)),
  setMenus: (menus) => {
    ipcRenderer.send('setMenus', menus);
  },
  enableDisableMenuItems: (menu, item) => {
    ipcRenderer.send('enable-disable-menu-items', menu, item);
  },
  setMenuItems: (menu, menuItems) => {
    ipcRenderer.send('set-menu-items', menu, menuItems);
  },
  showOpenDialog: (options) => ipcRenderer.invoke('showOpenDialog', options),
  showSaveDialog: (options) => ipcRenderer.invoke('showSaveDialog', options),
  log: (text)=> ipcRenderer.send('log', text),
  reloadApp: ()=>{ipcRenderer.send('reloadApp');},
  // Download related functions
  getDownloadStreamPath: (...args) => ipcRenderer.invoke('get-download-stream-path', ...args),
  downloadStreamSaveChunk: (...args) => ipcRenderer.send('download-stream-save-chunk', ...args),
  downloadStreamSaveTotal: (...args) => ipcRenderer.send('download-stream-save-total', ...args),
  downloadStreamSaveEnd: (...args) => ipcRenderer.send('download-stream-save-end', ...args),
  downloadBase64UrlData: (...args) => ipcRenderer.invoke('download-base64-url-data', ...args),
  downloadTextData: (...args) => ipcRenderer.invoke('download-text-data', ...args),
  //Auto-updater related functions
  sendDataForAppUpdate: (data) => ipcRenderer.send('sendDataForAppUpdate', data),
  notifyAppAutoUpdate: (callback) => {
    ipcRenderer.removeAllListeners('notifyAppAutoUpdate'); // Clean up previous listeners
    ipcRenderer.on('notifyAppAutoUpdate', (_, data) => callback(data));
  },
  workspaceWindows: {
    getCapabilities: () => ({
      nativeWindowPlacement: true,
      exactDisplayPlacement: process.platform !== 'linux' ||
        process.env.XDG_SESSION_TYPE !== 'wayland',
      crossWindowDrag: false,
      sameOriginCoordination: true,
    }),
    registerWindow: (details) => ipcRenderer.invoke(
      'workspace-window:register', details,
    ),
    listDisplays: () => ipcRenderer.invoke('workspace-window:list-displays'),
    moveToDisplay: (displayId) => ipcRenderer.invoke(
      'workspace-window:move-to-display', displayId,
    ),
    moveToAdjacentDisplay: (direction) => ipcRenderer.invoke(
      'workspace-window:move-to-adjacent-display', direction,
    ),
    notifyPlacement: (details) => ipcRenderer.send(
      'workspace-window:placement', details,
    ),
    onPlacement: (callback) => {
      const listener = (_event, details) => callback(details);
      ipcRenderer.on('workspace-window:placement', listener);
      return () => ipcRenderer.removeListener(
        'workspace-window:placement', listener,
      );
    },
  },
});
