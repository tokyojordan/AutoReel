"""extract.py — download video, cut screenshots and GIFs.

Reads moments from $WORKDIR/moments.json.
Writes media to $WORKDIR/media/.
Emits JSON-per-line progress events to stdout.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


# Resolved absolute paths from the Electron main process.
# Falls back to bare names when run standalone from a shell.
YTDLP = os.environ.get("YTDLP_PATH", "yt-dlp")
FFMPEG = os.environ.get("FFMPEG_PATH", "ffmpeg")


def emit(event):
    print(json.dumps(event), flush=True)


def parse_ts(ts):
    parts = [float(p) for p in str(ts).strip().split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"Bad timestamp: {ts}")


def get_video_id_and_title(url):
    """Resolve a URL to its (video_id, title) via yt-dlp without downloading.
    Used to build a stable per-video filename so cached files aren't reused
    across different videos."""
    cmd = [
        YTDLP, "--skip-download", "--no-warnings",
        "--print", "%(id)s",
        "--print", "%(title)s",
        url,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0:
        raise RuntimeError("yt-dlp metadata lookup failed: " + (res.stderr or "").strip())
    lines = [l for l in res.stdout.splitlines() if l.strip()]
    if len(lines) < 2:
        raise RuntimeError("Unexpected yt-dlp output: " + res.stdout)
    return lines[0], lines[1]  # id, title


def safe_filename(s):
    """Strip Windows-unsafe chars from a title and cap length."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(". ")  # Windows dislikes trailing dots/spaces
    return s[:80] or "video"


def download(url, out_path):
    if out_path.exists():
        emit({"type": "log", "message": f"Using cached video: {out_path.name}"})
        return
    cmd = [
        YTDLP,
        "-f", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", str(out_path),
        url,
    ]
    subprocess.run(cmd, check=True)


def screenshot(video, t, out):
    subprocess.run([
        FFMPEG, "-y", "-ss", str(t), "-i", str(video),
        "-frames:v", "1", "-q:v", "2", str(out),
    ], check=True, stderr=subprocess.DEVNULL)


def gif(video, start, end, out, width=480, fps=12):
    duration = max(0.5, end - start)
    palette = out.with_suffix(".palette.png")

    subprocess.run([
        FFMPEG, "-y", "-ss", str(start), "-t", str(duration), "-i", str(video),
        "-vf", f"fps={fps},scale={width}:-1:flags=lanczos,palettegen",
        str(palette),
    ], check=True, stderr=subprocess.DEVNULL)

    subprocess.run([
        FFMPEG, "-y", "-ss", str(start), "-t", str(duration), "-i", str(video),
        "-i", str(palette),
        "-lavfi", f"fps={fps},scale={width}:-1:flags=lanczos[x];[x][1:v]paletteuse",
        str(out),
    ], check=True, stderr=subprocess.DEVNULL)

    palette.unlink(missing_ok=True)


def main():
    youtube_url = sys.argv[1]
    workdir = Path(os.environ["WORKDIR"])
    media_dir = workdir / "media"

    # Clear stale media so previews don't show old runs
    if media_dir.exists():
        for f in media_dir.glob("moment_*"):
            f.unlink()
    media_dir.mkdir(parents=True, exist_ok=True)

    moments = json.loads((workdir / "moments.json").read_text())

    # Verify resolved tool paths actually exist (env vars come from Electron).
    for name, p in (("yt-dlp", YTDLP), ("ffmpeg", FFMPEG)):
        # If it's a bare name, fall back to PATH lookup; otherwise check the file exists.
        if os.sep in p or "/" in p:
            if not os.path.isfile(p):
                emit({"type": "log", "message": f"ERROR: {name} not found at {p}"})
                sys.exit(1)
        elif not shutil.which(p):
            emit({"type": "log", "message": f"ERROR: {name} not on PATH"})
            sys.exit(1)

    emit({"type": "log", "message": "Resolving video metadata…"})
    vid, title = get_video_id_and_title(youtube_url)
    video = workdir / f"{safe_filename(title)}_{vid}.mp4"
    if not video.exists():
        emit({"type": "log", "message": f"Downloading: {video.name}"})
    download(youtube_url, video)

    for i, m in enumerate(moments, 1):
        start = parse_ts(m["start"])
        end = parse_ts(m["end"])

        frame_pref = m.get("frame", "middle")
        if frame_pref == "start":
            snap_t = start
        elif frame_pref == "end":
            snap_t = end
        else:
            snap_t = (start + end) / 2

        screenshot(video, snap_t, media_dir / f"moment_{i:02d}.png")
        gif(video, start, end, media_dir / f"moment_{i:02d}.gif")

        emit({"type": "progress", "current": i, "total": len(moments),
              "label": m.get("title", "")})

    emit({"type": "result", "data": {"media_dir": str(media_dir)}})


if __name__ == "__main__":
    main()
