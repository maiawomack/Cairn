"""Generate scenario GPS tracks with realistic speeds, turns and GPS noise.

    python backend/tracks.py

Writes data/tracks/<scenario id>.json. Each track has:
  points  what the phone reports, 1 Hz: t, lat, lon, accuracy_m (with GPS noise)
  truth   where the skier really was: t, lat, lon, speed_kmh (for scoring scenarios)
Random numbers are seeded, so every run produces the same tracks.
"""

import json
import math
import random
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TRACKS_DIR = DATA_DIR / "tracks"
RUNS_FILE = DATA_DIR / "blackcomb_runs.geojson"
OSM_HAZARDS_FILE = DATA_DIR / "osm_hazards.geojson"
ZONES_FILE = DATA_DIR / "hazard_zones.geojson"

RATE_HZ = 1

# Demo trimming: replays start this long before the first warning, and a track
# that never warns (scenario 1) is cut to its first QUIET_S seconds.
LEAD_IN_S = 25
QUIET_S = 30
STEP_S = 0.1  # internal simulation step

# GPS error is correlated from one fix to the next, like a real receiver's drift.
GPS_SIGMA_M = 4.0
GPS_CORRELATION = 0.85
GPS_ACCURACY_M = 5

# Local flat projection around Spanky's Ladder.
LAT0, LON0 = 50.0977, -122.8811
M_PER_DEG_LAT = 111320
M_PER_DEG_LON = 111320 * math.cos(math.radians(LAT0))


def to_xy(lonlat):
    return ((lonlat[0] - LON0) * M_PER_DEG_LON, (lonlat[1] - LAT0) * M_PER_DEG_LAT)


def to_latlon(xy):
    return round(LAT0 + xy[1] / M_PER_DEG_LAT, 7), round(LON0 + xy[0] / M_PER_DEG_LON, 7)


def osm_line(path, osm_id):
    for feature in json.loads(path.read_text())["features"]:
        if feature["properties"]["osm_id"] == osm_id:
            return [to_xy(p) for p in feature["geometry"]["coordinates"]]
    raise KeyError(osm_id)


def bezier(p0, p1, p2, n=12):
    """Quadratic curve from p0 to p2 pulled toward p1, for a gradual drift off a run."""
    return [
        ((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0],
         (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1])
        for t in (k / n for k in range(1, n + 1))
    ]


class Route:
    """A polyline in local meters that can be walked by distance."""

    def __init__(self, points):
        self.points = points
        self.cumulative = [0.0]
        for a, b in zip(points, points[1:]):
            self.cumulative.append(self.cumulative[-1] + math.dist(a, b))
        self.length = self.cumulative[-1]

    def at(self, s):
        """Point and unit direction at distance s along the route."""
        s = max(0.0, min(s, self.length))
        i = 0
        while i < len(self.points) - 2 and self.cumulative[i + 1] < s:
            i += 1
        a, b = self.points[i], self.points[i + 1]
        segment = self.cumulative[i + 1] - self.cumulative[i] or 1
        f = (s - self.cumulative[i]) / segment
        direction = ((b[0] - a[0]) / segment, (b[1] - a[1]) / segment)
        return (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])), direction


