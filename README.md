# Video Downloader (for the person who builds and shares it)

A simple Windows app: paste a link (YouTube, Twitter/X, many other sites), choose quality, press Download.
People who use it install it with a normal Setup.exe. They do NOT need Python, ffmpeg or a terminal.

## What is inside

| Path | What it is |
|---|---|
| `app/video_downloader.py` | The window (Tkinter) |
| `app/core.py` | The engine that runs yt-dlp, shows progress, handles errors |
| `installer/installer.iss` | Recipe for the Setup.exe (Inno Setup) |
| `.github/workflows/build.yml` | Makes GitHub build the Setup.exe for you, for free |
| `build_local.bat` | Alternative: build on your own Windows PC |
| `app/icon.ico` | The app icon (replace it with your own .ico if you like) |
| `USER-GUIDE.md` | Plain-language guide you can give to users |

## Way 1 (recommended): let GitHub build it

1. Create a free account at github.com and make a **new repository** (public is simplest and free).
2. Upload everything in this folder to it. Easiest with **GitHub Desktop**: add the folder, press *Publish repository*. The hidden `.github` folder must be included.
3. In the repository open the **Actions** tab, choose **Build Windows installer**, press **Run workflow**, enter a version (for example `1.0.0`).
4. After a few minutes, open the finished run and download the **VideoDownloader** artifact. It holds `VideoDownloader-Setup.exe` and a portable zip.
5. To publish a proper download page for other people: create a tag instead, for example `v1.0.0` (GitHub Desktop: *Repository > Create tag*, then push). The workflow then creates a **Release** with the files attached. Share that Releases link.

To ship an update, change the code, push, and create a new tag such as `v1.0.1`.

## Way 2: build on your own PC

Install Python (tick "Add Python to PATH") and optionally Inno Setup 6, then double-click `build_local.bat`.
Without Inno Setup you still get a ready-to-run folder in `dist\VideoDownloader`.

## How it works (so you can change it)

- The first time someone opens the app, a short "Before you start" notice appears. They must tick "I understand" to continue; the choice is saved in `settings.json`. The wording is `NOTICE_TEXT` at the top of `app/video_downloader.py`.

- The app runs `yt-dlp.exe` and `ffmpeg.exe` from its `bin` folder.
- On first start, `yt-dlp.exe` is copied to `%LOCALAPPDATA%\VideoDownloader\`. The app runs `yt-dlp -U` on every start,
  so users get fixes automatically when YouTube or X change. No new Setup.exe is needed for that.
- Settings (folder, quality, browser) are saved in the same folder.
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
