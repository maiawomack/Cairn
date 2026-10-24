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

RATE_HZ = 1
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


def scenario_1():
    """Intermediate skis the top of Panorama, a groomed blue, and stays on it."""
    panorama = osm_line(RUNS_FILE, "way/49618443")  # drawn top to bottom in OSM
    route = Route(panorama)
    route = Route([route.at(s)[0] for s in range(0, 1201, 10)])  # top 1.2 km
    points, truth = simulate(route, [(route.length, 34, 7, 5)], seed=1)
    return {
        "id": "scenario_1",
        "description": "Intermediate stays on Panorama, a groomed intermediate run",
        "ability": "intermediate",
        "expected": "silence",
        "rate_hz": RATE_HZ,
        "points": points,
        "truth": truth,
    }


def scenario_2():
    """Intermediate comes down Rock Alley, misses its bend at the Ladder and drifts onto the cliff band."""
    rock_alley = osm_line(RUNS_FILE, "way/461517643")
    cliff = osm_line(OSM_HAZARDS_FILE, "way/1172043856")  # spankys-cliff-band-1

    leave = rock_alley[12]  # just past the Spanky's Ladder entrance, before Rock Alley bends west
    # Aim at the south end of the cliff line, 25% along its first segment. Approaching
    # straight up the ridge keeps about 20 m clear of chimney-ladder-closure-1, whose
    # corner sits just west of the cliff band.
    target = (cliff[0][0] + 0.25 * (cliff[1][0] - cliff[0][0]), cliff[0][1] + 0.25 * (cliff[1][1] - cliff[0][1]))
    # Keep going north along the ridge instead of following the run's bend, then onto the cliff.
    pull = (leave[0] + 8, leave[1] + 110)
    route = Route(rock_alley[:13] + bezier(leave, pull, target))
    on_run_m = Route(rock_alley[:13]).length

    legs = [
        (on_run_m, 28, 6, 5),         # confident turns on Rock Alley
        (on_run_m + 60, 20, 3, 7),    # leaves the run, turns get smaller
        (route.length, 15, 2, 8),     # slow, hesitant drift along the ridge onto the cliff band
    ]
    points, truth = simulate(route, legs, seed=2)
    return {
        "id": "scenario_2",
        "description": "Intermediate leaves Rock Alley at Spanky's Ladder and drifts onto spankys-cliff-band-1",
        "ability": "intermediate",
        "expected": "warning for spankys-cliff-band-1 early enough to stop, plus safe line",
        "target_zone": "spankys-cliff-band-1",
        "rate_hz": RATE_HZ,
        "points": points,
        "truth": truth,
    }


def main():
    TRACKS_DIR.mkdir(exist_ok=True)
    for build in (scenario_1, scenario_2):
        track = build()
        path = TRACKS_DIR / f"{track['id']}.json"
        path.write_text(json.dumps(track, indent=1))
        speeds = [p["speed_kmh"] for p in track["truth"]]
        print(f"wrote {path.relative_to(DATA_DIR.parent)}: {len(track['points'])} fixes, "
              f"{track['truth'][-1]['t']:.0f} s, speed {min(speeds):.0f}-{max(speeds):.0f} km/h")


if __name__ == "__main__":
    main()
