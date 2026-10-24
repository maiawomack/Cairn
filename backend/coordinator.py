"""Coordinator: turn a geofence alert into one spoken line.

    python backend/coordinator.py    run scenario 1 and 2's alerts through the coordinator

Cairn serves advanced and expert skiers in the Glacier bowls (CLAUDE.md). Each alert
level gets its own kind of line:
  - briefing (entered expert terrain): calm, at most 30 words, today's conditions and
    the main hazards in the area
  - guidance (cliff on trajectory): calm, names the hazard and the safe line, never "stop"
  - warning (at a cliff edge, closure or boundary): firm, may say "Stop now"
Claude writes the line from the facts. If it takes longer than COORDINATOR_TIMEOUT_S
(1.5 s), errors, refuses, or its line breaks a rule below, the Route Planner's line is
used if it passes the same rules, and otherwise a fixed template. Every decision is
logged with the path used.

Hard rule (CLAUDE.md): Claude only chooses the words. The geofence already decided that
the skier is close to the zone, and an alert is always spoken, whatever Claude does.
"""

import asyncio
import json
import os
import re
import time

import anthropic

import config

SYSTEM_PROMPT = """You write one spoken line for an advanced or expert skier in the Glacier bowls \
below Spanky's Ladder on Blackcomb. It is read aloud by text to speech into their earphones while \
they ski. They chose this terrain: never tell them to leave it, never call it too hard, never lecture.

What to write depends on "level":
- "briefing": they just entered an expert area. Calm and brief, at most 25 words: today's \
conditions in a few words, then the main hazards, each in a few words. Never tell them to stop or leave.
- "guidance": a cliff is on their line. Calm and clear, 6 to 12 words: name the hazard, then the \
safe line to move to. Example: "Cliff band below. Move to the Garnet Bowl Left line." Never say stop.
- "warning": they are at a cliff edge, in a closed area, or at the boundary. Firm and urgent, \
6 to 10 words, may start with "Stop now", then the safe line or exit if one is given.

Always:
- Plain spoken words. No lists, markup, quotes or emoji. Leave out numbers unless they matter.
- Never give a direction (left, right, uphill, downhill, back, across) unless that word appears \
in the facts. A wrong direction can send a skier toward the hazard.
- Use only area, line, run and exit names that appear in the facts.
- Never say or suggest that a hazard is not there or not dangerous.
- If the avalanche rating is not available, say so if you mention it; never state or guess a rating.

Reply with the line only."""

HAZARD_WORDS = {
    "cliff": "cliff band",
    "closure": "closed area",
    "expert_terrain": "expert terrain",
    "boundary": "ski area boundary",
}

# Words that point the skier somewhere. A line may only use one if it appears in the facts.
DIRECTION_WORDS = {"left", "right", "uphill", "downhill", "back", "across", "straight",
                   "north", "south", "east", "west"}

_client = None


def _claude():
    """Shared async client. No retries: a retry can't fit in the 1.5 s budget."""
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(max_retries=0, timeout=config.COORDINATOR_TIMEOUT_S + 1)
    return _client


def reroute_for(hazard_map, ability, zone_id):
    """The Route Planner's reroute for this zone and ability, with the exit's name, or None."""
    plan = (hazard_map.get("plans") or {}).get(ability) or {}
    reroute = next((r for r in plan.get("reroutes", []) if r.get("zone_id") == zone_id), None)
    if reroute is None:
        return None
    exit_ = next((e for e in hazard_map.get("safe_exits", []) if e["id"] == reroute.get("exit_id")), None)
    return {**reroute, "exit_name": exit_["name"] if exit_ else None}


def safe_line_name(hazard_map, zone):
    """The proposed safe line for a cliff zone (build_safe_lines.py), or None."""
    return next((l["name"] for l in hazard_map.get("safe_lines", []) if l["id"] == zone.get("safe_line")), None)


