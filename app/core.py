"""
core.py - the download engine (no GUI code in here).

It drives the yt-dlp program as a separate process. That design means the
yt-dlp engine can be updated on its own (yt-dlp -U) without rebuilding the app,
which matters because YouTube / X change often.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

IS_WINDOWS = sys.platform.startswith("win")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # hides the black console box on Windows

PLATFORMS = ["Auto-detect", "YouTube", "Twitter / X", "Other website"]
QUALITIES = ["Best available", "1080p", "720p", "480p", "360p", "Audio only (MP3)"]
BROWSERS = ["None", "chrome", "edge", "firefox", "brave"]


# ------------------------------------------------------------------ paths

def app_dir() -> Path:
    """Folder the app lives in (works both as a script and as a built .exe)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(name: str) -> Path:
    """Location of a file bundled inside the app (like the icon)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", app_dir())) / name
    return Path(__file__).resolve().parent / name


def data_dir() -> Path:
    """Per-user folder for settings and the updatable copy of yt-dlp."""
    base = (
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("XDG_DATA_HOME")
        or str(Path.home() / ".local" / "share")
    )
    folder = Path(base) / "VideoDownloader"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _exe(name: str) -> str:
    return name + (".exe" if IS_WINDOWS else "")


def bundled_bin_dir() -> Path:
    return app_dir() / "bin"


def ytdlp_command():
    """
    Command (list) used to run yt-dlp, or None if it can't be found.
    On Windows the installer ships yt-dlp.exe; we copy it to the user's data
    folder so it can update itself there without needing admin rights.
    """
    bundled = bundled_bin_dir() / _exe("yt-dlp")
    user_copy = data_dir() / _exe("yt-dlp")

    if bundled.exists():
        if not user_copy.exists():
            try:
                shutil.copy2(bundled, user_copy)
            except OSError:
                return [str(bundled)]
        return [str(user_copy)]

    found = shutil.which("yt-dlp")
    if found:
        return [found]

    if not getattr(sys, "frozen", False):  # developer fallback: pip install yt-dlp
        try:
            import yt_dlp  # noqa: F401
            return [sys.executable, "-m", "yt_dlp"]
        except ImportError:
            pass
    return None


def ffmpeg_dir():
    """Folder containing ffmpeg (bundled copy first, then PATH), or None."""
    bundled = bundled_bin_dir()
    if (bundled / _exe("ffmpeg")).exists():
        return str(bundled)
    found = shutil.which("ffmpeg")
    return str(Path(found).parent) if found else None


# ------------------------------------------------------------------ settings

def load_settings() -> dict:
    try:
        return json.loads((data_dir() / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    try:
        (data_dir() / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except OSError:
        pass


# ------------------------------------------------------------------ helpers

def detect_platform(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "YouTube"
    if any(d in host for d in ("twitter.com", "x.com", "t.co")):
        return "Twitter / X"
    return "Other website"


def extract_urls(text: str) -> list:
    """Pull every http(s) link out of whatever the user pasted."""
    seen, urls = set(), []
    for u in re.findall(r"https?://[^\s]+", text):
        u = u.rstrip(".,;)\"'")
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def clean_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "", name).strip().rstrip(".")


def friendly_error(raw: str) -> str:
    """Turn yt-dlp's technical errors into plain advice."""
    t = raw.lower()
    if "not a bot" in t or "sign in" in t or "log in" in t or "login" in t:
        return ("This video needs you to be signed in. Sign in to the site in your browser, "
                "then choose that browser under 'Use browser login' and try again.")
    if "private video" in t:
        return "This video is private, so it can't be downloaded."
    if "age" in t and "restrict" in t:
        return "This video is age-restricted. Choose your browser under 'Use browser login' and try again."
    if ("unavailable" in t or "removed" in t or "does not exist" in t
            or "no video could be found" in t or "404" in t):
        return "This video isn't available (it may be removed, private, or blocked in your country)."
    if "unsupported url" in t:
        return "This link isn't supported. Make sure it's a link to a video page."
    if "403" in t:
        return "The site refused the download (error 403). Click 'Update downloader' and try again."
    if "cookie" in t or "dpapi" in t or ("database" in t and "locked" in t):
        return ("Couldn't read your browser login. Close the browser and try again, "
                "or try a different browser.")
    if "unable to download" in t or "getaddrinfo" in t or "timed out" in t or "connection" in t:
        return "Couldn't reach the site. Check your internet connection and try again."
    if "ffmpeg" in t or "ffprobe" in t:
        return "A required component (ffmpeg) is missing or failed. Reinstall the app."
    return raw


# ------------------------------------------------------------------ command building

