"""Fetch Blackcomb runs, lifts and hazards around Spanky's Ladder from OpenStreetMap.

Run by hand when you want fresh data:

    python backend/fetch_osm.py

It writes two cached files that the app reads, with every feature clipped to
BBOX. The app never calls Overpass.
    data/blackcomb_runs.geojson   runs (piste:type=*) and lifts (aerialway=*)
    data/osm_hazards.geojson      cliffs, closed areas, ski area boundary
"""

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Overpass rejects requests without a descriptive User-Agent (HTTP 406).
USER_AGENT = "Cairn-hackathon/0.1 (ski safety demo)"

# South, west, north, east. Covers Spanky's Ladder, the Glacier bowls
# (Garnet, Ruby, Sapphire, Diamond), the Blackcomb Glacier run down to
# Body Bag Bowl, and Glacier Express / Showcase on the west side.
BBOX = (50.075, -122.905, 50.112, -122.858)

# Midpoint of OSM way 461517641 (Spanky's Ladder), as (lat, lon).
SPANKYS_LADDER = (50.0977, -122.8811)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RUNS_FILE = DATA_DIR / "blackcomb_runs.geojson"
HAZARDS_FILE = DATA_DIR / "osm_hazards.geojson"

# Tags that mean a closed or off limits area. Only the first two appear in
# the box today; the rest are common OSM tagging and cost nothing to ask for.
CLOSED_FILTERS = [
    '["hazard"]',
    '["access"~"^(no|exclusion_zone|private)$"]',
    '["ski"="no"]',
    '["piste:status"="closed"]',
]


def build_query(statements):
    bbox = "({},{},{},{})".format(*BBOX)
    body = "".join(f"way{s}{bbox};" for s in statements)
    return f"[out:json][timeout:90];({body});out geom;"


def overpass(query, attempts=4):
    data = urllib.parse.urlencode({"data": query}).encode()
    request = urllib.request.Request(OVERPASS_URL, data=data, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)["elements"]
        except urllib.error.HTTPError as error:
            # Overpass answers busy or rate limited requests with 429 / 5xx.
            if error.code not in (429, 502, 503, 504) or attempt == attempts:
                raise
            wait = 15 * attempt
            print(f"Overpass returned {error.code}, retrying in {wait}s")
            time.sleep(wait)


def is_area(tags):
    if tags.get("area") == "yes":
        return True
    if "natural" in tags and tags["natural"] != "cliff":
        return True
    return any(k in tags for k in ("landuse", "hazard", "access"))


def clip_segment(a, b):
    """Liang-Barsky: the part of segment a-b inside BBOX, or None."""
    south, west, north, east = BBOX
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - west), (dx, east - a[0]), (-dy, a[1] - south), (dy, north - a[1])):
        if p == 0:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return None
    return [a[0] + t0 * dx, a[1] + t0 * dy], [a[0] + t1 * dx, a[1] + t1 * dy]


def clip_line(coords):
    """Clip a line to BBOX. Returns a list of parts, since a line can leave and re-enter."""
    parts, current = [], []
    for a, b in zip(coords, coords[1:]):
        segment = clip_segment(a, b)
        if segment is None:
            if current:
                parts.append(current)
            current = []
            continue
        start, end = segment
        if current and current[-1] == start:
            current.append(end)
        else:
            if current:
                parts.append(current)
            current = [start, end]
    if current:
        parts.append(current)
    return parts