def simulate(route, legs, seed):
    """Ski the route. legs: list of (until_m, cruise_kmh, turn_amplitude_m, turn_period_s).

    Speed eases toward each leg's cruise speed (with gentle variation), and the
    skier makes S-turns across the centerline. Returns (points, truth) at RATE_HZ.
    """
    rng = random.Random(seed)
    s, t, speed = 0.0, 0.0, legs[0][1] / 3.6 * 0.6
    turn_phase = 0.0
    noise = [0.0, 0.0]
    points, truth = [], []
    next_fix = 0.0

    while s < route.length:
        leg = next(l for l in legs if s <= l[0] or l is legs[-1])
        _, cruise_kmh, amplitude, period = leg
        target = cruise_kmh / 3.6 * (1 + 0.12 * math.sin(2 * math.pi * t / 23))
        # Skiers speed up and slow down over a few seconds, not instantly.
        speed += max(-1.5, min(1.0, (target - speed) / 3)) * STEP_S
        turn_phase += 2 * math.pi * STEP_S / period
        # Turns fade out over the last 20 m so the track ends on the route.
        fade = min(1.0, (route.length - s) / 20)

        if t >= next_fix - 1e-9:
            (x, y), (dx, dy) = route.at(s)
            offset = amplitude * fade * math.sin(turn_phase)
            true_xy = (x - dy * offset, y + dx * offset)
            for axis in (0, 1):
                noise[axis] = (GPS_CORRELATION * noise[axis]
                               + math.sqrt(1 - GPS_CORRELATION ** 2) * rng.gauss(0, GPS_SIGMA_M))
            lat, lon = to_latlon((true_xy[0] + noise[0], true_xy[1] + noise[1]))
            points.append({"t": round(t, 1), "lat": lat, "lon": lon, "accuracy_m": GPS_ACCURACY_M})
            true_lat, true_lon = to_latlon(true_xy)
            truth.append({"t": round(t, 1), "lat": true_lat, "lon": true_lon, "speed_kmh": round(speed * 3.6, 1)})
            next_fix += 1 / RATE_HZ

        s += speed * STEP_S
        t += STEP_S
    return points, truth


# Start this far below the Ladder's expert zone: GPS noise (up to about 10 m) at the very
# start must not land a fix inside it and add a second briefing.
LADDER_START_MARGIN_M = 15


def below_ladder(line):
    """Drop the start of a line that lies inside the Spanky's Ladder entrance zone (plus a
    margin), so the scenario's first briefing is the bowl's, not a second one for the Ladder."""
    zones = json.loads(ZONES_FILE.read_text())["features"]
    ladder = next(z for z in zones if z["properties"]["id"] == "spankys-ladder-entrance")
    ring = [to_xy(p) for p in ladder["geometry"]["coordinates"][0]]
    route = Route(line)
    s = next(s for s in range(0, int(route.length), 2) if not point_in_ring(route.at(s)[0], ring)) + LADDER_START_MARGIN_M
    return [route.at(s)[0]] + [p for p, c in zip(line, route.cumulative) if c > s]


def point_in_ring(p, ring):
    result = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > p[1]) != (y2 > p[1]) and p[0] < (x2 - x1) * (p[1] - y1) / (y2 - y1) + x1:
            result = not result
    return result


def until_leaving(line, zone_id):
    """Cut a line where it leaves a zone it has entered."""
    zones = json.loads(ZONES_FILE.read_text())["features"]
    zone = next(z for z in zones if z["properties"]["id"] == zone_id)
    ring = [to_xy(p) for p in zone["geometry"]["coordinates"][0]]
    route = Route(line)
    entered = False
    for s in range(0, int(route.length), 2):
        inside_now = point_in_ring(route.at(s)[0], ring)
        if entered and not inside_now:
            return [p for p, c in zip(line, route.cumulative) if c < s] + [route.at(s)[0]]
        entered = entered or inside_now
    return line


def scenario_1(seed=11):
    """Advanced skier drops in below Spanky's Ladder and skis a clean line down Garnet Bowl Left.

    The track ends where the line leaves Garnet Bowl: past that, the mapped line runs out
    toward spankys-cliff-band-2, and guidance there is correct, not part of this scenario.
    """
    garnet_left = until_leaving(below_ladder(osm_line(RUNS_FILE, "way/1167852432")), "garnet-bowl")
    route = Route(garnet_left)
    points, truth = simulate(route, [(route.length, 36, 8, 4)], seed=seed)
    return {
        "id": "scenario_1",
        "description": "Advanced skier on a clean line down Garnet Bowl Left",
        "ability": "advanced",
        "expected": "only the Garnet Bowl entry briefing",
        "target_zone": "garnet-bowl",
        "rate_hz": RATE_HZ,
        "points": points,
        "truth": truth,
    }


