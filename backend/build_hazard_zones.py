"""Build data/hazard_zones.geojson from the cached OSM data near Spanky's Ladder.

Run after fetch_osm.py:

    python backend/build_hazard_zones.py

Each zone comes from one OSM way. Ids and names live in ZONES below so they
stay the same across reruns. Names come from the nearest named OSM runs.
Cliff lines are buffered into polygons so the geofence can test them.
The script also prints the zone list as { id, type, name } JSON.
"""

import json
import math
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RUNS_FILE = DATA_DIR / "blackcomb_runs.geojson"
OSM_HAZARDS_FILE = DATA_DIR / "osm_hazards.geojson"
ZONES_FILE = DATA_DIR / "hazard_zones.geojson"

# Same radius the map uses to pick OSM hazards near Spanky's Ladder.
HAZARD_RADIUS_M = 500

# Half width of the polygon drawn around a cliff line or the ladder.
CLIFF_BUFFER_M = 20
ENTRANCE_BUFFER_M = 25

# OSM way -> (id, type, name). Types: cliff, closure, expert_terrain, boundary.
# No boundary zone: the ski area boundary is about 1.5 km from the Ladder.
ZONES = {
    "way/461517641": ("spankys-ladder-entrance", "expert_terrain", "Spanky's Ladder entrance"),
    "way/1174298878": ("spankys-closure-1", "closure", "Permanently closed area beside Spanky's Ladder, toward Showcase Bowl"),
    "way/1174298879": ("chimney-ladder-closure-1", "closure", "Permanently closed area by Chimney Ladder and the Grey Zone"),
    "way/1172043856": ("spankys-cliff-band-1", "cliff", "Cliff band at the Spanky's entrance, along the Ruby Bowl Traverse"),
    "way/1167734553": ("spankys-cliff-band-2", "cliff", "Cliff band between Spanky's Chute and Spanky's Shoulder"),
    "way/1167734552": ("ruby-cliff-band-1", "cliff", "Long cliff band through Ruby Bowl"),
    "way/1167734535": ("garnet-cliff-band-1", "cliff", "Cliff band in Garnet Bowl Center"),
    "way/1167734536": ("garnet-cliff-band-2", "cliff", "Cliff band between Garnet Bowl Center and Diamond Centre"),
    "way/1167734539": ("diamond-cliff-band-1", "cliff", "Cliff band between Wild Thing and Diamond Centre"),
    "way/1167734544": ("diamond-cliff-band-2", "cliff", "Cliff at the Helter Skelter entrance to Diamond Bowl"),
    "way/1167734540": ("diamond-cliff-band-3", "cliff", "Cliff band beside Wild Thing"),
    "way/1167734537": ("diamond-cliff-band-4", "cliff", "Cliff band near Zut Zut"),
    "way/1167734547": ("diamond-cliff-band-5", "cliff", "Cliff band along the Bad Attitude Traverse"),
    "way/1167734558": ("sapphire-cliff-band-1", "cliff", "Cliff band in Sapphire Bowl by the Sapphire Chutes"),
    "way/1167734559": ("showcase-cliff-band-1", "cliff", "Cliff band along Showcase and the Blackcomb Glacier Traverse"),
}

ENTRANCE_OSM_ID = "way/461517641"


def segment_intersection(a, b, c, d):
    """Point where segments a-b and c-d cross, or None."""
    denom = (b[0] - a[0]) * (d[1] - c[1]) - (b[1] - a[1]) * (d[0] - c[0])
    if denom == 0:
        return None
    t = ((c[0] - a[0]) * (d[1] - c[1]) - (c[1] - a[1]) * (d[0] - c[0])) / denom
    u = ((c[0] - a[0]) * (b[1] - a[1]) - (c[1] - a[1]) * (b[0] - a[0])) / denom
    if 0 < t < 1 and 0 < u < 1:
        return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    return None


def remove_loops(side):
    """Cut out the small loops an offset line makes on the inside of sharp bends.

    A looped polygon can read as a hole in a point-in-polygon test, which would
    let a skier inside a cliff zone look like they are outside it.
    """
    points = list(side)
    i = 0
    while i < len(points) - 1:
        for j in range(len(points) - 2, i + 1, -1):
            crossing = segment_intersection(points[i], points[i + 1], points[j], points[j + 1])
            if crossing:
                points[i + 1:j + 1] = [crossing]
                break
        i += 1
    return points


