"""Active-time watchdog: state churn and repeated clicks alone are not progress."""
import numpy as np


class ProgressWatchdog:
    def __init__(self, settings):
        self.settings = settings
        self.baseline = None
        self.since = 0.0
        self.last_warning = None
        self.evidence = set()
        self.snapshot_saved = False

    def idle(self, active):
        return max(0.0, active - self.since)

    def observe(self, frame, active):
        if not isinstance(frame, np.ndarray):
            signature = frame  # deterministic synthetic state-machine tests
            changed = self.baseline is None or signature != self.baseline
        else:
            h, w = frame.shape[:2]
            ys = np.linspace(0, h - 1, 36, dtype=int)
            xs = np.linspace(0, w - 1, 64, dtype=int)
            signature = frame[ys[:, None], xs[None, :], :3].astype(float).mean(axis=2)
            changed = (self.baseline is None or not isinstance(self.baseline, np.ndarray)
                       or float(np.mean(np.abs(signature - self.baseline) > 25)) >= .10)
        if changed:
            self.baseline = signature
            self.since = active
            self.evidence.clear()
            self.last_warning = None
            self.snapshot_saved = False

    def confirmed(self, key, active):
        # A/B/A/B or repeated same-button detections cannot renew the timer forever.
        if key not in self.evidence:
            self.evidence.add(key)
            self.since = active
            self.last_warning = None

    def status(self, active):
        idle = self.idle(active)
        if idle >= self.settings['stop_seconds']:
            return 'stop'
        if idle >= self.settings['warn_seconds']:
            interval = self.settings.get('repeat_warning_seconds', 30)
            if self.last_warning is None or active - self.last_warning >= interval:
                self.last_warning = active
                return 'warning'
        return None
