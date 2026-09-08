from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from dng_core import (
    build_jobs,
    download_exiftool_windows,
    exiftool_metadata,
    locate_exiftool,
    process_batch,
    scan_dng_paths,
    verify_outputs,
)

APP_TITLE = "Anamorphic DNG Batch"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("900x690")
        self.minsize(760, 560)
        self.cancel_event = threading.Event()
        self.busy = False
        self._build_ui()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=(18, 14, 18, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Anamorphic DNG Batch", font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="RAW-preserving folder de-squeeze with per-file DNG RAW-IFD detection").grid(row=1, column=0, sticky="w", pady=(2, 0))

        body = ttk.Frame(self, padding=(18, 8, 18, 10))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        body.rowconfigure(9, weight=1)

        self.source_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.ratio_var = tk.StringVar(value="1.33")
        self.recursive_var = tk.BooleanVar(value=True)
        self.preserve_scale_var = tk.BooleanVar(value=True)
        self.verify_var = tk.BooleanVar(value=True)
        self.workers_var = tk.IntVar(value=2)
        self.collision_var = tk.StringVar(value="skip")

        ttk.Label(body, text="Source folder").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(body, textvariable=self.source_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(body, text="Browse…", command=self.choose_source).grid(row=0, column=2)

        ttk.Label(body, text="Output folder").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(body, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Button(body, text="Browse…", command=self.choose_output).grid(row=1, column=2)

        ttk.Label(body, text="Anamorphic ratio").grid(row=2, column=0, sticky="w", pady=5)
        ratio_box = ttk.Combobox(body, textvariable=self.ratio_var, values=("1.25", "1.33", "1.5", "1.55", "1.6", "1.8", "2.0"), width=12)
        ratio_box.grid(row=2, column=1, sticky="w", padx=8)
        ttk.Label(body, text="e.g. 1.33× or 2.0×").grid(row=2, column=1, sticky="w", padx=(135, 0))

        ttk.Label(body, text="Parallel workers").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Spinbox(body, from_=1, to=8, textvariable=self.workers_var, width=6).grid(row=3, column=1, sticky="w", padx=8)
        ttk.Label(body, text="2 is a good SSD default; HDDs usually prefer 1").grid(row=3, column=1, sticky="w", padx=(78, 0))

        ttk.Label(body, text="Existing outputs").grid(row=4, column=0, sticky="w", pady=5)
        collision_frame = ttk.Frame(body)
        collision_frame.grid(row=4, column=1, sticky="w", padx=8)
        for text, value in (("Skip", "skip"), ("Overwrite", "overwrite"), ("Keep both", "rename")):
            ttk.Radiobutton(collision_frame, text=text, value=value, variable=self.collision_var).pack(side="left", padx=(0, 15))

        checks = ttk.Frame(body)
        checks.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        ttk.Checkbutton(checks, text="Include subfolders and mirror folder structure", variable=self.recursive_var).pack(anchor="w")
        ttk.Checkbutton(checks, text="Preserve/multiply an existing DefaultScale instead of replacing it", variable=self.preserve_scale_var).pack(anchor="w")
        ttk.Checkbutton(checks, text="Verify output DefaultScale and RAW image digest when available", variable=self.verify_var).pack(anchor="w")

        info = ttk.LabelFrame(body, text="What this does", padding=10)
        info.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(8, 10))
        info.columnconfigure(0, weight=1)
        ttk.Label(info, wraplength=820, justify="left", text=(
            "The RAW mosaic/linear image is not resized or interpolated. The program detects which TIFF/DNG IFD actually contains the RAW image, then writes DefaultScale into that exact image directory. "
            "It also maps portrait/landscape and EXIF 90°/270° rotation to the correct stored RAW axis. Lightroom Classic/Desktop/Web should honor this; many ordinary image viewers and Lightroom Mobile may ignore DNG pixel-aspect metadata."
        )).grid(row=0, column=0, sticky="ew")

        button_row = ttk.Frame(body)
        button_row.grid(row=7, column=0, columnspan=3, sticky="ew")
        self.run_btn = ttk.Button(button_row, text="Process folder", command=self.start)
        self.run_btn.pack(side="left")
        self.cancel_btn = ttk.Button(button_row, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=8)
        ttk.Button(button_row, text="Install / update ExifTool", command=self.install_exiftool).pack(side="right")

        self.progress = ttk.Progressbar(body, mode="determinate")
        self.progress.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(12, 6))

        log_frame = ttk.Frame(body)
        log_frame.grid(row=9, column=0, columnspan=3, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=12, wrap="none", font=("Consolas", 9), state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        ysb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        xsb = ttk.Scrollbar(log_frame, orient="horizontal", command=self.log.xview)
        self.log.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status_var, anchor="w", padding=(18, 4, 18, 8)).grid(row=2, column=0, sticky="ew")

    def choose_source(self):
        path = filedialog.askdirectory(title="Choose folder containing DNG files")
        if path:
            self.source_var.set(path)
            if not self.output_var.get().strip():
                self.output_var.set(str(Path(path) / "desqueezed_dng"))

    def choose_output(self):
        path = filedialog.askdirectory(title="Choose output folder")
        if path:
            self.output_var.set(path)

    def append_log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def ui(self, func, *args, **kwargs):
        # Tk.after() only forwards positional arguments.  Some widget methods
        # (for example ttk.Progressbar.configure) are naturally called with
        # keyword arguments, so wrap those calls instead of passing kwargs to
        # App.ui itself.
        if kwargs:
            self.after(0, lambda: func(*args, **kwargs))
        else:
            self.after(0, func, *args)

    def set_busy(self, busy: bool):
        self.busy = busy
        self.run_btn.configure(state="disabled" if busy else "normal")
        self.cancel_btn.configure(state="normal" if busy else "disabled")

    def install_exiftool(self):
        if self.busy:
            return
        self.set_busy(True)
        def task():
            try:
                path = download_exiftool_windows(status=lambda s: self.ui(self.status_var.set, s))
                self.ui(self.append_log, f"ExifTool ready: {path}")
            except Exception as e:
                self.ui(messagebox.showerror, APP_TITLE, str(e))
            finally:
                self.ui(self.set_busy, False)
                self.ui(self.status_var.set, "Ready")
        threading.Thread(target=task, daemon=True).start()

    def cancel(self):
        self.cancel_event.set()
        self.status_var.set("Cancelling after current file operations…")

    def start(self):
        if self.busy:
            return
        try:
            source = Path(self.source_var.get().strip()).expanduser()
            output = Path(self.output_var.get().strip()).expanduser()
            ratio = float(self.ratio_var.get().strip().lower().replace("x", ""))
            if ratio <= 0:
                raise ValueError
        except Exception:
            messagebox.showerror(APP_TITLE, "Choose valid folders and enter a positive numeric ratio such as 1.33 or 2.0.")
            return
        if not source.is_dir():
            messagebox.showerror(APP_TITLE, "Source folder does not exist.")
            return
        try:
            if source.resolve() == output.resolve():
                messagebox.showerror(APP_TITLE, "Output folder must be different from the source folder so originals remain untouched.")
                return
        except OSError:
            pass

        exiftool = locate_exiftool()
        if not exiftool:
            if os.name == "nt" and messagebox.askyesno(APP_TITLE, "ExifTool is required. Download the current official Windows build automatically now?"):
                self.set_busy(True)
                def install_then_start():
                    try:
                        path = download_exiftool_windows(status=lambda s: self.ui(self.status_var.set, s))
                        self.ui(self.append_log, f"ExifTool ready: {path}")
                        self.ui(self.set_busy, False)
                        self.ui(self.start)
                    except Exception as e:
                        self.ui(self.set_busy, False)
                        self.ui(messagebox.showerror, APP_TITLE, str(e))
                threading.Thread(target=install_then_start, daemon=True).start()
            else:
                messagebox.showerror(APP_TITLE, "ExifTool was not found. Install it or use the Install / update ExifTool button.")
            return

        # Snapshot every Tk-bound option on the main thread before starting
        # the worker.  Calling Variable.get() from worker threads is not
        # reliably safe across Tcl/Tk builds.
        recursive = bool(self.recursive_var.get())
        preserve_scale = bool(self.preserve_scale_var.get())
        verify = bool(self.verify_var.get())
        workers = int(self.workers_var.get())
        collision = self.collision_var.get()

        self.cancel_event.clear()
        self.progress["value"] = 0
        self.set_busy(True)
        self.append_log("—" * 70)
        self.append_log(f"Source: {source}")
        self.append_log(f"Output: {output}")
        self.append_log(f"Ratio: {ratio:g}×")

        def task():
            try:
                self.ui(self.status_var.set, "Scanning DNG folder…")
                paths = scan_dng_paths(source, recursive)
                if not paths:
                    self.ui(messagebox.showinfo, APP_TITLE, "No .dng files were found in the selected folder.")
                    return
                self.ui(self.append_log, f"Found {len(paths)} DNG file(s). Reading metadata in one batch…")
                infos = exiftool_metadata(exiftool, paths)
                if len(infos) != len(paths):
                    self.ui(self.append_log, f"Warning: metadata was read for {len(infos)} of {len(paths)} files.")
                jobs = build_jobs(infos, source, output, ratio, recursive, preserve_scale)
                self.ui(self.progress.configure, maximum=max(1, len(jobs)))

                landscape = sum(1 for j in jobs if j.info.display_orientation == "landscape")
                portrait = sum(1 for j in jobs if j.info.display_orientation == "portrait")
                square = len(jobs) - landscape - portrait
                self.ui(self.append_log, f"Orientation: {landscape} landscape, {portrait} portrait, {square} square.")
                ifd_counts = {}
                for j in jobs:
                    ifd_counts[j.info.raw_ifd_group] = ifd_counts.get(j.info.raw_ifd_group, 0) + 1
                self.ui(self.append_log, "RAW image IFDs: " + ", ".join(f"{k}={v}" for k, v in sorted(ifd_counts.items())))
                self.ui(self.status_var.set, f"Processing {len(jobs)} DNGs…")

                def on_progress(done, total, result):
                    def update():
                        self.progress["value"] = done
                        flag = "OK" if result.ok else "ERROR"
                        self.append_log(f"[{done}/{total}] {flag}: {result.source.name} — {result.message}")
                        self.status_var.set(f"{done}/{total} processed")
                    self.ui(update)

                summary = process_batch(
                    exiftool,
                    jobs,
                    workers=workers,
                    collision=collision,
                    cancel_event=self.cancel_event,
                    progress=on_progress,
                )

                issues = []
                if verify and summary.succeeded and not self.cancel_event.is_set():
                    self.ui(self.status_var.set, "Verifying DNG metadata and RAW digest…")
                    issues = verify_outputs(exiftool, jobs, summary.results)
                    for issue in issues:
                        self.ui(self.append_log, "VERIFY: " + issue)

                mib = summary.bytes_written / (1024 * 1024)
                rate = mib / summary.elapsed_s if summary.elapsed_s else 0
                final = (f"Done: {summary.succeeded} written, {summary.skipped} skipped, {summary.failed} failed. "
                         f"{mib:.1f} MiB in {summary.elapsed_s:.1f}s ({rate:.1f} MiB/s).")
                if issues:
                    final += f" Verification found {len(issues)} issue(s)."
                elif verify and summary.succeeded:
                    final += " Verification passed."
                self.ui(self.append_log, final)
                self.ui(self.status_var.set, final)
                if summary.failed or issues:
                    self.ui(messagebox.showwarning, APP_TITLE, final)
                else:
                    self.ui(messagebox.showinfo, APP_TITLE, final)
            except Exception as e:
                self.ui(self.append_log, "FATAL: " + str(e))
                self.ui(messagebox.showerror, APP_TITLE, str(e))
                self.ui(self.status_var.set, "Error")
            finally:
                self.ui(self.set_busy, False)
        threading.Thread(target=task, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()
