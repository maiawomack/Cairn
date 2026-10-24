"""Play a generated GPS track back in real time, as skier messages for the dashboard.

    python backend/replay.py scenario_2 --speed 4    print the messages to the terminal

The FastAPI app streams the same messages to the browser (see main.py).
"""

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

TRACKS_DIR = Path(__file__).resolve().parent.parent / "data" / "tracks"


def load_track(scenario_id):
    """Load data/tracks/<scenario_id>.json. Raises FileNotFoundError for unknown ids."""
    if not re.fullmatch(r"[a-z0-9_]+", scenario_id):
        raise FileNotFoundError(scenario_id)
    return json.loads((TRACKS_DIR / f"{scenario_id}.json").read_text())


async def replay(track, speed=1.0):
    """Yield one skier position message per GPS fix, spaced like the real track (divided by speed).

    speed is a number, or a function of track time t that is asked before every fix,
    so playback speed can change during the replay.
    """
    speed_at = speed if callable(speed) else (lambda t: speed)
    previous_t = None
    for point in track["points"]:
        current = speed_at(point["t"])
        if previous_t is not None:
            await asyncio.sleep((point["t"] - previous_t) / current)
        previous_t = point["t"]
        yield {
            "from": "skier",
            "type": "position",
            "data": {"scenario": track["id"], **point, "playback_speed": current},
            "time": datetime.now(timezone.utc).isoformat(),
        }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario_id")
    parser.add_argument("--speed", type=float, default=1.0)
    args = parser.parse_args()
    async for message in replay(load_track(args.scenario_id), args.speed):
        print(json.dumps(message))


if __name__ == "__main__":
    asyncio.run(main())
