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

GUIDANCE_LEAD_S = 12  # scenario 2: guidance at least this long before the edge, had the skier kept drifting


def summary(alerts):
    return [(a["t"], a["level"], a["zone_id"]) for a in alerts]


def only_briefing(track, alerts, hazard_map):
    actual = [(level, zone) for _, level, zone in summary(alerts)]
    ok = actual == [("briefing", "garnet-bowl")]
    return ok, "only the Garnet Bowl briefing", ", ".join(f"{level} {zone} at {t:.0f} s" for t, level, zone in summary(alerts)) or "nothing"


def guidance_then_recovery(track, alerts, hazard_map):
    """Scenario 2: briefing; the first alert for the target cliff is guidance at least
    GUIDANCE_LEAD_S before they'd reach the edge if they kept drifting; "You're on Garnet Bowl Right"
    after it; the skier never enters the cliff zone; no alerts for any other zone."""
    target = next(z for z in hazard_map["zones"] if z["id"] == "garnet-cliff-band-1")
    lines = [[to_xy(lat, lon) for lat, lon in line] for line in target["cliff_lines"]]
    truth = {p["t"]: p for p in track["truth"]}
    levels = [(level, zone) for _, level, zone in summary(alerts)]
    first_target = next((a for a in alerts if a["zone_id"] == target["id"]), None)
    lead = None
    if first_target and first_target["level"] == "guidance":
        # How long the guidance left before the edge had the skier kept drifting: the same
        # track without the recovery (identical up to the turn) shows when they'd reach it.
        drifting = tracks.scenario_2(seed=track["seed"], recover=False)
        reach = next((p["t"] for p in drifting["truth"]
                      if geofence._edge_distance(to_xy(p["lat"], p["lon"]), lines)[0] < 3), None)
        lead = None if reach is None else reach - first_target["t"]
    on_line = next((a for a in alerts if a["level"] == "on_line"), None)
    closest = min(geofence._edge_distance(to_xy(p["lat"], p["lon"]), lines)[0] for p in track["truth"])
    extra = sorted({zone for _, zone in levels} - {"garnet-bowl", target["id"]})
    after = [a for a in alerts if on_line and a["t"] > on_line["t"]]  # nothing speaks after the confirmation
    ok = (not after and bool(levels) and levels[0] == ("briefing", "garnet-bowl") and lead is not None and lead >= GUIDANCE_LEAD_S
          and on_line is not None and on_line["line_name"] == "Garnet Bowl Right" and on_line["t"] > first_target["t"]
          and closest > 20 and not extra)
    parts = [f"guidance {lead:.0f} s before the edge" if lead is not None else
             f"first target alert: {first_target['level'] if first_target else 'none'}"]
    parts.append(f"on Garnet Bowl Right at {on_line['t']:.0f} s" if on_line else "never confirmed on the line")
    parts.append(f"closest to the edge {closest:.0f} m")
    if extra:
        parts.append(f"also alerted: {', '.join(extra)}")
    if after:
        parts.append(f"{len(after)} alert(s) after the confirmation")
    expected = (f"briefing, guidance at least {GUIDANCE_LEAD_S} s before the edge, then on Garnet Bowl Right; "
                "never inside the cliff zone; no other zones; silence after the confirmation")
    return ok, expected, "; ".join(parts)


SCENARIOS = [
    ("scenario_1", tracks.scenario_1, only_briefing),
    ("scenario_2", tracks.scenario_2, guidance_then_recovery),
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
