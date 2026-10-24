"""Incidents for the simulated patrol console, and the Incident Responder's report.

SIMULATED: nothing here contacts ski patrol, 911 or anyone else. An incident is a record
in this process's memory (session only, no history, no files) that the dashboard's patrol
view shows. There is no phone, SMS or network code in this module.

The report on escalation is cached or simulated by default, so the demo never waits on a
Creao run or spends credits:
  - if data/incident_report.cached.json exists (saved output of a real Incident Responder
    run, {"report": "..."}), it is used and labeled as a cached Creao run;
  - otherwise a template fills in the real incident facts and today's conditions and
    hazards from the hazard map, labeled as simulated.
A live Creao run is a separate, explicit action (POST /api/incident/live-run); see main.py.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import geofence
from geofence import to_xy, distance_to_ring

CACHED_REPORT_FILE = Path(__file__).resolve().parent.parent / "data" / "incident_report.cached.json"
NEARBY_HAZARD_M = 60
NEAR_BOWL_M = 40

INCIDENTS = []  # escalated incidents only, this session only


def _where(position, hazard_map):
    """Which expert area the skier is in (or None), and the cliffs and closures within NEARBY_HAZARD_M."""
    xy = to_xy(*position)
    bowls, nearby = [], []
    for zone in hazard_map["zones"]:
        distance = distance_to_ring(xy, [to_xy(lat, lon) for lat, lon in zone["polygon"]])
        if zone["type"] == "expert_terrain" and zone["id"] != "spankys-ladder-entrance":
            bowls.append((distance, zone["name"]))
        if zone["type"] in ("cliff", "closure") and distance <= NEARBY_HAZARD_M:
            nearby.append((distance, zone["name"].split(",")[0]))
    area = None
    if bowls:
        distance, name = min(bowls)
        # Inside a bowl, or just outside its mapped outline (OSM outlines are rough).
        area = name if distance == 0 else f"near {name}" if distance <= NEAR_BOWL_M else None
    return area, [name for _, name in sorted(nearby)[:3]]


def create(position, peak_kmh, hazard_map, started_at):
    """The facts of a check-in. Not an incident yet: it only becomes one, and only then appears
    in the patrol console, if the check-in escalates (see register)."""
    area, nearby = _where(position, hazard_map)
    incident = {
        "id": None,
        "started_at": started_at,
        "location": {"lat": position[0], "lon": position[1], "area": area, "nearby_hazards": nearby},
        "last_speed_kmh": peak_kmh,
        "skier_said": None,
        "status": "countdown",
        "report": None,
        "simulated": True,
    }
    return incident


def register(incident):
    """An escalated check-in becomes an incident in the patrol console (session only)."""
    incident["id"] = len(INCIDENTS) + 1
    INCIDENTS.append(incident)
    return incident


def update(incident, **fields):
    incident.update(fields)
    return incident


def report_for(incident, hazard_map, reason):
    """(text, source label) for an escalated incident. Never calls Creao."""
    if CACHED_REPORT_FILE.exists():
        cached = json.loads(CACHED_REPORT_FILE.read_text())
        return cached["report"], "cached Creao Incident Responder run"
    c = hazard_map.get("conditions") or {}
    weather, avalanche = c.get("weather") or {}, c.get("avalanche") or {}
    rating = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in avalanche.items() if k in ("alpine", "treeline", "below_treeline") and v) \
        or "not available for this date"
    loc = incident["location"]
    started = datetime.fromisoformat(incident["started_at"]).astimezone().strftime("%H:%M:%S")
    lines = [
        f"Skier stopped suddenly at {started} after skiing at {incident['last_speed_kmh']} km/h"
        + (f" {loc['area']}" if loc["area"] and loc["area"].startswith("near") else f" in {loc['area']}" if loc["area"] else "")
        + f" ({loc['lat']:.5f}, {loc['lon']:.5f}).",
        f"Check-in: {reason}. No contact with patrol has been made; this is a simulated console.",
        f"Conditions ({c.get('date')}, {c.get('mode')}): {weather.get('temp_c')} °C, gusts {weather.get('wind_gust_kmh')} km/h, "
        f"{weather.get('new_snow_cm')} cm new snow. Avalanche rating: {rating}.",
    ]
    if loc["nearby_hazards"]:
        lines.append("Mapped hazards within 60 m: " + "; ".join(loc["nearby_hazards"]) + ".")
    notes = hazard_map.get("notes_for_incident_responder") or []
    if notes:
        lines.append("Route Planner notes: " + " ".join(notes))
    return "\n".join(lines), "simulated report (template, no Creao run)"
