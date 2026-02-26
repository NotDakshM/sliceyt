"""
YouTube Clip Downloader - Flask web app.
Uses yt-dlp for download and trim in one command.
"""

import json
import os
import queue
import sys
import re
import subprocess
import tempfile
import threading
import traceback
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file

# ── helpers (unchanged from desktop app) ──────────────────────────────────────

def extract_video_id(url: str) -> str | None:
    patterns = [
        r"(?:youtube\.com/watch\?v=)([a-zA-Z0-9_-]{11})",
        r"(?:youtu\.be/)([a-zA-Z0-9_-]{11})",
        r"(?:youtube\.com/embed/)([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        m = re.search(pattern, url.strip())
        if m:
            return m.group(1)
    return None


def seconds_from_hms(h: int, m: int, s: int) -> int:
    return h * 3600 + m * 60 + s


def format_ytdlp_time(seconds: int) -> str:
    """Format seconds for yt-dlp --download-sections (MM:SS or HH:MM:SS)."""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _get_subprocess_env():
    """Return env with yt-dlp/FFmpeg on PATH. On Windows, prepend known install locations; on Linux, system PATH is used as-is."""
    import platform
    env = os.environ.copy()
    if platform.system() == "Windows":
        env["PATH"] = (
            "C:\\Users\\Admin\\AppData\\Local\\Programs\\Python\\Python313\\Scripts;"
            + "C:\\Users\\Admin\\AppData\\Local\\Microsoft\\WinGet\\Packages\\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\\ffmpeg-8.0.1-full_build\\bin;"
            + env.get("PATH", "")
        )
    return env


PROGRESS_REGEX = re.compile(r"(\d+\.?\d*)%")
FFMPEG_TIME_REGEX = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")


def fetch_video_info(url: str) -> tuple[str | None, str | None, int | None]:
    """Fetch video title, thumbnail URL, and duration via yt-dlp --dump-json. Returns (title, thumbnail_url, duration_sec) or (None, None, None) on failure."""
    video_id = extract_video_id(url)
    if not video_id:
        return None, None, None
    try:
        cmd = [sys.executable, "-m", "yt_dlp", "--force-ipv4", "--dump-json", "--no-download", url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=_get_subprocess_env())
        if result.returncode != 0:
            return None, None, None
        data = json.loads(result.stdout)
        title = data.get("title") or None
        thumbnail = data.get("thumbnail") or None
        duration = data.get("duration")
        duration = int(duration) if duration is not None else None
        return title, thumbnail, duration
    except Exception:
        return None, None, None


def download_with_ytdlp(
    url: str, quality: str, start_sec: int, end_sec: int, out_path: str,
    progress_callback=None,
) -> bool:
    duration_sec = max(end_sec - start_sec, 1)
    format_map = {
        "2160p": "bestvideo[height<=2160][vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]/best[height<=2160]",
        "1080p": "bestvideo[height<=1080][vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]",
        "720p":  "bestvideo[height<=720][vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]",
        "480p":  "bestvideo[height<=480][vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]",
        "360p":  "bestvideo[height<=360][vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360]",
    }
    fmt = format_map.get(quality, "best")
    start_str = format_ytdlp_time(start_sec)
    end_str = format_ytdlp_time(end_sec)
    section = f"*{start_str}-{end_str}"
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--force-ipv4",
        "-f", fmt,
        "--download-sections", section,
        "--merge-output-format", "mp4",
        "-o", out_path,
        "--compat-options", "no-direct-merge",
        "--retries", "10",
        "--fragment-retries", "10",
        "--newline",
        url,
    ]
    print("DEBUG: About to run yt-dlp:", cmd)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_get_subprocess_env(),
        bufsize=1,
    )

    def read_stderr():
        for line in iter(proc.stderr.readline, ""):
            print("STDERR:", line, end="")
            if progress_callback:
                m = PROGRESS_REGEX.search(line)
                if m:
                    pct = float(m.group(1)) / 100.0
                    progress_callback(pct, f"Downloading {float(m.group(1)):.1f}%")
                else:
                    fm = FFMPEG_TIME_REGEX.search(line)
                    if fm:
                        elapsed = int(fm.group(1)) * 3600 + int(fm.group(2)) * 60 + float(fm.group(3))
                        pct = min(elapsed / duration_sec, 1.0)
                        progress_callback(pct, f"Merging {pct * 100:.1f}%")

    def drain_stdout():
        for _ in iter(proc.stdout.readline, ""):
            pass

    reader = threading.Thread(target=read_stderr, daemon=True)
    stdout_drain = threading.Thread(target=drain_stdout, daemon=True)
    reader.start()
    stdout_drain.start()
    proc.wait()
    reader.join(timeout=1.0)
    stdout_drain.join(timeout=1.0)

    if progress_callback:
        progress_callback(1.0, "Complete")
    print("DEBUG: yt-dlp finished with returncode:", proc.returncode)
    return proc.returncode == 0


