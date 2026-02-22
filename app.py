"""
YouTube Clip Downloader - Download a time range from any YouTube video as MP4.
Uses yt-dlp for download and trim in one command.
"""

import customtkinter as ctk
import re
import os
import traceback
import subprocess
import threading
from pathlib import Path


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
    """Return env with Python Scripts and FFmpeg bin prepended to PATH."""
    env = os.environ.copy()
    env["PATH"] = (
        "C:\\Users\\Admin\\AppData\\Local\\Programs\\Python\\Python313\\Scripts;"
        + "C:\\Users\\Admin\\AppData\\Local\\Microsoft\\WinGet\\Packages\\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\\ffmpeg-8.0.1-full_build\\bin;"
        + env.get("PATH", "")
    )
    return env


def download_with_ytdlp(url: str, quality: str, start_sec: int, end_sec: int, out_path: str) -> bool:
    format_map = {
        "720p": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]",
        "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]",
        "Best available": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best",
    }
    fmt = format_map.get(quality, "best")
    start_str = format_ytdlp_time(start_sec)
    end_str = format_ytdlp_time(end_sec)
    section = f"*{start_str}-{end_str}"
    cmd = [
        "yt-dlp",
        "--force-ipv4",
        "-f", fmt,
        "--download-sections", section,
        "--merge-output-format", "mp4",
        "-o", out_path,
        url,
    ]
    print("DEBUG: About to run yt-dlp:", cmd)
    proc = subprocess.Popen(cmd, stdout=None, stderr=None, env=_get_subprocess_env())
    proc.wait()
    print("DEBUG: yt-dlp finished with returncode:", proc.returncode)
    return proc.returncode == 0


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("YouTube Clip Downloader")
        self.geometry("520x320")
        self.minsize(480, 280)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 20, "pady": 8}
        pad_sm = {"padx": 20, "pady": 4}

        ctk.CTkLabel(self, text="YouTube URL").pack(anchor="w", **pad_sm)
        self.url_entry = ctk.CTkEntry(self, placeholder_text="Paste YouTube URL...", height=36)
        self.url_entry.pack(fill="x", **pad)

        time_frame = ctk.CTkFrame(self, fg_color="transparent")
        time_frame.pack(fill="x", **pad)
        opts = {"values": [f"{i:02d}" for i in range(60)], "width": 65}

        ctk.CTkLabel(time_frame, text="Start (h:m:s)").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        self.start_h = ctk.CTkOptionMenu(time_frame, values=[str(i) for i in range(24)], width=65)
        self.start_h.grid(row=1, column=0, padx=(0, 4), pady=2)
        self.start_m = ctk.CTkOptionMenu(time_frame, **opts)
        self.start_m.grid(row=1, column=1, padx=2, pady=2)
        self.start_s = ctk.CTkOptionMenu(time_frame, **opts)
        self.start_s.grid(row=1, column=2, padx=2, pady=2)

        ctk.CTkLabel(time_frame, text="End (h:m:s)").grid(row=0, column=3, sticky="w", padx=(20, 8), pady=2)
        self.end_h = ctk.CTkOptionMenu(time_frame, values=[str(i) for i in range(24)], width=65)
        self.end_h.grid(row=1, column=3, padx=(20, 4), pady=2)
        self.end_m = ctk.CTkOptionMenu(time_frame, **opts)
        self.end_m.grid(row=1, column=4, padx=2, pady=2)
        self.end_s = ctk.CTkOptionMenu(time_frame, **opts)
        self.end_s.grid(row=1, column=5, padx=2, pady=2)

        dl_frame = ctk.CTkFrame(self, fg_color="transparent")
        dl_frame.pack(fill="x", **pad)
        ctk.CTkLabel(dl_frame, text="Quality").pack(side="left", padx=(0, 10))
        self.quality_var = ctk.StringVar(value="720p")
        ctk.CTkOptionMenu(
            dl_frame,
            values=["720p", "1080p", "Best available"],
            variable=self.quality_var,
            width=120,
        ).pack(side="left", padx=(0, 20))
        self.download_btn = ctk.CTkButton(dl_frame, text="Download", width=120, height=36, command=self._on_download)
        self.download_btn.pack(side="left")

        self.status_label = ctk.CTkLabel(self, text="", text_color="gray")
        self.status_label.pack(anchor="w", **pad_sm)

    def _on_download(self):
        url = self.url_entry.get().strip()
        if not url:
            self.status_label.configure(text="Enter a YouTube URL", text_color="orange")
            return

        video_id = extract_video_id(url)
        if not video_id:
            self.status_label.configure(text="Invalid YouTube URL", text_color="red")
            return

        start_sec = seconds_from_hms(int(self.start_h.get()), int(self.start_m.get()), int(self.start_s.get()))
        end_sec = seconds_from_hms(int(self.end_h.get()), int(self.end_m.get()), int(self.end_s.get()))
        if end_sec <= start_sec:
            self.status_label.configure(text="End time must be after start", text_color="red")
            return

        self.download_btn.configure(state="disabled")
        self.status_label.configure(text="Downloading...", text_color="gray")

        quality = self.quality_var.get()
        desktop = str(Path.home() / "Desktop")
        out_name = f"clip_{video_id}_{start_sec}-{end_sec}.mp4"
        out_path = os.path.join(desktop, out_name)

        def download():
            err_msg = "Unknown error"
            try:
                print("DEBUG: Download thread started")
                print("DEBUG: Calling download_with_ytdlp(url=%r, quality=%r, start_sec=%r, end_sec=%r, out_path=%r)" % (url, quality, start_sec, end_sec, out_path))
                ok = download_with_ytdlp(url, quality, start_sec, end_sec, out_path)
                print("DEBUG: download_with_ytdlp returned:", ok)
                if ok and os.path.exists(out_path):
                    print("DEBUG: Success, output exists at:", out_path)
                    self.after(0, lambda: self._done(True, ""))
                    return
                if not ok:
                    err_msg = "yt-dlp failed (check terminal for details)"
                else:
                    err_msg = "Output file not found after download"
            except Exception:
                print("DEBUG: Exception in download thread:")
                print(traceback.format_exc())
                err_msg = str(traceback.format_exc())[:500]
            print("DEBUG: Download finished with error:", err_msg)
            self.after(0, lambda: self._done(False, err_msg))

        def done_ui(ok, err):
            self.download_btn.configure(state="normal")
            if ok:
                self.status_label.configure(text="Saved to Desktop", text_color="lime")
            else:
                self.status_label.configure(text=f"Failed: {err}", text_color="red")

        self._done = done_ui
        threading.Thread(target=download, daemon=True).start()


if __name__ == "__main__":
    app = App()
    app.mainloop()
