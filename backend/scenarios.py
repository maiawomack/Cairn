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
from pathlib import Path

import checkin_agent
import config
import crashcheck
import geofence
import incident
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


# ---- Crash check-in (scenarios 4 and 4b, and the paths around them). All on a fake clock: the
# countdown is 30 real seconds, so tests drive tick() themselves. No API calls. ----

TICK_S = 0.1


def run_checkin(track, hazard_map, reply=None):
    """Replay a track through the detector and the countdown on a fake clock.
    reply = (seconds after the question, "okay" | "help") to answer on cue. Returns (events, incident)."""
    now = [0.0]
    events, state = [], {"asked_at": None}

    def emit(kind, data):
        if kind == "ask":
            state["asked_at"] = now[0]
        events.append((kind, round(now[0] - state["asked_at"], 1), data))

    machine = crashcheck.CheckinMachine(emit, clock=lambda: now[0])
    fence, detector, inc = geofence.Geofence(hazard_map, track["ability"]), crashcheck.CrashDetector(), None
    replied = False
    fixes = track["points"]
    for i, fix in enumerate(fixes):
        fence.update(fix)
        if fence.speed_ms is not None and not fence.last_rejected:
            kmh = fence.speed_ms * 3.6
            machine.movement(kmh)
            crash = detector.update(fix["t"], kmh)
            if crash and machine.state == "idle":
                inc = incident.create(fence.smoothed, crash["peak_kmh"], hazard_map, "2026-02-22T12:00:00+00:00")
                machine.start()
        end = fixes[i + 1]["t"] if i + 1 < len(fixes) else fix["t"] + 1
        t = fix["t"]
        while t < end - 1e-9:  # tick between fixes so reminder and timeout land on time
            now[0] = t
            machine.tick(t)
            if reply and not replied and machine.state == "asking" and now[0] - state["asked_at"] >= reply[0] - 1e-9:
                replied = True
                machine.reply(reply[1])
            t += TICK_S
    return events, inc, machine


def kinds(events):
    return [k for k, _, _ in events]


def at(events, kind):
    return next((t for k, t, _ in events if k == kind), None)


def check_scenario_4(hazard_map):
    track = json.loads((geofence.TRACKS_DIR / "scenario_4.json").read_text())
    events, inc, machine = run_checkin(track, hazard_map)
    ask, reminder, esc = at(events, "ask"), at(events, "reminder"), at(events, "escalated")
    reason = next((d["reason"] for k, _, d in events if k == "escalated"), None)
    report, source = incident.report_for(inc, hazard_map, reason) if inc else (None, None)
    ok = (kinds(events) == ["ask", "reminder", "escalated"] and abs(reminder - (config.CHECKIN_COUNTDOWN_S - config.CHECKIN_REMINDER_S)) <= 0.15
          and abs(esc - config.CHECKIN_COUNTDOWN_S) <= 0.15 and reason == "no reply" and bool(report) and "simulated" in source)
    return ok, "ask, reminder at 20 s, escalation at 30 s, then a simulated report for the patrol console", \
        f"{kinds(events)}, reminder at {reminder} s, escalation at {esc} s ({reason}); report: {source}"


def check_scenario_4b(hazard_map):
    track = json.loads((geofence.TRACKS_DIR / "scenario_4b.json").read_text())
    reply = track["reply"]
    events, inc, machine = run_checkin(track, hazard_map, reply=(reply["after_ask_s"], reply["said"]))
    cancelled = at(events, "cancelled")
    by = next((d["by"] for k, _, d in events if k == "cancelled"), None)
    ok = kinds(events) == ["ask", "cancelled"] and by == "okay" and abs(cancelled - reply["after_ask_s"]) <= 0.15
    return ok, "ask, then cancelled by \"I'm okay\" 12 s in; no reminder, no escalation", f"{kinds(events)}, cancelled by {by} at {cancelled} s"


