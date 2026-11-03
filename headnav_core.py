"""
HeadNav core logic: filters, calibration, cursor mapping and gesture detection.

Everything in this file is pure Python (no camera, no mouse, no GUI), so it
can be unit-tested with synthetic data. See test_core.py.
"""

import math
import statistics
from collections import deque
from dataclasses import dataclass, asdict, fields


# ---------------------------------------------------------------------------
# Head pose
# ---------------------------------------------------------------------------

def rotation_to_euler(R):
    """Convert a 3x3 rotation matrix to (yaw, pitch, roll) in degrees.

    The sign of each angle depends on the camera/model conventions. HeadNav
    doesn't care: the calibration wizard measures which direction is which.
    """
    pitch = math.degrees(math.atan2(R[2][1], R[2][2]))
    yaw = math.degrees(math.asin(max(-1.0, min(1.0, -R[2][0]))))
    roll = math.degrees(math.atan2(R[1][0], R[0][0]))
    return yaw, pitch, roll


# ---------------------------------------------------------------------------
# Filters  (all share the interface: f(x, t) -> filtered x)
# ---------------------------------------------------------------------------

class NoFilter:
    def __call__(self, x, t):
        return x

    def reset(self):
        pass


class EMAFilter:
    """Exponential moving average: xf[k] = a*x[k] + (1-a)*xf[k-1]"""

    def __init__(self, alpha=0.3):
        self.alpha = alpha
        self.y = None

    def __call__(self, x, t):
        self.y = x if self.y is None else self.alpha * x + (1 - self.alpha) * self.y
        return self.y

    def reset(self):
        self.y = None


def _smoothing_factor(cutoff_hz, dt):
    tau = 1.0 / (2 * math.pi * cutoff_hz)
    return 1.0 / (1.0 + tau / dt)


class OneEuroFilter:
    """Adaptive low-pass filter (Casiez et al., CHI 2012).

    When the head is still, the cutoff frequency is low -> jitter is removed.
    When the head moves fast, the cutoff rises -> little lag.
      min_cutoff: lower = less jitter when still
      beta:       higher = less lag when moving fast
    """

    def __init__(self, min_cutoff=0.8, beta=0.02, d_cutoff=1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.reset()

    def reset(self):
        self.x_prev = None
        self.dx_prev = 0.0
        self.t_prev = None

    def __call__(self, x, t):
        if self.t_prev is None:
            self.x_prev, self.t_prev = x, t
            return x
        dt = max(t - self.t_prev, 1e-3)
        dx = (x - self.x_prev) / dt
        a_d = _smoothing_factor(self.d_cutoff, dt)
        dx_hat = a_d * dx + (1 - a_d) * self.dx_prev
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = _smoothing_factor(cutoff, dt)
        x_hat = a * x + (1 - a) * self.x_prev
        self.x_prev, self.dx_prev, self.t_prev = x_hat, dx_hat, t
        return x_hat


FILTER_KINDS = ["one_euro", "ema", "none"]


def make_filter(settings):
    if settings.filter == "one_euro":
        return OneEuroFilter(settings.one_euro_min_cutoff, settings.one_euro_beta)
    if settings.filter == "ema":
        return EMAFilter(settings.ema_alpha)
    return NoFilter()


# ---------------------------------------------------------------------------
# Settings and calibration
# ---------------------------------------------------------------------------

@dataclass
class Settings:
    mode: str = "joystick"          # "joystick" (head = velocity) or "absolute" (head = position)
    max_speed: float = 1100.0       # cursor px/s at full head deflection (joystick mode)
    dead_zone: float = 0.15         # fraction of your range that does nothing (rest zone)
    gamma: float = 1.7              # >1 = finer control near the center
    range_fraction: float = 0.8     # full speed reached at this fraction of your comfortable range

    filter: str = "one_euro"        # "one_euro", "ema" or "none"
    one_euro_min_cutoff: float = 0.8
    one_euro_beta: float = 0.02
    ema_alpha: float = 0.3

    nod_click: bool = True
    nod_threshold_frac: float = 0.35   # nod must be this fraction of your up/down range
    nod_min_threshold_deg: float = 3.0
    nod_max_duration: float = 0.6      # seconds; slower movements are not nods
    double_nod_window: float = 1.0     # both nods must happen within this many seconds

    mouth_click: bool = True
    mouth_open_threshold: float = 0.55
    mouth_close_threshold: float = 0.30
    mouth_min_hold: float = 0.20       # open briefly -> left click
    mouth_right_click_hold: float = 1.2  # keep open this long -> right click

    dwell_click: bool = False
    dwell_time: float = 1.2
    dwell_radius: float = 25.0

    tilt_pause: bool = True
    tilt_pause_angle: float = 18.0
    tilt_pause_hold: float = 1.0

    @classmethod
    def from_dict(cls, d):
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in names})

    def to_dict(self):
        return asdict(self)