def buffer_line(coords, half_width_m):
    """Polygon ring around a line: offset both sides, rounded ends. Plain Python, no shapely."""
    lat0 = coords[0][1]
    meters_per_deg_lon = 111320 * math.cos(math.radians(lat0))
    meters_per_deg_lat = 111320
    points = [((lon - coords[0][0]) * meters_per_deg_lon, (lat - lat0) * meters_per_deg_lat) for lon, lat in coords]

    def unit_normal(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy) or 1
        return -dy / length, dx / length

    # Normal at each vertex is the average of the neighbouring segment normals.
    normals = []
    for i in range(len(points)):
        before = unit_normal(points[i - 1], points[i]) if i > 0 else None
        after = unit_normal(points[i], points[i + 1]) if i < len(points) - 1 else None
        if before and after:
            nx, ny = before[0] + after[0], before[1] + after[1]
            length = math.hypot(nx, ny) or 1
            normals.append((nx / length, ny / length))
        else:
            normals.append(before or after)

    left = remove_loops([(x + nx * half_width_m, y + ny * half_width_m) for (x, y), (nx, ny) in zip(points, normals)])
    right = remove_loops([(x - nx * half_width_m, y - ny * half_width_m) for (x, y), (nx, ny) in zip(points, normals)])

    def cap(center, normal):
        # Half circle from the left side around the end to the right side.
        start = math.atan2(normal[1], normal[0])
        return [
            (center[0] + half_width_m * math.cos(start - math.pi * k / 8),
             center[1] + half_width_m * math.sin(start - math.pi * k / 8))
            for k in range(1, 8)
        ]

    end_cap = cap(points[-1], normals[-1])
    start_cap = cap(points[0], (-normals[0][0], -normals[0][1]))
    ring = left + end_cap + right[::-1] + start_cap
    ring.append(ring[0])
    return [[coords[0][0] + x / meters_per_deg_lon, lat0 + y / meters_per_deg_lat] for x, y in ring]


def zone_polygon(feature, half_width_m):
    geometry = feature["geometry"]
    if geometry["type"] == "Polygon":
        return geometry["coordinates"]
    if geometry["type"] == "LineString":
        return [buffer_line(geometry["coordinates"], half_width_m)]
    raise ValueError(f"{feature['properties']['osm_id']}: cannot buffer {geometry['type']}")


def zone_feature(osm_feature, half_width_m):
    osm_id = osm_feature["properties"]["osm_id"]
    zone_id, zone_type, name = ZONES[osm_id]
    properties = {"id": zone_id, "type": zone_type, "name": name, "osm_id": osm_id}
    if "dist_to_spankys_m" in osm_feature["properties"]:
        properties["dist_to_spankys_m"] = osm_feature["properties"]["dist_to_spankys_m"]
    geometry = {"type": "Polygon", "coordinates": zone_polygon(osm_feature, half_width_m)}
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def main():
    runs = {f["properties"]["osm_id"]: f for f in json.loads(RUNS_FILE.read_text())["features"]}
    hazards = json.loads(OSM_HAZARDS_FILE.read_text())["features"]

    features = [zone_feature(runs[ENTRANCE_OSM_ID], ENTRANCE_BUFFER_M)]
    for hazard in sorted(hazards, key=lambda f: f["properties"]["dist_to_spankys_m"]):
        p = hazard["properties"]
        if p["kind"] not in ("cliff", "closed") or p["dist_to_spankys_m"] > HAZARD_RADIUS_M:
            continue
        if p["osm_id"] not in ZONES:
            # A refetch found a new hazard. Give it an id and name in ZONES.
            print(f"WARNING: {p['osm_id']} ({p['kind']}, {p['dist_to_spankys_m']} m) has no entry in ZONES, skipped")
            continue
        features.append(zone_feature(hazard, CLIFF_BUFFER_M))

    missing = set(ZONES) - {f["properties"]["osm_id"] for f in features}
    for osm_id in sorted(missing):
        print(f"WARNING: {osm_id} ({ZONES[osm_id][0]}) is in ZONES but not in the OSM data")

    collection = {"type": "FeatureCollection", "features": features}
    ZONES_FILE.write_text(json.dumps(collection, indent=1))
    print(f"wrote {len(features)} zones to {ZONES_FILE.relative_to(DATA_DIR.parent)}\n")

    summary = [{k: f["properties"][k] for k in ("id", "type", "name")} for f in features]
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