# ── Flask app ──────────────────────────────────────────────────────────────────

app = Flask(__name__)

# job_id → {"status": "pending"|"done"|"error", "file": path, "msg": str, "queue": Queue}
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

DOWNLOAD_DIR = os.path.join(tempfile.gettempdir(), "yt_clip_downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ── HTML page ──────────────────────────────────────────────────────────────────

def _build_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SliceYT \u2014 YouTube Clip Downloader</title>
  <link rel="icon" type="image/png" href="/logo">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --red: #ee0000;
      --red-dark: #cc0000;
      --bg: #0f0f0f;
      --surface: #161616;
      --surface2: #1e1e1e;
      --border: #222;
      --border2: #2e2e2e;
      --text: #e0e0e0;
      --muted: #555;
      --muted2: #3a3a3a;
    }

    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      overflow-x: hidden;
    }

    /* ── Animated grid background ── */
    body::before {
      content: '';
      position: fixed;
      inset: 0;
      background-image:
        linear-gradient(rgba(255,255,255,0.022) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,0.022) 1px, transparent 1px);
      background-size: 60px 60px;
      animation: gridDrift 28s linear infinite;
      pointer-events: none;
      z-index: 0;
    }
    @keyframes gridDrift {
      from { background-position: 0 0; }
      to   { background-position: 60px 60px; }
    }

    /* ── Radial glow at top ── */
    body::after {
      content: '';
      position: fixed;
      top: -280px; left: 50%;
      transform: translateX(-50%);
      width: 1000px; height: 700px;
      background: radial-gradient(ellipse, rgba(238,0,0,0.07) 0%, transparent 65%);
      pointer-events: none;
      z-index: 0;
    }

    /* ── Hero ── */
    #hero-wrap {
      position: relative;
      z-index: 1;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 40px 24px 100px;
      text-align: center;
    }

    .brand {
      font-size: 11px;
      font-weight: 900;
      letter-spacing: 5px;
      color: var(--red);
      text-transform: uppercase;
      margin-bottom: 40px;
    }

    .headline {
      font-size: clamp(36px, 6.5vw, 68px);
      font-weight: 900;
      color: #fff;
      line-height: 1.1;
      letter-spacing: -2px;
      margin-bottom: 16px;
    }
    .headline em {
      font-style: normal;
      background: linear-gradient(130deg, #ff5555 0%, #ee0000 60%, #aa0000 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }

    .sub {
      font-size: 16px;
      color: var(--muted);
      margin-bottom: 44px;
      max-width: 380px;
      line-height: 1.65;
    }

    /* ── URL bar ── */
    .url-bar {
      display: flex;
      align-items: center;
      gap: 8px;
      width: 100%;
      max-width: 620px;
      background: #131313;
      border: 1.5px solid #272727;
      border-radius: 14px;
      padding: 6px 6px 6px 20px;
      transition: border-color 0.2s, box-shadow 0.2s;
    }
    .url-bar:focus-within {
      border-color: var(--red);
      box-shadow: 0 0 0 3px rgba(238,0,0,0.08);
    }

    #url {
      flex: 1;
      background: transparent;
      border: none;
      color: #fff;
      font-size: 15px;
      padding: 10px 0;
      outline: none;
      min-width: 0;
    }
    #url::placeholder { color: var(--muted2); }

    #fetch-btn {
      background: var(--red);
      color: #fff;
      border: none;
      border-radius: 10px;
      font-size: 14px;
      font-weight: 700;
      padding: 12px 28px;
      cursor: pointer;
      transition: background 0.15s, transform 0.08s;
      white-space: nowrap;
      flex-shrink: 0;
    }
    #fetch-btn:hover:not(:disabled) { background: var(--red-dark); }
    #fetch-btn:active:not(:disabled) { transform: scale(0.97); }
    #fetch-btn:disabled { opacity: 0.5; cursor: not-allowed; }

    /* ── URL error hint ── */
    #url-err {
      margin-top: 12px;
      font-size: 13px;
      color: #ff5555;
      min-height: 18px;
    }

    /* ── Modal overlay ── */
    #modal-overlay {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.84);
      backdrop-filter: blur(8px);
      -webkit-backdrop-filter: blur(8px);
      z-index: 200;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
      animation: overlayIn 0.22s ease;
    }
    @keyframes overlayIn {
      from { opacity: 0; }
      to   { opacity: 1; }
    }

    #modal-card {
      background: var(--surface);
      border: 1px solid #252525;
      border-radius: 20px;
      width: 100%;
      max-width: 900px;
      max-height: 92vh;
      overflow-y: auto;
      position: relative;
      animation: cardIn 0.3s cubic-bezier(0.34, 1.5, 0.64, 1);
      scrollbar-width: thin;
      scrollbar-color: #333 transparent;
    }
    #modal-card::-webkit-scrollbar { width: 5px; }
    #modal-card::-webkit-scrollbar-track { background: transparent; }
    #modal-card::-webkit-scrollbar-thumb { background: #333; border-radius: 3px; }

    @keyframes cardIn {
      from { opacity: 0; transform: translateY(28px) scale(0.96); }
      to   { opacity: 1; transform: translateY(0)   scale(1); }
    }

    /* ── Close button ── */
    #modal-close {
      position: absolute;
      top: 16px; right: 16px;
      width: 32px; height: 32px;
      border-radius: 50%;
      background: #222;
      border: 1px solid #303030;
      color: #666;
      font-size: 18px;
      line-height: 1;
      display: flex; align-items: center; justify-content: center;
      cursor: pointer;
      z-index: 10;
      transition: background 0.15s, color 0.15s;
    }
    #modal-close:hover { background: #2e2e2e; color: #eee; }

    /* ── Modal body ── */
    .modal-body {
      display: grid;
      grid-template-columns: 270px 1fr;
    }

    /* ── Left panel ── */
    .modal-left {
      padding: 32px 22px 32px 28px;
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
    }

    #thumb {
      width: 100%;
      aspect-ratio: 16 / 9;
      object-fit: cover;
      border-radius: 10px;
      background: #111;
      display: block;
      flex-shrink: 0;
    }

    #title {
      font-size: 13px;
      font-weight: 600;
      color: #ccc;
      line-height: 1.6;
      margin-top: 14px;
    }

    #dur-badge {
      display: inline-block;
      margin-top: 10px;
      background: var(--surface2);
      border: 1px solid var(--border2);
      border-radius: 5px;
      font-size: 11px;
      color: var(--muted);
      padding: 3px 9px;
      font-variant-numeric: tabular-nums;
    }

    /* ── Right panel ── */
    .modal-right {
      padding: 28px 28px 28px 24px;
      display: flex;
      flex-direction: column;
    }

    .section { margin-bottom: 24px; }

    .sec-label {
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 1.3px;
      color: #383838;
      margin-bottom: 14px;
    }

    /* ── Dual-handle range slider ── */
    .dual-range-wrap {
      position: relative;
      height: 28px;
      margin-bottom: 16px;
    }
    .dual-track {
      position: absolute;
      left: 0; right: 0; top: 50%;
      transform: translateY(-50%);
      height: 4px;
      background: #252525;
      border-radius: 4px;
      pointer-events: none;
    }
    .dual-fill {
      position: absolute;
      top: 0; height: 100%;
      background: var(--red);
      border-radius: 4px;
      left: 0%; width: 100%;
      pointer-events: none;
    }
    .dual-thumb {
      position: absolute;
      top: 0; left: 0;
      width: 100%; height: 100%;
      margin: 0; padding: 0;
      background: transparent;
      -webkit-appearance: none;
      appearance: none;
      pointer-events: none;
      outline: none;
    }
    .dual-thumb::-webkit-slider-runnable-track { background: transparent; }
    .dual-thumb::-webkit-slider-thumb {
      -webkit-appearance: none;
      pointer-events: all;
      width: 20px; height: 20px;
      background: #fff;
      border-radius: 50%;
      cursor: pointer;
      box-shadow: 0 0 0 2px rgba(238,0,0,0.28), 0 2px 6px rgba(0,0,0,0.75);
      transition: box-shadow 0.15s;
    }
    .dual-thumb::-webkit-slider-thumb:hover {
      box-shadow: 0 0 0 6px rgba(238,0,0,0.14), 0 2px 6px rgba(0,0,0,0.75);
    }
    .dual-thumb::-moz-range-track { background: transparent; border: none; }
    .dual-thumb::-moz-range-thumb {
      pointer-events: all;
      width: 20px; height: 20px;
      background: #fff;
      border-radius: 50%;
      border: none;
      cursor: pointer;
      box-shadow: 0 0 0 2px rgba(238,0,0,0.28), 0 2px 6px rgba(0,0,0,0.75);
    }

    /* ── HMS grid ── */
    .hms-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    .hms-group-label {
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: #363636;
      margin-bottom: 8px;
    }
    .hms-row { display: flex; align-items: center; gap: 4px; }
    .hms-row input[type=number] {
      width: 44px;
      background: var(--surface2);
      border: 1px solid var(--border2);
      border-radius: 6px;
      color: #fff;
      font-size: 14px;
      font-weight: 600;
      font-variant-numeric: tabular-nums;
      text-align: center;
      padding: 7px 4px;
      outline: none;
      transition: border-color 0.15s;
      -moz-appearance: textfield;
    }
    .hms-row input[type=number]::-webkit-outer-spin-button,
    .hms-row input[type=number]::-webkit-inner-spin-button { -webkit-appearance: none; }
    .hms-row input[type=number]:focus { border-color: var(--red); }
    .hms-sep { font-size: 15px; color: #2e2e2e; }

    /* ── Quality tabs ── */
    .quality-tabs { display: flex; gap: 6px; flex-wrap: wrap; }
    .tab {
      background: var(--surface2);
      border: 1.5px solid #282828;
      border-radius: 8px;
      color: #505050;
      font-size: 12px;
      font-weight: 700;
      padding: 7px 16px;
      cursor: pointer;
      transition: all 0.12s;
    }
    .tab:hover { border-color: #444; color: #aaa; }
    .tab.active { background: var(--red); border-color: var(--red); color: #fff; }

    /* ── Download button ── */
    #dl-btn {
      width: 100%;
      background: var(--red);
      color: #fff;
      border: none;
      border-radius: 12px;
      font-size: 15px;
      font-weight: 800;
      padding: 15px;
      cursor: pointer;
      letter-spacing: 0.2px;
      margin-top: auto;
      transition: background 0.15s, transform 0.08s;
    }
    #dl-btn:hover:not(:disabled) { background: var(--red-dark); }
    #dl-btn:active:not(:disabled) { transform: scale(0.99); }
    #dl-btn:disabled { opacity: 0.4; cursor: not-allowed; }

    /* ── Status strip (full width, below both columns) ── */
    #status-card {
      border-top: 1px solid var(--border);
      padding: 18px 28px 22px;
    }
    #status { font-size: 13px; color: var(--muted); min-height: 18px; }
    #status.err { color: #ff5555; }
    #status.ok  { color: #44cc44; }

    .progress-track {
      height: 3px;
      background: #1e1e1e;
      border-radius: 3px;
      overflow: hidden;
      margin-top: 10px;
    }
    .progress-fill {
      height: 100%;
      background: var(--red);
      border-radius: 3px;
      width: 0%;
      transition: width 0.3s ease;
    }
    .progress-fill.indeterminate {
      width: 35%;
      animation: indeterminate 1.3s ease-in-out infinite;
    }
    @keyframes indeterminate {
      0%   { transform: translateX(-150%); }
      100% { transform: translateX(400%); }
    }

    #dl-link a {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      margin-top: 14px;
      background: #0d2a0d;
      border: 1px solid #1a5c1a;
      color: #44cc44;
      text-decoration: none;
      padding: 10px 18px;
      border-radius: 8px;
      font-size: 14px;
      font-weight: 700;
      transition: background 0.15s;
    }
    #dl-link a:hover { background: #112e11; }

    /* ── Responsive ── */
    @media (max-width: 620px) {
      .modal-body { grid-template-columns: 1fr; }
      .modal-left { border-right: none; border-bottom: 1px solid var(--border); }
      .headline { letter-spacing: -1px; }
    }

    [hidden] { display: none !important; }
  </style>
