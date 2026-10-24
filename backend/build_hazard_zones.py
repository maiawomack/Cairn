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

# The Glacier bowls below Spanky's Ladder, from their OSM ski area polygons. Entering one
# gives an advanced or expert skier a calm briefing, never a warning (CLAUDE.md).
BOWLS = {
    "way/1167734532": ("garnet-bowl", "expert_terrain", "Garnet Bowl"),
    "way/1167734534": ("ruby-bowl", "expert_terrain", "Ruby Bowl"),
    "way/1167734533": ("diamond-bowl", "expert_terrain", "Diamond Bowl"),
    "way/1167657607": ("sapphire-bowl", "expert_terrain", "Sapphire Bowl"),
}
ZONES.update(BOWLS)

# A cliff or closure this close to an expert zone counts as one of its main hazards
# (OSM cliffs are drawn along bowl edges, not inside the bowl polygons).
BRIEFING_HAZARD_RADIUS_M = 60

# Rock Alley (an intermediate run) runs through the Ladder entrance. Everything within
# this half width of it is cut out of spankys-ladder-entrance, so skiers on the run are
# outside the zone. 25 m matches the entrance buffer and covers turns plus GPS error.
RUN_CORRIDOR_OSM_ID = "way/461517643"
RUN_CORRIDOR_M = 25


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


def subtract_corridor(ring, line, half_width_m, cell_m=1.0):
    """Remove everything within half_width_m of `line` from the polygon `ring` (both lon/lat).

    Plain Python, no shapely: mark 1 m grid cells that are inside the ring and outside
    the corridor, trace the outline of the kept cells, and simplify it. Returns the
    kept rings (lon/lat, counterclockwise), largest first.
    """
    lon0, lat0 = ring[0]
    m_lon = 111320 * math.cos(math.radians(lat0))
    m_lat = 111320

    def local(p):
        return ((p[0] - lon0) * m_lon, (p[1] - lat0) * m_lat)

    poly = [local(p) for p in ring]
    path = [local(p) for p in line]
    min_x, max_x = min(x for x, _ in poly), max(x for x, _ in poly)
    min_y, max_y = min(y for _, y in poly), max(y for _, y in poly)
    nx, ny = int((max_x - min_x) / cell_m) + 1, int((max_y - min_y) / cell_m) + 1

    kept = set()
    for i in range(nx):
        for j in range(ny):
            p = (min_x + (i + 0.5) * cell_m, min_y + (j + 0.5) * cell_m)
            if point_in_ring(p, poly) and distance_to_line(p, path) > half_width_m:
                kept.add((i, j))

    # Directed outline edges with the kept cell on the left (counterclockwise outer rings).
    edges = {}
    for i, j in kept:
        for neighbour, start, end in (
            ((i, j - 1), (i, j), (i + 1, j)),
            ((i + 1, j), (i + 1, j), (i + 1, j + 1)),
            ((i, j + 1), (i + 1, j + 1), (i, j + 1)),
            ((i - 1, j), (i, j + 1), (i, j)),
        ):
            if neighbour not in kept:
                edges.setdefault(start, []).append(end)

    rings = []
    while edges:
        start = next(iter(edges))
        loop, current, previous = [start], start, None
        while True:
            options = edges[current]
            if len(options) > 1 and previous is not None:
                # Diagonal touch: turn left so the two cells stay separate rings.
                heading = (current[0] - previous[0], current[1] - previous[1])
                options.sort(key=lambda e: -(heading[0] * (e[1] - current[1]) - heading[1] * (e[0] - current[0])))
            nxt = options.pop(0)
            if not options:
                del edges[current]
            previous, current = current, nxt
            if current == start:
                break
            loop.append(current)
        points = [(min_x + i * cell_m, min_y + j * cell_m) for i, j in loop]
        if ring_area(points) > 0:  # holes (negative area) can't occur for a corridor crossing the zone
            rings.append(simplify(points, cell_m * 0.75))

    rings.sort(key=ring_area, reverse=True)
    return [[[lon0 + x / m_lon, lat0 + y / m_lat] for x, y in r + [r[0]]] for r in rings]


