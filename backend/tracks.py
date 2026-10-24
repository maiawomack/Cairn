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


def below_ladder(line):
    """Drop the start of a line that lies inside the Spanky's Ladder entrance zone, so the
    scenario's first briefing is the bowl's, not a second one for the Ladder."""
    zones = json.loads(ZONES_FILE.read_text())["features"]
    ladder = next(z for z in zones if z["properties"]["id"] == "spankys-ladder-entrance")
    ring = [to_xy(p) for p in ladder["geometry"]["coordinates"][0]]
    route = Route(line)
    s = next(s for s in range(0, int(route.length), 2) if not point_in_ring(route.at(s)[0], ring))
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


def scenario_1():
    """Advanced skier drops in below Spanky's Ladder and skis a clean line down Garnet Bowl Left.

    The track ends where the line leaves Garnet Bowl: past that, the mapped line runs out
    toward spankys-cliff-band-2, and guidance there is correct, not part of this scenario.
    """
    garnet_left = until_leaving(below_ladder(osm_line(RUNS_FILE, "way/1167852432")), "garnet-bowl")
    route = Route(garnet_left)
    points, truth = simulate(route, [(route.length, 36, 8, 4)], seed=11)
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


def scenario_2():
    """Advanced skier on Garnet Bowl Right in flat light drifts off the line onto garnet-cliff-band-1."""
    garnet_right = below_ladder(osm_line(RUNS_FILE, "way/907542959"))  # drawn top to bottom in OSM
    cliff = osm_line(OSM_HAZARDS_FILE, "way/1167734535")  # garnet-cliff-band-1, low side on its right

    on_line = Route(garnet_right)
    leave_s = on_line.length * 0.55
    leave, heading = on_line.at(leave_s)
    # Aim at the middle of the cliff line, approaching from its high (left) side: with
    # no contrast in flat light the skier keeps drifting with the fall line.
    target = cliff[len(cliff) // 2]
    pull = (leave[0] + heading[0] * 40, leave[1] + heading[1] * 40)
    route = Route([p for p, c in zip(garnet_right, on_line.cumulative) if c < leave_s] + [leave]
                  + bezier(leave, pull, target))

    legs = [
        (leave_s, 24, 6, 5),          # cautious turns on the line: flat light, little contrast
        (route.length, 20, 3, 7),     # drifting off the line, turns getting smaller
    ]
    points, truth = simulate(route, legs, seed=12)
    return {
        "id": "scenario_2",
        "description": "Advanced skier on Garnet Bowl Right in flat light drifts toward garnet-cliff-band-1",
        "ability": "advanced",
        "expected": "Garnet Bowl entry briefing, then guidance for garnet-cliff-band-1 in time to change line",
        "target_zone": "garnet-cliff-band-1",
        "rate_hz": RATE_HZ,
        "points": points,
        "truth": truth,
    }


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
    for build in (scenario_1, scenario_2):
        track = trim_for_demo(build())
        path = TRACKS_DIR / f"{track['id']}.json"
        path.write_text(json.dumps(track, indent=1))
        speeds = [p["speed_kmh"] for p in track["truth"]]
        print(f"wrote {path.relative_to(DATA_DIR.parent)}: {len(track['points'])} fixes, "
              f"{track['truth'][-1]['t']:.0f} s (from t={track['trimmed_from_s']} s of the full run), "
              f"speed {min(speeds):.0f}-{max(speeds):.0f} km/h")


if __name__ == "__main__":
    main()
