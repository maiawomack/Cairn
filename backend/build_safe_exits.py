"""Propose safe exits for each hazard zone from the OSM runs. A starting point for the Route Planner.

Run after build_hazard_zones.py:

    python backend/build_safe_exits.py

For each zone, the exit is the nearest easier run (easy or intermediate, or a
traverse no harder than advanced) that a skier can reach from the zone edge:
  - within MAX_EXIT_DISTANCE_M of the zone,
  - without climbing more than MAX_CLIMB_M (checked against the DEM),
  - without the path crossing another cliff or closure zone, or any OSM
    cliff line (its own zone's included), so an exit never leads over a cliff.
The exit path goes from the zone edge to the run, then follows the run
downhill for EXIT_RUN_LENGTH_M. Zones with no such run get no exit.

Ids are "<zone id>-exit", one per zone, so they stay stable across reruns.
Writes data/safe_exits.geojson and prints { id, from_zone, name } JSON.
"""

import json
import math
from pathlib import Path

from dem import elevation

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RUNS_FILE = DATA_DIR / "blackcomb_runs.geojson"
ZONES_FILE = DATA_DIR / "hazard_zones.geojson"
OSM_HAZARDS_FILE = DATA_DIR / "osm_hazards.geojson"
EXITS_FILE = DATA_DIR / "safe_exits.geojson"

MAX_EXIT_DISTANCE_M = 200
MAX_CLIMB_M = 10
EXIT_RUN_LENGTH_M = 150
PATH_CHECK_STEP_M = 5

EASIER = ("novice", "easy", "intermediate")

# Local flat projection around Spanky's Ladder, good to well under 1% in the box.
LAT0, LON0 = 50.0977, -122.8811
M_PER_DEG_LAT = 111320
M_PER_DEG_LON = 111320 * math.cos(math.radians(LAT0))


def to_xy(lonlat):
    return ((lonlat[0] - LON0) * M_PER_DEG_LON, (lonlat[1] - LAT0) * M_PER_DEG_LAT)


def to_lonlat(xy):
    return [round(LON0 + xy[0] / M_PER_DEG_LON, 7), round(LAT0 + xy[1] / M_PER_DEG_LAT, 7)]


def closest_on_segment(p, a, b):
    """Closest point to p on segment a-b, and its parameter t in [0, 1]."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_sq = dx * dx + dy * dy
    t = 0 if length_sq == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length_sq))
    return (a[0] + t * dx, a[1] + t * dy), t


def inside(p, ring):
    crossings = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > p[1]) != (y2 > p[1]) and p[0] < (x2 - x1) * (p[1] - y1) / (y2 - y1) + x1:
            crossings = not crossings
    return crossings


def is_easier(properties):
    if properties.get("kind") != "run" or properties.get("piste:type") != "downhill":
        return False
    difficulty = properties.get("piste:difficulty")
    is_traverse = "Traverse" in properties.get("name", "")
    return difficulty in EASIER or (is_traverse and difficulty == "advanced")


def run_parts(feature):
    geometry = feature["geometry"]
    if geometry["type"] == "LineString":
        return [[to_xy(p) for p in geometry["coordinates"]]]
    if geometry["type"] == "MultiLineString":
        return [[to_xy(p) for p in part] for part in geometry["coordinates"]]
    return []


def nearest_join(ring, part):
    """Closest pair between a zone ring and a run: (distance, zone point, run point, segment index, t)."""
    best = None
    for zone_point in ring:
        for i, (a, b) in enumerate(zip(part, part[1:])):
            run_point, t = closest_on_segment(zone_point, a, b)
            d = math.dist(zone_point, run_point)
            if best is None or d < best[0]:
                best = (d, zone_point, run_point, i, t)
    for i, run_point in enumerate(part):
        for a, b in zip(ring, ring[1:]):
            zone_point, _ = closest_on_segment(run_point, a, b)
            d = math.dist(zone_point, run_point)
            if best is None or d < best[0]:
                best = (d, zone_point, run_point, min(i, len(part) - 2), 0 if i < len(part) - 1 else 1)
    return best


def walk(part, segment, t, forward, length):
    """Points along a run from the join point for `length` meters, in either direction."""
    a, b = part[segment], part[segment + 1]
    start = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    indices = range(segment + 1, len(part)) if forward else range(segment, -1, -1)
    points, remaining = [start], length
    for i in indices:
        step = math.dist(points[-1], part[i])
        if step >= remaining:
            f = remaining / step if step else 0
            last = points[-1]
            points.append((last[0] + f * (part[i][0] - last[0]), last[1] + f * (part[i][1] - last[1])))
            return points
        points.append(part[i])
        remaining -= step
    return points


def segments_cross(a, b, c, d):
    def side(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0


def crosses_zone(path, blocking_rings, cliff_lines):
    """True if the path enters another zone or crosses any cliff line, its own zone's included."""
    for a, b in zip(path, path[1:]):
        for line in cliff_lines:
            if any(segments_cross(a, b, c, d) for c, d in zip(line, line[1:])):
                return True
        steps = max(1, int(math.dist(a, b) / PATH_CHECK_STEP_M))
        for k in range(steps + 1):
            p = (a[0] + (b[0] - a[0]) * k / steps, a[1] + (b[1] - a[1]) * k / steps)
            if any(inside(p, ring) for ring in blocking_rings):
                return True
    return False