def point_in_ring(p, ring):
    result = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > p[1]) != (y2 > p[1]) and p[0] < (x2 - x1) * (p[1] - y1) / (y2 - y1) + x1:
            result = not result
    return result


def distance_to_line(p, line):
    best = float("inf")
    for a, b in zip(line, line[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length_sq = dx * dx + dy * dy
        t = 0 if length_sq == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length_sq))
        best = min(best, math.hypot(a[0] + t * dx - p[0], a[1] + t * dy - p[1]))
    return best


def ring_area(points):
    """Signed area (positive = counterclockwise). Points without the closing repeat."""
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1])) / 2


def simplify(points, tolerance):
    """Douglas-Peucker on a closed ring (points without the closing repeat)."""
    def dp(chain):
        if len(chain) < 3:
            return chain
        a, b = chain[0], chain[-1]
        far, far_d = 0, -1.0
        for k in range(1, len(chain) - 1):
            d = distance_to_line(chain[k], [a, b])
            if d > far_d:
                far, far_d = k, d
        if far_d <= tolerance:
            return [a, b]
        return dp(chain[:far + 1])[:-1] + dp(chain[far:])

    # Split the ring at its two farthest-apart points so both halves simplify well.
    split = max(range(len(points)), key=lambda k: math.dist(points[0], points[k]))
    first = dp(points[:split + 1])
    second = dp(points[split:] + [points[0]])
    return first[:-1] + second[:-1]


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


def add_briefing_hazards(features):
    """Give each expert zone `hazards_inside`: the cliff and closure zones in or along it, nearest first."""
    lat0 = 50.0977
    m_lon, m_lat = 111320 * math.cos(math.radians(lat0)), 111320

    def ring_xy(feature):
        return [((lon + 122.8811) * m_lon, (lat - lat0) * m_lat) for lon, lat in feature["geometry"]["coordinates"][0]]

    hazards = [f for f in features if f["properties"]["type"] in ("cliff", "closure")]
    for expert in (f for f in features if f["properties"]["type"] == "expert_terrain"):
        ring = ring_xy(expert)
        nearby = []
        for hazard in hazards:
            other = ring_xy(hazard)
            touching = any(point_in_ring(p, ring) for p in other) or any(point_in_ring(p, other) for p in ring)
            gap = 0 if touching else min(distance_to_line(p, ring) for p in other)
            if gap <= BRIEFING_HAZARD_RADIUS_M:
                nearby.append((gap, hazard["properties"]["id"]))
        expert["properties"]["hazards_inside"] = [zone_id for _, zone_id in sorted(nearby)]


def main():
    runs = {f["properties"]["osm_id"]: f for f in json.loads(RUNS_FILE.read_text())["features"]}
    hazards = json.loads(OSM_HAZARDS_FILE.read_text())["features"]

    entrance = zone_feature(runs[ENTRANCE_OSM_ID], ENTRANCE_BUFFER_M)
    # Rock Alley shares the Ladder's path for about 100 m, so cut the run out of the zone.
    pieces = subtract_corridor(entrance["geometry"]["coordinates"][0],
                               runs[RUN_CORRIDOR_OSM_ID]["geometry"]["coordinates"], RUN_CORRIDOR_M)
    if not pieces:
        raise ValueError("the Rock Alley corridor removed the whole spankys-ladder-entrance zone")
    if len(pieces) > 1:
        print(f"WARNING: spankys-ladder-entrance split into {len(pieces)} pieces; kept the largest")
    entrance["geometry"]["coordinates"] = [pieces[0]]
    features = [entrance]
    for hazard in sorted(hazards, key=lambda f: f["properties"]["dist_to_spankys_m"]):
        p = hazard["properties"]
        if p["kind"] not in ("cliff", "closed") or p["dist_to_spankys_m"] > HAZARD_RADIUS_M:
            continue
        if p["osm_id"] not in ZONES:
            # A refetch found a new hazard. Give it an id and name in ZONES.
            print(f"WARNING: {p['osm_id']} ({p['kind']}, {p['dist_to_spankys_m']} m) has no entry in ZONES, skipped")
            continue
        features.append(zone_feature(hazard, CLIFF_BUFFER_M))

    for osm_id in BOWLS:
        features.append(zone_feature(runs[osm_id], 0))
    add_briefing_hazards(features)

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
