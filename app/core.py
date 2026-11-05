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
import hashlib
import tempfile
import threading
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

IS_WINDOWS = sys.platform.startswith("win")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # hides the black console box on Windows

try:  # written by the build (GitHub Actions) with the real version number and repository
    import build_info
    APP_VERSION = str(getattr(build_info, "VERSION", "dev"))
    UPDATE_REPO = str(getattr(build_info, "REPO", ""))
except ImportError:
    APP_VERSION, UPDATE_REPO = "dev", ""

PLATFORMS = ["Auto-detect", "YouTube", "Twitter / X", "Other website"]
QUALITIES = ["Best available", "2160p", "1440p", "1080p", "720p", "480p", "360p", "Audio only (MP3)"]
BROWSERS = ["None", "chrome", "edge", "firefox", "brave"]
_NICE_QUALITY = {"2160p": "2160p (4K)", "1440p": "1440p (2K)"}
MP3_KBPS = 192


def quality_label(key: str) -> str:
    return _NICE_QUALITY.get(key, key)


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


def deno_path():
    """Bundled Deno (JavaScript runtime yt-dlp needs for full YouTube support), or None."""
    p = bundled_bin_dir() / _exe("deno")
    return str(p) if p.exists() else None


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


# ------------------------------------------------------------------ download history

HISTORY_LIMIT = 300
_history_lock = threading.Lock()


