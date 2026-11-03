# AGENTS.md — Context for AI assistants (and new teammates)

Read this first. It is the single source of truth for picking up this project
without the original conversation. Keep it up to date when you change things
(see "Updating this file" at the bottom).

---

## 1. What this is

**HeadNav** — a webcam-based, hands-free mouse for people with limited
hand/arm mobility. Head movement moves the cursor; double-nod or mouth
gestures click. Built by **first-year Mechatronics Engineering students**
(University of Waterloo area) as a **resume/portfolio project**.

- Owner: Keshav (GitHub repo owner) and teammates
- Repo: https://github.com/KeshavV14/Facetracker (branch `main`)
- Platform the team uses: **Windows + VS Code**, Python
- Budget: ~$0 (laptop webcam). A wearable ESP32+IMU version is a planned extension.
- Skill level: beginners. Explain things plainly; prefer one-click scripts
  (`setup.bat`, `run.bat`, `test.bat`) over terminal instructions.

See `PROJECT.md` for goals, background, design decisions and roadmap.
See `README.md` for user-facing docs (setup, controls, math, testing).

---

## 2. Current status (as of 2026-11-02)

| Area | Status |
|---|---|
| Core algorithms (`headnav_core.py`) | ✅ Done, 25 unit tests passing |
| App (`headnav.py`) — camera, MediaPipe, mouse, UI, logging | ✅ Written, smoke-tested with fake camera/mouse |
| Target test / Fitts' law tool (`target_test.py`) | ✅ Written, logic tested with a stub window |
| Windows scripts (`setup.bat`, `run.bat`, `test.bat`) | ✅ Written (CRLF), **not yet run on a real Windows PC** |
| **Real webcam test** | ❌ **Not done yet** — the dev sandbox had no camera and couldn't download the model |
| Threshold tuning with real users | ❌ Not done |
| Collected performance numbers for resume | ❌ Not done (resume bullets in README have `__` blanks — never invent numbers) |
| Wearable IMU version | ❌ Future |

**Most likely first task for you:** help the team run `setup.bat` / `run.bat`
on their Windows laptop and fix whatever breaks on real hardware.

---

## 3. Architecture

```
headnav.py          App: webcam (OpenCV) → FaceTracker (MediaPipe Tasks API)
                    → HeadNavController → SystemMouse (PyAutoGUI)
                    + on-screen UI (OpenCV window), global hotkeys (pynput),
                    per-frame CSV logs in logs/, config.json persistence
headnav_core.py     Pure-Python logic, no I/O (fully unit-testable):
                    rotation_to_euler, OneEuroFilter/EMAFilter/NoFilter,
                    Settings, Calibration, CalibrationWizard, shape() (dead zone
                    + power curve), DoubleNodDetector, MouthClicker,
                    DwellClicker, TiltToggle, HeadNavController
test_core.py        unittest suite on synthetic head-motion signals
target_test.py      tkinter fullscreen click test → logs/target_test_*.csv
                    (movement time, miss rate, Fitts throughput ID/MT)
setup.bat           finds Python 3.10–3.13 (prefers 3.12), makes .venv,
                    pip installs, downloads face_landmarker.task, runs tests
run.bat / test.bat  launch app / target test from .venv
```

Data flow per frame (`App.step`):
1. Mirror frame, convert BGR→RGB, `FaceTracker.process` → `(yaw, pitch, roll, jawOpen, landmarks)` or `None`.
2. If calibration wizard active → feed it; on finish validate (`Calibration.problems()`), save `config.json`, start tracking.
3. Else `HeadNavController.update(...)` → filtered angles, normalized `nx, ny`, target cursor, clicks, pause toggle.
4. App moves/clicks the real mouse, logs a CSV row.
5. `App.draw` renders status bar, radar box, gesture progress bars, help.

`App` takes `tracker` and `mouse` objects so it can be tested with fakes
(see "Testing" below). Keep that seam.

---

## 4. Key design decisions (and why) — don't undo without reason

1. **MediaPipe Tasks API (`FaceLandmarker`), not `mp.solutions.face_mesh`.**
   Verified: mediapipe 0.10.32 **no longer has `mp.solutions`**. Most online
   tutorials use the old API and will break. Needs the model file
   `face_landmarker.task` (downloaded by setup.bat; git-ignored).
2. **Head pose from `facial_transformation_matrixes`**, converted to Euler angles.
   Sign conventions are *not* trusted: the **calibration wizard measures**
   left/right/up/down offsets, so signs and asymmetric ranges are handled
   automatically (`Calibration.normalized`).
3. **Joystick (velocity) mode by default**, absolute mode optional (`m`).
   Velocity mode works for users with very small range of motion.