</head>
<body>

<!-- ── Hero ── -->
<div id="hero-wrap">
  <div class="brand" style="display:flex;align-items:center;gap:10px;justify-content:center;">
    <img src="/logo" alt="" style="height:32px;">SliceYT
  </div>
  <h1 class="headline">Grab any moment<br>from <em>YouTube.</em></h1>
  <p class="sub">Paste a link. Set your timestamps.<br>Download the exact clip you want.</p>
  <div class="url-bar">
    <input id="url" type="text" placeholder="https://youtube.com/watch?v=\u2026" onkeydown="if(event.key==='Enter') fetchInfo()">
    <button id="fetch-btn" onclick="fetchInfo()">Get Video</button>
  </div>
  <div id="url-err"></div>
</div>

<!-- ── Modal overlay ── -->
<div id="modal-overlay" hidden>
  <div id="modal-card">
    <button id="modal-close" onclick="closeModal()" title="Close">&times;</button>

    <div class="modal-body">
      <!-- Left: thumbnail + meta -->
      <div class="modal-left">
        <img id="thumb" src="" alt="thumbnail">
        <div id="title"></div>
        <span id="dur-badge" hidden></span>
      </div>

      <!-- Right: controls -->
      <div class="modal-right">

        <div class="section">
          <div class="sec-label">Clip Range</div>
          <div class="dual-range-wrap">
            <div class="dual-track"><div id="range-fill" class="dual-fill"></div></div>
            <input type="range" id="start-slider" class="dual-thumb" min="0" max="100" value="0"   oninput="onStartSlider()">
            <input type="range" id="end-slider"   class="dual-thumb" min="0" max="100" value="100" oninput="onEndSlider()">
          </div>
          <div class="hms-grid">
            <div>
              <div class="hms-group-label">Start</div>
              <div class="hms-row">
                <input type="number" id="sh" value="0" min="0" oninput="hmsToSlider('start')">
                <span class="hms-sep">:</span>
                <input type="number" id="sm" value="0" min="0" max="59" oninput="hmsToSlider('start')">
                <span class="hms-sep">:</span>
                <input type="number" id="ss" value="0" min="0" max="59" oninput="hmsToSlider('start')">
              </div>
            </div>
            <div>
              <div class="hms-group-label">End</div>
              <div class="hms-row">
                <input type="number" id="eh" value="0" min="0" oninput="hmsToSlider('end')">
                <span class="hms-sep">:</span>
                <input type="number" id="em" value="0" min="0" max="59" oninput="hmsToSlider('end')">
                <span class="hms-sep">:</span>
                <input type="number" id="es" value="0" min="0" max="59" oninput="hmsToSlider('end')">
              </div>
            </div>
          </div>
        </div>

        <div class="section">
          <div class="sec-label">Quality</div>
          <div class="quality-tabs">
            <button class="tab" data-q="2160p" onclick="selectTab(this)">4K</button>
            <button class="tab active" data-q="1080p" onclick="selectTab(this)">1080p</button>
            <button class="tab" data-q="720p" onclick="selectTab(this)">720p</button>
            <button class="tab" data-q="480p" onclick="selectTab(this)">480p</button>
            <button class="tab" data-q="360p" onclick="selectTab(this)">360p</button>
          </div>
        </div>

        <button id="dl-btn" onclick="startDownload()">Download Clip</button>
      </div>
    </div>

    <!-- Status strip (full width, below both columns) -->
    <div id="status-card" hidden>
      <div id="status"></div>
      <div id="progress-area" hidden>
        <div class="progress-track">
          <div class="progress-fill" id="progress-fill"></div>
        </div>
      </div>
      <div id="dl-link"></div>
    </div>

  </div>
