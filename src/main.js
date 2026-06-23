// Electron main process
// Creates the split-screen window, exposes IPC bridges to Python workers.

const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const path = require('path');
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');

const WORKDIR = path.join(os.homedir(), '.yt-slides-app');
fs.mkdirSync(path.join(WORKDIR, 'media'), { recursive: true });

// Resolve the Python executable to an ABSOLUTE PATH.
// Windows: spawn() without shell doesn't resolve PATH for .bat/.cmd shims
// (like `py.exe`'s launcher), so we must resolve via `where` first.
function findPython() {
  const candidates = process.platform === 'win32'
    ? ['py', 'python', 'python3']
    : ['python3', 'python'];

  for (const cmd of candidates) {
    const absPath = findOnPath(cmd);
    if (!absPath) continue;
    // Verify it actually runs
    const res = spawnSync(absPath, ['--version'], { encoding: 'utf8' });
    if (res.status === 0) return absPath;
  }
  return null;
}

// Resolve external CLI tools to absolute paths.
function findOnPath(tool) {
  const cmd = process.platform === 'win32' ? 'where' : 'which';
  const res = spawnSync(cmd, [tool], { encoding: 'utf8' });
  if (res.status !== 0) return null;
  // `where` on Windows can return multiple lines; take the first .exe if any.
  const lines = res.stdout.trim().split(/\r?\n/).filter(Boolean);
  if (process.platform === 'win32') {
    const exe = lines.find(l => l.toLowerCase().endsWith('.exe'));
    return exe || lines[0] || null;
  }
  return lines[0] || null;
}

const PYTHON = findPython();

let mainWindow;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1600,
    height: 1000,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      // Required so the <webview> tag works in the renderer
      webviewTag: true,
    },
  });

  mainWindow.loadFile(path.join(__dirname, 'renderer.html'));
}

app.whenReady().then(createWindow);
app.on('window-all-closed', () => app.quit());

// ---------- Helpers ----------

// Spawn a Python script and stream stdout lines back to the renderer.
function runPython(scriptName, args, channel) {
  return new Promise((resolve, reject) => {
    if (!PYTHON) {
      mainWindow.webContents.send(channel, {
        type: 'log',
        message: 'ERROR: Python not found. Install Python 3 from python.org and tick "Add to PATH".',
      });
      reject(new Error('Python not found'));
      return;
    }

    const scriptPath = path.join(__dirname, '..', 'python', scriptName);
    const proc = spawn(PYTHON, [scriptPath, ...args], {
      env: {
        ...process.env,
        WORKDIR,
        PYTHONIOENCODING: 'utf-8',
        // Pass resolved absolute paths so Python's subprocess calls work
        // even when yt-dlp / ffmpeg are .cmd shims rather than .exe files.
        YTDLP_PATH: findOnPath('yt-dlp') || 'yt-dlp',
        FFMPEG_PATH: findOnPath('ffmpeg') || 'ffmpeg',
        CLAUDE_PATH: findOnPath('claude') || 'claude',
      },
    });

    let lastResult = null;
    let stderrBuf = '';

    proc.stdout.on('data', (data) => {
      const lines = data.toString().split('\n').filter(Boolean);
      for (const line of lines) {
        try {
          const event = JSON.parse(line);
          mainWindow.webContents.send(channel, event);
          if (event.type === 'result') lastResult = event.data;
        } catch {
          // Non-JSON line — forward as a log message
          mainWindow.webContents.send(channel, { type: 'log', message: line });
        }
      }
    });

    proc.stderr.on('data', (data) => {
      stderrBuf += data.toString();
      mainWindow.webContents.send(channel, {
        type: 'log',
        message: data.toString().trim(),
      });
    });

    proc.on('close', (code) => {
      if (code === 0) resolve(lastResult);
      else reject(new Error(`${scriptName} exited ${code}: ${stderrBuf}`));
    });
  });
}

// ---------- IPC handlers (renderer → main) ----------

// Save the moments JSON that the user pasted/edited so Python scripts can read it.
ipcMain.handle('save-moments', async (_evt, moments) => {
  const file = path.join(WORKDIR, 'moments.json');
  fs.writeFileSync(file, JSON.stringify(moments, null, 2));
  return file;
});

// Download video + cut GIFs and screenshots.
ipcMain.handle('extract-media', async (_evt, youtubeUrl) => {
  return runPython('extract.py', [youtubeUrl], 'extract-progress');
});

// Auto-pick moments via yt-dlp (transcript) + claude -p.
ipcMain.handle('auto-pick-moments', async (_evt, { url, title, channel, n, minDur, maxDur }) => {
  const argv = [url, '--n', String(n || 6)];
  if (minDur) argv.push('--min-dur', String(minDur));
  if (maxDur) argv.push('--max-dur', String(maxDur));
  if (title) argv.push('--title', title);
  if (channel) argv.push('--channel', channel);
  return runPython('analyze.py', argv, 'analyze-progress');
});

// Build the .pptx from moments + media.
ipcMain.handle('build-deck', async (_evt, { title, youtubeUrl }) => {
  const result = await runPython(
    'build.py',
    [title, youtubeUrl],
    'build-progress'
  );

  // Default the save name to the deck title from step 4, sanitized to a
  // safe filename. Falls back to 'highlights' if the title is empty.
  const baseName = (title || '')
    .replace(/[<>:"/\\|?*\x00-\x1f]/g, '')  // strip path-illegal chars
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 120) || 'highlights';

  // Ask user where to save the final file
  const { filePath } = await dialog.showSaveDialog(mainWindow, {
    defaultPath: `${baseName}.pptx`,
    filters: [{ name: 'PowerPoint', extensions: ['pptx'] }],
  });
  if (filePath) {
    fs.copyFileSync(result.path, filePath);
    return filePath;
  }
  return null;
});

// Show file in OS file manager
ipcMain.handle('reveal-file', async (_evt, filePath) => {
  shell.showItemInFolder(filePath);
});

// Report on environment so the renderer can show setup warnings up-front.
ipcMain.handle('check-env', async () => ({
  python: PYTHON,
  ytDlp: findOnPath('yt-dlp'),
  ffmpeg: findOnPath('ffmpeg'),
  claude: findOnPath('claude'),
  platform: process.platform,
}));

// Fetch video title + channel via YouTube's oEmbed endpoint.
// Done in the main process to avoid CORS concerns in the renderer.
// Returns null on any failure (private video, bad URL, network) — caller
// should fall back gracefully rather than blocking the user.
ipcMain.handle('fetch-video-metadata', async (_evt, youtubeUrl) => {
  try {
    const endpoint = 'https://www.youtube.com/oembed?url='
      + encodeURIComponent(youtubeUrl) + '&format=json';
    const res = await fetch(endpoint);
    if (!res.ok) return null;
    const data = await res.json();
    return { title: data.title || null, channel: data.author_name || null };
  } catch {
    return null;
  }
});

// List generated media files so the renderer can preview them.
// Convert backslashes to forward slashes for file:// URLs on Windows.
ipcMain.handle('list-media', async () => {
  const dir = path.join(WORKDIR, 'media');
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir)
    .filter(f => f.endsWith('.gif') || f.endsWith('.png'))
    .map(f => path.join(dir, f).replace(/\\/g, '/'));
});
