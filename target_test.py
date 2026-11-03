"""
HeadNav target test - measures how fast and accurately you can click targets.

Use it to get real numbers for your report/resume, e.g. compare:
  - a normal mouse vs HeadNav
  - One Euro filter vs EMA vs no filter
  - joystick vs absolute mode

Run:  python target_test.py <label>      e.g.  python target_test.py headnav_oneeuro
(or double-click test.bat). Start HeadNav first if you're testing it.

Click the START circle to begin. Each trial shows one target; click it.
Results are saved to logs/target_test_<label>_<time>.csv with a summary.
Esc quits early.

Metrics
  movement time  - seconds from target appearing to a successful click
  miss rate      - clicks that landed outside the target
  throughput     - Fitts' law: ID / MT, where ID = log2(D / W + 1) bits
                   (the standard way to compare pointing devices, ISO 9241-9)
"""

import csv
import math
import random
import statistics
import sys
import time
import tkinter as tk
from pathlib import Path

TRIALS = 21
SIZES = [90, 60, 40]           # target diameters in px
LOG_DIR = Path(__file__).resolve().parent / "logs"
BG, FG, TARGET, START, HIT = "#15171c", "#e8e8e8", "#3fa7f5", "#5cc46a", "#f5c542"


class TargetTest:
    def __init__(self, label):
        self.label = label
        self.root = tk.Tk()
        self.root.title("HeadNav target test")
        self.root.attributes("-fullscreen", True)
        self.root.configure(bg=BG)
        self.canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.root.bind("<Escape>", lambda e: self.finish(early=True))
        self.canvas.bind("<Button-1>", self.on_click)
        self.root.update()
        self.W, self.H = self.root.winfo_width(), self.root.winfo_height()
        self.rng = random.Random()
        self.trial = -1
        self.results = []
        self.misses = 0
        self.target = None
        self.done = False
        self.show_start()

    def text(self, y, msg, size=20, color=FG):
        self.canvas.create_text(self.W // 2, y, text=msg, fill=color, font=("Segoe UI", size))

    def show_start(self):
        self.canvas.delete("all")
        self.text(self.H // 2 - 160, f"Target test: {self.label}", 28)
        self.text(self.H // 2 - 110, f"{TRIALS} targets. Click each one as quickly and accurately as you can.", 16)
        self.text(self.H // 2 - 80, "Click the green circle to start. Esc to quit.", 16, "#9aa0a6")
        r = 60
        cx, cy = self.W // 2, self.H // 2 + 40
        self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=START, outline="")
        self.text(cy, "START", 18, BG)
        self.target = (cx, cy, 2 * r)
        self.prev_click = (cx, cy)

    def next_target(self):
        self.trial += 1
        if self.trial >= TRIALS:
            self.finish()
            return
        size = SIZES[self.trial % len(SIZES)]
        margin = 60
        px, py = self.prev_click
        for _ in range(200):     # targets at least 250 px from where you are
            x = self.rng.randint(margin, self.W - margin)
            y = self.rng.randint(margin + 40, self.H - margin)
            if math.dist((x, y), (px, py)) > 250:
                break
        self.target = (x, y, size)
        self.t_shown = time.perf_counter()
        self.trial_misses = 0
        self.canvas.delete("all")
        r = size / 2
        self.canvas.create_oval(x - r, y - r, x + r, y + r, fill=TARGET, outline="")
        self.canvas.create_text(20, 20, anchor="nw", fill="#9aa0a6", font=("Segoe UI", 12),
                                text=f"{self.label}   target {self.trial + 1}/{TRIALS}   misses {self.misses}")

    def on_click(self, event):
        if self.done or self.target is None:
            return
        x, y, size = self.target
        inside = math.dist((event.x, event.y), (x, y)) <= size / 2
        if self.trial == -1:                 # start button
            if inside:
                self.prev_click = (event.x, event.y)
                self.next_target()
            return
        if not inside:
            self.misses += 1
            self.trial_misses += 1
            self.canvas.create_oval(event.x - 4, event.y - 4, event.x + 4, event.y + 4, outline="#e05555")
            return
        mt = time.perf_counter() - self.t_shown
        d = math.dist(self.prev_click, (x, y))
        ide = math.log2(d / size + 1)
        self.results.append({
            "trial": self.trial + 1, "size_px": size, "distance_px": round(d, 1),
            "movement_time_s": round(mt, 3), "misses": self.trial_misses,
            "index_of_difficulty_bits": round(ide, 3), "throughput_bits_per_s": round(ide / mt, 3),
        })
        self.prev_click = (event.x, event.y)
        self.next_target()

    def finish(self, early=False):
        if self.done:
            return
        self.done = True
        self.canvas.delete("all")
        # the first trial includes "getting started" time, so drop it from the summary
        res = self.results[1:] if len(self.results) > 1 else self.results
        if not res:
            self.root.destroy()
            return
        LOG_DIR.mkdir(exist_ok=True)
        path = LOG_DIR / f"target_test_{self.label}_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(self.results[0].keys()))
            w.writeheader()
            w.writerows(self.results)
        mt = statistics.mean(r["movement_time_s"] for r in res)
        tp = statistics.mean(r["throughput_bits_per_s"] for r in res)
        clicks = len(self.results) + self.misses
        miss_rate = 100 * self.misses / clicks if clicks else 0
        by_size = {s: [r["movement_time_s"] for r in res if r["size_px"] == s] for s in SIZES}
        summary = [
            f"Results: {self.label}" + ("  (ended early)" if early else ""),
            f"Mean time per target:  {mt:.2f} s",
            f"Miss rate:  {miss_rate:.1f} %   ({self.misses} misses)",
            f"Throughput:  {tp:.2f} bits/s",
            "   ".join(f"{s}px: {statistics.mean(v):.2f}s" for s, v in by_size.items() if v),
            "",
            f"Saved to {path}",
            "Press Esc or click to close",
        ]
        for i, line in enumerate(summary):
            self.text(self.H // 2 - 140 + 40 * i, line, 24 if i == 0 else 18)
        print("\n".join(summary))
        self.canvas.bind("<Button-1>", lambda e: self.root.destroy())
        self.root.bind("<Escape>", lambda e: self.root.destroy())

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else (input("Label for this run (e.g. mouse, headnav): ").strip() or "run")
    label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
    TargetTest(label).run()
