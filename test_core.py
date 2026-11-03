"""Unit tests for HeadNav core logic. Run:  python -m unittest test_core -v"""

import math
import random
import statistics
import unittest

from headnav_core import (
    Calibration, CalibrationWizard, DoubleNodDetector, DwellClicker,
    HeadNavController, MouthClicker, OneEuroFilter, EMAFilter, Settings,
    TiltToggle, rotation_to_euler, shape,
)

FPS = 30
DT = 1 / FPS


def frames(seconds, t0=0.0, fps=FPS):
    n = int(round(seconds * fps))
    return [t0 + i / fps for i in range(n)]


def nod(t, start, depth=10.0, duration=0.35):
    """Smooth down-and-back pitch bump starting at `start`."""
    if start <= t <= start + duration:
        return depth * math.sin(math.pi * (t - start) / duration)
    return 0.0


class TestPose(unittest.TestCase):
    def test_identity_is_zero(self):
        I = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        self.assertEqual(rotation_to_euler(I), (0.0, 0.0, 0.0))

    def test_yaw_rotation(self):
        a = math.radians(20)
        Ry = [[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]]
        yaw, pitch, roll = rotation_to_euler(Ry)
        self.assertAlmostEqual(yaw, 20, places=6)
        self.assertAlmostEqual(pitch, 0, places=6)


class TestFilters(unittest.TestCase):
    def test_one_euro_reduces_jitter_when_still(self):
        rng = random.Random(1)
        f = OneEuroFilter()
        raw, out = [], []
        for t in frames(3):
            x = rng.gauss(0, 1.0)
            raw.append(x)
            out.append(f(x, t))
        self.assertLess(statistics.pstdev(out[30:]), 0.5 * statistics.pstdev(raw[30:]))

    def test_one_euro_less_lag_than_ema_on_fast_move(self):
        one, ema = OneEuroFilter(), EMAFilter(0.15)
        ts = frames(1)
        # 0 for a few frames, then a fast 20 degree step
        lag_one = lag_ema = None
        for i, t in enumerate(ts):
            x = 0.0 if i < 5 else 20.0
            a, b = one(x, t), ema(x, t)
            if lag_one is None and a > 18:
                lag_one = i
            if lag_ema is None and b > 18:
                lag_ema = i
        self.assertIsNotNone(lag_one)
        self.assertTrue(lag_ema is None or lag_one < lag_ema)


class TestCalibration(unittest.TestCase):
    def test_asymmetric_and_inverted_signs(self):
        # Model reports "right" as negative yaw, and user turns left further than right
        cal = Calibration(center_yaw=2, left_off=20, right_off=-10, up_off=8, down_off=-12, done=True)
        self.assertEqual(cal.problems(), [])
        nx, ny = cal.normalized(2 - 10, 0)   # fully right
        self.assertAlmostEqual(nx, 1.0)
        nx, _ = cal.normalized(2 + 20, 0)    # fully left
        self.assertAlmostEqual(nx, -1.0)
        _, ny = cal.normalized(0, -12)       # fully down
        self.assertAlmostEqual(ny, 1.0)

    def test_bad_calibration_flagged(self):
        cal = Calibration(left_off=5, right_off=6, up_off=-1, down_off=1)
        self.assertEqual(len(cal.problems()), 2)

    def test_wizard_end_to_end(self):
        wiz = CalibrationWizard(settle_s=0.5, record_s=0.5)
        poses = {"center": (1, 2, 0), "left": (-14, 2, 0), "right": (17, 2, 0),
                 "up": (1, -9, 0), "down": (1, 13, 0)}
        rng = random.Random(0)
        t = 0.0
        while not wiz.done and t < 20:
            key = wiz.STEPS[wiz.index][0]
            y, p, r = poses[key]
            wiz.update(t, y + rng.gauss(0, 0.3), p + rng.gauss(0, 0.3), r)
            t += DT
        self.assertTrue(wiz.done)
        cal = wiz.calibration()
        self.assertEqual(cal.problems(), [])
        self.assertAlmostEqual(cal.left_off, -15, delta=1)
        self.assertAlmostEqual(cal.right_off, 16, delta=1)
        self.assertAlmostEqual(cal.down_off, 11, delta=1)


class TestShape(unittest.TestCase):
    def test_dead_zone_and_curve(self):
        self.assertEqual(shape(0.1, 0.15, 1.7), 0.0)
        self.assertAlmostEqual(shape(1.0, 0.15, 1.7), 1.0)
        self.assertAlmostEqual(shape(-1.0, 0.15, 1.7), -1.0)
        self.assertLess(shape(0.5, 0.15, 1.7), 0.5)  # finer control near center


