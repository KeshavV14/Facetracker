# HeadNav — Project Brief

## Team & purpose
First-year Mechatronics Engineering students building a portfolio project to
strengthen co-op/internship resumes. The project should show mechanical/
electrical/software integration thinking, real engineering tradeoffs, and
**measured results**.

## How we got here
1. Brainstormed first-year mechatronics projects (self-balancing robot, robotic
   arm, pen plotter, assistive devices, etc.).
2. Considered a self-feeding robotic arm; pivoted to a **head-controlled mouse**
   because it is cheaper, has a clear accessibility application, and covers
   computer vision, signal processing and human-computer interaction.
3. Compared two approaches:
   - **A — Wearable IMU** (ESP32 + MPU6050 on a headband, Bluetooth HID mouse).
     More hardware/mechatronics; est. $45–110 CAD.
   - **B — Webcam** (Python, OpenCV, MediaPipe). ~$0, easiest for anyone to try.
4. **Chose B (webcam) first** to prove the interaction; A is the planned
   hardware extension, compared head-to-head using the same metrics.

## Problem statement
People with limited hand/arm mobility (e.g. spinal cord injury, ALS, arthritis,
amputation) who retain comfortable head movement need an affordable way to use
a computer. Commercial options exist (e.g. GlassOuse, Quha Zono) but cost money
and aren't easily customizable.

**Our angle / differentiator:** *adaptive* control — the software adapts to
each user's range of motion and filters unintended movement, rather than
"head moves cursor" alone.

Note: head-control may not suit everyone (e.g. some people with Parkinson's
have head tremor or rigidity). Primary target: upper-limb mobility limitations
with reliable head movement.

## Goals
- Hands-free move, left click, right click, pause/resume.
- Personal calibration for any comfortable range (5° to 30°+).
- Low jitter without noticeable lag (adaptive filtering).
- Few accidental clicks during normal use.
- Quantitative evaluation (Fitts' law throughput, time per target, miss rate,
  false clicks, latency/jitter) against a normal mouse and between filters.

## Engineering challenges → solutions
| Challenge | Solution in HeadNav |
|---|---|
| Cursor shake | One Euro adaptive filter (EMA / none for comparison) |
| Accidental clicks | Double-nod with timing thresholds + direction check; mouth hysteresis |
| Fatigue | Dead zone + power-curve velocity mapping, adjustable speed |
| Drift / posture change | Recalibration (F9), slowly adapting nod baseline |
| Small targets | Nonlinear gain = slow precise movement near center |
| Different ranges of motion | 5-pose calibration wizard, asymmetric ranges supported |
| Can't nod | Mouth click, dwell click alternatives |

## Phases
1. Head tracking (yaw/pitch/roll live) — ✅
2. Cursor movement (dead zone, sensitivity) — ✅
3. Clicking (double nod, mouth, dwell, cooldowns) — ✅
4. Accessibility (calibration wizard, adaptive smoothing, tilt pause, settings) — ✅ first version
5. Performance testing — 🔜 tool built (`target_test.py`), data not yet collected
6. Wearable IMU version + comparison — future

## Evaluation plan
- Baseline: normal mouse via `test.bat` (label `mouse`).
- HeadNav with One Euro / EMA / no filter; joystick vs absolute mode.
- Multiple users (classmates); record mean time, miss rate, throughput.
- From session logs: jitter (std of filtered angle while still), lag, false click count.
- Report results in README table; then fill resume bullets with real numbers.

## Resume framing
"HeadNav — Adaptive Hands-Free Cursor Control": computer vision + signal
processing + HCI evaluation, with an accessibility mission. See README for
bullet templates (numbers must be measured).