def build_args(url, folder, name="", quality="Best available", playlist=False,
               browser="", ffmpeg_location=None):
    """Everything after the yt-dlp program name."""
    args = [
        "--newline", "--progress", "--no-warnings", "--windows-filenames",
        "--trim-filenames", "150",
        "--progress-template",
        "download:[PROG]%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
        "--progress-template", "postprocess:[POST]%(progress.postprocessor)s",
        "--print", "after_move:[FILE]%(filepath)s",
        "-P", str(folder),
    ]

    if ffmpeg_location:
        args += ["--ffmpeg-location", ffmpeg_location]

    # output name
    if playlist:
        args += ["--yes-playlist", "-o",
                 "%(playlist_title|Videos)s/%(playlist_index&{} - |)s%(title)s.%(ext)s"]
    else:
        safe = clean_filename(name).replace("%", "%%")
        args += ["--no-playlist", "-o", (safe if safe else "%(title)s") + ".%(ext)s"]

    # quality
    if quality.startswith("Audio"):
        args += ["-f", "bestaudio/best", "-x", "--audio-format", "mp3", "--audio-quality", "0"]
    else:
        m = re.match(r"(\d+)p", quality)
        if ffmpeg_location:
            fmt = f"bv*[height<={m.group(1)}]+ba/b[height<={m.group(1)}]/b" if m else "bv*+ba/b"
            args += ["-f", fmt, "-S", "res,ext:mp4:m4a", "--merge-output-format", "mp4"]
        else:  # no ffmpeg: single-file formats only
            fmt = f"b[height<={m.group(1)}]/b" if m else "b"
            args += ["-f", fmt]

    if browser and browser != "None":
        args += ["--cookies-from-browser", browser]

    args.append(url)
    return args


# ------------------------------------------------------------------ running yt-dlp

def _env():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def kill_tree(pid: int) -> None:
    """Stop yt-dlp AND any child process it started."""
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, creationflags=NO_WINDOW)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def get_version() -> str:
    cmd = ytdlp_command()
    if not cmd:
        return "not found"
    try:
        out = subprocess.run(cmd + ["--version"], capture_output=True, text=True, timeout=60,
                             creationflags=NO_WINDOW, env=_env(), stdin=subprocess.DEVNULL)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def update_ytdlp() -> str:
    """Ask yt-dlp to update itself. Returns a short human message."""
    cmd = ytdlp_command()
    if not cmd:
        return "Downloader engine not found."
    try:
        out = subprocess.run(cmd + ["-U"], capture_output=True, text=True, timeout=120,
                             creationflags=NO_WINDOW, env=_env(), stdin=subprocess.DEVNULL,
                             encoding="utf-8", errors="replace")
        text = (out.stdout + "\n" + out.stderr).strip()
        low = text.lower()
        if "unable to obtain version info" in low or "error" in low:
            return "Couldn't check for updates right now (are you offline?). You can still download."
        if "up to date" in low:
            return "Downloader is up to date."
        if "updated yt-dlp" in low or "updating to" in low:
            return "Downloader updated to the latest version."
        return text.splitlines()[-1] if text else "Update check finished."
    except subprocess.TimeoutExpired:
        return "Update check timed out (are you offline?)."
    except (OSError, subprocess.SubprocessError) as e:
        return f"Update check failed: {e}"


class Result:
    def __init__(self):
        self.ok = False
        self.cancelled = False
        self.files = []
        self.error = ""


class Job:
    """One running download. Create it, call run(), and call cancel() from another thread."""

    def __init__(self):
        self.proc = None
        self.cancelled = False

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            kill_tree(self.proc.pid)

    def run(self, url, opts, on_progress=None, on_status=None) -> Result:
        res = Result()
        base = ytdlp_command()
        if not base:
            res.error = "The downloader engine (yt-dlp) was not found. Please reinstall the app."
            return res

        args = build_args(url, ffmpeg_location=ffmpeg_dir(), **opts)
        errors = []
        try:
            self.proc = subprocess.Popen(
                base + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                env=_env(), creationflags=NO_WINDOW,
            )
            for raw in self.proc.stdout:
                line = raw.strip()
                if line.startswith("[PROG]"):
                    parts = line[6:].split("|")
                    try:
                        pct = float(parts[0].strip().rstrip("%"))
                    except ValueError:
                        pct = None
                    speed = parts[1].strip() if len(parts) > 1 else ""
                    eta = parts[2].strip() if len(parts) > 2 else ""
                    if on_progress and pct is not None:
                        on_progress(pct, speed, eta)
                elif line.startswith("[POST]"):
                    if on_status:
                        on_status("Processing file...")
                elif line.startswith("[FILE]"):
                    res.files.append(line[6:])
                elif line.startswith("ERROR:"):
                    errors.append(line[6:].strip())
            code = self.proc.wait()
        except OSError as e:
            res.error = f"Could not start the downloader: {e}"
            return res

        res.cancelled = self.cancelled
        res.ok = (code == 0) and not self.cancelled
        if not res.ok and not res.cancelled:
            res.error = friendly_error(
                errors[-1] if errors else f"The downloader stopped unexpectedly (code {code})."
            )
        return res