</div>

<script>
var videoDuration = 0;

// Close modal and reset error
function closeModal() {
  document.getElementById('modal-overlay').hidden = true;
}

// Dismiss modal on backdrop click
document.getElementById('modal-overlay').addEventListener('click', function(e) {
  if (e.target === this) closeModal();
});

// Close on Escape
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape' && !document.getElementById('modal-overlay').hidden) closeModal();
});

function setUrlErr(msg) {
  document.getElementById('url-err').textContent = msg;
}

function fetchInfo() {
  var url = document.getElementById('url').value.trim();
  if (!url) return;
  setUrlErr('');
  var btn = document.getElementById('fetch-btn');
  btn.disabled = true;
  btn.textContent = 'Fetching\u2026';
  fetch('/fetch-info', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({url: url})
  }).then(r => r.json()).then(data => {
    btn.disabled = false;
    btn.textContent = 'Get Video';
    if (!data.title && !data.thumbnail) {
      setUrlErr('Could not fetch video info. Check the URL and try again.');
      return;
    }
    console.log('[SliceYT] /fetch-info raw response:', JSON.stringify(data));
    videoDuration = (data.duration != null && data.duration > 0) ? data.duration : 0;

    document.getElementById('title').textContent = data.title || '';
    document.getElementById('thumb').src = data.thumbnail || '';

    var badge = document.getElementById('dur-badge');
    if (videoDuration > 0) {
      badge.textContent = fmtTime(videoDuration);
      badge.hidden = false;
    } else {
      badge.hidden = true;
    }

    var startSlider = document.getElementById('start-slider');
    var endSlider   = document.getElementById('end-slider');
    if (videoDuration > 0) {
      startSlider.max = videoDuration;
      endSlider.max   = videoDuration;
      startSlider.value = 0;
      endSlider.value   = videoDuration;
    } else {
      startSlider.value = 0;
      endSlider.value   = endSlider.max;
    }
    startSlider.dispatchEvent(new Event('input'));
    endSlider.dispatchEvent(new Event('input'));
    console.log('[SliceYT] videoDuration=' + videoDuration + '  startSlider.max=' + startSlider.max + '  endSlider.max=' + endSlider.max + '  endSlider.value=' + endSlider.value);
    syncToHms('start');
    syncToHms('end');
    updateFill();
    updateZIndex();

    // Reset state from any previous run
    document.getElementById('status-card').hidden = true;
    document.getElementById('dl-link').innerHTML = '';
    document.getElementById('dl-btn').disabled = false;

    // Open modal
    document.getElementById('modal-overlay').hidden = false;
  }).catch(() => {
    btn.disabled = false;
    btn.textContent = 'Get Video';
    setUrlErr('Network error. Check the URL and try again.');
  });
}

