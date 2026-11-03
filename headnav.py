"""
HeadNav - Adaptive hands-free cursor control with a webcam.

Run:  python headnav.py        (or double-click run.bat on Windows)

Global hotkeys (work even when this window isn't focused):
  F8  pause / resume          F9  recalibrate          F10 quit
Hands-free:  hold a sideways head tilt for 1 s to pause / resume.

Keys when the HeadNav window is focused:
  space pause   c calibrate   m joystick/absolute   f cycle filter
  n nod-click   o mouth-click   d dwell-click   + / - speed
  [ / ] dead zone   s save settings   h help   q quit
"""

import csv
import json
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from headnav_core import (
    FILTER_KINDS, Calibration, CalibrationWizard, HeadNavController, Settings,
    rotation_to_euler,
)

HERE = Path(__file__).resolve().parent
MODEL_PATH = HERE / "face_landmarker.task"
CONFIG_PATH = HERE / "config.json"
LOG_DIR = HERE / "logs"
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/1/face_landmarker.task")
WINDOW = "HeadNav"

# colours (BGR)
GREEN, ORANGE, BLUE, WHITE, GREY, RED, YELLOW = (
    (90, 200, 90), (40, 160, 255), (230, 150, 40), (255, 255, 255), (130, 130, 130),
    (70, 70, 230), (60, 220, 230))


# ---------------------------------------------------------------------------
# I/O helpers (kept small so the app can be smoke-tested with fakes)
# ---------------------------------------------------------------------------

class SystemMouse:
    def __init__(self):
        import pyautogui
        pyautogui.FAILSAFE = False   # cursor is clamped; F8 is the emergency stop
        pyautogui.PAUSE = 0
        self.pg = pyautogui

    def size(self):
        return tuple(self.pg.size())

    def position(self):
        return tuple(self.pg.position())

    def move(self, x, y):
        self.pg.moveTo(int(round(x)), int(round(y)), _pause=False)

    def click(self, button, x, y):
        self.pg.click(int(round(x)), int(round(y)), button=button, _pause=False)


class FaceTracker:
    """Wraps MediaPipe Face Landmarker. Returns (yaw, pitch, roll, jaw_open, landmarks) or None."""

    def __init__(self, model_path):
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
        self.mp = mp
        options = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        self.landmarker = vision.FaceLandmarker.create_from_options(options)
        self.last_ms = -1

    def process(self, rgb, t):
        ms = int(t * 1000)
        if ms <= self.last_ms:          # MediaPipe needs strictly increasing timestamps
            ms = self.last_ms + 1
        self.last_ms = ms
        image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        result = self.landmarker.detect_for_video(image, ms)
        if not result.facial_transformation_matrixes:
            return None
        M = np.array(result.facial_transformation_matrixes[0])
        yaw, pitch, roll = rotation_to_euler(M[:3, :3])
        jaw = 0.0
        if result.face_blendshapes:
            for c in result.face_blendshapes[0]:
                if c.category_name == "jawOpen":
                    jaw = c.score
                    break
        pts = [(lm.x, lm.y) for lm in result.face_landmarks[0]] if result.face_landmarks else []
        return yaw, pitch, roll, jaw, pts

    def close(self):
        self.landmarker.close()


def load_config():
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        data = {}
    return (Settings.from_dict(data.get("settings")),
            Calibration.from_dict(data.get("calibration")),
            int(data.get("camera_index", 0)))


def save_config(settings, calibration, camera_index):
    CONFIG_PATH.write_text(json.dumps({
        "camera_index": camera_index,
        "settings": settings.to_dict(),
        "calibration": calibration.to_dict(),
    }, indent=2))


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------