def safe_exit_name(hazard_map, zone_id, reroute):
    """The plan's exit for this zone, else the zone's own code-owned safe exit, else None."""
    if reroute and reroute.get("exit_name"):
        return reroute["exit_name"]
    return next((e["name"] for e in hazard_map.get("safe_exits", []) if e["from_zone"] == zone_id), None)


def conditions_facts(hazard_map):
    conditions = hazard_map.get("conditions") or {}
    weather = conditions.get("weather") or {}
    avalanche = conditions.get("avalanche") or {}
    known = any(avalanche.get(k) is not None for k in ("alpine", "treeline", "below_treeline"))
    return {
        "new_snow_cm": weather.get("new_snow_cm"),
        "temp_c": weather.get("temp_c"),
        "wind_gust_kmh": weather.get("wind_gust_kmh"),
        "avalanche_rating": {k: avalanche.get(k) for k in ("alpine", "treeline", "below_treeline")}
        if known else "not available for this date",
    }


def facts_for(alert, zone, hazard_map, ability, reroute):
    level = alert["level"]
    facts = {"level": level, "skier_ability": ability, "hazard": HAZARD_WORDS.get(zone["type"], "hazard")}
    if level == "briefing":
        names = {z["id"]: z["name"] for z in hazard_map["zones"]}
        facts.update({
            "area": zone["name"],
            "conditions": conditions_facts(hazard_map),
            # Two at most, and only the part of each name before any comma, to keep it short.
            "main_hazards": [names[h].split(",")[0] for h in zone.get("hazards_inside", [])[:2] if h in names],
        })
        return facts
    facts.update({"zone_name": zone["name"], "hint": zone.get("hint"),
                  "distance_m": alert["distance_m"], "speed_kmh": alert["speed_kmh"]})
    if zone["type"] == "cliff":
        facts["safe_line"] = safe_line_name(hazard_map, zone)
    else:
        facts["safe_exit"] = safe_exit_name(hazard_map, alert["zone_id"], reroute)
    if level == "warning" and reroute and reroute.get("spoken"):
        facts["route_planner_line"] = reroute["spoken"]
    return facts


def problems(line, level, facts):
    """Why a line can't be spoken for this level (empty when it's fine)."""
    found = []
    words = re.findall(r"[a-z']+", line.lower())
    limit = config.COORDINATOR_BRIEFING_MAX_WORDS if level == "briefing" else config.COORDINATOR_MAX_WORDS - 1
    if len(line.split()) > limit:
        found.append(f"too long ({len(line.split())} words)")
    if level != "warning" and "stop" in words:
        found.append("says stop outside a warning")
    facts_words = set(re.findall(r"[a-z]+", json.dumps(facts).lower()))
    invented = sorted(w for w in set(words) if w in DIRECTION_WORDS - facts_words)
    if invented:
        found.append(f"direction not in the facts ({', '.join(invented)})")
    return found


def template_line(alert, zone, hazard_map, facts):
    """Fixed wording from the code-owned data, used when no other line passes."""
    hazard = HAZARD_WORDS.get(zone["type"], "hazard")
    if alert["level"] == "briefing":
        c = facts["conditions"]
        parts = [f"{zone['name']}."]
        if c.get("new_snow_cm") is not None:
            parts.append(f"{round(c['new_snow_cm'])} centimetres new snow.")
        if c.get("wind_gust_kmh") is not None:
            parts.append(f"Gusts to {round(c['wind_gust_kmh'])} kilometres an hour.")
        if c["avalanche_rating"] == "not available for this date":
            parts.append("Avalanche rating not available.")
        if facts["main_hazards"]:
            parts.append(f"Main hazards: {' and '.join(facts['main_hazards'][:2])}.")
        return " ".join(parts)
    target = facts.get("safe_line") and f"Move to the {facts['safe_line']} line."
    target = target or (facts.get("safe_exit") and f"{facts['safe_exit']}.") or ""
    if alert["level"] == "guidance":
        return f"{hazard.capitalize()} below. {target or 'Steer clear of it.'}"
    return f"Stop now. {hazard.capitalize()} ahead. {target}".strip()