// ── Dual-handle range slider logic ──
function onStartSlider() {
  var s = document.getElementById('start-slider');
  var e = document.getElementById('end-slider');
  if (parseInt(s.value) >= parseInt(e.value)) s.value = parseInt(e.value) - 1;
  syncToHms('start');
  updateFill();
  updateZIndex();
}

function onEndSlider() {
  var s = document.getElementById('start-slider');
  var e = document.getElementById('end-slider');
  if (parseInt(e.value) <= parseInt(s.value)) e.value = parseInt(s.value) + 1;
  syncToHms('end');
  updateFill();
  updateZIndex();
}

function syncToHms(which) {
  var val = parseInt(document.getElementById(which + '-slider').value);
  var p = which[0]; // 's' or 'e'
  document.getElementById(p + 'h').value = Math.floor(val / 3600);
  document.getElementById(p + 'm').value = Math.floor((val % 3600) / 60);
  document.getElementById(p + 's').value = val % 60;
}

function hmsToSlider(which) {
  var p = which[0];
  var h = parseInt(document.getElementById(p + 'h').value) || 0;
  var m = parseInt(document.getElementById(p + 'm').value) || 0;
  var s = parseInt(document.getElementById(p + 's').value) || 0;
  var total = Math.max(0, Math.min(h * 3600 + m * 60 + s, videoDuration));
  var sv = parseInt(document.getElementById('start-slider').value);
  var ev = parseInt(document.getElementById('end-slider').value);
  if (which === 'start') total = Math.min(total, ev - 1);
  else                   total = Math.max(total, sv + 1);
  document.getElementById(which + '-slider').value = total;
  updateFill();
  updateZIndex();
}