def load_history() -> list:
    try:
        data = json.loads((data_dir() / "history.json").read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write_history(entries: list) -> None:
    try:
        (data_dir() / "history.json").write_text(
            json.dumps(entries[:HISTORY_LIMIT], indent=2), encoding="utf-8")
    except OSError:
        pass


def add_history(entry: dict) -> None:
    with _history_lock:
        entries = load_history()
        entries.insert(0, entry)
        _write_history(entries)


def clear_history() -> None:
    with _history_lock:
        _write_history([])


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


# ------------------------------------------------------------------ link preview

def fetch_info(url: str, browser: str = "") -> dict:
    """
    Look up a link without downloading it. Returns a dict with
    title / uploader / duration / thumbnail / is_playlist / count,
    or {"error": "plain-language message"}.
    """
    base = ytdlp_command()
    if not base:
        return {"error": "The downloader engine was not found."}

    args = ["--dump-single-json", "--no-warnings", "--skip-download",
            "--no-playlist", "--flat-playlist"]
    deno = deno_path()
    if deno:
        args += ["--js-runtimes", f"deno:{deno}"]
    if browser and browser != "None":
        args += ["--cookies-from-browser", browser]
    args.append(url)

    try:
        out = subprocess.run(base + args, capture_output=True, text=True, timeout=60,
                             creationflags=NO_WINDOW, env=_env(), stdin=subprocess.DEVNULL,
                             encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"error": "Looking up this link took too long."}
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": f"Could not start the downloader: {e}"}

    if out.returncode != 0:
        errs = [ln for ln in out.stderr.splitlines() if ln.startswith("ERROR")]
        raw = errs[-1][6:].strip() if errs else "Could not read this link."
        return {"error": friendly_error(raw)}

    try:
        data = json.loads(out.stdout)
    except ValueError:
        return {"error": "Could not read the video details."}

    thumb = data.get("thumbnail")
    if not thumb and data.get("thumbnails"):
        thumb = data["thumbnails"][-1].get("url")
    entries = data.get("entries") or []
    is_playlist = data.get("_type") == "playlist"
    try:
        plans = [] if is_playlist else plan_qualities(data)
    except Exception:
        plans = []
    return {
        "title": data.get("title") or url,
        "uploader": data.get("uploader") or data.get("channel") or "",
        "duration": data.get("duration_string") or "",
        "thumbnail": thumb or "",
        "is_playlist": is_playlist,
        "count": len(entries),
        "plans": plans,
    }


def fetch_thumbnail(url: str):
    """Download a thumbnail image. Returns bytes, or None if it fails."""
    if not url or not url.lower().startswith(("http://", "https://")):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read(3_000_000)
    except Exception:
        return None


# ------------------------------------------------------------------ quality planning
#
# We choose the exact video + audio streams ourselves (instead of letting yt-dlp's
# default ranking decide) so that:
#   * the size shown to the user is the size of the streams we will really download,
#   * HDR versions (which look washed-out on normal screens) are avoided,
#   * 60 fps is preferred, and
#   * up to 1080p we prefer H.264 + AAC (plays everywhere), above that VP9/AV1.

def _num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _vc(f):
    v = f.get("vcodec")
    return "unknown" if v in (None, "") else str(v).lower()


def _ac(f):
    a = f.get("acodec")
    return "unknown" if a in (None, "") else str(a).lower()


def _has_video(f):
    return _vc(f) != "none" and _num(f.get("height")) > 0


def _has_audio(f):
    return _ac(f) != "none"


def _is_audio_only(f):
    return _vc(f) == "none" and _ac(f) not in ("none", "unknown")


def _family(f):
    v = _vc(f)
    if v.startswith(("avc1", "h264")):
        return "avc"
    if v.startswith(("vp09", "vp9")):
        return "vp9"
    if v.startswith("av01"):
        return "av1"
    if v.startswith(("hev", "hvc", "h265")):
        return "hevc"
    return "other"


def _bucket(f):
    """Quality bucket (the usual '1080p' naming). Uses the short side so vertical videos are named correctly."""
    h, w = _num(f.get("height")), _num(f.get("width"))
    q = min(h, w) if (h and w) else h
    for b in (2160, 1440, 1080, 720, 480, 360, 240, 144):
        if abs(q - b) <= b * 0.08:
            return b
    return int(q)


def _size_of(f, duration):
    """(bytes or None, is_estimate) for one stream."""
    for field, approx in (("filesize", False), ("filesize_approx", True)):
        v = _num(f.get(field))
        if v > 0:
            return int(v), approx
    kbps = _num(f.get("tbr")) or _num(f.get("vbr")) or _num(f.get("abr"))
    if kbps > 0 and duration > 0:
        return int(kbps * 1000 / 8 * duration), True
    return None, True


def _video_rank(f, bucket):
    sdr = f.get("dynamic_range") in (None, "", "SDR")
    smooth = 1 if _num(f.get("fps")) >= 50 else 0
    if bucket <= 1080:
        codec = {"avc": 4, "vp9": 3, "av1": 2, "hevc": 1}.get(_family(f), 0)
    else:
        codec = {"vp9": 4, "av1": 3, "hevc": 2, "avc": 1}.get(_family(f), 0)
    combined = 1 if (_has_audio(f) and _ac(f) != "unknown") else 0
    bitrate = _num(f.get("tbr")) or _num(f.get("vbr"))
    return (1 if sdr else 0, smooth, codec, combined, bitrate)


def _pick_audio(audios, video):
    if not audios:
        return None
    wants_aac = _family(video) in ("avc", "av1", "hevc")      # keeps the file a normal .mp4

    def rank(a):
        lang = _num(a.get("language_preference"))             # original language first
        aac = 1 if (wants_aac and _ac(a).startswith("mp4a")) else 0
        return (lang, aac, _num(a.get("abr")) or _num(a.get("tbr")))

    return max(audios, key=rank)


def _safe_id(fid):
    return bool(fid) and re.fullmatch(r"[A-Za-z0-9._-]+", str(fid)) is not None


def format_size(n) -> str:
    if not n:
        return "size unknown"
    n = float(n)
    if n < 1024 ** 2:
        return f"~{max(n / 1024, 1):.0f} KB"
    if n < 1024 ** 3:
        mb = n / 1024 ** 2
        return f"~{mb:.0f} MB" if mb >= 100 else f"~{mb:.1f} MB"
    return f"~{n / 1024 ** 3:.2f} GB"


def plan_qualities(data: dict) -> list:
    """
    Work out which qualities a video offers, the exact streams for each, and the size.
    Returns [] when the site gives no usable format list (the app then falls back to yt-dlp's own choice).
    """
    formats = [f for f in (data.get("formats") or []) if isinstance(f, dict)]
    duration = _num(data.get("duration"))
    videos = [f for f in formats if _has_video(f)]
    audios = [f for f in formats if _is_audio_only(f)]
    if not videos:
        return []

    groups = {}
    for f in videos:
        groups.setdefault(_bucket(f), []).append(f)

    plans = []
    for bucket in sorted(groups, reverse=True)[:7]:
        best = max(groups[bucket], key=lambda f: _video_rank(f, bucket))
        need_audio = _ac(best) == "none"
        aud = _pick_audio(audios, best) if need_audio else None

        v_size, v_est = _size_of(best, duration)
        a_size, a_est = _size_of(aud, duration) if aud else (0, False)
        size = (v_size or 0) + (a_size or 0) if v_size else None

        ids = str(best.get("format_id"))
        if aud:
            ids += "+" + str(aud.get("format_id"))
        usable = _safe_id(best.get("format_id")) and (not aud or _safe_id(aud.get("format_id")))

        plans.append({
            "bucket": bucket,
            "name": f"{bucket}p" + (str(int(_num(best.get("fps")))) if _num(best.get("fps")) >= 50 else ""),
            "fmt": ids if usable else None,
            "size": size,
        })

    out = []
    top = plans[0]
    out.append({"key": "Best available", "label": f"Best available ({top['name']})",
                "fmt": top["fmt"], "size": top["size"]})
    for p in plans[1:]:
        out.append({"key": f"{p['bucket']}p", "label": quality_label(f"{p['bucket']}p") if p["name"] == f"{p['bucket']}p" else p["name"],
                    "fmt": p["fmt"], "size": p["size"]})
    if duration > 0:
        out.append({"key": "Audio only (MP3)", "label": "Audio only (MP3)", "fmt": None,
                    "size": int(duration * MP3_KBPS * 1000 / 8)})
    else:
        out.append({"key": "Audio only (MP3)", "label": "Audio only (MP3)", "fmt": None, "size": None})
    return out


# ------------------------------------------------------------------ command building

# Used only when we could not plan the streams ourselves (playlists, or the preview failed).
FALLBACK_SORT = "res,fps,+hdr,vcodec:h264,acodec:aac"


def build_args(url, folder, name="", quality="Best available", playlist=False,
               browser="", ffmpeg_location=None, fmt=None, js_runtime=None):
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
    if js_runtime:
        args += ["--js-runtimes", f"deno:{js_runtime}"]

    # output name
    if playlist:
        args += ["--yes-playlist", "-o",
                 "%(playlist_title|Videos)s/%(playlist_index&{} - |)s%(title)s.%(ext)s"]
    else:
        safe = clean_filename(name).replace("%", "%%")
        args += ["--no-playlist", "-o", (safe if safe else "%(title)s") + ".%(ext)s"]

    # quality
    if quality.startswith("Audio"):
        args += ["-f", "bestaudio/best", "-x", "--audio-format", "mp3",
                 "--audio-quality", f"{MP3_KBPS}K"]
    else:
        m = re.match(r"(\d+)p", quality)
        if ffmpeg_location:
            cap = m.group(1) if m else None
            fallback = f"bv*[height<={cap}]+ba/b[height<={cap}]/b" if cap else "bv*+ba/b"
            chosen = f"{fmt}/{fallback}" if fmt else fallback
            # mp4 when the streams allow it (H.264/AAC), otherwise mkv (VP9/Opus keep full quality)
            args += ["-f", chosen, "-S", FALLBACK_SORT, "--merge-output-format", "mp4/mkv"]
        else:  # no ffmpeg: single-file formats only
            single = f"b[height<={m.group(1)}]/b" if m else "b"
            args += ["-f", single]

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


def _move_out_of_temp(files, tmp_dir: Path, final_folder: Path) -> list:
    """Move finished files from the temp folder into the real folder (never overwriting)."""
    moved = []
    for f in files:
        src = Path(f)
        try:
            rel = src.relative_to(tmp_dir)
        except ValueError:
            rel = Path(src.name)
        dest = final_folder / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        n = 1
        while dest.exists():
            dest = dest.with_name(f"{rel.stem} ({n}){rel.suffix}")
            n += 1
        try:
            shutil.move(str(src), str(dest))
            moved.append(str(dest))
        except OSError:
            moved.append(str(src))
    return moved


# ------------------------------------------------------------------ app updates (GitHub Releases)

UPDATE_API = "https://api.github.com/repos/{repo}/releases/latest"
UPDATE_DOWNLOAD_PREFIX = "https://github.com/{repo}/releases/download/"
SETUP_NAME = "VideoDownloader-Setup.exe"


class UpdateError(Exception):
    pass


def parse_version(text) -> tuple:
    """'v1.2.3' -> (1, 2, 3). Returns () when there are no digits (e.g. 'dev')."""
    return tuple(int(n) for n in re.findall(r"\d+", str(text).split("-")[0]))


def _http_get(url, timeout=20, limit=2_000_000) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "VideoDownloader-updater",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(limit)


def check_for_update():
    """
    Ask GitHub for the newest release. Returns a dict describing it when it is newer than this app,
    otherwise None (also None when offline, in a development run, or the repo is private).
    """
    if not UPDATE_REPO:
        return None
    try:
        data = json.loads(_http_get(UPDATE_API.format(repo=UPDATE_REPO)).decode("utf-8", "replace"))
    except Exception:
        return None
    latest, current = parse_version(data.get("tag_name", "")), parse_version(APP_VERSION)
    if not latest or not current or latest <= current:
        return None
    assets = {a.get("name"): a for a in (data.get("assets") or []) if isinstance(a, dict)}
    setup = assets.get(SETUP_NAME)
    if not setup or not setup.get("browser_download_url"):
        return None
    sha = assets.get(SETUP_NAME + ".sha256") or {}
    return {
        "version": str(data.get("tag_name", "")).lstrip("vV"),
        "url": setup["browser_download_url"],
        "size": setup.get("size") or 0,
        "sha_url": sha.get("browser_download_url", ""),
        "digest": setup.get("digest") or "",
        "notes": str(data.get("body") or "")[:600],
        "page": data.get("html_url") or "",
    }


def download_update(info, on_progress=None, should_cancel=None) -> str:
    """Download the new installer to a temp folder and check its checksum. Returns the file path."""
    url = info["url"]
    if not url.startswith(UPDATE_DOWNLOAD_PREFIX.format(repo=UPDATE_REPO)):
        raise UpdateError("The update address looks wrong, so nothing was downloaded.")

    expected = None
    if info.get("sha_url"):
        try:
            m = re.search(r"\b[0-9a-fA-F]{64}\b", _http_get(info["sha_url"]).decode("ascii", "replace"))
            expected = m.group(0).lower() if m else None
        except Exception:
            expected = None
    if not expected and str(info.get("digest", "")).startswith("sha256:"):
        expected = info["digest"][7:].strip().lower()

    folder = Path(tempfile.gettempdir()) / "VideoDownloader-update"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / SETUP_NAME
    digest = hashlib.sha256()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "VideoDownloader-updater"})
        with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as out:
            total = int(resp.headers.get("Content-Length") or info.get("size") or 0)
            done = 0
            while True:
                if should_cancel and should_cancel():
                    raise UpdateError("Update cancelled.")
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
    except UpdateError:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(folder, ignore_errors=True)
        raise UpdateError(f"The download failed: {e}")

    if expected and digest.hexdigest().lower() != expected:
        shutil.rmtree(folder, ignore_errors=True)
        raise UpdateError("The downloaded file did not match its checksum, so it was not installed.")
    return str(dest)


