#!/usr/bin/env python3
"""AudiobookSpecFix desktop shell (tkinter). Pick the chapter folder -> check every
chapter against the ACX spec, fix what a level/format pass can fix, then
re-measure everything and write the fails-first report."""
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from acx_ready import run_book


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AudiobookSpecFix — ACX Spec Check, Fix & Re-Check")
        self.geometry("700x560")
        self.indir = tk.StringVar()
        self.outdir = tk.StringVar()
        self.check_only = tk.BooleanVar(value=False)
        tk.Label(self, text="Chapter folder (one book: MP3 chapters, in order):").pack(
            anchor="w", padx=12, pady=(12, 2))
        row = tk.Frame(self)
        row.pack(fill="x", padx=12)
        tk.Entry(row, textvariable=self.indir).pack(side="left", fill="x", expand=True)
        tk.Button(row, text="Browse…", command=self.pick_in).pack(side="left", padx=6)
        tk.Label(self, text="Output folder (fixed chapters + acx_report.md):").pack(
            anchor="w", padx=12, pady=(10, 2))
        row = tk.Frame(self)
        row.pack(fill="x", padx=12)
        tk.Entry(row, textvariable=self.outdir).pack(side="left", fill="x", expand=True)
        tk.Button(row, text="Browse…", command=self.pick_out).pack(side="left", padx=6)
        tk.Checkbutton(self, text="Check only — measure and report, do not fix",
                       variable=self.check_only).pack(anchor="w", padx=12, pady=(8, 0))
        self.btn = tk.Button(self, text="Check, Fix & Re-Check", command=self.run, height=2)
        self.btn.pack(pady=12)
        self.log = scrolledtext.ScrolledText(self, wrap="word")
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.write("AudiobookSpecFix measures every chapter against the ACX submission spec\n"
                   "(RMS, true peak, noise floor, room tone, channels, 192 kbps CBR),\n"
                   "fixes what a level/format pass can fix, then re-measures the outputs\n"
                   "and reports fails first. A chapter whose noise floor was recorded too\n"
                   "hot is reported honestly as unfixable — never silently 'fixed'.\n"
                   "Everything runs locally — your manuscript audio never leaves this PC.\n")

    def write(self, s):
        self.log.insert("end", s)
        self.log.see("end")

    def pick_in(self):
        p = filedialog.askdirectory()
        if p:
            self.indir.set(p)
            if not self.outdir.get():
                self.outdir.set(os.path.join(p, "audiobookspecfix_output"))

    def pick_out(self):
        p = filedialog.askdirectory()
        if p:
            self.outdir.set(p)

    def run(self):
        src = self.indir.get().strip()
        out = self.outdir.get().strip()
        if not src or not os.path.isdir(src):
            messagebox.showerror("AudiobookSpecFix", "Choose the chapter folder first.")
            return
        if not out:
            messagebox.showerror("AudiobookSpecFix", "Choose an output folder.")
            return
        self.btn.config(state="disabled")
        threading.Thread(target=self._work, args=(src, out), daemon=True).start()

    def _work(self, src, out):
        try:
            s = run_book(src, out, fix=not self.check_only.get())
            self.write(f"\nDone. Book verdict: {s['book_verdict']}\n")
            for e in s["results"]:
                self.write(f"  {e['name']}: {e['status']}\n")
            self.write(f"Report: {s['report_path']}\n")
        except Exception as e:
            self.write(f"\nError: {e}\n")
        finally:
            self.btn.config(state="normal")


if __name__ == "__main__":
    App().mainloop()