@dataclass
class Calibration:
    """Neutral pose plus how far (and in which sign) each direction goes.

    *_off values are angle offsets from center measured during calibration,
    e.g. right_off = yaw when looking right - center yaw. Because they are
    measured, the sign convention of the head-pose model doesn't matter, and
    asymmetric ranges (turning left further than right) are supported.
    """
    center_yaw: float = 0.0
    center_pitch: float = 0.0
    center_roll: float = 0.0
    left_off: float = -15.0
    right_off: float = 15.0
    up_off: float = -12.0
    down_off: float = 12.0
    done: bool = False

    MIN_RANGE_DEG = 3.0

    @classmethod
    def from_dict(cls, d):
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in names})

    def to_dict(self):
        return asdict(self)

    def problems(self):
        out = []
        for a, b, axis in ((self.left_off, self.right_off, "left/right"),
                           (self.up_off, self.down_off, "up/down")):
            if a * b >= 0:
                out.append(f"{axis}: both directions measured the same way - try again, moving further")
            if min(abs(a), abs(b)) < self.MIN_RANGE_DEG:
                out.append(f"{axis}: range under {self.MIN_RANGE_DEG:.0f} deg - move a little further")
        return out

    def normalized(self, yaw, pitch, range_fraction=1.0):
        """Map head angles to (nx, ny): +1 = right/down edge of comfortable range."""
        nx = _norm(yaw - self.center_yaw, self.right_off, self.left_off) / range_fraction
        ny = _norm(pitch - self.center_pitch, self.down_off, self.up_off) / range_fraction
        return nx, ny

    def roll_offset(self, roll):
        d = roll - self.center_roll
        return (d + 180) % 360 - 180

    def pitch_range(self):
        return min(abs(self.up_off), abs(self.down_off))


def _norm(d, pos_extent, neg_extent):
    if pos_extent != 0 and d * pos_extent > 0:
        return d / pos_extent
    if neg_extent != 0 and d * neg_extent > 0:
        return -d / neg_extent
    return 0.0


class CalibrationWizard:
    """Guides the user through 5 poses and builds a Calibration."""

    STEPS = [
        ("center", "Look at the CENTER of your screen"),
        ("left", "Turn your head LEFT (comfortably)"),
        ("right", "Turn your head RIGHT (comfortably)"),
        ("up", "Tilt your head UP (comfortably)"),
        ("down", "Tilt your head DOWN (comfortably)"),
    ]

    def __init__(self, settle_s=1.5, record_s=1.2):
        self.settle_s = settle_s
        self.record_s = record_s
        self.index = 0
        self.step_start = None
        self.samples = []
        self.results = {}
        self.done = False

    @property
    def instruction(self):
        return "Done!" if self.done else self.STEPS[self.index][1]

    @property
    def step_label(self):
        return f"Step {min(self.index + 1, len(self.STEPS))}/{len(self.STEPS)}"

    def progress(self, t):
        if self.done or self.step_start is None:
            return 0.0
        return min(1.0, (t - self.step_start) / (self.settle_s + self.record_s))

    def face_lost(self):
        # restart the current step so we never record a half-finished pose
        self.step_start = None
        self.samples = []

    def update(self, t, yaw, pitch, roll):
        if self.done:
            return True
        if self.step_start is None:
            self.step_start = t
        elapsed = t - self.step_start
        if elapsed >= self.settle_s:
            self.samples.append((yaw, pitch, roll))
        if elapsed >= self.settle_s + self.record_s and self.samples:
            key = self.STEPS[self.index][0]
            self.results[key] = tuple(statistics.median(s[i] for s in self.samples) for i in range(3))
            self.index += 1
            self.step_start = None
            self.samples = []
            if self.index >= len(self.STEPS):
                self.done = True
        return self.done

    def calibration(self):
        cy, cp, cr = self.results["center"]
        return Calibration(
            center_yaw=cy, center_pitch=cp, center_roll=cr,
            left_off=self.results["left"][0] - cy,
            right_off=self.results["right"][0] - cy,
            up_off=self.results["up"][1] - cp,
            down_off=self.results["down"][1] - cp,
            done=True,
        )


# ---------------------------------------------------------------------------
# Cursor mapping
# ---------------------------------------------------------------------------

def shape(n, dead_zone, gamma):
    """Dead zone + power curve. Small movements -> slow, precise; big -> fast."""
    a = abs(n)
    if a <= dead_zone:
        return 0.0
    m = min((a - dead_zone) / (1.0 - dead_zone), 1.5)
    return math.copysign(m ** gamma, n)


# ---------------------------------------------------------------------------
# Gesture detectors
# ---------------------------------------------------------------------------