def elevation_xy(xy):
    return elevation(*to_lonlat(xy))


def propose_exit(zone, runs, blocking_rings, cliff_lines):
    ring = [to_xy(p) for p in zone["geometry"]["coordinates"][0]]
    options = []
    for run in runs:
        for part in run_parts(run):
            if len(part) < 2:
                continue
            distance, zone_point, run_point, segment, t = nearest_join(ring, part)
            if distance <= MAX_EXIT_DISTANCE_M:
                options.append((distance, run, part, zone_point, run_point, segment, t))

    for distance, run, part, zone_point, run_point, segment, t in sorted(options, key=lambda o: o[0]):
        climb = elevation_xy(run_point) - elevation_xy(zone_point)
        if climb > MAX_CLIMB_M:
            continue
        # Follow the run whichever way goes downhill, as long as it stays out of other zones.
        directions = [walk(part, segment, t, forward, EXIT_RUN_LENGTH_M) for forward in (True, False)]
        directions.sort(key=lambda points: elevation_xy(points[-1]))
        for along_run in directions:
            if len(along_run) < 2 or elevation_xy(along_run[-1]) >= elevation_xy(along_run[0]):
                continue
            path = [zone_point] + along_run
            if crosses_zone(path, blocking_rings, cliff_lines):
                continue
            return run, path, distance, climb
    return None


def main():
    runs = [f for f in json.loads(RUNS_FILE.read_text())["features"] if is_easier(f["properties"])]
    zones = json.loads(ZONES_FILE.read_text())["features"]

    # The original OSM cliff lines behind the cliff zones. An exit must never cross one.
    osm_hazards = {f["properties"]["osm_id"]: f for f in json.loads(OSM_HAZARDS_FILE.read_text())["features"]}
    cliff_lines = []
    for zone in zones:
        if zone["properties"]["type"] == "cliff":
            cliff_lines.extend(run_parts(osm_hazards[zone["properties"]["osm_id"]]))

    features = []
    for zone in zones:
        zone_id = zone["properties"]["id"]
        # The exit path may start inside its own zone, but must not cross any other cliff or closure.
        blocking_rings = [
            [to_xy(p) for p in other["geometry"]["coordinates"][0]]
            for other in zones
            if other is not zone and other["properties"]["type"] in ("cliff", "closure")
        ]
        proposal = propose_exit(zone, runs, blocking_rings, cliff_lines)
        if proposal is None:
            print(f"no exit: {zone_id}")
            continue
        run, path, distance, climb = proposal
        p = run["properties"]
        run_name = p.get("name") or f"unnamed {p['piste:difficulty']} run"
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [to_lonlat(xy) for xy in path]},
            "properties": {
                "id": f"{zone_id}-exit",
                "from_zone": zone_id,
                "name": f"Exit to {run_name}",
                "exit_osm_id": p["osm_id"],
                "exit_difficulty": p["piste:difficulty"],
                "distance_to_run_m": round(distance),
                "climb_m": round(climb),
                "descent_m": round(elevation_xy(path[1]) - elevation_xy(path[-1])),
            },
        })

    EXITS_FILE.write_text(json.dumps({"type": "FeatureCollection", "features": features}, indent=1))
    print(f"wrote {len(features)} exits to {EXITS_FILE.relative_to(DATA_DIR.parent)}\n")
    print(json.dumps([{k: f["properties"][k] for k in ("id", "from_zone", "name")} for f in features], indent=2))


if __name__ == "__main__":
    main()