def clip_ring(ring):
    """Sutherland-Hodgman: clip a closed polygon ring to BBOX, or None if nothing is left."""
    south, west, north, east = BBOX
    # (axis, limit, keep points with value >= limit?) with axis 0 = lon, 1 = lat
    edges = [(0, west, True), (0, east, False), (1, south, True), (1, north, False)]
    points = ring[:-1]
    for axis, limit, keep_greater in edges:
        def inside(p):
            return p[axis] >= limit if keep_greater else p[axis] <= limit

        def crossing(p, q):
            t = (limit - p[axis]) / (q[axis] - p[axis])
            return [p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])]

        clipped = []
        for i, current in enumerate(points):
            previous = points[i - 1]
            if inside(current):
                if not inside(previous):
                    clipped.append(crossing(previous, current))
                clipped.append(current)
            elif inside(previous):
                clipped.append(crossing(previous, current))
        points = clipped
        if not points:
            return None
    if len(points) < 3:
        return None
    return points + [points[0]]


def to_feature(way, kind):
    """Build a GeoJSON feature clipped to BBOX, or None if it lies outside."""
    coords = [[p["lon"], p["lat"]] for p in way["geometry"]]
    tags = way.get("tags", {})
    closed = len(coords) > 3 and coords[0] == coords[-1]
    if closed and is_area(tags):
        ring = clip_ring(coords)
        if ring is None:
            return None
        geometry = {"type": "Polygon", "coordinates": [ring]}
    else:
        parts = clip_line(coords)
        if not parts:
            return None
        if len(parts) == 1:
            geometry = {"type": "LineString", "coordinates": parts[0]}
        else:
            geometry = {"type": "MultiLineString", "coordinates": parts}
    properties = {"osm_id": f"way/{way['id']}", "kind": kind, **tags}
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def distance_to_spankys_m(feature):
    """Shortest distance in meters from Spanky's Ladder to the feature's edges."""
    lat0, lon0 = SPANKYS_LADDER
    meters_per_deg_lat = 111320
    meters_per_deg_lon = 111320 * math.cos(math.radians(lat0))

    def local(p):
        return ((p[0] - lon0) * meters_per_deg_lon, (p[1] - lat0) * meters_per_deg_lat)

    geometry = feature["geometry"]
    if geometry["type"] == "LineString":
        lines = [geometry["coordinates"]]
    else:
        lines = geometry["coordinates"]  # MultiLineString parts or Polygon rings

    best = float("inf")
    for line in lines:
        for a, b in zip(line, line[1:]):
            (ax, ay), (bx, by) = local(a), local(b)
            length_sq = (bx - ax) ** 2 + (by - ay) ** 2
            t = 0 if length_sq == 0 else max(0, min(1, -(ax * (bx - ax) + ay * (by - ay)) / length_sq))
            best = min(best, math.hypot(ax + t * (bx - ax), ay + t * (by - ay)))
    return round(best)


def hazard_kind(tags):
    if tags.get("natural") == "cliff":
        return "cliff"
    if tags.get("landuse") == "winter_sports":
        return "ski_area_boundary"
    return "closed"


def save(path, features):
    south, west, north, east = BBOX
    collection = {
        "type": "FeatureCollection",
        "bbox": [west, south, east, north],
        "features": features,
    }
    # Write to a temp file first so a failed run never leaves a half written cache.
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(collection, indent=1))
    tmp.replace(path)
    print(f"wrote {len(features)} features to {path.relative_to(DATA_DIR.parent)}")


def main():
    DATA_DIR.mkdir(exist_ok=True)

    runs_and_lifts = overpass(build_query(['["piste:type"]', '["aerialway"]']))
    features = []
    for way in runs_and_lifts:
        kind = "lift" if "aerialway" in way.get("tags", {}) else "run"
        feature = to_feature(way, kind)
        if feature:
            features.append(feature)
    save(RUNS_FILE, features)

    hazards = overpass(build_query(['["natural"="cliff"]', '["landuse"="winter_sports"]'] + CLOSED_FILTERS))
    features = []
    for way in hazards:
        feature = to_feature(way, hazard_kind(way["tags"]))
        if feature:
            feature["properties"]["dist_to_spankys_m"] = distance_to_spankys_m(feature)
            features.append(feature)
    save(HAZARDS_FILE, features)


if __name__ == "__main__":
    main()