async def ask_claude(facts):
    """One short request. Returns (line or None, reason, milliseconds)."""
    started = time.monotonic()
    try:
        response = await asyncio.wait_for(
            _claude().messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=200,
                # Fastest settings on Haiku 5.5: low effort, thinking off (accepted at low effort).
                output_config={"effort": "low"},
                thinking={"type": "disabled"},
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": json.dumps(facts)}],
            ),
            timeout=config.COORDINATOR_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return None, f"timeout after {config.COORDINATOR_TIMEOUT_S} s", _ms(started)
    except anthropic.RateLimitError:
        return None, "rate limited", _ms(started)
    except anthropic.APIStatusError as error:
        return None, f"API error {error.status_code}", _ms(started)
    except anthropic.APIConnectionError:
        return None, "connection error", _ms(started)
    except anthropic.AnthropicError as error:  # e.g. no credentials configured
        return None, f"client error: {error}", _ms(started)

    ms = _ms(started)
    if response.stop_reason == "refusal":
        return None, "refusal", ms
    text = " ".join(b.text for b in response.content if b.type == "text").strip().strip('"')
    if not text:
        return None, f"empty reply (stop_reason {response.stop_reason})", ms
    line = text.splitlines()[0].strip()
    found = problems(line, facts["level"], facts)
    if found:
        return None, f"{'; '.join(found)}: {line}", ms
    return line, "ok", ms


def _ms(started):
    return round((time.monotonic() - started) * 1000)


async def decide(alert, hazard_map, ability):
    """Choose the spoken line for one geofence alert. Always returns a line."""
    zone = next(z for z in hazard_map["zones"] if z["id"] == alert["zone_id"])
    reroute = reroute_for(hazard_map, ability, alert["zone_id"])
    facts = facts_for(alert, zone, hazard_map, ability, reroute)

    if os.environ.get("ANTHROPIC_API_KEY"):
        claude_line, reason, claude_ms = await ask_claude(facts)
    else:
        claude_line, reason, claude_ms = None, "ANTHROPIC_API_KEY not set", 0

    # The Route Planner's line only stands in if it passes the same rules for this level.
    route_line = (reroute or {}).get("spoken")
    route_ok = bool(route_line) and alert["level"] != "briefing" and not problems(route_line, alert["level"], facts)
    if claude_line:
        path, line = "claude", claude_line
    elif route_ok:
        path, line = "route_planner", route_line
    else:
        path, line = "template", template_line(alert, zone, hazard_map, facts)

    return {
        "t": alert["t"],
        "zone_id": alert["zone_id"],
        "level": alert["level"],
        "line": line,
        "path": path,
        "claude_reason": reason,
        "claude_ms": claude_ms,
        "claude_line": claude_line,
        "route_planner_line": route_line,
        "model": config.CLAUDE_MODEL,
    }


async def _main():
    import geofence

    hazard_map = geofence.load_hazard_map()
    print(f"model {config.CLAUDE_MODEL}, budget {config.COORDINATOR_TIMEOUT_S} s, "
          f"Route Planner plans: {'yes' if hazard_map.get('complete') else 'not yet'}")
    for name in ("scenario_1", "scenario_2"):
        track = json.loads((geofence.TRACKS_DIR / f"{name}.json").read_text())
        print(f"\n{name} ({track['ability']}): {track['description']}")
        for alert in geofence.run_track(track, hazard_map):
            decision = await decide(alert, hazard_map, track["ability"])
            print(f"t={decision['t']:4.0f} s {decision['level']:9} {decision['zone_id']:22} path={decision['path']:13} "
                  f"claude: {decision['claude_reason']} ({decision['claude_ms']} ms)")
            print(f"           says: \"{decision['line']}\"")


if __name__ == "__main__":
    asyncio.run(_main())