def check_help(hazard_map):
    track = json.loads((geofence.TRACKS_DIR / "scenario_4.json").read_text())
    events, inc, machine = run_checkin(track, hazard_map, reply=(5, "help"))
    reason = next((d["reason"] for k, _, d in events if k == "escalated"), None)
    ok = kinds(events) == ["ask", "escalated"] and reason == "asked for help" and abs(at(events, "escalated") - 5) <= 0.15
    return ok, "help 5 s in escalates at once", f"{kinds(events)}, escalated at {at(events, 'escalated')} s ({reason})"


def check_resume():
    """Normal skiing again cancels (3 fixes in a row at or above NORMAL_KMH); two then a slow one does not."""
    results = []
    for speeds, expect in (([20, 20, 20], "moving"), ([20, 20, 4, 20, 20, 4], None)):
        now, events = [0.0], []
        machine = crashcheck.CheckinMachine(lambda k, d: events.append((k, d)), clock=lambda: now[0])
        machine.start()
        for kmh in speeds:
            machine.movement(kmh)
        by = next((d["by"] for k, d in events if k == "cancelled"), None)
        results.append(by == expect)
    return all(results), "3 fast fixes in a row cancel; 2 fast, slow, 2 fast, slow do not", f"checks passed: {results}"


def check_mock_path():
    """With the check-in agent id blank, everything above ran with the scripted-voice path."""
    # Force the id blank whatever .env says, so this never depends on (or touches) a real agent.
    saved = config.CHECKIN_AGENT_ID
    config.CHECKIN_AGENT_ID = ""
    try:
        ok = not checkin_agent.configured()
    finally:
        config.CHECKIN_AGENT_ID = saved
    return ok, "agent id blank -> mock path (scripted voice, dashboard buttons); the agent code never runs here", \
        f"configured() with a blank id = {not ok}"


def check_safety():
    safe = {"tools": [{"id": "tool_ok"}, {"id": "tool_help"}], "system_tools": {"end_call": {}}}
    cases = [
        ("safe agent (allowed tools, end_call only)", checkin_agent.unsafe_agent_config(safe, ["tool_ok", "tool_help"]), False),
        ("transfer_to_number enabled", checkin_agent.unsafe_agent_config({**safe, "system_tools": {"transfer_to_number": {"transfers": []}}}, ["tool_ok", "tool_help"]), True),
        ("send_dtmf enabled", checkin_agent.unsafe_agent_config({**safe, "system_tools": {"send_dtmf": {}}}, ["tool_ok", "tool_help"]), True),
        ("an unlisted tool (e.g. an SMS webhook)", checkin_agent.unsafe_agent_config({**safe, "tools": safe["tools"] + [{"id": "tool_sms"}]}, ["tool_ok", "tool_help"]), True),
        ("no allow-list set", checkin_agent.unsafe_agent_config(safe, []), True),
        ("unreadable config", checkin_agent.unsafe_agent_config(None, ["tool_ok"]), True),
    ]
    source = Path(incident.__file__).read_text()
    no_network = not any(f"import {m}" in source for m in ("socket", "urllib", "requests", "httpx", "websockets", "smtplib"))
    ok = all(bool(reasons) == should_refuse for _, reasons, should_refuse in cases) and no_network
    actual = "; ".join(f"{name}: {'REFUSED' if reasons else 'allowed'}" for name, reasons, _ in cases) \
        + f"; incident.py imports no network library: {no_network}"
    return ok, "refuse transfer, DTMF, unlisted tools, no allow-list and unreadable config; allow the safe agent", actual


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

    print("\nCrash check-in (fake clock, no API calls):")
    for name, check in (("scenario_4", lambda: check_scenario_4(hazard_map)), ("scenario_4b", lambda: check_scenario_4b(hazard_map)),
                        ("help button", lambda: check_help(hazard_map)), ("resume skiing", check_resume),
                        ("mock path", check_mock_path), ("safety check", check_safety)):
        ok, expected, actual = check()
        all_ok &= ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
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
