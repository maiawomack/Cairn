"""Merge the agents' outputs with the code-owned geometry into data/hazard_map.json.

    python backend/hazard_map.py    rebuild from data/agent_outputs/ (no credits spent)

The merge owns the hazard map schema in CLAUDE.md. Rules it enforces:
  - every zone in hazard_zones.geojson stays in the map; agents only annotate
  - a cliff stays active even if an agent marks it inactive
  - geometry comes only from the geojson files
  - off-limits or reroute entries naming unknown zone or exit ids are dropped
"""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ZONES_FILE = DATA_DIR / "hazard_zones.geojson"
EXITS_FILE = DATA_DIR / "safe_exits.geojson"
SAFE_LINES_FILE = DATA_DIR / "safe_lines.geojson"
OSM_HAZARDS_FILE = DATA_DIR / "osm_hazards.geojson"
OUTPUTS_DIR = DATA_DIR / "agent_outputs"
HAZARD_MAP_FILE = DATA_DIR / "hazard_map.json"
CACHED_FILE = DATA_DIR / "hazard_map.cached.json"


# Used only when the Terrain Scout leaves a zone out, so the zone still warns.
DEFAULT_ANNOTATION = {"severity": "high", "applies_to": ["beginner", "intermediate"], "hint": None}


def load_zones():
    return json.loads(ZONES_FILE.read_text())["features"]


def load_exits():
    return json.loads(EXITS_FILE.read_text())["features"]


def load_safe_lines():
    return json.loads(SAFE_LINES_FILE.read_text())["features"] if SAFE_LINES_FILE.exists() else []


def zone_list():
    """Terrain Scout input: [{id, type, name}]."""
    return [{k: z["properties"][k] for k in ("id", "type", "name")} for z in load_zones()]


def exit_list():
    """[{id, from_zone, name}] for the code-owned safe exits."""
    return [{k: e["properties"][k] for k in ("id", "from_zone", "name")} for e in load_exits()]


def safe_line_list():
    """Route Planner input (safe_lines_json): [{id, name, bowl, for_zones}]."""
    return [{k: f["properties"][k] for k in ("id", "name", "bowl", "for_zones")} for f in load_safe_lines()]


def zone_types():
    """Route Planner input: {zone id: type}."""
    return {z["properties"]["id"]: z["properties"]["type"] for z in load_zones()}


def latlon(coordinates):
    return [[lat, lon] for lon, lat in coordinates]


