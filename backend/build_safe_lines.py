"""Propose safe lines through each Glacier bowl from the OSM runs, for cliff guidance.

Run after build_hazard_zones.py:

    python backend/build_safe_lines.py

Cairn serves advanced and expert skiers, so guidance names a line through the bowl
("Move to the Garnet Bowl Left line") instead of an exit off the mountain. A run is a
safe line when it is an OSM downhill run lying mostly inside a bowl's polygon, it never
crosses a mapped cliff line, and it stays at least SAFE_LINE_CLEARANCE_M from every
cliff and closure zone. Each cliff zone is then given the nearest safe line it can reach
without the path crossing a cliff line.

Writes data/safe_lines.geojson (proposals: review them before relying on them) and
prints them as JSON.
"""

import json
import math
import re
from pathlib import Path

from build_safe_exits import segments_cross, to_xy
from dem import elevation
from geofence import distance_to_ring, inside

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RUNS_FILE = DATA_DIR / "blackcomb_runs.geojson"
ZONES_FILE = DATA_DIR / "hazard_zones.geojson"
OSM_HAZARDS_FILE = DATA_DIR / "osm_hazards.geojson"
LINES_FILE = DATA_DIR / "safe_lines.geojson"

SAFE_LINE_CLEARANCE_M = 5
IN_BOWL_SHARE = 0.5


def line_parts(feature):
    geometry = feature["geometry"]
    return [geometry["coordinates"]] if geometry["type"] == "LineString" else geometry["coordinates"]


def ring(feature):
    return [to_xy(p) for p in feature["geometry"]["coordinates"][0]]


def main():
    runs = json.loads(RUNS_FILE.read_text())["features"]
    zones = json.loads(ZONES_FILE.read_text())["features"]
    osm = {f["properties"]["osm_id"]: f for f in json.loads(OSM_HAZARDS_FILE.read_text())["features"]}

    bowls = [z for z in zones if z["properties"]["type"] == "expert_terrain" and z["properties"]["id"].endswith("-bowl")]
    hazards = {z["properties"]["id"]: ring(z) for z in zones if z["properties"]["type"] in ("cliff", "closure")}
    cliffs = [z for z in zones if z["properties"]["type"] == "cliff"]
    cliff_lines = [[to_xy(p) for p in part] for z in cliffs for part in line_parts(osm[z["properties"]["osm_id"]])]

    lines = []
    rejected = []
    for bowl in bowls:
        bowl_ring = ring(bowl)
        for run in runs:
            p = run["properties"]
            if run["geometry"]["type"] == "Polygon" or p.get("piste:type") != "downhill":
                continue
            for part in line_parts(run):
                pts = [to_xy(c) for c in part]
                if sum(inside(q, bowl_ring) for q in pts) / len(pts) < IN_BOWL_SHARE:
                    continue
                name = p.get("name", "(unnamed)")
                crosses = any(segments_cross(a, b, c, d) for a, b in zip(pts, pts[1:])
                              for cl in cliff_lines for c, d in zip(cl, cl[1:]))
                clearance, closest = min((min(distance_to_ring(q, hr) for q in pts), h) for h, hr in hazards.items())
                if crosses or clearance < SAFE_LINE_CLEARANCE_M:
                    why = "crosses a cliff line" if crosses else f"{clearance:.0f} m from {closest}"
                    rejected.append((bowl["properties"]["name"], name, why))
                    continue
                top, bottom = (part[0], part[-1]) if elevation(*part[0]) >= elevation(*part[-1]) else (part[-1], part[0])
                lines.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": part},
                    "properties": {
                        "id": re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") + "-line",
                        "name": name,
                        "bowl": bowl["properties"]["name"],
                        "osm_id": p["osm_id"],
                        "difficulty": p.get("piste:difficulty"),
                        "length_m": round(sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))),
                        "descent_m": round(elevation(*top) - elevation(*bottom)),
                        "clearance_m": round(clearance),
                        "closest_hazard": closest,
                        "for_zones": [],
                    },
                })

    # Recommend a line for each cliff: the nearest one in a bowl that borders the cliff,
    # reachable without crossing a cliff line. A cliff no bowl borders gets no line.
    bowls_of = {}
    for bowl in bowls:
        for zone_id in bowl["properties"].get("hazards_inside", []):
            bowls_of.setdefault(zone_id, set()).add(bowl["properties"]["name"])
    for cliff in cliffs:
        cid = cliff["properties"]["id"]
        options = []
        for line in lines:
            if line["properties"]["bowl"] not in bowls_of.get(cid, set()):
                continue
            pts = [to_xy(c) for c in line["geometry"]["coordinates"]]
            gap, a, b = min((math.dist(p, q), p, q) for p in hazards[cid] for q in pts)
            if not any(segments_cross(a, b, c, d) for cl in cliff_lines for c, d in zip(cl, cl[1:])):
                options.append((gap, line))
        if options:
            min(options, key=lambda o: o[0])[1]["properties"]["for_zones"].append(cid)

    LINES_FILE.write_text(json.dumps({"type": "FeatureCollection", "features": lines}, indent=1))
    print(f"wrote {len(lines)} safe lines to {LINES_FILE.relative_to(DATA_DIR.parent)}\n")
    print(json.dumps([{k: f["properties"][k] for k in ("id", "bowl", "name", "clearance_m", "for_zones")} for f in lines], indent=2))
    print("\nnot safe lines:")
    for bowl, name, why in rejected:
        print(f"  {bowl}: {name} ({why})")
    served = {z for f in lines for z in f["properties"]["for_zones"]}
    print("\ncliffs with no safe line:", sorted({c["properties"]["id"] for c in cliffs} - served) or "none")


if __name__ == "__main__":
    main()
