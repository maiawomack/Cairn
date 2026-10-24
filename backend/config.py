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


# Closures sit right next to runs (Rock Alley passes them), so they only warn when the
# skier keeps heading in: a shorter look ahead and about 5 s of agreeing fixes. Cliffs
# and boundaries keep the settings above. Expert terrain never warns: entering it gives
# a briefing (geofence.py).
GEOFENCE_CLOSURE_WARNING_BASE_M = _number("GEOFENCE_CLOSURE_WARNING_BASE_M", 15)
GEOFENCE_CLOSURE_WARNING_LEAD_S = _number("GEOFENCE_CLOSURE_WARNING_LEAD_S", 4)
GEOFENCE_CLOSURE_CONFIRM_FIXES = int(_number("GEOFENCE_CLOSURE_CONFIRM_FIXES", 5))
# Tighter than cliffs: with 15 m / 10 m, turns plus GPS error reach into the 25 m Rock Alley corridor.
GEOFENCE_CLOSURE_NEAR_M = _number("GEOFENCE_CLOSURE_NEAR_M", 5)
GEOFENCE_CLOSURE_PATH_MARGIN_M = _number("GEOFENCE_CLOSURE_PATH_MARGIN_M", 0)
SUSTAINED_HEADING_TYPES = ("closure",)

# Cliffs give calm guidance on trajectory; "stop" only inside the cliff zone or this close to it.
CLIFF_STOP_M = _number("CLIFF_STOP_M", 15)
# On a cliff's high side this close to the edge always alerts, whatever the heading.
CLIFF_EDGE_M = _number("CLIFF_EDGE_M", 8)
# Extra look ahead rays this many degrees either side of the smoothed heading. Measured on 20
# flat-light drifts: 0 deg gave guidance first in 16/20 (one 2 s before the edge), 6 deg in 20/20.
CLIFF_RAY_SPREAD_DEG = _number("CLIFF_RAY_SPREAD_DEG", 6)


def warning_distance_m(speed_m_s, zone_type="cliff"):
    """Look ahead distance for a zone type."""
    if zone_type in SUSTAINED_HEADING_TYPES:
        return GEOFENCE_CLOSURE_WARNING_BASE_M + speed_m_s * GEOFENCE_CLOSURE_WARNING_LEAD_S
    return GEOFENCE_WARNING_BASE_M + speed_m_s * GEOFENCE_WARNING_LEAD_S


def confirm_fixes(zone_type="cliff"):
    return GEOFENCE_CLOSURE_CONFIRM_FIXES if zone_type in SUSTAINED_HEADING_TYPES else GEOFENCE_CONFIRM_FIXES


def near_m(zone_type="cliff"):
    return GEOFENCE_CLOSURE_NEAR_M if zone_type in SUSTAINED_HEADING_TYPES else GEOFENCE_NEAR_M


def path_margin_m(zone_type="cliff"):
    return GEOFENCE_CLOSURE_PATH_MARGIN_M if zone_type in SUSTAINED_HEADING_TYPES else GEOFENCE_PATH_MARGIN_M


# ---- Coordinator (spoken warning wording) ----

# Claude model that words the spoken warning. Haiku 5.5: the fastest current model.
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL") or "claude-haiku-5-5"
# If Claude hasn't answered by then, speak the Route Planner's line (or a template).
COORDINATOR_TIMEOUT_S = _number("COORDINATOR_TIMEOUT_S", 1.5)
# Claude's line must be under this many words, or the fallback is used.
COORDINATOR_MAX_WORDS = int(_number("COORDINATOR_MAX_WORDS", 15))
# Entry briefings carry conditions and hazards, so they may be longer.
COORDINATOR_BRIEFING_MAX_WORDS = int(_number("COORDINATOR_BRIEFING_MAX_WORDS", 30))


# ---- Voice (Cartesia Sonic over WebSocket) ----

CARTESIA_TTS_MODEL = os.environ.get("CARTESIA_TTS_MODEL") or "sonic-3.6"
CARTESIA_VERSION = os.environ.get("CARTESIA_VERSION") or "2026-08-14"
# Raw PCM the browser decodes itself; 16 bit at 24 kHz keeps each streamed chunk small.
VOICE_SAMPLE_RATE = 24000
VOICE_ENCODING = "pcm_s16le"
# generation_config per style (Sonic treats these as guidance). speed 0.6-1.5, volume 0.5-2.0.
VOICE_STYLES = {
    "firm": {"speed": 1.2, "volume": 1.4, "emotion": "determined"},  # warnings: firm and quick
    "guide": {"speed": 1.05, "volume": 1.25, "emotion": "confident"},  # cliff guidance: clear, not alarmed
    "calm": {"speed": 1.0, "volume": 1.1, "emotion": "calm"},  # briefings and the safe line after a warning
}
