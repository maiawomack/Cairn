"""Run the test scenarios through the geofence and print expected vs actual.

    python backend/scenarios.py              the demo tracks in data/tracks/
    python backend/scenarios.py --seeds 20   also 20 fresh random variants of each scenario

Exit code 1 if a demo scenario fails. No API calls: this tests the geofence (what alerts,
and when), not the wording. CLAUDE.md lists the scenarios.
"""

import argparse
import json
import math
import sys

import geofence
import tracks
from geofence import to_xy

GUIDANCE_LEAD_S = 12  # scenario 2: guidance at least this long before the skier reaches the edge


def summary(alerts):
    return [(a["t"], a["level"], a["zone_id"]) for a in alerts]


def edge_reached_t(track, zone):
    """When the true track first comes within 3 m of the zone's cliff line."""
    lines = [[to_xy(lat, lon) for lat, lon in line] for line in zone["cliff_lines"]]
    for p in track["truth"]:
        q = to_xy(p["lat"], p["lon"])
        if geofence._edge_distance(q, lines)[0] < 3:
            return p["t"]
    return None


def only_briefing(track, alerts, hazard_map):
    actual = [(level, zone) for _, level, zone in summary(alerts)]
    ok = actual == [("briefing", "garnet-bowl")]
    return ok, "only the Garnet Bowl briefing", ", ".join(f"{level} {zone} at {t:.0f} s" for t, level, zone in summary(alerts)) or "nothing"


def guidance_in_time(track, alerts, hazard_map):
    target = next(z for z in hazard_map["zones"] if z["id"] == "garnet-cliff-band-1")
    edge_t = edge_reached_t(track, target)
    levels = [(level, zone) for _, level, zone in summary(alerts)]
    guidance = next((a for a in alerts if a["zone_id"] == target["id"] and a["level"] == "guidance"), None)
    first_target = next((a for a in alerts if a["zone_id"] == target["id"]), None)
    lead = None if guidance is None or edge_t is None else edge_t - guidance["t"]
    ok = (bool(levels) and levels[0] == ("briefing", "garnet-bowl") and guidance is not None
          and first_target is guidance and lead is not None and lead >= GUIDANCE_LEAD_S)
    extra = sorted({zone for _, zone in levels} - {"garnet-bowl", target["id"]})
    actual = (f"guidance {lead:.0f} s before the edge" if lead is not None else
              f"no guidance first (first target alert: {first_target['level'] if first_target else 'none'})")
    if extra:
        actual += f"; also alerted: {', '.join(extra)}"
    return ok, f"briefing, then guidance for garnet-cliff-band-1 at least {GUIDANCE_LEAD_S} s before the edge", actual


SCENARIOS = [
    ("scenario_1", tracks.scenario_1, only_briefing),
    ("scenario_2", tracks.scenario_2, guidance_in_time),
    ("scenario_3", tracks.scenario_3, only_briefing),
]
SEED_BASE = {"scenario_1": 200, "scenario_2": 300, "scenario_3": 100}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=0, help="also run this many random variants of each scenario")
    args = parser.parse_args()
    hazard_map = geofence.load_hazard_map()

    all_ok = True
    for name, build, check in SCENARIOS:
        track = json.loads((geofence.TRACKS_DIR / f"{name}.json").read_text())
        ok, expected, actual = check(track, geofence.run_track(track, hazard_map), hazard_map)
        all_ok &= ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {track['description']}")
        print(f"      expected: {expected}")
        print(f"      actual:   {actual}")

    if args.seeds:
        print(f"\nRandom variants ({args.seeds} seeds each):")
        for name, build, check in SCENARIOS:
            failures = []
            for seed in range(SEED_BASE[name], SEED_BASE[name] + args.seeds):
                track = build(seed=seed)
                ok, _, actual = check(track, geofence.run_track(track, hazard_map), hazard_map)
                if not ok:
                    failures.append(f"seed {seed}: {actual}")
            print(f"  {name}: {args.seeds - len(failures)}/{args.seeds} pass")
            for failure in failures:
                print(f"    {failure}")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