class App:
    def __init__(self, tracker, mouse, headless=False):
        self.settings, self.cal, self.camera_index = load_config()
        self.tracker = tracker
        self.mouse = mouse
        self.headless = headless
        w, h = mouse.size()
        self.ctrl = HeadNavController(self.settings, self.cal, w, h, mouse.position())
        self.last_commanded = None
        self.paused = True
        self.wizard = None if self.cal.done else CalibrationWizard()
        self.message, self.message_until = "", 0.0
        self.show_help = False
        self.quit = False
        self.fps = 0.0
        self.t_prev_frame = None
        self.last_out = None
        self.face = None
        self._flags = set()             # set by the global-hotkey thread
        self._lock = threading.Lock()
        self._open_log()
        if self.cal.done:
            self.flash("Paused. Press F8 / space or tilt your head to start.", 6)
        else:
            self.flash("First run: let's calibrate. Follow the instructions.", 4)

    # ----- logging ----------------------------------------------------------
    def _open_log(self):
        LOG_DIR.mkdir(exist_ok=True)
        path = LOG_DIR / time.strftime("session_%Y%m%d_%H%M%S.csv")
        self.log_file = open(path, "w", newline="")
        self.log = csv.writer(self.log_file)
        self.log.writerow(["t", "face", "yaw_raw", "pitch_raw", "roll_raw", "yaw_f", "pitch_f",
                           "roll_f", "nx", "ny", "cursor_x", "cursor_y", "jaw_open", "paused",
                           "filter", "event"])

    def _log(self, t, raw, out, event=""):
        if raw is None:
            self.log.writerow([f"{t:.3f}", 0] + [""] * 11 + [int(self.paused), self.settings.filter, event])
            return
        if out is None:     # e.g. during calibration: raw angles only
            filtered = [""] * 5
        else:
            filtered = [f"{out.yaw:.3f}", f"{out.pitch:.3f}", f"{out.roll:.3f}",
                        f"{out.nx:.3f}", f"{out.ny:.3f}"]
        cx, cy = (f"{out.cursor[0]:.1f}", f"{out.cursor[1]:.1f}") if out and out.cursor else ("", "")
        self.log.writerow([f"{t:.3f}", 1, *(f"{v:.3f}" for v in raw[:3]), *filtered,
                           cx, cy, f"{raw[3]:.3f}", int(self.paused), self.settings.filter, event])

    # ----- actions ----------------------------------------------------------
    def flash(self, text, seconds=2.0):
        self.message, self.message_until = text, time.monotonic() + seconds

    def set_paused(self, paused):
        self.paused = paused
        if not paused:
            self.ctrl.sync_position(*self.mouse.position())
        self.flash("PAUSED" if paused else "Tracking ON", 1.5)

    def start_calibration(self):
        self.wizard = CalibrationWizard()
        self.flash("Calibrating - follow the instructions", 2)

    def request(self, flag):
        """Called from the hotkey thread."""
        with self._lock:
            self._flags.add(flag)

    def _handle_flags(self):
        with self._lock:
            flags, self._flags = self._flags, set()
        if "pause" in flags and not self.wizard:
            self.set_paused(not self.paused)
        if "calibrate" in flags:
            self.start_calibration()
        if "quit" in flags:
            self.quit = True

    def handle_key(self, key):
        s = self.settings
        if key in (ord("q"), 27):
            self.quit = True
        elif key == ord(" ") and not self.wizard:
            self.set_paused(not self.paused)
        elif key == ord("c"):
            self.start_calibration()
        elif key == ord("h"):
            self.show_help = not self.show_help
        elif key == ord("m"):
            s.mode = "absolute" if s.mode == "joystick" else "joystick"
            self.flash(f"Mode: {s.mode}")
        elif key == ord("f"):
            s.filter = FILTER_KINDS[(FILTER_KINDS.index(s.filter) + 1) % len(FILTER_KINDS)]
            self.ctrl.rebuild_filters()
            self.flash(f"Filter: {s.filter}")
        elif key == ord("n"):
            s.nod_click = not s.nod_click
            self.flash(f"Double-nod click: {'ON' if s.nod_click else 'OFF'}")
        elif key == ord("o"):
            s.mouth_click = not s.mouth_click
            self.flash(f"Mouth click: {'ON' if s.mouth_click else 'OFF'}")
        elif key == ord("d"):
            s.dwell_click = not s.dwell_click
            self.ctrl.dwell.reset()
            self.flash(f"Dwell click: {'ON' if s.dwell_click else 'OFF'}")
        elif key in (ord("+"), ord("=")):
            s.max_speed = min(4000, s.max_speed * 1.15)
            self.flash(f"Speed: {s.max_speed:.0f} px/s")
        elif key in (ord("-"), ord("_")):
            s.max_speed = max(150, s.max_speed / 1.15)
            self.flash(f"Speed: {s.max_speed:.0f} px/s")
        elif key == ord("]"):
            s.dead_zone = min(0.6, s.dead_zone + 0.03)
            self.flash(f"Dead zone: {s.dead_zone:.2f}")
        elif key == ord("["):
            s.dead_zone = max(0.0, s.dead_zone - 0.03)
            self.flash(f"Dead zone: {s.dead_zone:.2f}")
        elif key == ord("s"):
            save_config(s, self.cal, self.camera_index)
            self.flash("Settings saved")

    # ----- per-frame --------------------------------------------------------
    def step(self, frame_bgr, t):
        self._handle_flags()
        if self.t_prev_frame is not None:
            self.fps = 0.9 * self.fps + 0.1 / max(t - self.t_prev_frame, 1e-6)
        self.t_prev_frame = t

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        self.face = self.tracker.process(rgb, t)

        if self.face is None:
            self.ctrl.face_lost()
            if self.wizard:
                self.wizard.face_lost()
            self.last_out = None
            self._log(t, None, None)
            return

        yaw, pitch, roll, jaw, _ = self.face

        # ---- calibration wizard ----
        if self.wizard:
            if self.wizard.update(t, yaw, pitch, roll):
                cal = self.wizard.calibration()
                problems = cal.problems()
                self.wizard = None
                if problems:
                    self.flash("Calibration problem: " + problems[0], 6)
                    if not self.cal.done:
                        self.wizard = CalibrationWizard()   # must calibrate at least once
                else:
                    self.cal = cal
                    self.ctrl.cal = cal
                    self.ctrl.face_lost()
                    save_config(self.settings, cal, self.camera_index)
                    self.paused = False
                    self.ctrl.sync_position(*self.mouse.position())
                    self.flash("Calibrated! Tracking ON. Tilt head or F8 to pause.", 5)
            self._log(t, self.face, None, "calibrating")
            return

        # ---- someone used the real mouse? follow it ----
        real = self.mouse.position()
        if self.last_commanded and (abs(real[0] - self.last_commanded[0]) > 3 or
                                    abs(real[1] - self.last_commanded[1]) > 3):
            self.ctrl.sync_position(*real)

        out = self.ctrl.update(t, yaw, pitch, roll, jaw, paused=self.paused)
        self.last_out = out
        events = []

        if out.toggle_pause:
            self.set_paused(not self.paused)
            events.append("pause" if self.paused else "resume")

        if out.cursor and not self.paused:
            self.mouse.move(*out.cursor)
            self.last_commanded = (int(round(out.cursor[0])), int(round(out.cursor[1])))
            for button, (x, y) in out.clicks:
                self.mouse.click(button, x, y)
                events.append(f"{button}_click")
                self.flash("RIGHT CLICK" if button == "right" else "CLICK", 0.6)

        self._log(t, self.face, out, ";".join(events))

    # ----- drawing ----------------------------------------------------------
    def draw(self, frame, t):
        h, w = frame.shape[:2]
        s = self.settings

        if self.face:
            for x, y in self.face[4][::8]:
                cv2.circle(frame, (int(x * w), int(y * h)), 1, (90, 220, 90), -1)

        # status bar
        if self.wizard:
            status, color = "CALIBRATING", BLUE
        elif self.face is None:
            status, color = "NO FACE", RED
        elif self.paused:
            status, color = "PAUSED", ORANGE
        else:
            status, color = "ACTIVE", GREEN
        cv2.rectangle(frame, (0, 0), (w, 36), color, -1)
        cv2.putText(frame, status, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, WHITE, 2)
        clicks = [n for n, on in (("nod", s.nod_click), ("mouth", s.mouth_click),
                                  ("dwell", s.dwell_click)) if on]
        info = f"{s.mode} | {s.filter} | click: {'+'.join(clicks) or 'none'} | {self.fps:4.1f} fps"
        cv2.putText(frame, info, (170, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.5, WHITE, 1)

        if self.wizard:
            self._draw_wizard(frame, t)
        elif self.last_out:
            self._draw_radar(frame, self.last_out)
            self._draw_progress(frame, t)

        if self.show_help:
            self._draw_help(frame)
        else:
            cv2.putText(frame, "h: help   F8: pause   F9: calibrate   F10: quit",
                        (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.45, WHITE, 1)

        if time.monotonic() < self.message_until:
            self._banner(frame, self.message, h // 2 + 120 if self.wizard else h - 50)

    def _banner(self, frame, text, y):
        h, w = frame.shape[:2]
        scale = 0.65
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
        while tw > w - 30 and scale > 0.35:
            scale -= 0.05
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
        x = (w - tw) // 2
        cv2.rectangle(frame, (x - 10, y - th - 10), (x + tw + 10, y + 10), (30, 30, 30), -1)
        cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, YELLOW, 2)

    def _draw_wizard(self, frame, t):
        h, w = frame.shape[:2]
        wz = self.wizard
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, h // 2 - 70), (w, h // 2 + 60), (40, 40, 40), -1)
        cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)
        cv2.putText(frame, wz.step_label, (20, h // 2 - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, GREY, 1)
        text = wz.instruction if self.face else "Can't see your face - move into view"
        scale = 0.8
        (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
        while tw > w - 40 and scale > 0.4:
            scale -= 0.05
            (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
        cv2.putText(frame, text, ((w - tw) // 2, h // 2), cv2.FONT_HERSHEY_SIMPLEX, scale, WHITE, 2)
        p = wz.progress(t)
        cv2.rectangle(frame, (40, h // 2 + 25), (w - 40, h // 2 + 40), GREY, 1)
        cv2.rectangle(frame, (40, h // 2 + 25), (40 + int((w - 80) * p), h // 2 + 40), BLUE, -1)
        cv2.putText(frame, "Hold the pose until the bar fills", (40, h // 2 + 56),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, GREY, 1)

    def _draw_radar(self, frame, out):
        """Small box showing where your head is relative to the dead zone."""
        h, w = frame.shape[:2]
        size = 110
        x0, y0 = w - size - 12, 48
        cx, cy = x0 + size // 2, y0 + size // 2
        cv2.rectangle(frame, (x0, y0), (x0 + size, y0 + size), (40, 40, 40), -1)
        cv2.rectangle(frame, (x0, y0), (x0 + size, y0 + size), GREY, 1)
        dz = int(self.settings.dead_zone * size / 2)
        cv2.rectangle(frame, (cx - dz, cy - dz), (cx + dz, cy + dz), (90, 90, 90), -1)
        px = int(cx + max(-1, min(1, out.nx)) * size / 2)
        py = int(cy + max(-1, min(1, out.ny)) * size / 2)
        cv2.circle(frame, (px, py), 6, GREEN if not self.paused else ORANGE, -1)
        cv2.putText(frame, f"yaw {out.yaw - self.cal.center_yaw:+5.1f}", (x0, y0 + size + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, WHITE, 1)
        cv2.putText(frame, f"pitch {out.pitch - self.cal.center_pitch:+5.1f}", (x0, y0 + size + 32),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, WHITE, 1)

    def _draw_progress(self, frame, t):
        """Progress bars for gestures that take time (so users know what's happening)."""
        s, c = self.settings, self.ctrl
        bars = []
        if s.tilt_pause and c.tilt.progress(t, s) > 0:
            bars.append(("pause" if not self.paused else "resume", c.tilt.progress(t, s), ORANGE))
        if s.mouth_click and c.mouth.hold_fraction(t, s) > 0:
            bars.append(("right click", c.mouth.hold_fraction(t, s), YELLOW))
        if s.dwell_click and not self.paused and c.dwell.progress(t, s) > 0:
            bars.append(("dwell", c.dwell.progress(t, s), GREEN))
        for i, (label, p, color) in enumerate(bars):
            y = 52 + i * 26
            cv2.rectangle(frame, (10, y), (190, y + 16), GREY, 1)
            cv2.rectangle(frame, (10, y), (10 + int(180 * p), y + 16), color, -1)
            cv2.putText(frame, label, (200, y + 13), cv2.FONT_HERSHEY_SIMPLEX, 0.45, WHITE, 1)

    def _draw_help(self, frame):
        lines = [
            "Move: turn / tilt your head (rest in the grey square to stop)",
            "Click: double-nod, or open mouth briefly",
            "Right click: hold mouth open for 1 s",
            "Pause/resume: hold a sideways head tilt for 1 s, or F8",
            "space pause  c calibrate  m mode  f filter",
            "n nod  o mouth  d dwell  +/- speed  [/] dead zone",
            "s save settings  q quit  h close help",
        ]
        h, w = frame.shape[:2]
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, h - 30 - 22 * len(lines)), (w, h), (30, 30, 30), -1)
        cv2.addWeighted(overlay, 0.85, frame, 0.15, 0, frame)
        for i, line in enumerate(lines):
            cv2.putText(frame, line, (12, h - 20 - 22 * (len(lines) - 1 - i)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.47, WHITE, 1)

    def close(self):
        try:
            self.log_file.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def start_global_hotkeys(app):
    try:
        from pynput import keyboard
    except Exception:
        print("pynput not available - global hotkeys disabled (window keys still work).")
        return None

    def on_press(key):
        if key == keyboard.Key.f8:
            app.request("pause")
        elif key == keyboard.Key.f9:
            app.request("calibrate")
        elif key == keyboard.Key.f10:
            app.request("quit")

    listener = keyboard.Listener(on_press=on_press, daemon=True)
    listener.start()
    return listener


def open_camera(index):
    if sys.platform.startswith("win"):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)   # opens much faster on Windows
    else:
        cap = cv2.VideoCapture(index)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)
    return cap


def main():
    if not MODEL_PATH.exists():
        print("Face model not found. Run setup.bat, or download it into this folder from:")
        print("  " + MODEL_URL)
        input("Press Enter to exit...")
        return

    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)   # correct cursor coords on scaled displays
        except Exception:
            pass

    _, _, cam_index = load_config()
    cap = open_camera(cam_index)
    if not cap.isOpened():
        print(f"Could not open webcam {cam_index}. Close other apps using the camera,")
        print('or set "camera_index": 1 in config.json and try again.')
        input("Press Enter to exit...")
        return

    tracker = FaceTracker(MODEL_PATH)
    app = App(tracker, SystemMouse())
    hotkeys = start_global_hotkeys(app)

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW, 640, 480)
    try:
        cv2.setWindowProperty(WINDOW, cv2.WND_PROP_TOPMOST, 1)
    except Exception:
        pass

    start = time.monotonic()
    try:
        while not app.quit:
            ok, frame = cap.read()
            if not ok:
                print("Lost the camera feed.")
                break
            frame = cv2.flip(frame, 1)          # mirror view feels natural
            t = time.monotonic() - start
            app.step(frame, t)
            app.draw(frame, t)
            cv2.imshow(WINDOW, frame)
            key = cv2.waitKey(1) & 0xFF
            if key != 255:
                app.handle_key(key)
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break                           # window closed with the X button
    finally:
        app.close()
        tracker.close()
        cap.release()
        cv2.destroyAllWindows()
        if hotkeys:
            hotkeys.stop()


if __name__ == "__main__":
    main()