def launch_installer(path: str) -> None:
    """Start the installer quietly in the background. The caller must close the app right after."""
    if not IS_WINDOWS:
        raise UpdateError("Automatic install only works on Windows.")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                     creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
        if self.cancelled:
            res.cancelled = True
            return res
        base = ytdlp_command()
        if not base:
            res.error = "The downloader engine (yt-dlp) was not found. Please reinstall the app."
            return res

        # Audio extraction deletes its source file afterwards. Work inside a private temp
        # folder so a video you already saved with the same name can never be touched.
        final_folder = Path(opts["folder"])
        tmp_dir = None
        run_opts = dict(opts)
        if opts.get("quality", "").startswith("Audio"):
            tmp_dir = final_folder / (".vd_tmp_" + uuid.uuid4().hex[:8])
            run_opts["folder"] = str(tmp_dir)

        args = build_args(url, ffmpeg_location=ffmpeg_dir(), js_runtime=deno_path(), **run_opts)
        errors = []
        try:
            self.proc = subprocess.Popen(
                base + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                env=_env(), creationflags=NO_WINDOW,
            )
            part, last_pct = 1, -1.0
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
                    if pct is not None:
                        if pct + 30 < last_pct:      # progress restarted: next stream (e.g. audio)
                            part += 1
                        last_pct = pct
                        if on_progress:
                            on_progress(pct, speed, eta, part)
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
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            return res

        if tmp_dir:
            if code == 0 and not self.cancelled:
                res.files = _move_out_of_temp(res.files, tmp_dir, final_folder)
            shutil.rmtree(tmp_dir, ignore_errors=True)

        res.cancelled = self.cancelled
        res.ok = (code == 0) and not self.cancelled
        if not res.ok and not res.cancelled:
            res.error = friendly_error(
                errors[-1] if errors else f"The downloader stopped unexpectedly (code {code})."
            )
        return res