function updateFill() {
  var sEl = document.getElementById('start-slider');
  var eEl = document.getElementById('end-slider');
  var maxVal = parseInt(sEl.max) || 100;
  var pctS = parseInt(sEl.value) / maxVal * 100;
  var pctE = parseInt(eEl.value) / maxVal * 100;
  var fill = document.getElementById('range-fill');
  fill.style.left  = pctS + '%';
  fill.style.width = (pctE - pctS) + '%';
}

function updateZIndex() {
  var sv = parseInt(document.getElementById('start-slider').value);
  // When start handle is in the upper half, bring it to front so it stays reachable
  var startOnTop = sv > videoDuration * 0.5;
  document.getElementById('start-slider').style.zIndex = startOnTop ? 4 : 2;
  document.getElementById('end-slider').style.zIndex   = startOnTop ? 2 : 4;
}

function selectTab(el) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
}

function startDownload() {
  var url = document.getElementById('url').value.trim();
  if (!url) { showStatusCard(); setStatus('Enter a YouTube URL.', 'err'); return; }
  var start = parseInt(document.getElementById('start-slider').value) || 0;
  var end   = parseInt(document.getElementById('end-slider').value)   || 0;
  if (end <= start) { showStatusCard(); setStatus('End time must be after start time.', 'err'); return; }
  var quality = (document.querySelector('.tab.active') || {}).dataset.q || '1080p';

  document.getElementById('dl-btn').disabled = true;
  document.getElementById('dl-link').innerHTML = '';
  showStatusCard();
  show('progress-area');
  setProgress(0, true);
  setStatus('Starting download\u2026', '');

  fetch('/download', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({url: url, quality: quality, start: start, end: end})
  }).then(r => r.json()).then(data => {
    if (!data.job_id) {
      setStatus(data.error || 'Error starting download.', 'err');
      resetUI(); return;
    }
    listenProgress(data.job_id);
  }).catch(() => { setStatus('Error starting download.', 'err'); resetUI(); });
}

