"""Crash check-in: detect a fast run then a sudden stop, and run the countdown.

CrashDetector reads the smoothed speed of accepted GPS fixes (fixes left out of the fit as
GPS bursts never reach it). It fires once when the skier was moving at FAST_KMH or more,
dropped below STOP_KMH within SUDDEN_DROP_S, and stays below it for STILL_S. A gradual stop
(a lift line, the end of a run) never qualifies.

CheckinMachine owns the timing, in our code and not the LLM, so it is exact: the question
at 0, a reminder when CHECKIN_REMINDER_S seconds are left, escalation at CHECKIN_COUNTDOWN_S.
It is driven by tick(now) with an injectable clock (tests use a fake one). It cancels on
"I'm okay" or on NORMAL_FIXES fixes in a row at NORMAL_KMH or faster, and escalates on a
help request or when the countdown runs out. It only reports what happened through the
emit callback; speaking, the incident and the patrol console live in main.py and incident.py.
Nothing here contacts anyone.
"""

import time
from collections import deque

import config


def lines():
    """The spoken lines (CLAUDE.md), with the configured seconds."""
    n, left = round(config.CHECKIN_COUNTDOWN_S), round(config.CHECKIN_REMINDER_S)
    return {
        "ask": f"Hey, it looks like you stopped suddenly. Are you okay? If I don't hear from you in {n} seconds, I'll alert ski patrol.",
        "reminder": f"Alerting ski patrol in {left} seconds unless you tell me you're okay.",
        "ok": "Glad you're okay. I'll keep watching.",
        "moving": "Looks like you're moving again. I'll keep watching.",
        "escalated": "Alerting ski patrol now with your location. Stay where you are.",
    }


class CrashDetector:
    def __init__(self):
        self.last_fast = None  # (t, km/h) of the latest fix at or above FAST_KMH
        self.stop_started = None
        self.noisy = 0  # fixes in a row at or above STOP_KMH while counted as stopped
        self.fired = False

    def update(self, t, kmh):
        """Feed one accepted fix's smoothed speed (km/h). Returns {"t", "peak_kmh", "stopped_at"} once, else None.

        A skier who is perfectly still still reads a few km/h, because speed comes from noisy
        GPS positions (median about 4, 90th percentile about 8). So STOP_KMH sits above that
        noise, and one noisy fix does not end the stopped phase: it takes two in a row.
        """
        if kmh >= config.FAST_KMH:
            self.last_fast, self.stop_started, self.noisy, self.fired = (t, kmh), None, 0, False
            return None
        if self.stop_started is not None and kmh >= config.STOP_KMH:
            self.noisy += 1
            if self.noisy >= 2:  # really moving again
                self.stop_started, self.noisy = None, 0
            return None
        if kmh >= config.STOP_KMH:
            return None  # still slowing down, not stopped
        self.noisy = 0
        if self.stop_started is None:
            if self.last_fast is None or t - self.last_fast[0] > config.SUDDEN_DROP_S:
                self.last_fast = None  # too gradual (or never fast): not a crash
                return None
            self.stop_started = t
        if not self.fired and t - self.stop_started >= config.STILL_S:
            self.fired = True
            return {"t": t, "peak_kmh": round(self.last_fast[1], 1), "stopped_at": self.stop_started}
        return None


class CheckinMachine:
    """States: idle -> asking -> cancelled | escalated. emit(kind, data) reports every step."""

    def __init__(self, emit, clock=time.monotonic):
        self.emit, self.clock = emit, clock
        self.state = "idle"
        self.started = None
        self.reminded = False
        self.moving_fixes = 0

    def start(self, context=None):
        if self.state == "asking":
            return False
        self.state, self.started, self.reminded, self.moving_fixes = "asking", self.clock(), False, 0
        self.emit("ask", {"countdown_s": config.CHECKIN_COUNTDOWN_S, "reminder_s": config.CHECKIN_REMINDER_S,
                          **(context or {})})
        return True

    def seconds_left(self, now=None):
        return config.CHECKIN_COUNTDOWN_S - ((now if now is not None else self.clock()) - self.started)

    def tick(self, now=None):
        """Call often (every 0.1 s). Fires the reminder and the timeout exactly when due."""
        if self.state != "asking":
            return
        left = self.seconds_left(now)
        if not self.reminded and left <= config.CHECKIN_REMINDER_S:
            self.reminded = True
            self.emit("reminder", {"seconds_left": round(left, 1)})
        if left <= 0:
            self._finish("escalated", {"reason": "no reply", "said": "no response"})

    def reply(self, said):
        """The skier answered: "okay" cancels, "help" escalates."""
        if self.state != "asking":
            return False
        if said == "okay":
            self._finish("cancelled", {"by": "okay", "said": "I'm okay"})
        elif said == "help":
            self._finish("escalated", {"reason": "asked for help", "said": "asked for help"})
        else:
            return False
        return True

    def movement(self, kmh):
        """Feed each accepted fix's smoothed speed while asking: normal skiing again cancels."""
        if self.state != "asking":
            return
        self.moving_fixes = self.moving_fixes + 1 if kmh >= config.NORMAL_KMH else 0
        if self.moving_fixes >= config.NORMAL_FIXES:
            self._finish("cancelled", {"by": "moving", "said": "back to skiing"})

    def _finish(self, state, data):
        self.state = state
        self.emit(state, {**data, "elapsed_s": round(self.clock() - self.started, 1)})
