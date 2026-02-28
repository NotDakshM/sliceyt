# YouTube Clip Downloader — Claude Code Instructions

## What This Project Is
A Flask web app that lets users download trimmed clips from YouTube videos.
The user pastes a YouTube URL, sets a start and end time, picks a quality, and downloads the clip as an MP4.

## Tech Stack
- **Backend:** Python / Flask (`app.py`) — already built and working
- **Frontend:** HTML/CSS/JS served directly from Flask (inside `_build_html()` in `app.py`)
- **Tools used:** yt-dlp, FFmpeg

## Existing API Routes (DO NOT change these)
- `GET /` — serves the main HTML page
- `POST /fetch-info` — fetches video title, thumbnail, and duration in seconds
- `POST /download` — starts a download job, returns `job_id`
- `GET /progress/<job_id>` — Server-Sent Events stream for progress updates
- `GET /file/<job_id>` — serves the finished MP4 file for download

## Current Working State — DO NOT BREAK THIS
As of latest checkpoint, the following is fully working:
- Duration is correctly fetched from /fetch-info for any video length (tested: 2 min, 33 min, 6 hours)
- Dual range slider sets left handle to 0 and right handle to exact video duration on every fetch
- HMS fields are synced bidirectionally with the slider
- Quality tabs: 4K 2160p / 1080p / 720p / 480p / 360p
- Download Clip button triggers download correctly

## Frontend Rules
- **Always invoke the frontend-design skill before writing any frontend code, every session, no exceptions.**
- The frontend HTML lives inside the `_build_html()` function in `app.py`. All UI changes go there.
- Use modern, clean, professional design — dark theme preferred (dark background, bright accents).
- The UI must feel like a real tool, not a default HTML form. No raw unstyled inputs.
- Use smooth animations and transitions where appropriate.
- Mobile responsive is a bonus but desktop-first is fine for now.

## Backend Rules
- Do NOT modify any backend logic, routes, or the `download_with_ytdlp` function unless explicitly asked.
- Do NOT change subprocess commands, PATH settings, or yt-dlp arguments.
- The backend is working correctly — frontend is the focus.

## Key UI Elements
1. Hero headline + URL input + Fetch button — visible on load, everything else hidden
2. After fetch: modal/overlay reveals with thumbnail, title, video duration
3. Dual range slider (two handles on one track) for Start and End
4. HMS fields synced bidirectionally to the slider
5. Quality tabs: 4K 2160p / 1080p / 720p / 480p / 360p
6. Full-width red Download Clip button
7. Status card with progress bar — hidden until download starts
8. Green Save MP4 link on completion

## Development Workflow
- Always test on localhost (`python app.py`) before considering anything done.
- **Screenshot loop:** After every UI change, take a screenshot of the localhost page, review it visually, identify anything that looks broken or unprofessional, and fix it before finishing.
- Do NOT push to GitHub unless explicitly told to.
- Keep all changes inside `app.py` unless creating new static asset files.
- **When fixing a bug, only change the specific broken thing. Do not rewrite or refactor surrounding code.**

## Known Bugs — Never Repeat These

### Dual Range Slider Duration
- Slider max and end value MUST come from the `duration` field returned by `/fetch-info`
- NEVER hardcode 3600 or any number as the slider max or fallback
- After setting `.max` and `.value` on both sliders, always dispatch input events to force browser re-render:
  startSlider.dispatchEvent(new Event('input'))
  endSlider.dispatchEvent(new Event('input'))
- updateFill() must always read sEl.max directly from the slider element — never from an external variable
- On every new fetch: left handle = 0, right handle = fetched duration, Start HMS = 00:00:00, End HMS = actual video duration
### Auto Download After Clip is Ready
- When the SSE progress stream receives done=true and ok=true, the browser must automatically trigger the file download
- Do this by creating a hidden anchor element, setting href to /file/jobId, setting download attribute, appending to body, calling .click(), then removing it
- Do NOT show a "Save MP4" link that requires manual clicking — the download must be automatic
- Do not change this behavior when making other fixes

### Slider Fill Gradient
- Fill percentage must always be (slider.value / slider.max) * 100
- Never base the fill on a hardcoded max value

### YouTube Shared Links with &t= Parameter
- YouTube shared links sometimes contain &t=100 which means "start at 100 seconds"
- This is NOT a bug — it is expected behavior from YouTube
- Future improvement: auto-read the t= parameter from the URL and pre-fill the Start HMS field with that value

### General Safety Rules
- After any change, verify: dual handles still present, HMS fields synced, duration correct
- If a fix breaks something else, revert before trying again
- Never rewrite the whole slider logic to fix a small bug

## Brand / Style Direction
- Dark background (#0f0f0f)
- Cards: #181818
- Accent: YouTube red #ee0000
- Clean sans-serif font (Inter or system-ui)
- Subtle animated background
- Progress bar custom styled — not default browser element

## Railway Deployment Configuration — DO NOT CHANGE THESE

### Working Setup (as of latest checkpoint)
- yt-dlp installed via requirements.txt as Python package
- ffmpeg installed via RAILPACK_DEPLOY_APT_PACKAGES=ffmpeg environment variable in Railway
- deno installed via railpack.json packages
- Cookies passed via COOKIES_CONTENT environment variable in Railway
- Gunicorn timeout set to 300 seconds in Procfile to handle long downloads

### Procfile
web: gunicorn app:app --bind 0.0.0.0:$PORT --timeout 300 --workers 2 --worker-class gthread --threads 4

### railpack.json
deno: latest (for YouTube JS challenge solving)

### Railway Environment Variables Required
- COOKIES_CONTENT: YouTube cookies in Netscape format
- RAILPACK_DEPLOY_APT_PACKAGES: ffmpeg
- PORT: set automatically by Railway

### yt-dlp command flags that work on Railway
- --remote-components ejs:github
- --js-runtimes deno
- --cookies /tmp/cookies.txt (written from COOKIES_CONTENT env var at startup)

### DO NOT
- Change the Procfile timeout below 300
- Remove --remote-components or --js-runtimes flags
- Hardcode any ports
- Remove the COOKIES_CONTENT startup block
- Remove the disk cleanup thread
