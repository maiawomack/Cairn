"""Settings from .env. Keys live only in .env (never in code); see .env.example."""

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env(path=ENV_FILE):
    """Read KEY=VALUE lines into os.environ. Variables already set in the shell win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


load_env()


def _number(name, default):
    return float(os.environ.get(name, default))


# ---- Geofence tuning (one place; override any of these in .env) ----

# GPS fixes the smoother fits a line through (1 Hz, so about this many seconds).
GEOFENCE_SMOOTHING_FIXES = int(_number("GEOFENCE_SMOOTHING_FIXES", 5))
# Warning distance = base + speed x lead time. The lead time covers speaking the
# warning (about 3 s), reacting (about 1.5 s) and stopping on snow, with margin.
GEOFENCE_WARNING_BASE_M = _number("GEOFENCE_WARNING_BASE_M", 30)
GEOFENCE_WARNING_LEAD_S = _number("GEOFENCE_WARNING_LEAD_S", 8)
# Closer than this to a zone warns whatever the heading (slow skiers, sharp turns).
GEOFENCE_NEAR_M = _number("GEOFENCE_NEAR_M", 15)
# The path ahead counts as reaching a zone if it passes within this margin of it.
GEOFENCE_PATH_MARGIN_M = _number("GEOFENCE_PATH_MARGIN_M", 10)
# Consecutive smoothed fixes that must agree before warning, so one noisy point never warns.
GEOFENCE_CONFIRM_FIXES = int(_number("GEOFENCE_CONFIRM_FIXES", 2))


def warning_distance_m(speed_m_s):
    return GEOFENCE_WARNING_BASE_M + speed_m_s * GEOFENCE_WARNING_LEAD_S