4. **One Euro filter** default (adaptive cutoff) vs EMA/none switchable (`f`)
   — the filter comparison is a core part of the evaluation story.
5. **Double nod, not single nod**, for click. Detector (DoubleNodDetector):
   - high-pass pitch vs slow baseline (tau 0.8 s); baseline **frozen during a candidate nod**
   - nod = excursion > threshold that comes back (overshoot counts) within 0.6 s
   - second nod must be **same direction** (rejects the bounce after one nod)
   - "steady" check (0.2 s window) re-arms immediately after the head settles in a new spot
   - nod detection uses **raw** pitch (filter lag would shrink nods)
   - click is applied at the cursor position **from before the gesture began** (nodding moves the cursor)
   Bugs already found and fixed here: baseline drifting with the nod; detector
   stuck for ~1.5 s after returning to center; float-precision bug that kept
   the steady window from ever filling at 30 fps. Tests cover 15/24/30/60 fps.
6. **Mouth click** uses `jawOpen` blendshape with hysteresis (0.55 open / 0.30
   close) so talking doesn't click; hold ≥1.2 s = right click.
7. **Dwell click off by default** (in joystick mode the cursor rests a lot);
   it requires moving away before re-arming.
8. **Tilt-to-pause** (roll > 18° for 1 s) = hands-free on/off. F8 = emergency pause.
9. `pyautogui.FAILSAFE = False` because the cursor is clamped to the screen
   and corner-reaching is normal for head control; F8/F10 are the safety exits.
10. Windows DPI awareness set in `main()` so coordinates are correct on scaled displays.

---

## 5. Conventions

- Python 3.10–3.13 (MediaPipe doesn't support the newest Python immediately).
- Keep `headnav_core.py` free of OpenCV/MediaPipe/pyautogui imports.
- New tunable values go in `Settings` (dataclass → `config.json`), not hard-coded.
- `.bat` files must have **CRLF** line endings.
- Never commit `.venv/`, `logs/`, `config.json`, `face_landmarker.task` (see `.gitignore`).
- Never fabricate performance numbers; resume bullets must use measured data.
- Plain-language explanations for the team; they're first-year students.

---

## 6. Testing

```bash
python -m unittest test_core -v      # 25 tests, pure Python, no camera needed
```
No-camera end-to-end check: construct `headnav.App(fake_tracker, fake_mouse, headless=True)`
where `fake_tracker.process(rgb, t)` returns `(yaw, pitch, roll, jaw, landmarks)`
and `fake_mouse` has `size/position/move/click`; call `app.step(frame, t)` and
`app.draw(frame, t)` in a loop. (Note: it writes `config.json` and `logs/` next
to `headnav.py`, so run it in a scratch copy.)

`target_test.py` needs tkinter (bundled with Windows Python; may be missing on Linux).

Simulation results so far (synthetic data, NOT real users): 0 false double-nod
clicks in 10 simulated minutes of looking around; 192/200 varied double nods detected.

---

## 7. Known risks / unverified on real hardware

- Real MediaPipe noise levels and angle ranges — thresholds may need tuning
  (`nod_threshold_frac`, `mouth_open_threshold`, `dead_zone`, `max_speed`).
- `cv2.WND_PROP_TOPMOST` support depends on OpenCV build.
- pynput global hotkeys may need permissions on macOS; fine on Windows.
- Camera index: if wrong camera opens, set `"camera_index": 1` in `config.json`.
- setup.bat's model download uses `curl.exe` then PowerShell fallback.

---

## 8. Next tasks (priority order)

1. Run on the team's Windows laptop; fix setup/runtime errors.
2. Tune default thresholds from real `logs/session_*.csv` data.
3. Collect evaluation data with `test.bat` (mouse vs HeadNav; One Euro vs EMA vs none; several users) and fill the README results table + resume bullets.
4. Add an analysis script (e.g. `analyze_logs.py`) to plot jitter/lag per filter from session logs.
5. Nice-to-haves: settings GUI, on-screen keyboard, tremor-aware filtering, learned nod classifier.
6. Hardware extension: ESP32 + MPU6050 headband as a Bluetooth HID mouse; compare with the webcam version using the same target test.

---

## 9. Updating this file

When you finish a chunk of work: update **Current status**, add any new
**design decisions** (with the reason), move done items out of **Next tasks**,
and add a dated line to the log below.

### Log
- 2026-11-02 — Project created: core logic, app, target test, Windows scripts, README, 25 tests. Not yet run on a real webcam.
- 2026-11-02 — Pushed to GitHub (KeshavV14/Facetracker), added AGENTS.md / PROJECT.md / CLAUDE.md.