class DoubleNodDetector:
    """Detects two quick nods in a row.

    A nod is a fast pitch excursion away from a slowly-adapting baseline that
    returns within nod_max_duration. Slow, deliberate head movements take too
    long and are ignored. The second nod must go the same direction as the
    first, so the natural "bounce back" after one nod doesn't count.
    """

    def __init__(self, baseline_tau=0.8):
        self.baseline_tau = baseline_tau
        self.reset()

    STABLE_WINDOW = 0.2   # seconds of steady head needed to re-arm after a big movement

    def reset(self):
        self.baseline = None
        self.t_prev = None
        self.state = "idle"     # idle | out | wait_return
        self.out_start = None
        self.out_sign = 0
        self.last_nod = None    # (t_start, t_end, sign)
        self.recent = deque()   # (t, pitch) over the last STABLE_WINDOW seconds

    def _is_steady(self, t, tolerance):
        if not self.recent or t - self.recent[0][0] < self.STABLE_WINDOW - 1e-6:
            return False
        values = [p for _, p in self.recent]
        return max(values) - min(values) < tolerance

    def update(self, t, pitch, threshold, max_duration, window):
        """Returns the start time of the gesture when a double nod completes, else None."""
        if self.baseline is None:
            self.baseline, self.t_prev = pitch, t
            return None
        dt = max(t - self.t_prev, 1e-3)
        self.t_prev = t
        self.recent.append((t, pitch))
        # keep exactly one sample at/before (t - window) so the window is always full
        while len(self.recent) > 1 and t - self.recent[1][0] >= self.STABLE_WINDOW:
            self.recent.popleft()
        hp = pitch - self.baseline
        if self.state != "out":
            # baseline follows slow posture changes, but is frozen during a candidate nod
            self.baseline += (dt / (self.baseline_tau + dt)) * hp

        result = None
        if self.state == "idle":
            if abs(hp) > threshold:
                self.state, self.out_start, self.out_sign = "out", t, (1 if hp > 0 else -1)
        elif self.state == "out":
            if self._is_steady(t, 0.25 * threshold):
                # moved and stopped in a new position (not a nod): adopt it
                self.baseline = pitch
                self.state = "idle"
            elif t - self.out_start > max_duration:
                self.state = "wait_return"          # too slow: deliberate movement
            elif self.out_sign * hp < 0.4 * threshold:   # came back (overshoot counts)
                nod = (self.out_start, t, self.out_sign)
                prev = self.last_nod
                if prev and nod[2] == prev[2] and nod[0] - prev[1] < window:
                    result = prev[0]
                    self.last_nod = None
                else:
                    self.last_nod = nod
                self.state = "idle"
        elif self.state == "wait_return":
            if abs(hp) < 0.4 * threshold:
                self.state = "idle"
            elif self._is_steady(t, 0.5 * threshold):
                # head has settled in a new position: adopt it as the baseline
                self.baseline = pitch
                self.state = "idle"
        if self.last_nod and t - self.last_nod[1] > window:
            self.last_nod = None
        return result


class MouthClicker:
    """Open mouth briefly -> left click (on close). Hold open -> right click."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.is_open = False
        self.t_open = None
        self.right_fired = False

    def hold_fraction(self, t, s):
        if not self.is_open or self.right_fired:
            return 0.0
        return min(1.0, (t - self.t_open) / s.mouth_right_click_hold)

    def update(self, t, jaw_open, s):
        if not self.is_open:
            if jaw_open > s.mouth_open_threshold:
                self.is_open, self.t_open, self.right_fired = True, t, False
            return None
        if not self.right_fired and t - self.t_open >= s.mouth_right_click_hold:
            self.right_fired = True
            return "right"
        if jaw_open < s.mouth_close_threshold:
            self.is_open = False
            if not self.right_fired and t - self.t_open >= s.mouth_min_hold:
                return "left"
        return None


class DwellClicker:
    """Click when the cursor rests inside a small circle for dwell_time.

    After clicking, the cursor must move away before it can click again, so it
    never machine-guns clicks while you rest.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.anchor = None
        self.t_anchor = None
        self.rearm_point = None
        self.armed = False

    def progress(self, t, s):
        if not self.armed or self.t_anchor is None:
            return 0.0
        return min(1.0, (t - self.t_anchor) / s.dwell_time)

    def update(self, t, x, y, s):
        if self.anchor is None:
            self.anchor, self.t_anchor, self.rearm_point = (x, y), t, (x, y)
            return False
        if math.dist((x, y), self.anchor) > s.dwell_radius:
            self.anchor, self.t_anchor = (x, y), t
        if not self.armed and math.dist((x, y), self.rearm_point) > 2 * s.dwell_radius:
            self.armed = True
        if self.armed and t - self.t_anchor >= s.dwell_time:
            self.armed = False
            self.rearm_point = (x, y)
            return True
        return False


