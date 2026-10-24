"""Geofence: smooth the GPS, then check distance and heading to every hazard zone.

    python backend/geofence.py    run scenario 1 and 2 through the geofence and report

For each fix the skier's position, speed and heading come from a straight line fitted
through the last few fixes (no lag, and a single jumpy point barely moves it). Cairn
serves advanced and expert skiers (CLAUDE.md):
  - entering expert terrain gives a calm briefing, never a warning
  - a cliff on the skier's trajectory gives guidance (hazard + safe line); inside the
    cliff zone or within CLIFF_STOP_M it becomes a "stop" warning
  - closures and the boundary are hard warnings
A zone is "on trajectory" when the skier is within a near distance of it, or heading so
that the path ahead reaches it within the speed scaled look ahead. Tuning is in config.py.

Hard rule (CLAUDE.md): a cliff zone hit always alerts. This module decides that a zone
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


def to_latlon(xy):
    return round(LAT0 + xy[1] / M_PER_DEG_LAT, 7), round(LON0 + xy[0] / M_PER_DEG_LON, 7)


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
    """How each active zone alerts this skier: ({zone id: "briefing" | "cliff" | "warning"}, source).

    Cairn serves advanced and expert skiers in the Glacier bowls (CLAUDE.md), so the zone
    type decides, for every ability:
      - expert terrain: a calm briefing on entry, never a warning
      - cliff: guidance on trajectory, a "stop" warning only inside the zone or within
        CLIFF_STOP_M (decided per fix in update())
      - closure and boundary: hard warnings
    Agents can't switch any of these off; the Route Planner's plan only supplies wording.
    """
    by_type = {"expert_terrain": "briefing", "cliff": "cliff", "closure": "warning", "boundary": "warning"}
    levels = {z["id"]: by_type[z["type"]] for z in hazard_map["zones"]
              if z.get("active", True) and z["type"] in by_type}
    return levels, "zone type"


class Geofence:
    """Feed GPS fixes in order with update(); it returns the zones that newly alert."""

    def __init__(self, hazard_map, ability):
        self.ability = ability
        levels, self.source = alert_levels(hazard_map, ability)
        self.zones = [
            {**z, "level": levels[z["id"]], "ring": [to_xy(lat, lon) for lat, lon in z["polygon"]],
             "lines": [[to_xy(lat, lon) for lat, lon in line] for line in z.get("cliff_lines") or []]}
            for z in hazard_map["zones"]
            if z["id"] in levels
        ]
        self.fixes = deque(maxlen=config.GEOFENCE_SMOOTHING_FIXES)
        self.streak = {}  # zone id -> consecutive fixes in range
        self.sent = set()  # (zone id, level) already alerted
        self.rejected_in_a_row = 0
        self.last_warning = None  # (t, zone id) of the latest warning
        # The safe line each cliff alert offers, and the lines being watched for "on_line".
        self.lines_offered = {z["id"]: offered_line(hazard_map, z) for z in hazard_map["zones"] if z["type"] == "cliff"}
        self.watching = {}
        self.last_rejected = None  # why the latest fix was left out of the fit, or None
        self.smoothed = None  # (lat, lon) of the smoothed position

    def update(self, fix):
        """fix: {t, lat, lon}. Returns new alerts.

        level is "briefing" (entered expert terrain), "guidance" (cliff on trajectory),
        "warning" (closure or boundary, or a cliff from inside or within CLIFF_STOP_M), or
        "on_line" (the skier reached the safe line a cliff alert offered).
        Each (zone, level) alerts once; a cliff can go from guidance to warning. For
        CLIFF_QUIET_AFTER_WARNING_S after any warning, guidance for other cliffs waits
        (closures and the boundary still speak).
        """
        x, y = to_xy(fix["lat"], fix["lon"])
        self.last_rejected = self._reject_reason(fix, (x, y))
        if self.last_rejected:
            # Left out of the fit, so nothing new can alert from it. For the dashboard, carry the
            # smoothed position forward along the last good fit instead of freezing it.
            if len(self.fixes) >= 3:
                (px, py), speed, heading = fit_motion(list(self.fixes))
                dt = fix["t"] - self.fixes[-1][0]
                self.smoothed = to_latlon((px + math.sin(math.radians(heading)) * speed * dt,
                                           py + math.cos(math.radians(heading)) * speed * dt))
            return []
        self.fixes.append((fix["t"], x, y))
        if len(self.fixes) >= 3:
            position, speed, heading = fit_motion(list(self.fixes))
        else:
            position, speed, heading = (x, y), 0.0, None

        self.smoothed = to_latlon(position)
        cliff_hits = self._cliff_hits(position, speed, heading)
        warnings = []
        for zone in self.zones:
            if zone["level"] == "cliff":
                warn_m = config.warning_distance_m(speed, "cliff")
                hit = cliff_hits.get(zone["id"])
                confirm = config.confirm_fixes("cliff")
            elif zone["level"] == "briefing":
                # Briefings are for entering the zone, not approaching it.
                warn_m = 0.0
                inside_now = distance_to_ring(position, zone["ring"]) == 0
                hit = {"reason": "entered", "distance_m": 0.0, "ahead_m": None} if inside_now else None
                confirm = config.GEOFENCE_CONFIRM_FIXES
            else:
                # Closures use a shorter look ahead and need a sustained heading (config.py).
                warn_m = config.warning_distance_m(speed, zone["type"])
                hit = self._check(zone, position, speed, heading, warn_m)
                confirm = config.confirm_fixes(zone["type"])
            self.streak[zone["id"]] = self.streak.get(zone["id"], 0) + 1 if hit else 0
            if not hit or self.streak[zone["id"]] < confirm:
                continue
            level = zone["level"]
            if level == "cliff":
                level = "warning" if hit["distance_m"] <= config.CLIFF_STOP_M else "guidance"
            if (zone["id"], level) in self.sent or (level == "guidance" and (zone["id"], "warning") in self.sent):
                continue
            if level == "guidance" and self.last_warning and self.last_warning[1] != zone["id"] \
                    and fix["t"] - self.last_warning[0] < config.CLIFF_QUIET_AFTER_WARNING_S:
                continue  # one urgent message at a time; it can still alert after the quiet period
            self.sent.add((zone["id"], level))
            if level == "warning":
                self.last_warning = (fix["t"], zone["id"])
            if level in ("guidance", "warning") and zone["type"] == "cliff":
                line = self.lines_offered.get(zone["id"])
                if line and line["id"] not in self.watching:
                    self.watching[line["id"]] = {**line, "zone_id": zone["id"], "streak": 0}
            warnings.append({
                "t": fix["t"],
                "level": level,
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
        warnings.extend(self._on_line(fix, position, speed, heading, cliff_hits))
        return warnings

    def _on_line(self, fix, position, speed, heading, cliff_hits):
        """Confirm once when the skier comes back onto the safe line a cliff alert offered: off
        the line since the alert, now holding on it, and no longer heading over that cliff."""
        confirmed = []
        for line_id, line in list(self.watching.items()):
            near = min(distance_to_ring(position, [a, b, a]) for a, b in zip(line["path"], line["path"][1:]))
            # Only a return counts: the skier must have been off the line since the alert.
            line["left"] = line.get("left", False) or near > config.ON_LINE_M
            settled = line["left"] and near <= config.ON_LINE_M and line["zone_id"] not in cliff_hits
            line["streak"] = line["streak"] + 1 if settled else 0
            if line["streak"] >= config.ON_LINE_CONFIRM_FIXES:
                del self.watching[line_id]
                # Back on a safe line: that cliff can alert again if the skier heads for it later.
                self.sent -= {(line["zone_id"], "guidance"), (line["zone_id"], "warning")}
                confirmed.append({
                    "t": fix["t"], "level": "on_line", "zone_id": line["zone_id"], "zone_type": "cliff",
                    "zone_name": line["name"], "line_id": line_id, "line_name": line["name"],
                    "reason": "on the safe line", "distance_m": round(near, 1), "ahead_m": None,
                    "speed_kmh": round(speed * 3.6, 1), "heading_deg": None if heading is None else round(heading),
                    "warning_distance_m": 0.0,
                })
        return confirmed

    def _reject_reason(self, fix, xy):
        """Why a fix should be left out of the fit, or None.

        Phones report a multipath burst as a short jump with degraded accuracy. A fix is
        left out when its reported accuracy is worse than GEOFENCE_MAX_ACCURACY_M, or when
        it lands more than GEOFENCE_JUMP_GATE_M from where the current fit predicts. Never
        blind for long: after GEOFENCE_JUMP_ACCEPT_FIXES left out in a row, the fixes are
        taken as real movement and the fit restarts from them.
        """
        reason = None
        if fix.get("accuracy_m") is not None and fix["accuracy_m"] > config.GEOFENCE_MAX_ACCURACY_M:
            reason = f"accuracy {fix['accuracy_m']} m"
        elif len(self.fixes) >= 3:
            (px, py), speed, heading = fit_motion(list(self.fixes))
            dt = fix["t"] - self.fixes[-1][0]
            predicted = (px + math.sin(math.radians(heading)) * speed * dt,
                         py + math.cos(math.radians(heading)) * speed * dt)
            jump = math.dist(predicted, xy)
            if jump > config.GEOFENCE_JUMP_GATE_M:
                reason = f"jump {jump:.0f} m"
        if reason is None:
            self.rejected_in_a_row = 0
            return None
        self.rejected_in_a_row += 1
        if self.rejected_in_a_row >= config.GEOFENCE_JUMP_ACCEPT_FIXES:
            self.rejected_in_a_row = 0
            self.fixes.clear()
            return None
        return reason

    def _cliff_hits(self, position, speed, heading):
        """Cliffs the skier could go over: {zone id: hit}.

        OSM draws cliffs with the drop on the right of the line's direction, so a cliff is
        only a hazard from its high (left) side. A cliff alerts when the path ahead, within
        the look ahead, crosses its line from high to low side (only the first one crossed),
        or when the skier is on its high side within CLIFF_EDGE_M of the edge. Rays at
        +-CLIFF_RAY_SPREAD_DEG around the heading catch a drift that turns hide.
        """
        warn_m = config.warning_distance_m(speed, "cliff")
        hits, first = {}, None
        moving = heading is not None and speed >= 0.5
        # Turns swing the smoothed heading, so cast rays a few degrees either side of it.
        ends = []
        if moving:
            for spread in (-config.CLIFF_RAY_SPREAD_DEG, 0, config.CLIFF_RAY_SPREAD_DEG):
                angle = math.radians(heading + spread)
                ends.append((position[0] + math.sin(angle) * warn_m, position[1] + math.cos(angle) * warn_m))
        for zone in self.zones:
            if zone["level"] != "cliff" or not zone["lines"]:
                continue
            edge, high_side = _edge_distance(position, zone["lines"])
            if high_side and edge <= config.CLIFF_EDGE_M:
                hits[zone["id"]] = {"reason": "at edge", "distance_m": distance_to_ring(position, zone["ring"]),
                                    "ahead_m": None}
            for end in ends:
                ahead = _crossing_ahead(position, end, zone["lines"])
                if ahead is not None and (first is None or ahead * warn_m < first[1]):
                    first = (zone, ahead * warn_m)
        if first and first[0]["id"] not in hits:
            zone, ahead_m = first
            hits[zone["id"]] = {"reason": "heading over", "distance_m": distance_to_ring(position, zone["ring"]),
                                "ahead_m": ahead_m}
        return hits

    def _check(self, zone, position, speed, heading, warn_m):
        distance = distance_to_ring(position, zone["ring"])
        if distance == 0:
            return {"reason": "inside", "distance_m": 0.0, "ahead_m": 0.0}
        if distance <= config.near_m(zone["type"]):
            return {"reason": "near", "distance_m": distance, "ahead_m": None}
        if distance > warn_m or heading is None or speed < 0.5:
            return None
        # Walk the path ahead along the smoothed heading.
        ux, uy = math.sin(math.radians(heading)), math.cos(math.radians(heading))
        step = PATH_STEP_M
        while step <= warn_m:
            ahead = (position[0] + ux * step, position[1] + uy * step)
            if distance_to_ring(ahead, zone["ring"]) <= config.path_margin_m(zone["type"]):
                return {"reason": "heading", "distance_m": distance, "ahead_m": step}
            step += PATH_STEP_M
        return None


def _side(p, a, b):
    """Positive when p is left of the direction a -> b."""
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def _edge_distance(p, lines):
    """Distance to the nearest point of a cliff's lines, and whether p is on the high (left) side there."""
    best, high = float("inf"), False
    for line in lines:
        for a, b in zip(line, line[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length_sq = dx * dx + dy * dy
            t = 0 if length_sq == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length_sq))
            d = math.hypot(a[0] + t * dx - p[0], a[1] + t * dy - p[1])
            if d < best:
                best, high = d, _side(p, a, b) > 0
    return best, high


