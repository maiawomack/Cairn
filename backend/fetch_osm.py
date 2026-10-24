"""Fetch Blackcomb runs, lifts and hazards around Spanky's Ladder from OpenStreetMap.

Run by hand when you want fresh data:

    python backend/fetch_osm.py

It writes two cached files that the app reads. The app never calls Overpass.
    data/blackcomb_runs.geojson   runs (piste:type=*) and lifts (aerialway=*)
    data/osm_hazards.geojson      cliffs, closed areas, ski area boundary
"""

import json
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


def to_feature(way, kind):
    coords = [[p["lon"], p["lat"]] for p in way["geometry"]]
    tags = way.get("tags", {})
    closed = len(coords) > 3 and coords[0] == coords[-1]
    if closed and is_area(tags):
        geometry = {"type": "Polygon", "coordinates": [coords]}
    else:
        geometry = {"type": "LineString", "coordinates": coords}
    properties = {"osm_id": f"way/{way['id']}", "kind": kind, **tags}
    return {"type": "Feature", "geometry": geometry, "properties": properties}


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
        features.append(to_feature(way, kind))
    save(RUNS_FILE, features)

    hazards = overpass(build_query(['["natural"="cliff"]', '["landuse"="winter_sports"]'] + CLOSED_FILTERS))
    save(HAZARDS_FILE, [to_feature(way, hazard_kind(way["tags"])) for way in hazards])


if __name__ == "__main__":
    main()