class TiltToggle:
    """Hold a sideways head tilt to toggle pause - a hands-free on/off switch."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.t_start = None
        self.armed = True

    def progress(self, t, s):
        if self.t_start is None or not self.armed:
            return 0.0
        return min(1.0, (t - self.t_start) / s.tilt_pause_hold)

    def update(self, t, roll_off, s):
        if abs(roll_off) > s.tilt_pause_angle:
            if self.t_start is None:
                self.t_start = t
            elif self.armed and t - self.t_start >= s.tilt_pause_hold:
                self.armed = False
                return True
        else:
            self.t_start = None
            if abs(roll_off) < 0.5 * s.tilt_pause_angle:
                self.armed = True
        return False


# ---------------------------------------------------------------------------
# Controller: ties everything together, one call per camera frame
# ---------------------------------------------------------------------------

@dataclass
class FrameOutput:
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    nx: float = 0.0
    ny: float = 0.0
    cursor: tuple = None            # where the cursor should be (None = don't move)
    clicks: list = None             # [("left"|"right", (x, y)), ...]
    toggle_pause: bool = False


class HeadNavController:
    def __init__(self, settings, calibration, screen_w, screen_h, start_pos=None):
        self.s = settings
        self.cal = calibration
        self.w, self.h = screen_w, screen_h
        x, y = start_pos or (screen_w / 2, screen_h / 2)
        self.x, self.y = float(x), float(y)
        self.t_prev = None
        self.history = deque(maxlen=120)  # (t, x, y) for undoing nod movement
        self.nod = DoubleNodDetector()
        self.mouth = MouthClicker()
        self.dwell = DwellClicker()
        self.tilt = TiltToggle()
        self.rebuild_filters()

    def rebuild_filters(self):
        self.filters = [make_filter(self.s) for _ in range(3)]

    def sync_position(self, x, y):
        """Adopt the real cursor position (e.g. someone used the physical mouse)."""
        self.x, self.y = float(x), float(y)

    def face_lost(self):
        self.t_prev = None
        for f in self.filters:
            f.reset()
        self.nod.reset()
        self.mouth.reset()
        self.tilt.reset()

    def _clamp(self):
        self.x = min(max(self.x, 1), self.w - 2)
        self.y = min(max(self.y, 1), self.h - 2)

    def _position_at(self, t):
        for ht, hx, hy in reversed(self.history):
            if ht <= t:
                return hx, hy
        return (self.history[0][1], self.history[0][2]) if self.history else (self.x, self.y)

    def update(self, t, yaw, pitch, roll, jaw_open=0.0, paused=False):
        s, cal = self.s, self.cal
        out = FrameOutput(clicks=[])
        dt = 0.0 if self.t_prev is None else min(t - self.t_prev, 0.1)
        self.t_prev = t

        fy, fp, fr = (f(v, t) for f, v in zip(self.filters, (yaw, pitch, roll)))
        out.yaw, out.pitch, out.roll = fy, fp, fr
        out.nx, out.ny = cal.normalized(fy, fp, s.range_fraction)

        if s.tilt_pause and self.tilt.update(t, cal.roll_offset(fr), s):
            out.toggle_pause = True

        nod_start = None
        if s.nod_click:
            thr = max(s.nod_min_threshold_deg, s.nod_threshold_frac * cal.pitch_range())
            nod_start = self.nod.update(t, pitch, thr, s.nod_max_duration, s.double_nod_window)
        mouth = self.mouth.update(t, jaw_open, s) if s.mouth_click else None

        if paused:
            self.history.clear()
            self.dwell.reset()
            return out

        # --- move ---
        if s.mode == "absolute":
            self.x = self.w / 2 + max(-1, min(1, out.nx)) * self.w / 2
            self.y = self.h / 2 + max(-1, min(1, out.ny)) * self.h / 2
        else:
            self.x += s.max_speed * shape(out.nx, s.dead_zone, s.gamma) * dt
            self.y += s.max_speed * shape(out.ny, s.dead_zone, s.gamma) * dt
        self._clamp()
        self.history.append((t, self.x, self.y))
        out.cursor = (self.x, self.y)

        # --- click ---
        if nod_start is not None:
            # nodding moved the cursor; click where it was before the gesture began
            self.x, self.y = self._position_at(nod_start - 0.1)
            out.cursor = (self.x, self.y)
            out.clicks.append(("left", out.cursor))
            self.history.clear()
            self.dwell.reset()
        if mouth:
            out.clicks.append((mouth, out.cursor))
        if s.dwell_click and self.dwell.update(t, self.x, self.y, s):
            out.clicks.append(("left", out.cursor))
        return out
