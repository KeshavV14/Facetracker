# HeadNav — Adaptive Hands-Free Cursor Control

Control your computer's mouse with head movements using only a webcam. Built for people with limited hand or arm mobility: it adapts to each user's own range of motion and offers several hands-free ways to click.

**Cost: $0** (any laptop webcam). Python + OpenCV + MediaPipe.

---

## Quick start (Windows)

1. **Install Python 3.12** from [python.org](https://www.python.org/downloads/). On the first installer screen, tick **"Add python.exe to PATH"**.
2. **Unzip this folder** somewhere easy, like your Desktop.
3. **Double-click `setup.bat`.** It installs everything and downloads the face model (a few minutes, one time only).
4. **Double-click `run.bat`.** Follow the on-screen calibration (look center, left, right, up, down). Then you're controlling the cursor with your head.

**Emergency stop:** press **F8** any time to pause. **F10** quits.

<details>
<summary>Mac / Linux</summary>

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
curl -L -o face_landmarker.task https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
python headnav.py
```
On macOS, allow Terminal/VS Code under System Settings → Privacy & Security → Camera, Accessibility and Input Monitoring.
</details>

---

## How to use it

| You do | Computer does |
|---|---|
| Turn / tilt your head | Cursor moves that way. Further = faster. |
| Rest your head in the middle | Cursor stops (the "dead zone", grey square in the window) |
| **Double nod** (two quick nods) | Left click |
| **Open mouth briefly** | Left click |
| **Hold mouth open ~1 s** | Right click |
| Rest still on a target (dwell, off by default — press `d`) | Left click |
| **Hold a sideways head tilt ~1 s** | Pause / resume (hands-free on/off switch) |

**Global hotkeys** (work from any window): `F8` pause/resume · `F9` recalibrate · `F10` quit

**Keys in the HeadNav window:** `space` pause · `c` calibrate · `m` joystick/absolute mode · `f` cycle filter · `n`/`o`/`d` toggle nod/mouth/dwell click · `+`/`-` speed · `[`/`]` dead zone · `s` save settings · `h` help · `q` quit

All settings live in `config.json` (created after your first calibration), so they can be tuned per user.

---

## How it works

```
Webcam ─► MediaPipe Face Landmarker ─► head pose (yaw, pitch, roll) + mouth openness
       ─► One Euro filter (adaptive smoothing)
       ─► personal calibration  (angles → -1…+1 of YOUR comfortable range)
       ─► dead zone + power curve ─► cursor velocity ─► OS mouse (PyAutoGUI)
       └► gesture detectors (double nod, mouth, dwell, tilt) ─► clicks / pause
```

**1. Head pose.** MediaPipe returns a 4×4 facial transformation matrix each frame. The rotation part is converted to Euler angles (yaw = left/right, pitch = up/down, roll = tilt).

**2. Adaptive smoothing — One Euro filter.** A plain moving average forces a tradeoff: smooth enough to stop jitter means laggy during fast moves. The [One Euro filter](https://gery.casiez.net/1euro/) changes its cutoff frequency with speed:

  f_c = f_min + β·|ẋ|,  α = 1 / (1 + 1/(2π f_c Δt)),  x̂ₖ = α xₖ + (1−α) x̂ₖ₋₁

Still head → low cutoff → jitter removed. Fast head → high cutoff → little lag. You can switch to EMA or no filter (`f`) to compare.

**3. Personal calibration.** The wizard records your neutral pose and how far you can *comfortably* turn in each direction. Every user's range maps to the same −1…+1 scale, so someone who can only turn 5° gets the same control as someone who turns 30°. Asymmetric ranges (more left than right) are handled, and because directions are *measured*, the sign conventions of the pose model don't matter.

**4. Joystick mapping.** In joystick mode, head offset sets cursor *velocity*:

  v = v_max · sign(n) · ((|n| − d) / (1 − d))^γ  for |n| > d, else 0

The dead zone d gives a comfortable resting position; γ > 1 gives slow, precise movement for small head offsets and fast travel for large ones. (Absolute mode maps head angle directly to screen position instead.)

**5. Click detection.**
- *Double nod:* pitch is high-pass filtered against a slowly adapting baseline. A nod is a fast excursion that returns within 0.6 s; slow, deliberate movements time out and are ignored. Two same-direction nods within 1 s = click. Because nodding moves the cursor, HeadNav clicks where the cursor was *before* the gesture started.
- *Mouth:* MediaPipe's `jawOpen` blendshape with hysteresis (open > 0.55, close < 0.30) so talking doesn't trigger it.
- *Dwell:* cursor stays within 25 px for 1.2 s. It must move away before it can click again.
- *Tilt-to-pause:* hold roll beyond 18° for 1 s. Lets a user turn HeadNav on/off without hands.

---

## Measuring performance (do this — it's what makes the project impressive)

1. Double-click `test.bat`, enter label `mouse`, and do the test with a normal mouse (your baseline).
2. Start `run.bat`, then run `test.bat` with label `headnav_oneeuro`.
3. Press `f` in HeadNav to switch to EMA, test again as `headnav_ema`. Then `none` → `headnav_nofilter`.
4. Optional: compare joystick vs absolute mode, or different dead zones.

Each test reports **mean time per target**, **miss rate**, and **throughput (bits/s)** — the Fitts' law metric from ISO 9241-9 used to compare pointing devices. Results go into `logs/`. Every HeadNav session also logs raw and filtered angles per frame (`logs/session_*.csv`), so you can plot jitter and lag for each filter in Python/Excel/MATLAB.

Test with several people (classmates) for a stronger report, and record results in a table like:

| Condition | Mean time (s) | Miss rate | Throughput (bits/s) |
|---|---|---|---|
| Mouse | | | |
| HeadNav – One Euro | | | |
| HeadNav – EMA | | | |
| HeadNav – no filter | | | |

---

## Project structure

| File | What it is |
|---|---|
| `headnav.py` | Main app: camera, MediaPipe, mouse control, on-screen UI, logging |
| `headnav_core.py` | All the algorithms (filters, calibration, mapping, gestures) — pure Python |
| `test_core.py` | 25 unit tests using synthetic head-motion data (`python -m unittest test_core -v`) |
| `target_test.py` | Fitts' law click-speed/accuracy test |
| `setup.bat` / `run.bat` / `test.bat` | One-click Windows setup, launch and testing |
| `PROJECT.md` | Project brief: goals, background, design choices, evaluation plan |
| `AGENTS.md` | Handoff context for AI assistants / new teammates (status, architecture, next tasks) |

---

## Ideas for next steps

- **Wearable IMU version:** ESP32 + MPU6050 headband acting as a Bluetooth mouse — adds the hardware side, and you can compare it against the webcam version with the same target test.
- **Tremor-aware filtering:** detect involuntary movement frequency per user and notch it out.
- **Learned nod detector:** collect labelled gesture data from the session logs and train a small classifier.
- **Settings GUI** and an on-screen keyboard for full hands-free typing.
- **User testing** with an occupational therapist or accessibility centre.

---

## Resume bullets (fill in your measured numbers — don't guess)

- Developed **HeadNav**, a webcam-based hands-free mouse in Python (OpenCV, MediaPipe) enabling users with limited upper-limb mobility to control a computer through head movement, double-nod, and mouth gestures
- Implemented an adaptive **One Euro filter** and per-user range-of-motion calibration, reducing cursor jitter by **__%** versus unfiltered tracking while keeping latency under **__ ms**
- Designed gesture detection with hysteresis and timing thresholds, achieving **__%** click recognition with **__** false clicks across **__** test sessions
- Evaluated performance using an **ISO 9241-9 / Fitts' law** target test across **__** users, reaching **__ bits/s** throughput (**__%** of a standard mouse)
- Wrote 25 unit tests validating filters and gesture detectors on synthetic motion data

---

## Troubleshooting

- **"Could not find Python"** — install Python 3.12 and tick *Add to PATH*. MediaPipe doesn't support the very newest Python yet.
- **Camera won't open** — close Zoom/Teams/Camera app. If you have two cameras, set `"camera_index": 1` in `config.json`.
- **Cursor moves the wrong way / too fast / drifts** — press `c` (or F9) to recalibrate, sitting how you normally sit. Lower speed with `-`, enlarge the dead zone with `]`.
- **Accidental clicks** — turn off the gesture causing it (`n` or `o`), or raise thresholds in `config.json`.
- **Cursor in wrong spot on a high-res laptop** — HeadNav sets DPI awareness automatically; if it's still off, set Windows display scaling to 100% to test.
