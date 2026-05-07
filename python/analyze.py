"""analyze.py — fetch YouTube auto-captions, ask Claude to pick moments.

Pipeline:
  1. yt-dlp --write-auto-subs to grab the English VTT
  2. Parse cues; dedupe rolling-caption growth
  3. Build a prompt anchored on title/channel + transcript
  4. Pipe to `claude -p` via stdin, capture stdout
  5. Strip markdown fences, parse the JSON array

Emits JSON-per-line events; the final {"type":"result","data":[...]} is
the moments list the renderer will populate.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


YTDLP = os.environ.get("YTDLP_PATH", "yt-dlp")
CLAUDE = os.environ.get("CLAUDE_PATH", "claude")


def emit(event):
    print(json.dumps(event), flush=True)


def fetch_subs(url, workdir):
    out_template = str(workdir / "transcript.%(ext)s")
    cmd = [
        YTDLP,
        "--write-auto-subs", "--skip-download",
        "--sub-format", "vtt", "--sub-lang", "en",
        "-o", out_template,
        url,
    ]
    emit({"type": "log", "message": "Fetching transcript via yt-dlp…"})
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0:
        emit({"type": "log", "message": "yt-dlp: " + (res.stderr or "").strip()})
        raise RuntimeError("yt-dlp failed")
    candidates = sorted(workdir.glob("transcript*.vtt"))
    if not candidates:
        raise RuntimeError("No transcript produced — video may not have English captions.")
    return candidates[0]


def parse_vtt(path):
    """Return list of (start_seconds, text). Dedupes rolling auto-captions."""
    text = path.read_text(encoding="utf-8")
    cues = []
    cur_start = None
    cur_lines = []

    def flush():
        if cur_start is None or not cur_lines:
            return
        joined = " ".join(cur_lines).strip()
        cleaned = re.sub(r"<[^>]+>", "", joined).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if cleaned:
            cues.append((cur_start, cleaned))

    for line in text.split("\n"):
        line = line.rstrip("\r")
        if "-->" in line:
            flush()
            cur_lines = []
            m = re.match(r"(\d+):(\d+):(\d+)[.,]\d+", line)
            if m:
                h, mn, s = (int(x) for x in m.groups())
                cur_start = h * 3600 + mn * 60 + s
            else:
                cur_start = None
        elif cur_start is not None and line.strip():
            cur_lines.append(line)
    flush()

    # YouTube auto-captions emit rolling cues — each one extends the previous
    # by appending a few words. Collapse those into the longest version.
    deduped = []
    for start, txt in cues:
        if deduped and txt.startswith(deduped[-1][1]):
            deduped[-1] = (deduped[-1][0], txt)
        else:
            deduped.append((start, txt))
    return deduped


def fmt_ts(seconds):
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build_prompt(url, title, channel, n, min_dur, max_dur, cues):
    head = []
    head.append(f"Pick the {n} most notable moments from this YouTube video using the transcript below.")
    head.append("")
    if title:
        head.append(f'Video: "{title}"')
    if channel:
        head.append(f"Channel: {channel}")
    head.append(f"URL: {url}")
    head.append("")
    head.append("For each moment, return:")
    head.append("  - start: timestamp from the transcript (MM:SS or HH:MM:SS)")
    head.append(f"  - end: {min_dur}-{max_dur} seconds after start")
    head.append("  - title: 4-8 word slide title")
    head.append("  - description: 1-2 sentence caption shown on the slide, grounded in what was said")
    head.append('  - notes: 3-5 talking-point bullets for the presenter, one bullet per line prefixed with "- " (speaker-only, distinct from description)')
    head.append("")
    head.append("Return ONLY a JSON array. No markdown fences. No prose. Example:")
    head.append('[{"start":"01:23","end":"01:28","title":"Key reveal","description":"…","notes":"- Set up why this matters\\n- Reference the earlier setup\\n- Pause after the reveal"}]')
    head.append("")
    head.append("Transcript:")
    body = "\n".join(f"[{fmt_ts(s)}] {t}" for s, t in cues)
    return "\n".join(head) + "\n" + body


def run_claude(prompt):
    # On Windows, the npm-installed `claude.cmd` shim must be invoked
    # through cmd.exe — Node 20+ refuses to spawn .cmd directly, and
    # Python's subprocess inherits the same restriction without shell=True.
    if sys.platform == "win32" and CLAUDE.lower().endswith((".cmd", ".bat")):
        cmd = ["cmd.exe", "/c", CLAUDE, "-p"]
    else:
        cmd = [CLAUDE, "-p"]
    emit({"type": "log", "message": "Running claude -p (this can take a minute)…"})
    res = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if res.returncode != 0:
        emit({"type": "log", "message": "claude: " + (res.stderr or "").strip()})
        raise RuntimeError(f"claude exited {res.returncode}")
    return res.stdout


def extract_json_array(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end <= start:
        raise ValueError("no JSON array in response")
    return json.loads(text[start:end + 1])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--n", type=int, default=6)
    parser.add_argument("--min-dur", type=int, default=3)
    parser.add_argument("--max-dur", type=int, default=30)
    parser.add_argument("--title", default="")
    parser.add_argument("--channel", default="")
    args = parser.parse_args()

    workdir = Path(os.environ["WORKDIR"])
    workdir.mkdir(parents=True, exist_ok=True)
    for f in workdir.glob("transcript*.vtt"):
        f.unlink()

    try:
        vtt = fetch_subs(args.url, workdir)
    except Exception as e:
        emit({"type": "log", "message": f"ERROR: {e}"})
        sys.exit(1)

    cues = parse_vtt(vtt)
    if not cues:
        emit({"type": "log", "message": "ERROR: transcript parsed empty."})
        sys.exit(1)
    emit({"type": "log", "message": f"Parsed {len(cues)} transcript lines."})

    prompt = build_prompt(
        args.url, args.title, args.channel,
        args.n, args.min_dur, args.max_dur, cues,
    )

    try:
        raw = run_claude(prompt)
    except Exception as e:
        emit({"type": "log", "message": f"ERROR: {e}"})
        sys.exit(1)

    try:
        moments = extract_json_array(raw)
    except Exception as e:
        emit({"type": "log", "message": f"ERROR parsing Claude output: {e}"})
        emit({"type": "log", "message": "First 400 chars: " + raw[:400]})
        sys.exit(1)

    emit({"type": "result", "data": moments})


if __name__ == "__main__":
    main()