# Scenario 2: the drift turns back this close to the cliff line, a beat after the warning
# (which comes about 31-35 m out), and rejoins Garnet Bowl Right this far below where it left.
TURN_BACK_AT_M = 24
REJOIN_AFTER_M = 25
# Where on Garnet Bowl Right the drift starts (share of the line). Earlier starts give a longer
# run-out but a shallower approach, and guidance then has under 12 s before the edge.
DRIFT_STARTS_AT = 0.55
EXIT_MARGIN_M = 4


def scenario_2(seed=12, recover=True):
    """Advanced skier on Garnet Bowl Right in flat light drifts off the line toward
    garnet-cliff-band-1, hears the warning, turns back onto Garnet Bowl Right and skis it out.

    recover=False keeps drifting onto the cliff instead (identical up to the turn), so a
    test can measure how long the guidance left before the edge."""
    garnet_right = below_ladder(osm_line(RUNS_FILE, "way/907542959"))  # drawn top to bottom in OSM
    # Like scenario 1, the run out ends where the line leaves Garnet Bowl: past that, the mapped
    # line points toward diamond-cliff-band-1, and guidance there is correct, not this scenario.
    # Ends at the line's real east exit from the bowl (about 2,100 m), the same rule as scenarios 1
    # and 3; Garnet Bowl Right is not made to go lower. Stops EXIT_MARGIN_M short, inside the GPS
    # accuracy: the line ends beside the north tip of garnet-cliff-band-1, and from 2 m or less the
    # last fixes point at it and (correctly) give guidance again after the confirmation.
    bowl_end_m = Route(until_leaving(garnet_right, "garnet-bowl")).length - EXIT_MARGIN_M
    cliff = osm_line(OSM_HAZARDS_FILE, "way/1167734535")  # garnet-cliff-band-1, low side on its right

    on_line = Route(garnet_right)
    leave_s = on_line.length * DRIFT_STARTS_AT
    leave, heading = on_line.at(leave_s)
    # Aim at the middle of the cliff line, approaching from its high (left) side: with
    # no contrast in flat light the skier keeps drifting with the fall line.
    target = cliff[len(cliff) // 2]
    pull = (leave[0] + heading[0] * 40, leave[1] + heading[1] * 40)
    drift = bezier(leave, pull, target, n=40)
    turn_i = next(i for i, q in enumerate(drift) if math.dist(q, nearest_on_line(q, cliff)) <= TURN_BACK_AT_M)
    turn = drift[turn_i]

    # Following the advice: momentum carries them a few metres on, then they curve back
    # onto the line further down and ski the rest of it.
    rejoin_s = min(on_line.length - 10, leave_s + REJOIN_AFTER_M)
    rejoin, _ = on_line.at(rejoin_s)
    dx, dy = turn[0] - drift[turn_i - 1][0], turn[1] - drift[turn_i - 1][1]
    norm = math.hypot(dx, dy) or 1
    carry = (turn[0] + dx / norm * 8, turn[1] + dy / norm * 8)
    recovery = bezier(turn, carry, rejoin, n=16)
    before = [q for q, c in zip(garnet_right, on_line.cumulative) if c < leave_s] + [leave]
    rest = [q for q, c in zip(garnet_right, on_line.cumulative) if rejoin_s < c < bowl_end_m] + [on_line.at(bowl_end_m)[0]]
    if not recover:
        route = Route(before + drift[1:])
        legs = [(leave_s, 24, 6, 5), (route.length, 20, 3, 7)]
        points, truth = simulate(route, legs, seed=seed)
        return {"id": "scenario_2_no_recovery", "ability": "advanced", "points": points, "truth": truth}
    route = Route(before + drift[1:turn_i + 1] + recovery + rest)

    drift_end = Route(before + drift[1:turn_i + 1]).length
    recovery_end = Route(before + drift[1:turn_i + 1] + recovery).length
    legs = [
        (leave_s, 24, 6, 5),             # cautious turns on the line: flat light, little contrast
        (drift_end, 20, 3, 7),           # drifting off the line, turns getting smaller
        (recovery_end, 13, 2, 6),        # hears the warning, checks speed, turns back to the line
        (route.length, 24, 6, 5),        # back on Garnet Bowl Right, skiing it out
    ]
    points, truth = simulate(route, legs, seed=seed)
    return {
        "id": "scenario_2",
        "description": "Advanced skier on Garnet Bowl Right in flat light drifts toward garnet-cliff-band-1, "
                       "takes the advice and skis Garnet Bowl Right out",
        "ability": "advanced",
        "expected": "briefing, guidance for garnet-cliff-band-1 in time to change line, warning, "
                    "then \"You're on Garnet Bowl Right.\" and nothing else",
        "target_zone": "garnet-cliff-band-1",
        "seed": seed,
        "rate_hz": RATE_HZ,
        "points": points,
        "truth": truth,
    }


def nearest_on_line(p, line):
    best = None
    for a, b in zip(line, line[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length_sq = dx * dx + dy * dy or 1
        t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length_sq))
        q = (a[0] + t * dx, a[1] + t * dy)
        if best is None or math.dist(p, q) < math.dist(p, best):
            best = q
    return best


def add_gps_burst(points, truth, line, rng):
    """A multipath burst: for 2-3 fixes the reported position is pulled 20-40 m toward
    `line`, building up over the burst, then snaps back. The phone's reported accuracy
    degrades with it, as real receivers do. Normal GPS noise carries on underneath.
    Starts so the worst fix lands where the true track passes closest to the line."""
    closest = min(range(len(truth)), key=lambda i: math.dist(
        to_xy((truth[i]["lon"], truth[i]["lat"])), nearest_on_line(to_xy((truth[i]["lon"], truth[i]["lat"])), line)))
    duration = rng.choice([2, 3])
    magnitude = rng.uniform(20, 40)
    profile = [0.65, 1.0] if duration == 2 else [0.55, 1.0, 0.8]
    start = max(1, closest - 1)
    for k, share in enumerate(profile):
        i = start + k
        true_xy = to_xy((truth[i]["lon"], truth[i]["lat"]))
        target = nearest_on_line(true_xy, line)
        d = math.dist(true_xy, target) or 1
        ux, uy = (target[0] - true_xy[0]) / d, (target[1] - true_xy[1]) / d
        pull, wobble = magnitude * share, rng.gauss(0, 3)  # a little sideways scatter too
        x, y = to_xy((points[i]["lon"], points[i]["lat"]))
        lat, lon = to_latlon((x + ux * pull - uy * wobble, y + uy * pull + ux * wobble))
        points[i] = {**points[i], "lat": lat, "lon": lon,
                     "accuracy_m": round(max(GPS_ACCURACY_M, pull * 0.8 + rng.gauss(0, 3)))}
    return {"start_t": points[start]["t"], "end_t": points[start + duration - 1]["t"],
            "magnitude_m": round(magnitude, 1), "toward": "garnet-cliff-band-1"}


def scenario_3(seed=13):
    """Scenario 1's clean line down Garnet Bowl Left, with a 2-3 s GPS burst that jumps the
    reported position 20-40 m toward garnet-cliff-band-1 and snaps back."""
    track = scenario_1(seed=seed)
    cliff = osm_line(OSM_HAZARDS_FILE, "way/1167734535")  # garnet-cliff-band-1
    burst = add_gps_burst(track["points"], track["truth"], cliff, random.Random(seed + 1000))
    return {**track,
            "id": "scenario_3",
            "description": "Advanced skier on Garnet Bowl Left; a 2-3 s GPS burst jumps 20-40 m toward garnet-cliff-band-1",
            "expected": "only the Garnet Bowl entry briefing; no guidance or warning from the GPS burst",
            "target_zone": "garnet-cliff-band-1",
            "gps_burst": burst}


def append_still(points, truth, seconds, seed):
    """Stop dead where the track ends and stay there. GPS noise carries on from the last fix's
    error, so the reported position wanders a few metres while the skier is perfectly still."""
    rng = random.Random(seed + 5000)
    true_xy = to_xy((truth[-1]["lon"], truth[-1]["lat"]))
    last = to_xy((points[-1]["lon"], points[-1]["lat"]))
    noise = [last[0] - true_xy[0], last[1] - true_xy[1]]
    t0 = truth[-1]["t"]
    for k in range(1, seconds + 1):
        for axis in (0, 1):
            noise[axis] = (GPS_CORRELATION * noise[axis]
                           + math.sqrt(1 - GPS_CORRELATION ** 2) * rng.gauss(0, GPS_SIGMA_M))
        lat, lon = to_latlon((true_xy[0] + noise[0], true_xy[1] + noise[1]))
        points.append({"t": round(t0 + k, 1), "lat": lat, "lon": lon, "accuracy_m": GPS_ACCURACY_M})
        true_lat, true_lon = to_latlon(true_xy)
        truth.append({"t": round(t0 + k, 1), "lat": true_lat, "lon": true_lon, "speed_kmh": 0.0})


# How long the skier stays still after the crash. The countdown is 30 s of real time, so the
# track has to outlast it for the replay to show the whole check-in.
STILL_AFTER_CRASH_S = 45


def scenario_4(seed=14):
    """Advanced skier on a fast, clean line down Garnet Bowl Left, then stops dead and stays still
    (a fall, or a collision). The check-in asks, counts down, reminds, and escalates."""
    track = scenario_1(seed=seed)
    append_still(track["points"], track["truth"], STILL_AFTER_CRASH_S, seed)
    return {**track, "id": "scenario_4",
            "description": "Advanced skier on a fast line down Garnet Bowl Left stops dead and stays still",
            "expected": "check-in question, 10 s reminder, then escalation to the simulated patrol console",
            "target_zone": "garnet-bowl", "checkin": True}


def scenario_4b(seed=14):
    """Scenario 4, but the skier answers 'I'm okay' 12 s into the countdown: it cancels."""
    track = scenario_4(seed=seed)
    return {**track, "id": "scenario_4b",
            "description": "Same stop as scenario 4; the skier says they're okay 12 s into the countdown",
            "expected": "check-in question, then cancelled by 'I'm okay'; no escalation",
            "reply": {"after_ask_s": 12, "said": "okay"}}


def trim(track, start_s, end_s=None):
    """Keep fixes from start_s (to end_s), with times re-zeroed so the replay starts there."""
    def window(fixes):
        kept = [f for f in fixes if f["t"] >= start_s and (end_s is None or f["t"] <= end_s)]
        return [{**f, "t": round(f["t"] - start_s, 1)} for f in kept]
    return {**track, "points": window(track["points"]), "truth": window(track["truth"]),
            "trimmed_from_s": start_s}


def trim_for_demo(track):
    """Start LEAD_IN_S before the first geofence warning; a track that never warns keeps its first QUIET_S."""
    import geofence  # the current hazard map decides where the first warning is

    alerts = geofence.run_track(track, geofence.load_hazard_map())
    if not alerts:
        return trim(track, 0, QUIET_S)
    return trim(track, max(0, alerts[0]["t"] - LEAD_IN_S))


def main():
    TRACKS_DIR.mkdir(exist_ok=True)
    for build in (scenario_1, scenario_2, scenario_3, scenario_4, scenario_4b):
        track = trim_for_demo(build())
        path = TRACKS_DIR / f"{track['id']}.json"
        path.write_text(json.dumps(track, indent=1))
        speeds = [p["speed_kmh"] for p in track["truth"]]
        print(f"wrote {path.relative_to(DATA_DIR.parent)}: {len(track['points'])} fixes, "
              f"{track['truth'][-1]['t']:.0f} s (from t={track['trimmed_from_s']} s of the full run), "
              f"speed {min(speeds):.0f}-{max(speeds):.0f} km/h")


if __name__ == "__main__":
    main()
