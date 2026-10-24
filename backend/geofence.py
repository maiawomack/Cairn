"""Geofence: smooth the GPS, then check distance and heading to every hazard zone.

    python backend/geofence.py    run scenario 1 and 2 through the geofence and report

For each fix the skier's position, speed and heading come from a straight line fitted
through the last few fixes (no lag, and a single jumpy point barely moves it). A zone
that is active and applies to the skier's ability warns when the skier is inside it,
within GEOFENCE_NEAR_M of it, or heading so that the path ahead reaches it within the
speed scaled warning distance. Tuning lives in config.py.

Hard rule (CLAUDE.md): a cliff zone hit always warns. This module decides that a zone
is close; Claude only chooses the words later.
"""

import json
import math
from collections import deque
from pathlib import Path

import config

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
HAZARD_MAP_FILE = DATA_DIR / "hazard_map.json"
TRACKS_DIR = DATA_DIR / "tracks"

# Local flat projection around Spanky's Ladder (meters).
LAT0, LON0 = 50.0977, -122.8811
M_PER_DEG_LAT = 111320
M_PER_DEG_LON = 111320 * math.cos(math.radians(LAT0))
PATH_STEP_M = 5


def to_xy(lat, lon):
    return ((lon - LON0) * M_PER_DEG_LON, (lat - LAT0) * M_PER_DEG_LAT)


def inside(p, ring):
    result = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > p[1]) != (y2 > p[1]) and p[0] < (x2 - x1) * (p[1] - y1) / (y2 - y1) + x1:
            result = not result
    return result


def distance_to_ring(p, ring):
    """Meters from p to the polygon (0 inside)."""
    if inside(p, ring):
        return 0.0
    best = float("inf")
    for a, b in zip(ring, ring[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length_sq = dx * dx + dy * dy
        t = 0 if length_sq == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length_sq))
        best = min(best, math.hypot(a[0] + t * dx - p[0], a[1] + t * dy - p[1]))
    return best


def fit_motion(fixes):
    """Least squares line through (t, x) and (t, y). Returns position at the last t, speed, heading."""
    n = len(fixes)
    t_mean = sum(f[0] for f in fixes) / n
    x_mean = sum(f[1] for f in fixes) / n
    y_mean = sum(f[2] for f in fixes) / n
    var_t = sum((f[0] - t_mean) ** 2 for f in fixes)
    vx = sum((f[0] - t_mean) * (f[1] - x_mean) for f in fixes) / var_t if var_t else 0.0
    vy = sum((f[0] - t_mean) * (f[2] - y_mean) for f in fixes) / var_t if var_t else 0.0
    t_last = fixes[-1][0]
    position = (x_mean + vx * (t_last - t_mean), y_mean + vy * (t_last - t_mean))
    speed = math.hypot(vx, vy)
    heading = (math.degrees(math.atan2(vx, vy)) + 360) % 360  # compass degrees, 0 = north
    return position, speed, heading


def alert_levels(hazard_map, ability):
    """Which zones alert this skier, and how: ({zone id: "warning" | "heads_up"}, source).

    With a Route Planner plan for the ability: off_limits zones warn; for advanced and
    expert, a reroute with exit_id null is a one time calm heads up. For beginner and
    intermediate, other reroute zones also warn, and an active cliff always warns even if
    the plan leaves it out (CLAUDE.md: no agent can make a cliff disappear).
    Until the Route Planner has run, every active zone whose applies_to has the ability warns.
    """
    zones = {z["id"]: z for z in hazard_map["zones"] if z.get("active", True)}
    plan = (hazard_map.get("plans") or {}).get(ability)
    if not hazard_map.get("complete") or not plan:
        return {zid: "warning" for zid, z in zones.items() if ability in z.get("applies_to", [])}, "applies_to"

    levels = {zid: "warning" for zid in plan.get("off_limits", []) if zid in zones}
    careful = ability in ("beginner", "intermediate")
    for reroute in plan.get("reroutes", []):
        zid = reroute.get("zone_id")
        if zid not in zones or zid in levels:
            continue
        if careful:
            levels[zid] = "warning"
        elif reroute.get("exit_id") is None:
            levels[zid] = "heads_up"
    if careful:
        for zid, zone in zones.items():
            if zone["type"] == "cliff":
                levels.setdefault(zid, "warning")
    return levels, "plan"