function listenProgress(jobId) {
  var evtSrc = new EventSource('/progress/' + jobId);
  evtSrc.onmessage = function(e) {
    var d = JSON.parse(e.data);
    setStatus(d.msg, '');
    if (d.pct !== undefined) setProgress(d.pct * 100, false);
    if (d.done) {
      evtSrc.close();
      document.getElementById('dl-btn').disabled = false;
      if (d.ok) {
        setProgress(100, false);
        setStatus('Download complete \u2014 your file is saving.', 'ok');
        var a = document.createElement('a');
        a.href = '/file/' + jobId;
        a.download = 'clip.mp4';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      } else {
        hide('progress-area');
        setStatus(d.msg, 'err');
      }
    }
  };
  evtSrc.onerror = function() {
    evtSrc.close();
    setStatus('Connection error.', 'err');
    resetUI();
  };
}

function fmtTime(s) {
  var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h > 0 ? h + ':' + pad(m) + ':' + pad(sec) : m + ':' + pad(sec);
}
function pad(n) { return n < 10 ? '0' + n : '' + n; }
function show(id) { document.getElementById(id).hidden = false; }
function hide(id) { document.getElementById(id).hidden = true; }
function showStatusCard() { show('status-card'); }
function setStatus(msg, cls) {
  var el = document.getElementById('status');
  el.textContent = msg; el.className = cls;
}
function setProgress(pct, indeterminate) {
  var fill = document.getElementById('progress-fill');
  fill.className = 'progress-fill' + (indeterminate ? ' indeterminate' : '');
  if (!indeterminate) fill.style.width = pct + '%';
}
function resetUI() {
  document.getElementById('dl-btn').disabled = false;
  hide('progress-area');
}
</script>
</body>
</html>"""

_HTML_PAGE = _build_html()


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return _HTML_PAGE


@app.route("/fetch-info", methods=["POST"])
def api_fetch_info():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    video_id = extract_video_id(url)
    if not url or not video_id:
        return jsonify({"error": "invalid url"})
    title, thumbnail, duration = fetch_video_info(url)
    if not thumbnail:
        thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    return jsonify({"title": title, "thumbnail": thumbnail, "duration": duration})


@app.route("/download", methods=["POST"])
def api_download():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    quality = data.get("quality", "720p")
    start_sec = int(data.get("start", 0))
    end_sec = int(data.get("end", 0))

    video_id = extract_video_id(url)
    if not video_id:
        return jsonify({"error": "invalid url"}), 400
    if end_sec <= start_sec:
        return jsonify({"error": "end must be after start"}), 400

    job_id = str(uuid.uuid4())
    q: queue.Queue = queue.Queue()
    with _jobs_lock:
        _jobs[job_id] = {"status": "pending", "file": None, "msg": "", "queue": q}

    out_name = f"clip_{video_id}_{start_sec}-{end_sec}.mp4"
    out_path = os.path.join(DOWNLOAD_DIR, out_name)

    def run():
        def on_progress(pct: float, msg: str):
            q.put({"pct": pct, "msg": msg, "done": False, "ok": False})

        try:
            ok = download_with_ytdlp(url, quality, start_sec, end_sec, out_path, progress_callback=on_progress)
            if ok and os.path.exists(out_path):
                with _jobs_lock:
                    _jobs[job_id]["status"] = "done"
                    _jobs[job_id]["file"] = out_path
                q.put({"pct": 1.0, "msg": "Complete", "done": True, "ok": True})
            else:
                with _jobs_lock:
                    _jobs[job_id]["status"] = "error"
                    _jobs[job_id]["msg"] = "yt-dlp failed"
                q.put({"pct": 0, "msg": "Download failed", "done": True, "ok": False})
        except Exception:
            err = traceback.format_exc()
            print(err)
            with _jobs_lock:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["msg"] = err[:500]
            q.put({"pct": 0, "msg": "Error: " + err[:200], "done": True, "ok": False})

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/progress/<job_id>")
def api_progress(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return Response(
            'data: {"done":true,"ok":false,"msg":"Job not found"}\n\n',
            mimetype="text/event-stream",
        )

    q: queue.Queue = job["queue"]

    def generate():
        while True:
            try:
                event = q.get(timeout=30)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("done"):
                    break
            except queue.Empty:
                yield ": keepalive\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/logo")
def logo():
    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview-removebg-preview.png")
    return send_file(logo_path, mimetype="image/png")


@app.route("/file/<job_id>")
def api_file(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job or job["status"] != "done" or not job["file"]:
        return "File not found", 404
    path = job["file"]
    if not os.path.exists(path):
        return "File not found", 404
    return send_file(
        path,
        as_attachment=True,
        download_name=os.path.basename(path),
        mimetype="video/mp4",
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000, threaded=True)

