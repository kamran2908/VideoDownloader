"""
Video Downloader - simple window for saving videos from YouTube, Twitter/X and
other sites. All the real work is done by core.py (which runs yt-dlp).
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import core


NOTICE_TEXT = (
    "This app saves videos from sites like YouTube and X.\n\n"
    "Only download videos that you own, that are free to use, or that you have "
    "permission to save. Websites have their own rules and copyright law applies. "
    "You are responsible for how you use this app."
)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Video Downloader")
        root.geometry("700x680")
        root.minsize(640, 620)

        self.msgs = queue.Queue()
        self.job = core.Job()
        self.stop_all = False
        self.busy = False

        s = core.load_settings()
        default_folder = str(Path.home() / "Downloads")
        self.platform_var = tk.StringVar(value=core.PLATFORMS[0])
        self.name_var = tk.StringVar()
        self.folder_var = tk.StringVar(value=s.get("folder", default_folder))
        self.quality_var = tk.StringVar(
            value=s.get("quality") if s.get("quality") in core.QUALITIES else core.QUALITIES[0]
        )
        self.browser_var = tk.StringVar(
            value=s.get("browser") if s.get("browser") in core.BROWSERS else core.BROWSERS[0]
        )
        self.playlist_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Starting...")
        self.engine_var = tk.StringVar(value="")

        try:
            icon = core.resource_path("icon.ico")
            if icon.exists() and sys.platform.startswith("win"):
                root.iconbitmap(str(icon))
        except tk.TclError:
            pass

        self._build_ui()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        if not self._first_run_notice():
            root.after(0, root.destroy)
            return

        if core.ytdlp_command() is None:
            messagebox.showerror(
                "Component missing",
                "The downloader engine (yt-dlp) was not found.\nPlease reinstall the app.",
            )
        self.dl_btn.configure(state="disabled")
        self.update_btn.configure(state="disabled")
        threading.Thread(target=self._startup_update, daemon=True).start()
        self._poll()

    # -------------------------------------------------------------- UI

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        pad = {"padx": 8, "pady": 5}

        ttk.Label(outer, text="Video Downloader", font=("Segoe UI", 17, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=8)
        ttk.Label(outer, text="Paste a link, choose options, press Download.",
                  foreground="gray").grid(row=1, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 8))

        # links
        ttk.Label(outer, text="Video link(s)").grid(row=2, column=0, sticky="nw", **pad)
        link_frame = ttk.Frame(outer)
        link_frame.grid(row=2, column=1, sticky="ew", **pad)
        link_frame.columnconfigure(0, weight=1)
        self.links = tk.Text(link_frame, height=4, wrap="word")
        self.links.grid(row=0, column=0, sticky="ew")
        ls = ttk.Scrollbar(link_frame, command=self.links.yview)
        ls.grid(row=0, column=1, sticky="ns")
        self.links["yscrollcommand"] = ls.set
        ttk.Button(outer, text="Paste", command=self.paste).grid(row=2, column=2, sticky="n", **pad)
        ttk.Label(outer, text="Tip: you can paste several links, one per line.",
                  foreground="gray").grid(row=3, column=1, sticky="w", padx=8)

        # platform
        ttk.Label(outer, text="Platform").grid(row=4, column=0, sticky="w", **pad)
        ttk.Combobox(outer, textvariable=self.platform_var, values=core.PLATFORMS,
                     state="readonly").grid(row=4, column=1, columnspan=2, sticky="ew", **pad)

        # name
        ttk.Label(outer, text="File name (optional)").grid(row=5, column=0, sticky="w", **pad)
        ttk.Entry(outer, textvariable=self.name_var).grid(row=5, column=1, columnspan=2, sticky="ew", **pad)

        # folder
        ttk.Label(outer, text="Save to").grid(row=6, column=0, sticky="w", **pad)
        ttk.Entry(outer, textvariable=self.folder_var).grid(row=6, column=1, sticky="ew", **pad)
        ttk.Button(outer, text="Browse...", command=self.browse).grid(row=6, column=2, **pad)

        # quality
        ttk.Label(outer, text="Quality").grid(row=7, column=0, sticky="w", **pad)
        ttk.Combobox(outer, textvariable=self.quality_var, values=core.QUALITIES,
                     state="readonly").grid(row=7, column=1, columnspan=2, sticky="ew", **pad)

        # playlist
        ttk.Checkbutton(outer, text="If the link is a playlist, download the whole playlist",
                        variable=self.playlist_var).grid(row=8, column=1, columnspan=2, sticky="w", **pad)

        # browser login
        ttk.Label(outer, text="Use browser login").grid(row=9, column=0, sticky="w", **pad)
        ttk.Combobox(outer, textvariable=self.browser_var, values=core.BROWSERS,
                     state="readonly").grid(row=9, column=1, columnspan=2, sticky="ew", **pad)
        ttk.Label(outer, text="Only needed for age-restricted or sign-in-only videos.",
                  foreground="gray").grid(row=10, column=1, columnspan=2, sticky="w", padx=8)

        # buttons
        bar = ttk.Frame(outer)
        bar.grid(row=11, column=0, columnspan=3, pady=12)
        self.dl_btn = ttk.Button(bar, text="Download", command=self.start)
        self.dl_btn.pack(side="left", padx=5)
        self.cancel_btn = ttk.Button(bar, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=5)
        ttk.Button(bar, text="Open folder", command=self.open_folder).pack(side="left", padx=5)
        self.update_btn = ttk.Button(bar, text="Update downloader", command=self.manual_update)
        self.update_btn.pack(side="left", padx=5)

        # progress
        self.progress = ttk.Progressbar(outer, maximum=100)
        self.progress.grid(row=12, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(outer, textvariable=self.status_var).grid(row=13, column=0, columnspan=3, sticky="w", padx=8)

        # log
        outer.rowconfigure(14, weight=1)
        lf = ttk.Frame(outer)
        lf.grid(row=14, column=0, columnspan=3, sticky="nsew", **pad)
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)
        self.log_box = tk.Text(lf, height=7, state="disabled", wrap="word")
        self.log_box.grid(row=0, column=0, sticky="nsew")
        sc = ttk.Scrollbar(lf, command=self.log_box.yview)
        sc.grid(row=0, column=1, sticky="ns")
        self.log_box["yscrollcommand"] = sc.set

        ttk.Label(outer, text="Only download videos you own or have permission to save.",
                  foreground="gray").grid(row=15, column=0, columnspan=3, sticky="w", padx=8)
        ttk.Label(outer, textvariable=self.engine_var, foreground="gray").grid(
            row=16, column=0, columnspan=3, sticky="w", padx=8)

    # -------------------------------------------------------------- first-run notice

    def _first_run_notice(self) -> bool:
        """Show the 'before you start' message once. Returns False if the user declines."""
        settings = core.load_settings()
        if settings.get("accepted_notice"):
            return True

        dlg = tk.Toplevel(self.root)
        dlg.title("Before you start")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        accepted = {"ok": False}

        frm = ttk.Frame(dlg, padding=20)
        frm.pack()
        ttk.Label(frm, text="Before you start", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        ttk.Label(frm, text=NOTICE_TEXT, wraplength=420, justify="left").pack(anchor="w", pady=12)

        agree = tk.BooleanVar(value=False)

        def accept():
            accepted["ok"] = True
            dlg.destroy()

        cont = ttk.Button(frm, text="Continue", state="disabled", command=accept)
        ttk.Checkbutton(
            frm, text="I understand", variable=agree,
            command=lambda: cont.configure(state="normal" if agree.get() else "disabled"),
        ).pack(anchor="w")
        cont.pack(anchor="e", pady=(12, 0))

        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        try:
            dlg.wait_visibility()
            dlg.grab_set()
        except tk.TclError:
            pass
        self.root.wait_window(dlg)

        if accepted["ok"]:
            settings["accepted_notice"] = True
            core.save_settings(settings)
        return accepted["ok"]

    # -------------------------------------------------------------- small actions

    def log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def paste(self):
        try:
            clip = self.root.clipboard_get().strip()
        except tk.TclError:
            return
        if self.links.get("1.0", "end").strip():
            self.links.insert("end", "\n")
        self.links.insert("end", clip)

    def browse(self):
        folder = filedialog.askdirectory(initialdir=self.folder_var.get() or str(Path.home()))
        if folder:
            self.folder_var.set(folder)

    def open_folder(self):
        folder = self.folder_var.get()
        if not os.path.isdir(folder):
            messagebox.showinfo("Folder", "That folder doesn't exist yet. It is created on the first download.")
            return
        if sys.platform.startswith("win"):
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.run(["open", folder])
        else:
            subprocess.run(["xdg-open", folder])

    def set_busy(self, busy):
        self.busy = busy
        self.dl_btn.configure(state="disabled" if busy else "normal")
        self.update_btn.configure(state="disabled" if busy else "normal")
        self.cancel_btn.configure(state="normal" if busy else "disabled")

    # -------------------------------------------------------------- updates

    def _startup_update(self):
        self.msgs.put(("status", "Checking for downloader updates..."))
        note = core.update_ytdlp()
        self.msgs.put(("engine", core.get_version(), note, False))

    def manual_update(self):
        self.set_busy(True)
        self.cancel_btn.configure(state="disabled")
        self.status_var.set("Updating downloader...")

        def work():
            note = core.update_ytdlp()
            self.msgs.put(("engine", core.get_version(), note, True))

        threading.Thread(target=work, daemon=True).start()

    # -------------------------------------------------------------- downloading

    def start(self):
        urls = core.extract_urls(self.links.get("1.0", "end"))
        if not urls:
            messagebox.showwarning("Link needed", "Please paste a link that starts with http:// or https://")
            return

        folder = Path(self.folder_var.get().strip().strip('"') or Path.home() / "Downloads").expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Folder problem", f"Can't use that folder:\n{e}")
            return

        chosen = self.platform_var.get()
        detected = core.detect_platform(urls[0])
        if chosen != "Auto-detect" and detected != "Other website" and chosen != detected:
            self.log(f"Note: the link looks like {detected}, but you chose {chosen}. Continuing anyway.")

        name = self.name_var.get()
        if name.strip() and (len(urls) > 1 or self.playlist_var.get()):
            self.log("Note: the custom file name is ignored for several links or playlists.")
            name = ""

        opts = dict(folder=str(folder), name=name, quality=self.quality_var.get(),
                    playlist=self.playlist_var.get(), browser=self.browser_var.get())
        settings = core.load_settings()
        settings.update({"folder": str(folder), "quality": opts["quality"], "browser": opts["browser"]})
        core.save_settings(settings)

        self.stop_all = False
        self.progress["value"] = 0
        self.set_busy(True)
        threading.Thread(target=self._work, args=(urls, opts), daemon=True).start()

    def cancel(self):
        self.stop_all = True
        self.job.cancel()
        self.status_var.set("Cancelling...")

    def _work(self, urls, opts):
        done = failed = 0
        for i, url in enumerate(urls, 1):
            if self.stop_all:
                break
            self.job = core.Job()
            self.msgs.put(("log", f"[{i}/{len(urls)}] Starting: {url}"))
            self.msgs.put(("progress", 0, f"Starting download {i} of {len(urls)}..."))

            def on_progress(pct, speed, eta, i=i):
                text = f"Downloading {i} of {len(urls)}: {pct:.1f}%"
                if speed and "Unknown" not in speed:
                    text += f"   {speed}"
                if eta and eta not in ("NA", "Unknown"):
                    text += f"   {eta} left"
                self.msgs.put(("progress", pct, text))

            res = self.job.run(url, opts, on_progress,
                               lambda t: self.msgs.put(("status", t)))
            if res.cancelled:
                break
            if res.ok:
                done += 1
                for f in res.files[-1:]:
                    self.msgs.put(("log", f"Saved: {f}"))
                if not res.files:
                    self.msgs.put(("log", "Saved."))
            else:
                failed += 1
                self.msgs.put(("log", f"Problem: {res.error}"))

        if self.stop_all:
            self.msgs.put(("finished", "Cancelled.", 0))
        elif failed == 0:
            self.msgs.put(("finished", f"Finished. {done} downloaded.", 100))
        else:
            self.msgs.put(("finished", f"Finished: {done} downloaded, {failed} failed.", 0))

    # -------------------------------------------------------------- messages from worker threads

    def _poll(self):
        try:
            while True:
                kind, *rest = self.msgs.get_nowait()
                if kind == "log":
                    self.log(rest[0])
                elif kind == "status":
                    self.status_var.set(rest[0])
                elif kind == "progress":
                    self.progress["value"] = rest[0]
                    self.status_var.set(rest[1])
                elif kind == "finished":
                    self.status_var.set(rest[0])
                    self.progress["value"] = rest[1]
                    self.log(rest[0])
                    self.set_busy(False)
                elif kind == "engine":
                    version, note, manual = rest
                    self.engine_var.set(f"Engine: yt-dlp {version}")
                    self.status_var.set("Ready")
                    if manual or "up to date" not in note.lower():
                        self.log(note)
                    self.set_busy(False)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def on_close(self):
        if self.busy and not messagebox.askyesno("Quit", "A download is running. Stop it and quit?"):
            return
        self.stop_all = True
        self.job.cancel()
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista" if sys.platform.startswith("win") else "clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