def build(conditions_output, terrain_output, route_output, creao_runs, warn=print):
    """Return the merged hazard map dict. warn(message) reports anything dropped or defaulted."""
    zones = load_zones()
    exits = load_exits()
    safe_lines = load_safe_lines()
    safe_line_for = {zone_id: f["properties"]["id"] for f in safe_lines for zone_id in f["properties"]["for_zones"]}
    cliff_lines = {}
    for f in json.loads(OSM_HAZARDS_FILE.read_text())["features"]:
        g = f["geometry"]
        parts = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"] if g["type"] == "MultiLineString" else []
        cliff_lines[f["properties"]["osm_id"]] = [latlon(part) for part in parts]
    zone_ids = {z["properties"]["id"] for z in zones}

    annotations = {}
    for annotation in terrain_output.get("zones", []):
        if annotation.get("id") in zone_ids:
            annotations[annotation["id"]] = annotation
        else:
            warn(f"Terrain Scout zone {annotation.get('id')!r} is not in hazard_zones.geojson, ignored")

    merged_zones = []
    for zone in zones:
        p = zone["properties"]
        annotation = annotations.get(p["id"])
        if annotation is None:
            warn(f"Terrain Scout left out {p['id']}, kept with default annotation")
            annotation = DEFAULT_ANNOTATION
        active = annotation.get("active", True)
        if p["type"] == "cliff" and active is False:
            warn(f"Terrain Scout marked cliff {p['id']} inactive, kept active")
            active = True
        merged_zones.append({
            "id": p["id"],
            "type": p["type"],
            "name": p["name"],
            "active": active,
            "severity": annotation.get("severity", DEFAULT_ANNOTATION["severity"]),
            "applies_to": annotation.get("applies_to", DEFAULT_ANNOTATION["applies_to"]),
            "hint": annotation.get("hint"),
            "briefing": annotation.get("briefing"),  # Terrain Scout v1.1: spoken entry briefing
            "annotated_by": "terrain_scout" if p["id"] in annotations else "default",
            # Expert zones: the hazards to name in the entry briefing. Cliffs: the safe line to name in guidance.
            "hazards_inside": p.get("hazards_inside", []),
            "safe_line": safe_line_for.get(p["id"]),
            # Cliffs: the OSM cliff line(s), drawn so the drop is on the right (geofence.py).
            "cliff_lines": cliff_lines.get(p["osm_id"]) if p["type"] == "cliff" else None,
            "polygon": latlon(zone["geometry"]["coordinates"][0]),
        })

    # Route Planner (safe_lines_json input): a recommended line per bowl, spoken guidance per
    # cliff, and spoken hard limits per closure. Unknown zone or line ids are dropped.
    line_ids = {f["properties"]["id"] for f in safe_lines}
    bowl_ids = {z["properties"]["name"]: z["properties"]["id"] for z in zones if z["properties"]["type"] == "expert_terrain"}
    route = {"bowls": [], "cliff_guidance": [], "hard_limits": []}
    for bowl in route_output.get("bowls") or []:
        zone_id = bowl_ids.get(bowl.get("bowl_id"), bowl.get("bowl_id"))  # the agent may give the bowl's name or id
        if zone_id not in zone_ids or bowl.get("recommended_line_id") not in line_ids:
            warn(f"Route Planner bowl {bowl.get('bowl_id')!r} -> {bowl.get('recommended_line_id')!r} names an unknown bowl or line, dropped")
            continue
        route["bowls"].append({"zone_id": zone_id, "recommended_line_id": bowl["recommended_line_id"],
                               "why": bowl.get("why"), "line_note": short_note(bowl.get("line_note"), warn)})
    for guidance in route_output.get("cliff_guidance") or []:
        line_id = guidance.get("line_id")
        if guidance.get("zone_id") not in zone_ids or (line_id is not None and line_id not in line_ids):
            warn(f"Route Planner cliff guidance {guidance.get('zone_id')!r} -> {line_id!r} names an unknown zone or line, dropped")
            continue
        route["cliff_guidance"].append({k: guidance.get(k) for k in ("zone_id", "line_id", "spoken")}
                                       | {"line_note": short_note(guidance.get("line_note"), warn)})

    # A bowl's recommended line is the line for every cliff it covers (its for_zones), so the
    # guidance never contradicts the recommendation.
    covers = {f["properties"]["id"]: set(f["properties"]["for_zones"]) for f in safe_lines}
    recommended = {zone: b["recommended_line_id"] for b in route["bowls"] for zone in covers[b["recommended_line_id"]]}
    for guidance in route["cliff_guidance"]:
        line_id = recommended.get(guidance["zone_id"])
        if line_id and guidance["line_id"] != line_id:
            if guidance["line_id"] is not None:
                warn(f"Route Planner gave {guidance['zone_id']} line {guidance['line_id']!r}; its bowl recommends {line_id!r}, used that")
            guidance["line_id"] = line_id
            guidance["line_id_from"] = "bowl recommendation"
    for limit in route_output.get("hard_limits") or []:
        if limit.get("zone_id") not in zone_ids:
            warn(f"Route Planner hard limit {limit.get('zone_id')!r} names an unknown zone, dropped")
            continue
        route["hard_limits"].append({k: limit.get(k) for k in ("zone_id", "spoken")})

    conditions = conditions_output["conditions"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # False until the Route Planner has produced its route output (the demo copy waits for it).
        "complete": bool(route["bowls"] or route["cliff_guidance"]),
        "date": conditions.get("date"),
        "creao_run_link": None,
        "creao_runs": creao_runs,
        "conditions": conditions,
        "planner_notes": conditions_output.get("planner_notes"),
        "zones": merged_zones,
        "safe_lines": [
            {k: f["properties"][k] for k in ("id", "name", "bowl")} | {"path": latlon(f["geometry"]["coordinates"])}
            for f in safe_lines
        ],
        "safe_exits": [
            {k: e["properties"][k] for k in ("id", "from_zone", "name")} | {"path": latlon(e["geometry"]["coordinates"])}
            for e in exits
        ],
        "route": route,
        "notes_for_incident_responder": route_output.get("notes_for_incident_responder"),
    }


LINE_NOTE_MAX_WORDS = 10


def short_note(note, warn):
    """A Route Planner line_note, kept only if it fits LINE_NOTE_MAX_WORDS."""
    if not note:
        return None
    if len(note.split()) > LINE_NOTE_MAX_WORDS:
        warn(f"Route Planner line_note over {LINE_NOTE_MAX_WORDS} words, dropped: {note!r}")
        return None
    return note


def save(hazard_map, cached=True):
    """Write hazard_map.json and, for a complete chain, the demo copy hazard_map.cached.json."""
    HAZARD_MAP_FILE.write_text(json.dumps(hazard_map, indent=1))
    if cached:
        shutil.copyfile(HAZARD_MAP_FILE, CACHED_FILE)


def save_output(role, output):
    OUTPUTS_DIR.mkdir(exist_ok=True)
    (OUTPUTS_DIR / f"{role}.json").write_text(json.dumps(output, indent=1))


def load_output(role):
    return json.loads((OUTPUTS_DIR / f"{role}.json").read_text())


def main():
    runs_file = OUTPUTS_DIR / "creao_runs.json"
    creao_runs = json.loads(runs_file.read_text()) if runs_file.exists() else []
    complete = (OUTPUTS_DIR / "route.json").exists()
    if not complete:
        print("no Route Planner output yet: plans left empty, demo copy not written")
    route = load_output("route") if complete else {}
    hazard_map = build(load_output("conditions"), load_output("terrain"), route, creao_runs)
    save(hazard_map, cached=hazard_map["complete"])
    written = HAZARD_MAP_FILE.name + (f" and {CACHED_FILE.name}" if hazard_map["complete"] else "")
    print(f"wrote {written}: {len(hazard_map['zones'])} zones, {len(hazard_map['safe_exits'])} exits")


if __name__ == "__main__":
    main()
