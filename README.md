# YouTube → Slides

Turn a YouTube video into a PowerPoint deck of "highlight" slides — each
slide pairs a screenshot and a short animated GIF of a notable moment,
with a title, description, and presenter speaker notes.

This is an Electron desktop app that runs locally. Two ways to pick the
notable moments:

1. **Gemini in an embedded webview** (left panel). Sign in with your
   normal Google account, paste the generated prompt, copy the JSON
   response back. No API key needed.
2. **Claude Code in non-interactive print mode** (`claude -p`). Fully
   automated: yt-dlp pulls the auto-captions, Claude reads the
   transcript and returns a JSON array. No paste step.

Either path populates the same moments table, which you can edit before
extraction.

## Architecture

```text
┌──────────────────────────────────────────────────────────────────┐
│ Electron main process (Node.js)                                  │
│   • <webview> for Gemini, IPC handlers, subprocess management    │
└───┬──────────────────────────────────────────────────────┬───────┘
    │ IPC                                                  │ spawn
    ▼                                                      ▼
┌─────────────────┐                          ┌──────────────────────┐
│ Renderer (HTML) │                          │ Python workers       │
│  Gemini webview │                          │  analyze.py          │
│  Workflow panel │                          │   yt-dlp + claude    │
└─────────────────┘                          │  extract.py          │
                                             │   yt-dlp + ffmpeg    │
                                             │  build.py            │
                                             │   python-pptx        │
                                             └──────────────────────┘
```

The `<webview>` tag uses a persistent partition (`persist:gemini`) so
the Google login is remembered between launches like a normal browser
session.

## Workflow

1. **Step 1 — URL & settings.** Paste a YouTube URL. Adjust three
   sliders: number of moments (3-30), minimum GIF duration, maximum
   GIF duration. Then either:
   - Click **Copy prompt for Gemini** → paste into the embedded
     Gemini panel → copy its JSON back into the import box.
   - Or click **Auto-pick with Claude** → fully automated transcript
     pipeline. Requires the `claude` CLI on PATH.
2. **Step 2 — Moments.** An editable table with start time, end time,
   frame selector (S / M / E for which frame to use as the screenshot),
   title, description. Speaker notes ride along on each row, edited via
   the Import / Export JSON section. Timestamps auto-format ("123" →
   "1:23"). Live JSON view stays in sync with edits.
3. **Step 3 — Extract.** yt-dlp downloads the video (cached per-video
   by title + 11-character ID). ffmpeg cuts a screenshot and a
   2-pass-palette GIF for each moment. A paginated carousel previews
   the GIF + frame for every moment.
4. **Step 4 — Build.** python-pptx assembles a 16:9 deck with a
   navy/Georgia title slide, then one slide per moment with the GIF
   and screenshot side by side and the talking-point bullets in the
   speaker-notes pane.

## Setup

### Windows

```powershell
winget install OpenJS.NodeJS.LTS
winget install Python.Python.3.12      # tick "Add Python to PATH"
winget install Gyan.FFmpeg              # see ffmpeg note below

# Restart your terminal so PATH updates take effect:
npm install
pip install yt-dlp python-pptx
```

**ffmpeg via winget caveat:** winget installs ffmpeg as an "App
Execution Alias", which Electron's `where.exe` lookup can miss. If the
setup banner shows "ffmpeg not found" after restart, manually add the
real install directory to your user PATH:
`%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\...\bin`.

### macOS / Linux

```bash
brew install node python ffmpeg          # macOS
# or: apt install nodejs python3 ffmpeg   # Debian/Ubuntu

npm install
pip install yt-dlp python-pptx
```

### Optional: Auto-pick with Claude

The "Auto-pick with Claude" button shells out to the
[Claude Code CLI](https://docs.claude.com/claude-code) in non-interactive
print mode. Install it once globally:

```bash
npm install -g @anthropic-ai/claude-code
claude login   # one-time
```

Without the CLI, the rest of the app works fine — use the Gemini path
instead.

## Run

```bash
npm start
```

The app launches a single 1600×1000 window split 50/50: Gemini on the
left, the four-step workflow on the right. There's no build step.

## Working files

The app stores cached video downloads, generated media, and the
intermediate `.pptx` in `~/.yt-slides-app/`. The user is prompted with
a save dialog for the final deck. Safe to delete the cache directory
to reset state — the next run will re-download as needed.

Files inside the cache directory:

- `<title>_<id>.mp4` — cached video, keyed per YouTube ID
- `moments.json` — current moments (overwritten each extract)
- `media/moment_NN.{gif,png}` — generated media
- `transcript.en.vtt` — auto-captions (when using Auto-pick)
- `highlights.pptx` — intermediate deck before save dialog

## Project layout

```text
src/
  main.js          # Electron main: window, IPC, subprocess plumbing
  preload.js       # contextBridge for renderer ↔ main
  renderer.html    # UI: split-screen, settings, moments table, carousel
python/
  analyze.py       # yt-dlp transcript → claude -p → moments JSON
  extract.py       # yt-dlp download → ffmpeg GIF + screenshot
  build.py         # python-pptx deck assembly
```

## Implementation notes

A few details that took some figuring out and are easy to break.

- **Windows `spawn()` doesn't resolve PATH for `.cmd` shims.** Tools
  like `claude.cmd` (npm-installed) need the absolute path AND
  invocation through `cmd.exe /c` since Node 20+ refuses to spawn
  `.cmd` directly. The main process resolves absolute paths once at
  startup (via `where.exe`) and passes them to Python via
  `YTDLP_PATH` / `FFMPEG_PATH` / `CLAUDE_PATH` env vars. `analyze.py`
  detects `.cmd` shims and routes through `cmd.exe` accordingly.
- **`PYTHONIOENCODING=utf-8`** is set in the spawn env because Windows
  defaults to cp1252, which breaks the JSON-per-line stdout protocol
  if a video title or description contains non-ASCII characters.
- **`file://` URLs need three slashes** for Windows local paths. Path
  separators are normalized to forward slashes in the IPC response
  from `list-media`.
- **CORS-free metadata fetch.** The YouTube oEmbed call to grab title
  and channel name is done in the main process, not the renderer.
- **Anti-hallucination prompt.** Both prompts (Gemini and Claude)
  embed the video title + channel and instruct the model not to invent
  moments from a similarly-named video. Claude's prompt also includes
  the actual transcript so it has ground truth to anchor on.
- **Rolling auto-caption dedup.** YouTube auto-captions emit each line
  multiple times as a "rolling" preview with `<c>` tags. `analyze.py`
  strips inline tags and collapses prefix-redundant cues.
- **Per-video file cache.** Downloads are named `<safe_title>_<id>.mp4`
  so re-running on the same URL reuses the cached video, and a new URL
  triggers a fresh download.

## License

MIT — see [LICENSE](LICENSE).
