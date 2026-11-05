# Video Downloader (for the person who builds and shares it)

A modern Windows app (built with CustomTkinter): paste a link (YouTube, Twitter/X, many other sites), see a preview, add it to a queue with a progress bar per video, and find it again in the History tab. Light, dark and system themes.
People who use it install it with a normal Setup.exe. They do NOT need Python, ffmpeg or a terminal.

## What is inside

| Path | What it is |
|---|---|
| `app/video_downloader.py` | The window (CustomTkinter): preview, queue, history, theme |
| `app/core.py` | The engine: runs yt-dlp, picks the best streams, estimates sizes, checks for app updates |
| `app/build_info.py` | Version + repository name. The GitHub build rewrites it automatically; the committed copy is only a placeholder |
| `installer/installer.iss` | Recipe for the Setup.exe (Inno Setup) |
| `.github/workflows/build.yml` | Makes GitHub build the Setup.exe for you, for free |
| `requirements.txt` | Python packages needed to run or build it (`pip install -r requirements.txt`) |
| `build_local.bat` | Alternative: build on your own Windows PC |
| `app/icon.ico` | The app icon (replace it with your own .ico if you like) |
| `USER-GUIDE.md` | Plain-language guide you can give to users |

## Way 1 (recommended): let GitHub build it

1. Create a free account at github.com and make a **new repository** (public is simplest and free).
2. Upload everything in this folder to it. Easiest with **GitHub Desktop**: add the folder, press *Publish repository*. The hidden `.github` folder must be included.
3. In the repository open the **Actions** tab, choose **Build Windows installer**, press **Run workflow**, enter a version (for example `1.0.0`).
4. After a few minutes, open the finished run and download the **VideoDownloader** artifact. It holds `VideoDownloader-Setup.exe` and a portable zip.
5. To publish a proper download page for other people: create a tag instead, for example `v1.0.0` (GitHub Desktop: *Repository > Create tag*, then push). The workflow then creates a **Release** with the files attached. Share that Releases link.

To ship an update, change the code, push, and create a new tag such as `v1.1.0` (use a higher number than before). People install the new Setup.exe over their old version; settings and history are kept.

## Way 2: build on your own PC

Install Python (tick "Add Python to PATH") and optionally Inno Setup 6, then double-click `build_local.bat`.
Without Inno Setup you still get a ready-to-run folder in `dist\VideoDownloader`.

## Try it without building (for you, while developing)

```
pip install -r requirements.txt
pip install yt-dlp
python app\video_downloader.py
```
You also need ffmpeg on your PATH for HD merging and MP3 (`winget install ffmpeg`). The installed app has its own copy.

## In-app updates (like VS Code)

When the app starts it asks GitHub for your newest **Release**. If its version is higher than the installed one,
a banner appears: **Update now / What's new / Later**. "Update now" downloads `VideoDownloader-Setup.exe`,
checks it against the published `.sha256` checksum, installs it quietly, and reopens the app.

What it needs from you:
- The repository must be **public** (the app asks GitHub without logging in).
- Every release must be made by pushing a **tag with a higher number** (`v1.2.0`, then `v1.2.1`, ...).
  The version inside the app comes from the tag, and the repository name from GitHub, automatically.
- The release must contain `VideoDownloader-Setup.exe` and `VideoDownloader-Setup.exe.sha256` (the workflow does this).
- Nothing is ever downloaded from anywhere except `github.com/<your repo>/releases/download/`.
- Anyone who installed the old version (before in-app updates existed) must install this version by hand once;
  from then on updates are one click.

## How it works (so you can change it)

- The first time someone opens the app, a short "Before you start" notice appears. They must tick "I understand" to continue; the choice is saved in `settings.json`. The wording is `NOTICE_TEXT` at the top of `app/video_downloader.py`.

- The app runs `yt-dlp.exe` and `ffmpeg.exe` from its `bin` folder.
- On first start, `yt-dlp.exe` is copied to `%LOCALAPPDATA%\VideoDownloader\`. The app runs `yt-dlp -U` on every start,
  so users get fixes automatically when YouTube or X change. No new Setup.exe is needed for that.
- Settings, the download history (`history.json`) and the theme are saved in the same folder.
- Pasting a link runs a quick lookup (`yt-dlp --dump-single-json`) to get the title, thumbnail and every available quality.
- The app chooses the exact video + audio streams itself: highest resolution, 60 fps preferred, HDR avoided (it looks washed out on normal screens), H.264 + AAC up to 1080p (plays everywhere), VP9 + Opus above that. Those same streams are what the size list shows, so the sizes match what gets downloaded.
- Files are `.mp4` when the streams allow it, otherwise `.mkv` (for example 4K VP9/Opus). VLC plays both.
- YouTube needs a JavaScript runtime for full quality. The installer bundles **Deno** in `bin\deno.exe` and yt-dlp is told where it is.
- Downloads run one at a time. MP3 downloads work in a private temp folder, so they can never delete a video you already saved.
- The Setup.exe installs per user (no admin password needed).

## Things to know before sharing

- **Windows SmartScreen:** because the installer is not code-signed (certificates cost money), Windows shows
  "Windows protected your PC". Users click **More info > Run anyway**. Tell them in advance (see USER-GUIDE.md).
- **Antivirus:** PyInstaller apps are sometimes flagged by mistake. Submitting the file to Microsoft Defender's false-positive form helps.
- **Size:** the installer is large (roughly 100-200 MB) because ffmpeg is included.
- **Rules:** YouTube's and X's terms don't allow downloading without their permission, and copyright law applies.
  Sharing with friends and classmates for personal use is one thing; a public site or heavy promotion can bring
  complaints or takedowns, and you must not charge money or run ads on it. This is general information, not legal advice.
- **ffmpeg license:** the build uses an LGPL version of FFmpeg and ships `THIRD-PARTY-NOTICES.txt`. Keep that file.
- **Not tested on Windows by me:** the engine and the window were tested on Linux with local files. The Windows-specific
  parts (installer, PyInstaller build, SmartScreen behavior, Chrome login on Windows) are written carefully but need
  one real run on your PC before you share.