"""Strict HH:MM:SS parsing and two-frame countdown deadlines, independent of IO."""
from dataclasses import dataclass
import math
import re

MIN_SCORE = .95
BUFFER_SECONDS = 3.
TIMER_ROI = (680, 522, 95, 29)


def parse_timer(text):
    if not isinstance(text, str):
        return None
    # Permit a full-width colon, not guessed digits, missing fields or partial text.
    match = re.fullmatch(r'([0-9]{2}):([0-9]{2}):([0-9]{2})', text.strip().replace('：', ':'))
    if not match:
        return None
    hours, minutes, seconds = map(int, match.groups())
    if hours > 23 or minutes > 59 or seconds > 59:
        return None
    return hours * 3600 + minutes * 60 + seconds


@dataclass(frozen=True)
class TimerSample:
    text: str
    seconds: int
    score: float
    captured_at: float  # monotonic, at capture rather than after OCR


def timer_sample(text, score, captured_at):
    seconds = parse_timer(text)
    if seconds is None or isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    if not math.isfinite(score) or not MIN_SCORE <= score <= 1:
        return None
    if not isinstance(captured_at, (int, float)) or not math.isfinite(captured_at):
        return None
    return TimerSample(text.strip().replace('：', ':'), seconds, float(score), captured_at)


@dataclass(frozen=True)
class CooldownEstimate:
    deadline: float  # monotonic; includes a small buffer, never restarted at close time
    text: str
    score: float

    def to_schedule(self, monotonic_now, wall_now):
        return DeferredCooldown(wall_now + max(1., self.deadline - monotonic_now), self.text, self.score)


@dataclass(frozen=True)
class DeferredCooldown:
    ready_at: float  # persistent wall-clock deadline, compatible with ScheduleStore
    text: str
    score: float

    def valid_at(self, now):
        return (isinstance(self.ready_at, (int, float)) and not isinstance(self.ready_at, bool)
                and math.isfinite(self.ready_at) and -30 <= self.ready_at - now <= 86403
                and timer_sample(self.text, self.score, 0) is not None)


def confirm_countdown(first, second):
    if not isinstance(first, TimerSample) or not isinstance(second, TimerSample):
        return None
    # Validate even hand-constructed samples so low-score/invalid data cannot pass.
    for sample in (first, second):
        if timer_sample(sample.text, sample.score, sample.captured_at) != sample:
            return None
    elapsed = second.captured_at - first.captured_at
    dropped = first.seconds - second.seconds
    if not .1 <= elapsed <= 15 or dropped < 0 or abs(dropped - elapsed) > 2:
        return None
    deadline = max(first.captured_at + first.seconds, second.captured_at + second.seconds) + BUFFER_SECONDS
    return CooldownEstimate(deadline, second.text, min(first.score, second.score))