class TestDoubleNod(unittest.TestCase):
    def run_signal(self, fn, seconds=4, fps=FPS):
        d = DoubleNodDetector()
        hits = []
        for t in frames(seconds, fps=fps):
            r = d.update(t, fn(t), threshold=4.0, max_duration=0.6, window=1.0)
            if r is not None:
                hits.append((t, r))
        return hits

    def test_double_nod_detected(self):
        hits = self.run_signal(lambda t: nod(t, 1.0) + nod(t, 1.5))
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0][1], 1.0, delta=0.15)  # gesture start time

    def test_single_nod_ignored(self):
        self.assertEqual(self.run_signal(lambda t: nod(t, 1.0)), [])

    def test_single_nod_with_overshoot_ignored(self):
        # down then a bounce up: opposite signs must not count as two nods
        self.assertEqual(self.run_signal(lambda t: nod(t, 1.0) + nod(t, 1.35, depth=-8)), [])

    def test_slow_deliberate_movement_ignored(self):
        # looking down slowly to move the cursor, holding, coming back
        def slow(t):
            if t < 1:
                return 0
            if t < 2:
                return 15 * (t - 1)
            if t < 3:
                return 15
            return max(0, 15 - 15 * (t - 3))
        self.assertEqual(self.run_signal(slow, 5), [])

    def test_double_nod_right_after_moving(self):
        # look down to move the cursor, snap back to center, nod twice soon after
        for fps in (15, 24, 30, 60):
            for gap in (0.4, 0.5):
                def fn(t, gap=gap):
                    base = -12 if 0.5 <= t < 1.5 else 0
                    return base + nod(t, 1.5 + gap) + nod(t, 2.0 + gap)
                hits = self.run_signal(fn, fps=fps)
                self.assertEqual(len(hits), 1, f"fps={fps} gap={gap}")

    def test_double_nod_various_fps(self):
        for fps in (15, 24, 30, 60):
            hits = self.run_signal(lambda t: nod(t, 1.0) + nod(t, 1.5), fps=fps)
            self.assertEqual(len(hits), 1, f"fps={fps}")

    def test_nods_too_far_apart_ignored(self):
        self.assertEqual(self.run_signal(lambda t: nod(t, 0.5) + nod(t, 2.5)), [])

    def test_noise_does_not_click(self):
        rng = random.Random(3)
        self.assertEqual(self.run_signal(lambda t: rng.gauss(0, 0.8), 10), [])


class TestMouth(unittest.TestCase):
    def test_short_open_left_long_open_right(self):
        s = Settings()
        m = MouthClicker()
        events = []
        for t in frames(6):
            jaw = 0.8 if 1.0 <= t < 1.4 or 3.0 <= t < 4.8 else 0.05
            e = m.update(t, jaw, s)
            if e:
                events.append(e)
        self.assertEqual(events, ["left", "right"])

    def test_talking_flicker_ignored(self):
        s = Settings()
        m = MouthClicker()
        events = [m.update(t, 0.35 + 0.1 * math.sin(20 * t), s) for t in frames(5)]
        self.assertFalse(any(events))


class TestDwell(unittest.TestCase):
    def test_click_once_then_rearm(self):
        s = Settings(dwell_click=True)
        d = DwellClicker()
        clicks = []
        for t in frames(8):
            # rest at A for 3s (start: should not click), move to B, rest 3s
            x = 100 if t < 3 else 400
            if d.update(t, x, 100, s):
                clicks.append(t)
        self.assertEqual(len(clicks), 1)
        self.assertGreater(clicks[0], 3 + s.dwell_time - 0.1)


class TestTilt(unittest.TestCase):
    def test_hold_tilt_toggles_once(self):
        s = Settings()
        tt = TiltToggle()
        toggles = [t for t in frames(5) if tt.update(t, 25 if 1 <= t < 3.5 else 0, s)]
        self.assertEqual(len(toggles), 1)


class TestController(unittest.TestCase):
    def make(self, **kw):
        cal = Calibration(left_off=-15, right_off=15, up_off=-12, down_off=12, done=True)
        s = Settings(**kw)
        return HeadNavController(s, cal, 1920, 1080)

    def test_still_head_does_not_move(self):
        c = self.make()
        for t in frames(2):
            out = c.update(t, 0.5, -0.5, 0)
        self.assertAlmostEqual(out.cursor[0], 960, delta=1)
        self.assertAlmostEqual(out.cursor[1], 540, delta=1)

    def test_turn_right_moves_right_and_clamps(self):
        c = self.make()
        for t in frames(3):
            out = c.update(t, 14, 0, 0)
        self.assertGreater(out.cursor[0], 1800)
        self.assertLessEqual(out.cursor[0], 1918)

    def test_paused_does_not_move(self):
        c = self.make()
        for t in frames(2):
            out = c.update(t, 14, 0, 0, paused=True)
        self.assertIsNone(out.cursor)

    def test_double_nod_clicks_at_pre_nod_position(self):
        c = self.make()
        clicks = []
        for t in frames(4):
            out = c.update(t, 0, nod(t, 1.0, depth=10) + nod(t, 1.5, depth=10), 0)
            clicks += out.clicks
        self.assertEqual(len(clicks), 1)
        button, (x, y) = clicks[0]
        self.assertEqual(button, "left")
        self.assertAlmostEqual(y, 540, delta=3)  # nod movement undone

    def test_tilt_requests_pause_toggle(self):
        c = self.make()
        toggles = sum(c.update(t, 0, 0, 25 if t > 1 else 0).toggle_pause for t in frames(3))
        self.assertEqual(toggles, 1)


if __name__ == "__main__":
    unittest.main()