class Geofence:
    """Feed GPS fixes in order with update(); it returns the zones that newly alert."""

    def __init__(self, hazard_map, ability):
        self.ability = ability
        levels, self.source = alert_levels(hazard_map, ability)
        self.zones = [
            {**z, "level": levels[z["id"]], "ring": [to_xy(lat, lon) for lat, lon in z["polygon"]]}
            for z in hazard_map["zones"]
            if z["id"] in levels
        ]
        self.fixes = deque(maxlen=config.GEOFENCE_SMOOTHING_FIXES)
        self.streak = {}  # zone id -> consecutive fixes in warning range
        self.warned = set()

    def update(self, fix):
        """fix: {t, lat, lon}. Returns new alerts (one per zone, first time only).

        Each alert has level "warning" or "heads_up" (a calm, one time notice).
        """
        x, y = to_xy(fix["lat"], fix["lon"])
        self.fixes.append((fix["t"], x, y))
        if len(self.fixes) >= 3:
            position, speed, heading = fit_motion(list(self.fixes))
        else:
            position, speed, heading = (x, y), 0.0, None

        warnings = []
        for zone in self.zones:
            # Closures use a shorter look ahead and need a sustained heading (config.py).
            warn_m = config.warning_distance_m(speed, zone["type"])
            hit = self._check(zone, position, speed, heading, warn_m)
            self.streak[zone["id"]] = self.streak.get(zone["id"], 0) + 1 if hit else 0
            if hit and zone["id"] not in self.warned and self.streak[zone["id"]] >= config.confirm_fixes(zone["type"]):
                self.warned.add(zone["id"])
                warnings.append({
                    "t": fix["t"],
                    "level": zone["level"],
                    "zone_id": zone["id"],
                    "zone_type": zone["type"],
                    "zone_name": zone["name"],
                    "reason": hit["reason"],
                    "distance_m": round(hit["distance_m"], 1),
                    "ahead_m": None if hit["ahead_m"] is None else round(hit["ahead_m"], 1),
                    "speed_kmh": round(speed * 3.6, 1),
                    "heading_deg": None if heading is None else round(heading),
                    "warning_distance_m": round(warn_m, 1),
                })
        return warnings

    def _check(self, zone, position, speed, heading, warn_m):
        distance = distance_to_ring(position, zone["ring"])
        if distance == 0:
            return {"reason": "inside", "distance_m": 0.0, "ahead_m": 0.0}
        if distance <= config.GEOFENCE_NEAR_M:
            return {"reason": "near", "distance_m": distance, "ahead_m": None}
        if distance > warn_m or heading is None or speed < 0.5:
            return None
        # Walk the path ahead along the smoothed heading.
        ux, uy = math.sin(math.radians(heading)), math.cos(math.radians(heading))
        step = PATH_STEP_M
        while step <= warn_m:
            ahead = (position[0] + ux * step, position[1] + uy * step)
            if distance_to_ring(ahead, zone["ring"]) <= config.GEOFENCE_PATH_MARGIN_M:
                return {"reason": "heading", "distance_m": distance, "ahead_m": step}
            step += PATH_STEP_M
        return None


def load_hazard_map():
    return json.loads(HAZARD_MAP_FILE.read_text())


def run_track(track, hazard_map):
    """Run a whole track through a fresh geofence. Returns all alerts in order."""
    geofence = Geofence(hazard_map, track["ability"])
    warnings = []
    for fix in track["points"]:
        warnings.extend(geofence.update(fix))
    return warnings


def main():
    hazard_map = load_hazard_map()
    zones = hazard_map["zones"]
    osm = {f["properties"]["osm_id"]: f for f in json.loads((DATA_DIR / "osm_hazards.geojson").read_text())["features"]}

    for name in ("scenario_1", "scenario_2"):
        track = json.loads((TRACKS_DIR / f"{name}.json").read_text())
        truth = {p["t"]: p for p in track["truth"]}
        warnings = run_track(track, hazard_map)
        print(f"\n{name} ({track['ability']}): {track['description']}")
        print(f"  expected: {track['expected']}")
        print(f"  zones chosen by: {Geofence(hazard_map, track['ability']).source}")
        if not warnings:
            print("  no warnings")
        for w in warnings:
            zone = next(z for z in zones if z["id"] == w["zone_id"])
            true = truth[w["t"]]
            true_xy = to_xy(true["lat"], true["lon"])
            ring = [to_xy(lat, lon) for lat, lon in zone["polygon"]]
            line = f"  t={w['t']:4.0f} s  {w['level']:8} {w['zone_id']:26} ({w['reason']}, smoothed {w['distance_m']} m from zone, " \
                   f"{w['speed_kmh']} km/h, warn range {w['warning_distance_m']} m)"
            line += f"\n           truth: {distance_to_ring(true_xy, ring):.0f} m from zone edge"
            if zone["type"] == "cliff":
                cliff = next(z for z in json.loads((DATA_DIR / "hazard_zones.geojson").read_text())["features"]
                             if z["properties"]["id"] == zone["id"])["properties"]["osm_id"]
                coords = [to_xy(lat, lon) for lon, lat in osm[cliff]["geometry"]["coordinates"]]
                to_line = min(distance_to_ring(true_xy, [a, b, a]) for a, b in zip(coords, coords[1:]))
                speed = true["speed_kmh"] / 3.6
                stop_m = speed * 1.5 + speed ** 2 / (2 * 2.0)  # 1.5 s reaction, 2 m/s^2 braking on snow
                line += f", {to_line:.0f} m from the cliff line; stopping needs about {stop_m:.0f} m at {true['speed_kmh']} km/h"
                reach = next((p["t"] for p in track["truth"] if p["t"] >= w["t"] and
                              min(distance_to_ring(to_xy(p["lat"], p["lon"]), [a, b, a]) for a, b in zip(coords, coords[1:])) < 3), None)
                if reach is not None:
                    line += f"; reaches the cliff line at t={reach:.0f} s ({reach - w['t']:.0f} s after the warning)"
            print(line)


if __name__ == "__main__":
    main()
