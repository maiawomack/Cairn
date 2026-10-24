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
OUTPUTS_DIR = DATA_DIR / "agent_outputs"
HAZARD_MAP_FILE = DATA_DIR / "hazard_map.json"
CACHED_FILE = DATA_DIR / "hazard_map.cached.json"

ABILITIES = ("beginner", "intermediate", "advanced", "expert")

# Used only when the Terrain Scout leaves a zone out, so the zone still warns.
DEFAULT_ANNOTATION = {"severity": "high", "applies_to": ["beginner", "intermediate"], "hint": None}


def load_zones():
    return json.loads(ZONES_FILE.read_text())["features"]


def load_exits():
    return json.loads(EXITS_FILE.read_text())["features"]


def zone_list():
    """Terrain Scout input: [{id, type, name}]."""
    return [{k: z["properties"][k] for k in ("id", "type", "name")} for z in load_zones()]


def exit_list():
    """Route Planner input: [{id, from_zone, name}]."""
    return [{k: e["properties"][k] for k in ("id", "from_zone", "name")} for e in load_exits()]


def zone_types():
    """Route Planner input: {zone id: type}."""
    return {z["properties"]["id"]: z["properties"]["type"] for z in load_zones()}


def latlon(coordinates):
    return [[lat, lon] for lon, lat in coordinates]


def build(conditions_output, terrain_output, route_output, creao_runs, warn=print):
    """Return the merged hazard map dict. warn(message) reports anything dropped or defaulted."""
    zones = load_zones()
    exits = load_exits()
    zone_ids = {z["properties"]["id"] for z in zones}
    exit_ids = {e["properties"]["id"] for e in exits}

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
            "annotated_by": "terrain_scout" if p["id"] in annotations else "default",
            "polygon": latlon(zone["geometry"]["coordinates"][0]),
        })

    plans = {}
    for ability in ABILITIES:
        plan = (route_output.get("plans") or {}).get(ability) or {}
        off_limits = []
        for zone_id in plan.get("off_limits", []):
            if zone_id in zone_ids:
                off_limits.append(zone_id)
            else:
                warn(f"Route Planner {ability} off_limits names unknown zone {zone_id!r}, dropped")
        reroutes = []
        for reroute in plan.get("reroutes", []):
            # exit_id null is allowed: no exit, just a heads up (see geofence.py).
            exit_ok = reroute.get("exit_id") is None or reroute.get("exit_id") in exit_ids
            if reroute.get("zone_id") not in zone_ids or not exit_ok:
                warn(f"Route Planner {ability} reroute {reroute.get('zone_id')!r} -> {reroute.get('exit_id')!r} "
                     "names an unknown zone or exit, dropped")
                continue
            reroutes.append({k: reroute.get(k) for k in ("zone_id", "exit_id", "spoken")})
        plans[ability] = {"off_limits": off_limits, "reroutes": reroutes}

    conditions = conditions_output["conditions"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # False until the Route Planner has produced plans; the geofence then falls back to applies_to.
        "complete": bool(route_output.get("plans")),
        "date": conditions.get("date"),
        "creao_run_link": None,
        "creao_runs": creao_runs,
        "conditions": conditions,
        "planner_notes": conditions_output.get("planner_notes"),
        "zones": merged_zones,
        "safe_exits": [
            {k: e["properties"][k] for k in ("id", "from_zone", "name")} | {"path": latlon(e["geometry"]["coordinates"])}
            for e in exits
        ],
        "plans": plans,
        "notes_for_incident_responder": route_output.get("notes_for_incident_responder"),
    }


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