def _crossing_ahead(start, end, lines):
    """Fraction along start -> end where the path first goes over a cliff line (high to low side), or None."""
    first = None
    for line in lines:
        for a, b in zip(line, line[1:]):
            s1, s2 = _side(start, a, b), _side(end, a, b)
            if not (s1 > 0 >= s2):  # must go from the high (left) side to the low side
                continue
            t1, t2 = _side(a, start, end), _side(b, start, end)
            if t1 * t2 > 0:
                continue
            fraction = s1 / (s1 - s2)
            if first is None or fraction < first:
                first = fraction
    return first


def offered_line(hazard_map, zone):
    """The safe line a cliff alert offers: the Route Planner's line for this cliff (its bowl's
    recommendation) if there is one, else the code-owned safe line. {id, name, path xy, line_note} or None."""
    route = hazard_map.get("route") or {}
    guidance = next((g for g in route.get("cliff_guidance", []) if g["zone_id"] == zone["id"]), {})
    line_id = guidance.get("line_id") or zone.get("safe_line")
    line = next((l for l in hazard_map.get("safe_lines", []) if l["id"] == line_id), None)
    if line is None:
        return None
    bowl = next((b for b in route.get("bowls", []) if b["recommended_line_id"] == line_id), {})
    return {"id": line["id"], "name": line["name"], "path": [to_xy(lat, lon) for lat, lon in line["path"]],
            "line_note": guidance.get("line_note") or bowl.get("line_note")}


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
