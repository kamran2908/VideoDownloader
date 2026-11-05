"""
Video Downloader - modern window (CustomTkinter).

Features: link preview (thumbnail + title), download queue with a progress bar
per video, download history, and a light / dark / system theme.
All the real work is done by core.py (which runs yt-dlp).
"""

import io
import os
import queue
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageOps

import core

NOTICE_TEXT = (
    "This app saves videos from sites like YouTube and X.\n\n"
    "Only download videos that you own, that are free to use, or that you have "
    "permission to save. Websites have their own rules and copyright law applies. "
    "You are responsible for how you use this app."
)

PREVIEW_SIZE = (288, 162)
THUMB_SIZE = (112, 63)
PAD = 12

BLUE = ("#3B8ED0", "#1F6AA5")
GREEN = ("#2e7d32", "#43a047")
RED = ("#c62828", "#e53935")
STATUS_COLORS = {
    "Queued": ("gray35", "gray70"),
    "Downloading": ("#1f6aa5", "#5aa9f0"),
    "Done": ("#2e7d32", "#6fcf73"),
    "Failed": ("#c62828", "#ff7b7b"),
    "Cancelled": ("gray35", "gray70"),
}
FINISHED = ("Done", "Failed", "Cancelled")


def placeholder_image(size):
    return Image.new("RGB", size, (105, 110, 118))


def shorten(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def reveal_file(path):
    """Show a file in Explorer / Finder (falls back to opening its folder)."""
    p = Path(path)
    try:
        if sys.platform.startswith("win"):
            if p.exists():
                subprocess.Popen(f'explorer /select,"{p}"')
            else:
                os.startfile(str(p.parent))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)] if p.exists() else ["open", str(p.parent)])
        else:
            subprocess.Popen(["xdg-open", str(p.parent)])
    except OSError:
        pass


class Item:
    """One video in the queue."""
    _next_id = 1

    def __init__(self, url, title, meta, thumb, opts, platform):
        self.id = Item._next_id
        Item._next_id += 1
        self.url = url
        self.title = title
        self.meta = meta
        self.thumb = thumb            # PIL image or None
        self.opts = opts
        self.platform = platform
        self.status = "Queued"
        self.pct = 0.0
        self.wait_text = "Waiting in queue"
        self.text = self.wait_text
        self.job = None
        self.files = []
        self.w = None                 # widgets, filled in when the card is built


class App(ctk.CTk):
    def __init__(self):
        settings = core.load_settings()
        theme = settings.get("theme", "System")
        if theme not in ("System", "Light", "Dark"):
            theme = "System"
        ctk.set_appearance_mode(theme)
        ctk.set_default_color_theme("blue")
        super().__init__()

        self.title("Video Downloader")
        self.geometry(f"1060x{min(820, max(self.winfo_screenheight() - 90, 640))}")
        self.minsize(960, 600)
        self.after(250, self._set_icon)

        self.msgs = queue.Queue()
        self.lock = threading.Lock()
        self.items = []
        self.runner_active = False
        self.engine_ready = threading.Event()
        self.closing = False

        self.preview_url = ""
        self.preview_token = 0
        self.preview_info = None
        self.preview_thumb = None
        self._preview_ctk = None
        self._debounce = None

        self.quality_options = {}        # dropdown text -> {key, label, fmt, size}
        self.quality_menu = None
        self.plan_url = ""               # the link the size list was made for
        self.update_info = None
        self.update_busy = False

        self.url_var = ctk.StringVar()
        self.quality_var = ctk.StringVar()
        saved_quality = settings.get("quality") if settings.get("quality") in core.QUALITIES else core.QUALITIES[0]
        self._default_quality_options(saved_quality)
        self.platform_var = ctk.StringVar(value=core.PLATFORMS[0])
        self.folder_var = ctk.StringVar(value=settings.get("folder", str(Path.home() / "Downloads")))
        self.browser_var = ctk.StringVar(
            value=settings.get("browser") if settings.get("browser") in core.BROWSERS else core.BROWSERS[0])
        self.playlist_var = ctk.BooleanVar(value=False)
        self.theme_var = ctk.StringVar(value=theme)
        self.status_var = ctk.StringVar(value="Checking for updates...")
        self.engine_var = ctk.StringVar(value=f"App {core.APP_VERSION}")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        if not self._first_run_notice():
            self.after(0, self.destroy)
            return

        if core.ytdlp_command() is None:
            messagebox.showerror(
                "Component missing",
                "The downloader engine (yt-dlp) was not found.\nPlease reinstall the app.")

        self.url_var.trace_add("write", self._on_url_change)
        threading.Thread(target=self._startup_update, daemon=True).start()
        threading.Thread(target=self._check_app_update, daemon=True).start()
        self._sync_empty()
        self.refresh_history()
        self._poll()

    # ================================================================ UI building

    def _set_icon(self):
        try:
            icon = core.resource_path("icon.ico")
            if icon.exists() and sys.platform.startswith("win"):
                self.iconbitmap(str(icon))
        except Exception:
            pass

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # ---- header
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=PAD + 4, pady=(PAD, 0))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Video Downloader",
                     font=ctk.CTkFont(size=24, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(head, text="Only download videos you own or have permission to save.",
                     text_color=("gray35", "gray65")).grid(row=1, column=0, sticky="w")
        ctk.CTkSegmentedButton(head, values=["System", "Light", "Dark"], variable=self.theme_var,
                               command=self.change_theme, width=210).grid(row=0, column=1, rowspan=2, sticky="e")

        # ---- update banner (hidden until a newer version is found)
        self.banner = ctk.CTkFrame(self, corner_radius=8, fg_color=("#dbeafe", "#1e3a5f"))
        self.banner.grid(row=1, column=0, columnspan=2, sticky="ew", padx=PAD + 4, pady=(10, 0))
        self.banner.grid_columnconfigure(0, weight=1)
        self.banner_label = ctk.CTkLabel(self.banner, text="", anchor="w",
                                         font=ctk.CTkFont(size=14, weight="bold"))
        self.banner_label.grid(row=0, column=0, sticky="w", padx=14, pady=(10, 0))
        self.banner_sub = ctk.CTkLabel(self.banner, text="", anchor="w", text_color=("gray25", "gray75"))
        self.banner_sub.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 10))
        self.banner_bar = ctk.CTkProgressBar(self.banner)
        self.banner_bar.set(0)
        bb = ctk.CTkFrame(self.banner, fg_color="transparent")
        bb.grid(row=0, column=1, rowspan=2, padx=10)
        self.banner_now = ctk.CTkButton(bb, text="Update now", width=110, command=self.start_update)
        self.banner_now.grid(row=0, column=0, padx=4)
        self.banner_notes = ctk.CTkButton(bb, text="What's new", width=92, fg_color="transparent", border_width=1,
                                          text_color=("gray10", "gray90"), command=self.open_release_page)
        self.banner_notes.grid(row=0, column=1, padx=4)
        self.banner_later = ctk.CTkButton(bb, text="Later", width=70, fg_color="transparent", border_width=1,
                                          text_color=("gray10", "gray90"), command=self.hide_banner)
        self.banner_later.grid(row=0, column=2, padx=4)
        self.banner.grid_remove()

        # ---- left: new download card
        # the form scrolls if the window is short; the "Add to queue" button stays pinned at the bottom
        left_outer = ctk.CTkFrame(self, width=372)
        left_outer.grid(row=2, column=0, sticky="ns", padx=(PAD, PAD // 2), pady=PAD)
        left_outer.grid_propagate(False)
        left_outer.grid_columnconfigure(0, weight=1)
        left_outer.grid_rowconfigure(0, weight=1)
        left = ctk.CTkScrollableFrame(left_outer, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(left, text="New download", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 0))

        row = ctk.CTkFrame(left, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", padx=14)
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(row, text="Video link  (paste it, then press Enter or Add to queue)", anchor="w",
                     text_color=("gray35", "gray65")).grid(row=0, column=0, columnspan=2, sticky="w")
        self.url_entry = ctk.CTkEntry(row, textvariable=self.url_var, height=36)
        self.url_entry.grid(row=1, column=0, sticky="ew")
        self.url_entry.bind("<Return>", lambda e: self.add_to_queue())
        ctk.CTkButton(row, text="Paste", width=64, height=36, command=self.paste).grid(
            row=1, column=1, padx=(8, 0))

        self.preview_label = ctk.CTkLabel(
            left, text="Preview appears here", compound="center", width=PREVIEW_SIZE[0],
            height=PREVIEW_SIZE[1], corner_radius=8, text_color="white",
            image=self._ctk_image(placeholder_image(PREVIEW_SIZE), PREVIEW_SIZE))
        self.preview_label.grid(row=2, column=0, padx=14, pady=(10, 4))
        self.preview_title = ctk.CTkLabel(left, text="", wraplength=300, justify="left", anchor="w",
                                          font=ctk.CTkFont(size=13, weight="bold"), height=36)
        self.preview_title.grid(row=3, column=0, sticky="ew", padx=14)
        self.preview_meta = ctk.CTkLabel(left, text="", anchor="w", text_color=("gray35", "gray65"))
        self.preview_meta.grid(row=4, column=0, sticky="ew", padx=14)

        ctk.CTkLabel(left, text="Quality  (file size appears after the preview loads)", anchor="w",
                     text_color=("gray35", "gray65")).grid(row=5, column=0, sticky="w", padx=14, pady=(8, 0))
        self.quality_menu = ctk.CTkOptionMenu(left, variable=self.quality_var,
                                              values=list(self.quality_options), dynamic_resizing=False)
        self.quality_menu.grid(row=6, column=0, sticky="ew", padx=14)

        self.name_entry = ctk.CTkEntry(left, placeholder_text="File name (optional)")
        self.name_entry.grid(row=7, column=0, sticky="ew", padx=14, pady=(10, 0))

        frow = ctk.CTkFrame(left, fg_color="transparent")
        frow.grid(row=8, column=0, sticky="ew", padx=14, pady=(8, 0))
        frow.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(frow, textvariable=self.folder_var).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(frow, text="Browse", width=64, command=self.browse).grid(row=0, column=1, padx=(8, 0))

        ctk.CTkCheckBox(left, text="Download the whole playlist", variable=self.playlist_var).grid(
            row=9, column=0, sticky="w", padx=14, pady=(10, 0))

        two = ctk.CTkFrame(left, fg_color="transparent")
        two.grid(row=10, column=0, sticky="ew", padx=14, pady=(8, 0))
        two.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(two, text="Platform", anchor="w", text_color=("gray35", "gray65")).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(two, text="Browser login (if asked)", anchor="w", text_color=("gray35", "gray65")).grid(
            row=0, column=1, sticky="w", padx=(8, 0))
        ctk.CTkOptionMenu(two, variable=self.platform_var, values=core.PLATFORMS).grid(row=1, column=0, sticky="ew")
        ctk.CTkOptionMenu(two, variable=self.browser_var, values=core.BROWSERS).grid(
            row=1, column=1, sticky="ew", padx=(8, 0))

        ctk.CTkButton(left_outer, text="Add to queue", height=42, font=ctk.CTkFont(size=14, weight="bold"),
                      command=self.add_to_queue).grid(row=1, column=0, sticky="ew", padx=14, pady=(8, 14))

        # ---- right: queue + history tabs
        self.tabs = ctk.CTkTabview(self, command=self._on_tab)
        self.tabs.grid(row=2, column=1, sticky="nsew", padx=(PAD // 2, PAD), pady=PAD)
        qt = self.tabs.add("Queue")
        ht = self.tabs.add("History")

        qt.grid_columnconfigure(0, weight=1)
        qt.grid_rowconfigure(1, weight=1)
        qbar = ctk.CTkFrame(qt, fg_color="transparent")
        qbar.grid(row=0, column=0, sticky="ew")
        qbar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(qbar, text="Videos are downloaded one at a time, in order.",
                     text_color=("gray35", "gray65")).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(qbar, text="Clear finished", width=110, fg_color="transparent", border_width=1,
                      text_color=("gray10", "gray90"), command=self.clear_finished).grid(row=0, column=1)
        self.queue_frame = ctk.CTkScrollableFrame(qt, fg_color="transparent")
        self.queue_frame.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.queue_frame.grid_columnconfigure(0, weight=1)
        self.empty_label = None
        self._make_empty_label()

        ht.grid_columnconfigure(0, weight=1)
        ht.grid_rowconfigure(1, weight=1)
        hbar = ctk.CTkFrame(ht, fg_color="transparent")
        hbar.grid(row=0, column=0, sticky="ew")
        hbar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hbar, text="Your finished downloads.", text_color=("gray35", "gray65")).grid(
            row=0, column=0, sticky="w")
        ctk.CTkButton(hbar, text="Clear history", width=110, fg_color="transparent", border_width=1,
                      text_color=("gray10", "gray90"), command=self.clear_history).grid(row=0, column=1)
        self.hist_frame = ctk.CTkScrollableFrame(ht, fg_color="transparent")
        self.hist_frame.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.hist_frame.grid_columnconfigure(0, weight=1)

        # ---- footer
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=3, column=0, columnspan=2, sticky="ew", padx=PAD + 4, pady=(0, PAD))
        foot.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(foot, textvariable=self.status_var, anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(foot, textvariable=self.engine_var, text_color=("gray35", "gray65")).grid(
            row=0, column=1, padx=10)
        self.update_btn = ctk.CTkButton(foot, text="Update downloader", width=140, height=28,
                                        command=self.manual_update)
        self.update_btn.grid(row=0, column=2)

    def _make_empty_label(self):
        """(Re)create the 'queue is empty' text. Rebuilt on theme change so its background follows the theme."""
        if self.empty_label is not None:
            self.empty_label.destroy()
        self.empty_label = ctk.CTkLabel(
            self.queue_frame, text="Nothing here yet.\nPaste a link on the left and press “Add to queue”.",
            text_color=("gray35", "gray65"), justify="center")
        self._sync_empty()

    @staticmethod
    def _ctk_image(pil, size):
        return ctk.CTkImage(light_image=pil, dark_image=pil, size=size)

    # ================================================================ first-run notice

    def _first_run_notice(self) -> bool:
        settings = core.load_settings()
        if settings.get("accepted_notice"):
            return True

        dlg = ctk.CTkToplevel(self)
        dlg.title("Before you start")
        dlg.resizable(False, False)
        dlg.transient(self)
        accepted = {"ok": False}

        frm = ctk.CTkFrame(dlg, fg_color="transparent")
        frm.pack(padx=26, pady=22)
        ctk.CTkLabel(frm, text="Before you start", font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(frm, text=NOTICE_TEXT, wraplength=420, justify="left").pack(anchor="w", pady=14)

        agree = ctk.BooleanVar(value=False)

        def accept():
            accepted["ok"] = True
            dlg.destroy()

        cont = ctk.CTkButton(frm, text="Continue", state="disabled", command=accept)
        ctk.CTkCheckBox(
            frm, text="I understand", variable=agree,
            command=lambda: cont.configure(state="normal" if agree.get() else "disabled"),
        ).pack(anchor="w")
        cont.pack(anchor="e", pady=(14, 0))

        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        dlg.geometry(f"+{max((dlg.winfo_screenwidth() - w) // 2, 0)}+{max((dlg.winfo_screenheight() - h) // 3, 0)}")
        dlg.after(120, dlg.lift)
        try:
            dlg.wait_visibility()
            dlg.grab_set()
        except Exception:
            pass
        self.wait_window(dlg)

        if accepted["ok"]:
            settings["accepted_notice"] = True
            core.save_settings(settings)
        return accepted["ok"]

    # ================================================================ small actions

    def paste(self):
        try:
            clip = self.clipboard_get().strip()
        except Exception:
            return
        self.url_var.set(clip)

    def browse(self):
        folder = filedialog.askdirectory(initialdir=self.folder_var.get() or str(Path.home()))
        if folder:
            self.folder_var.set(folder)

    def change_theme(self, mode):
        ctk.set_appearance_mode(mode)
        self.after(80, self._make_empty_label)
        if self.tabs.get() == "History":
            self.after(80, self.refresh_history)
        settings = core.load_settings()
        settings["theme"] = mode
        core.save_settings(settings)

    # ================================================================ quality list (with sizes)

    def _apply_quality_options(self, options, select_key, show_sizes):
        """Rebuild the quality dropdown. options: [{key, label, fmt, size}]."""
        mapping = {}
        for o in options:
            text = o["label"] + (("   ·   " + core.format_size(o.get("size"))) if show_sizes else "")
            mapping[text] = o
        self.quality_options = mapping
        labels = list(mapping)
        pick = next((t for t, o in mapping.items() if o["key"] == select_key), labels[0])
        self.quality_var.set(pick)
        if self.quality_menu is not None:
            self.quality_menu.configure(values=labels)

    def current_quality(self):
        return self.quality_options.get(self.quality_var.get()) or \
            {"key": core.QUALITIES[0], "label": core.QUALITIES[0], "fmt": None, "size": None}

    def _default_quality_options(self, select_key=None):
        """The plain list (no sizes), used before a preview is available."""
        if select_key is None:
            select_key = self.current_quality()["key"]
        options = [{"key": k, "label": core.quality_label(k), "fmt": None, "size": None} for k in core.QUALITIES]
        self._apply_quality_options(options, select_key, False)
        self.plan_url = ""

    # ================================================================ link preview

    def _on_url_change(self, *_):
        if self._debounce:
            self.after_cancel(self._debounce)
            self._debounce = None
        urls = core.extract_urls(self.url_var.get())
        if not urls:
            self._clear_preview()
            return
        self._debounce = self.after(600, lambda u=urls[0]: self._start_preview(u))

    def _clear_preview(self):
        self.preview_token += 1
        self.preview_url, self.preview_info, self.preview_thumb = "", None, None
        self._show_preview_image(None, "Preview appears here")
        self.preview_title.configure(text="")
        self.preview_meta.configure(text="")
        self._default_quality_options()

    def _show_preview_image(self, pil, text=""):
        self._preview_ctk = self._ctk_image(pil or placeholder_image(PREVIEW_SIZE), PREVIEW_SIZE)
        self.preview_label.configure(image=self._preview_ctk, text=text)

    def _start_preview(self, url):
        self._debounce = None
        if url == self.preview_url:
            return
        self.preview_token += 1
        token = self.preview_token
        self.preview_url, self.preview_info, self.preview_thumb = url, None, None
        self._show_preview_image(None, "Looking up this link...")
        self.preview_title.configure(text="")
        self.preview_meta.configure(text="")
        self._default_quality_options()
        browser = self.browser_var.get()

        def work():
            info = core.fetch_info(url, browser)
            pil = None
            if "error" not in info:
                data = core.fetch_thumbnail(info.get("thumbnail", ""))
                if data:
                    try:
                        pil = ImageOps.fit(Image.open(io.BytesIO(data)).convert("RGB"),
                                           (PREVIEW_SIZE[0] * 2, PREVIEW_SIZE[1] * 2))
                    except Exception:
                        pil = None
            self.msgs.put(("preview", token, url, info, pil))

        threading.Thread(target=work, daemon=True).start()

    def _apply_preview(self, token, url, info, pil):
        if token != self.preview_token:
            return                     # an older lookup, ignore
        self.preview_info, self.preview_thumb = info, pil
        if "error" in info:
            self._show_preview_image(None, "No preview")
            self.preview_title.configure(text="Couldn't read this link", text_color=RED)
            self.preview_meta.configure(text=shorten(info["error"], 120))
            return
        self._show_preview_image(pil, "" if pil else "No thumbnail")
        self.preview_title.configure(text=shorten(info["title"], 90), text_color=("gray10", "gray95"))
        bits = []
        if info.get("is_playlist"):
            bits.append(f"Playlist · {info['count']} videos" if info.get("count") else "Playlist")
            self.playlist_var.set(True)
        if info.get("uploader"):
            bits.append(info["uploader"])
        if info.get("duration"):
            bits.append(info["duration"])
        self.preview_meta.configure(text=shorten("  ·  ".join(bits), 70))
        if info.get("plans"):                       # real qualities + sizes for this video
            self._apply_quality_options(info["plans"], self.current_quality()["key"], True)
            self.plan_url = url

    # ================================================================ queue

    def add_to_queue(self):
        if self.update_busy:
            messagebox.showinfo("Updating", "The app is updating. Please wait a moment.")
            return
        urls = core.extract_urls(self.url_var.get())
        if not urls:
            messagebox.showwarning("Link needed", "Please paste a link that starts with http:// or https://")
            return
        url = urls[0]

        folder = Path(self.folder_var.get().strip().strip('"') or Path.home() / "Downloads").expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Folder problem", f"Can't use that folder:\n{e}")
            return

        chosen = self.platform_var.get()
        detected = core.detect_platform(url)
        platform = detected if chosen == "Auto-detect" else chosen

        name = self.name_entry.get().strip()
        playlist = bool(self.playlist_var.get())
        if name and playlist:
            name = ""                                   # a single name can't apply to a playlist

        q = self.current_quality()
        use_plan = (self.plan_url == url) and not playlist     # sizes/streams belong to this exact link
        opts = dict(folder=str(folder), name=name, quality=q["key"], playlist=playlist,
                    browser=self.browser_var.get(), fmt=q["fmt"] if use_plan else None)
        settings = core.load_settings()
        settings.update({"folder": str(folder), "quality": opts["quality"], "browser": opts["browser"]})
        core.save_settings(settings)

        info = self.preview_info if (self.preview_url == url and self.preview_info
                                     and "error" not in self.preview_info) else None
        title = info["title"] if info else url
        size_text = core.format_size(q["size"]) if (use_plan and q.get("size")) else ""
        meta = " · ".join(b for b in (q["label"], size_text) if b)
        item = Item(url, title, meta, self.preview_thumb if info else None, opts, platform)
        item.wait_text = "Waiting in queue · " + meta
        item.text = item.wait_text
        self.items.append(item)
        self._build_card(item)
        self._sync_empty()

        # reset the "new download" box for the next link
        self.url_var.set("")
        self.name_entry.delete(0, "end")
        self.name_entry.configure(placeholder_text="File name (optional)")
        self.playlist_var.set(False)
        self.tabs.set("Queue")
        self._ensure_runner()
        self._update_summary()

    def _build_card(self, item):
        card = ctk.CTkFrame(self.queue_frame)
        card.grid_columnconfigure(1, weight=1)
        thumb = self._ctk_image(item.thumb or placeholder_image(THUMB_SIZE), THUMB_SIZE)
        img = ctk.CTkLabel(card, text="", image=thumb, width=THUMB_SIZE[0], height=THUMB_SIZE[1])
        img.grid(row=0, column=0, rowspan=3, padx=10, pady=10)
        title = ctk.CTkLabel(card, text=shorten(item.title, 80), anchor="w", justify="left", wraplength=290,
                             font=ctk.CTkFont(size=13, weight="bold"))
        title.grid(row=0, column=1, sticky="ew", pady=(10, 0))
        status = ctk.CTkLabel(card, text="", anchor="w", justify="left", wraplength=290)
        status.grid(row=1, column=1, sticky="ew")
        bar = ctk.CTkProgressBar(card)
        bar.set(0)
        bar.grid(row=2, column=1, sticky="ew", pady=(2, 10), padx=(0, 10))
        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.grid(row=0, column=2, rowspan=3, padx=(0, 10))
        a = ctk.CTkButton(btns, text="Cancel", width=92, command=lambda i=item: self._action_a(i))
        b = ctk.CTkButton(btns, text="Remove", width=92, fg_color="transparent", border_width=1,
                          text_color=("gray10", "gray90"), command=lambda i=item: self._remove(i))
        a.grid(row=0, column=0, pady=(0, 6))
        item.w = dict(card=card, img=img, thumb=thumb, title=title, status=status, bar=bar, a=a, b=b)
        card.grid(row=len(self.items), column=0, sticky="ew", padx=2, pady=4)
        self._refresh_item(item)

    def _refresh_item(self, item):
        w = item.w
        if not w:
            return
        st = item.status
        w["title"].configure(text=shorten(item.title, 80))
        w["status"].configure(text=item.text, text_color=STATUS_COLORS.get(st, ("gray35", "gray70")))
        w["bar"].set(max(0.0, min(1.0, item.pct / 100)))
        w["bar"].configure(progress_color=GREEN if st == "Done" else RED if st == "Failed" else BLUE)
        if st in ("Queued", "Downloading"):
            w["a"].configure(text="Cancel")
            w["b"].grid_remove()
        elif st == "Done":
            w["a"].configure(text="Show file")
            w["b"].grid(row=1, column=0)
        else:
            w["a"].configure(text="Retry")
            w["b"].grid(row=1, column=0)

    def _action_a(self, item):
        if item.status in ("Queued", "Downloading"):
            self._cancel(item)
        elif item.status == "Done":
            if item.files:
                reveal_file(item.files[-1])
            else:
                reveal_file(Path(item.opts["folder"]) / "x")
        else:                                              # Failed / Cancelled -> try again
            with self.lock:
                item.status, item.pct, item.text = "Queued", 0.0, item.wait_text
            self._refresh_item(item)
            self._ensure_runner()
            self._update_summary()

    def _cancel(self, item):
        with self.lock:
            if item.status == "Queued":
                item.status, item.text, item.pct = "Cancelled", "Cancelled", 0.0
            elif item.status == "Downloading" and item.job:
                item.text = "Cancelling..."
                item.job.cancel()
        self._refresh_item(item)
        self._update_summary()

    def _remove(self, item):
        if item.status in ("Queued", "Downloading"):
            return
        if item in self.items:
            self.items.remove(item)
        if item.w:
            item.w["card"].destroy()
        self._sync_empty()
        self._update_summary()

    def clear_finished(self):
        for item in [i for i in self.items if i.status in FINISHED]:
            self._remove(item)

    def _sync_empty(self):
        if self.empty_label is None:
            return
        if self.items:
            self.empty_label.grid_forget()
        else:
            self.empty_label.grid(row=0, column=0, pady=60)

    def _update_summary(self):
        if not self.engine_ready.is_set():
            return
        active = [i for i in self.items if i.status == "Downloading"]
        waiting = [i for i in self.items if i.status == "Queued"]
        done = [i for i in self.items if i.status == "Done"]
        if active or waiting:
            bits = []
            if active:
                bits.append("1 downloading")
            if waiting:
                bits.append(f"{len(waiting)} waiting")
            if done:
                bits.append(f"{len(done)} done")
            self.status_var.set(" · ".join(bits))
        elif self.items:
            self.status_var.set(f"All finished · {len(done)} done")
        else:
            self.status_var.set("Ready")

    # ---------------------------------------------------------------- queue worker

    def _ensure_runner(self):
        with self.lock:
            if self.runner_active:
                return
            self.runner_active = True
        threading.Thread(target=self._runner, daemon=True).start()

    def _runner(self):
        self.engine_ready.wait()                     # wait for the startup update check
        while not self.closing:
            with self.lock:
                item = next((i for i in self.items if i.status == "Queued"), None)
                if item is None:
                    self.runner_active = False
                    break
                item.status, item.text, item.pct = "Downloading", "Starting...", 0.0
                item.job = core.Job()
            self.msgs.put(("refresh", item.id))
            self._run_item(item)
        self.msgs.put(("summary",))

    def _run_item(self, item):
        job = item.job

        def on_progress(pct, speed, eta, part):
            item.pct = pct
            bits = [f"{pct:.0f}%"]
            if speed and "Unknown" not in speed:
                bits.append(speed)
            if eta and eta not in ("NA", "Unknown"):
                bits.append(f"{eta} left")
            prefix = f"Part {part} · " if part > 1 else ""
            item.text = "Downloading · " + prefix + " · ".join(bits)
            self.msgs.put(("refresh", item.id))

        def on_status(text):
            item.text = text
            item.pct = 100.0
            self.msgs.put(("refresh", item.id))

        res = job.run(item.url, item.opts, on_progress, on_status)

        with self.lock:
            item.job = None
            if res.cancelled:
                item.status, item.text, item.pct = "Cancelled", "Cancelled", 0.0
            elif res.ok:
                item.status, item.pct = "Done", 100.0
                item.files = list(res.files)
                item.text = "Done · " + (Path(res.files[-1]).name if res.files else "saved")
            else:
                item.status, item.pct = "Failed", 0.0
                item.text = res.error or "Download failed."

        if item.status == "Done":
            if item.title == item.url and item.files:
                item.title = Path(item.files[-1]).stem
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            for f in (item.files or []):
                core.add_history({
                    "title": item.title if len(item.files) == 1 else Path(f).stem,
                    "file": f, "url": item.url, "quality": item.opts["quality"],
                    "platform": item.platform, "date": now,
                })
            self.msgs.put(("history",))
        self.msgs.put(("refresh", item.id))
        self.msgs.put(("summary",))

    # ================================================================ history

    def _on_tab(self):
        if self.tabs.get() == "History":
            self.refresh_history()

    def refresh_history(self):
        for w in self.hist_frame.winfo_children():
            w.destroy()
        entries = core.load_history()
        if not entries:
            ctk.CTkLabel(self.hist_frame, text="No downloads yet.\nFinished videos will be listed here.",
                         text_color=("gray35", "gray65")).grid(row=0, column=0, pady=60)
            return
        for r, e in enumerate(entries[:100]):
            exists = Path(e.get("file", "")).exists()
            row = ctk.CTkFrame(self.hist_frame)
            row.grid(row=r, column=0, sticky="ew", padx=2, pady=4)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=shorten(e.get("title", ""), 80), anchor="w", justify="left",
                         wraplength=480, font=ctk.CTkFont(size=13, weight="bold")).grid(
                row=0, column=0, sticky="ew", padx=12, pady=(10, 0))
            meta = " · ".join(x for x in (e.get("date", ""), e.get("platform", ""), e.get("quality", "")) if x)
            if not exists:
                meta += "  ·  file moved or deleted"
            ctk.CTkLabel(row, text=meta, anchor="w", text_color=("gray35", "gray65")).grid(
                row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
            ctk.CTkButton(row, text="Show file", width=92, state="normal" if exists else "disabled",
                          command=lambda p=e.get("file", ""): reveal_file(p)).grid(
                row=0, column=1, rowspan=2, padx=12)

    def clear_history(self):
        if core.load_history() and messagebox.askyesno(
                "Clear history", "Remove the list of past downloads?\n(Your video files are not deleted.)"):
            core.clear_history()
            self.refresh_history()

    # ================================================================ app updates (new version of this app)

    def _check_app_update(self):
        info = core.check_for_update()
        if info:
            self.msgs.put(("update_found", info))

    def _show_banner(self, info):
        self.update_info = info
        self.banner_label.configure(text=f"Version {info['version']} is available   (you have {core.APP_VERSION})")
        self.banner_sub.configure(
            text="Press “Update now”. The app downloads it, installs it and reopens by itself.",
            text_color=("gray25", "gray75"))
        self.banner_bar.grid_remove()
        self.banner_now.configure(state="normal")
        self.banner_later.configure(state="normal")
        self.banner.grid()

    def hide_banner(self):
        if not self.update_busy:
            self.banner.grid_remove()

    def open_release_page(self):
        if self.update_info and self.update_info.get("page"):
            webbrowser.open(self.update_info["page"])

    def start_update(self):
        info = self.update_info
        if not info or self.update_busy:
            return
        if any(i.status in ("Queued", "Downloading") for i in self.items):
            messagebox.showinfo("Busy", "Please wait until the downloads finish, then update.")
            return
        self.update_busy = True
        self.banner_now.configure(state="disabled")
        self.banner_later.configure(state="disabled")
        self.banner_sub.configure(text="Downloading the update...", text_color=("gray25", "gray75"))
        self.banner_bar.set(0)
        self.banner_bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 12))

        def work():
            try:
                path = core.download_update(
                    info, on_progress=lambda done, total: self.msgs.put(("update_progress", done, total)))
                self.msgs.put(("update_ready", path))
            except Exception as e:
                self.msgs.put(("update_error", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _update_failed(self, message):
        self.update_busy = False
        self.banner_now.configure(state="normal", text="Try again")
        self.banner_later.configure(state="normal")
        self.banner_bar.grid_remove()
        self.banner_sub.configure(text=shorten(message, 140), text_color=RED)

    def _install_update(self, path):
        self.banner_sub.configure(text="Installing... the app will close and reopen in a moment.")
        self.banner_bar.set(1)
        try:
            core.launch_installer(path)
        except Exception as e:
            self._update_failed(str(e))
            return
        self.closing = True
        self.after(700, self.destroy)

    # ================================================================ engine updates

    def _startup_update(self):
        note = core.update_ytdlp()
        version = core.get_version()
        self.engine_ready.set()
        self.msgs.put(("engine", version, note))

    def manual_update(self):
        if self.runner_active:
            messagebox.showinfo("Busy", "Please wait until the downloads finish, then update.")
            return
        self.engine_ready.clear()
        self.update_btn.configure(state="disabled")
        self.status_var.set("Updating downloader...")

        def work():
            note = core.update_ytdlp()
            version = core.get_version()
            self.engine_ready.set()
            self.msgs.put(("engine", version, note))

        threading.Thread(target=work, daemon=True).start()

    # ================================================================ messages from worker threads

    def _poll(self):
        if self.closing:
            return
        try:
            while True:
                kind, *rest = self.msgs.get_nowait()
                if kind == "refresh":
                    item = next((i for i in self.items if i.id == rest[0]), None)
                    if item:
                        self._refresh_item(item)
                elif kind == "summary":
                    self._update_summary()
                elif kind == "history":
                    if self.tabs.get() == "History":
                        self.refresh_history()
                elif kind == "preview":
                    self._apply_preview(*rest)
                elif kind == "update_found":
                    self._show_banner(rest[0])
                elif kind == "update_progress":
                    done, total = rest
                    pct = (done / total) if total else 0
                    self.banner_bar.set(min(pct, 1.0))
                    self.banner_sub.configure(text=f"Downloading the update...  {pct * 100:.0f}%")
                elif kind == "update_ready":
                    self._install_update(rest[0])
                elif kind == "update_error":
                    self._update_failed(rest[0])
                elif kind == "engine":
                    version, note = rest
                    self.engine_var.set(f"App {core.APP_VERSION}   ·   yt-dlp {version}")
                    self.update_btn.configure(state="normal")
                    self._update_summary()
                    if "up to date" not in note.lower() and self.status_var.get() == "Ready":
                        self.status_var.set(note)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def on_close(self):
        active = any(i.status == "Downloading" for i in self.items)
        if active and not messagebox.askyesno("Quit", "A download is running. Stop it and quit?"):
            return
        self.closing = True
        for i in self.items:
            if i.job:
                i.job.cancel()
        self.destroy()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()