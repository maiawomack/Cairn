"""Coordinator: turn a geofence alert into one spoken line.

    python backend/coordinator.py    run scenario 2's alerts through the coordinator

When the geofence fires, Claude gets the zone hint, the Route Planner's reroute for the
skier's ability, distance and speed, and returns one spoken line under 15 words. If
Claude takes longer than COORDINATOR_TIMEOUT_S (1.5 s), errors, refuses, or returns an
unusable line, the Route Planner's spoken line is used instead, and if there is none, a
fixed template from the zone (CLAUDE.md). Every decision is logged with the path used.

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

SYSTEM_PROMPT = """You write one spoken safety line for a skier who is close to a hazard on the mountain. \
It is read aloud by text to speech into their earphones while they ski.

Rules:
- Aim for 6 to 10 words; never 15 or more. Short lines are spoken sooner.
- Plain spoken words. No lists, markup, quotes or emoji. Leave out numbers unless they matter.
- Lead with what to do now.
- If a safe exit is given, send the skier to it by its name.
- Never give a direction (left, right, uphill, downhill, back, across) unless that word appears \
in the facts. A wrong direction can send a skier toward the hazard. Use only run names and \
distances that appear in the facts.
- Never say or suggest that the hazard is not there or not dangerous.
- level "warning": firm and urgent. level "heads_up": calm and informational, no alarm.
- If the avalanche rating is unknown, you may urge caution, but never state a rating.

Reply with the line only."""

TEMPLATE_HAZARD = {
    "cliff": "cliff",
    "closure": "closed area",
    "expert_terrain": "expert terrain",
    "boundary": "ski area boundary",
}

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


def template_line(alert, reroute, hazard_map):
    """Fixed wording from the zone type and an exit name: the plan's, else the zone's own safe exit."""
    hazard = TEMPLATE_HAZARD.get(alert["zone_type"], "hazard")
    if alert["level"] == "heads_up":
        return f"Heads up, {hazard} ahead."
    line = f"Stop now. {hazard.capitalize()} ahead."
    exit_name = safe_exit_name(hazard_map, alert["zone_id"], reroute)
    if exit_name:
        line += f" {exit_name}."
    return line


def safe_exit_name(hazard_map, zone_id, reroute):
    """The plan's exit for this zone, else the zone's own code-owned safe exit, else None."""
    if reroute and reroute.get("exit_name"):
        return reroute["exit_name"]
    return next((e["name"] for e in hazard_map.get("safe_exits", []) if e["from_zone"] == zone_id), None)


# Words that point the skier somewhere. Claude may only use one if it appears in the facts.
DIRECTION_WORDS = {"left", "right", "uphill", "downhill", "back", "across", "straight",
                   "north", "south", "east", "west"}


def invented_directions(line, facts):
    facts_words = set(re.findall(r"[a-z]+", json.dumps(facts).lower()))
    return sorted(w for w in set(re.findall(r"[a-z]+", line.lower())) if w in DIRECTION_WORDS - facts_words)


def facts_for(alert, zone, reroute, ability, avalanche_known, exit_name):
    return {
        "safe_exit": exit_name,
        "level": alert["level"],
        "skier_ability": ability,
        "zone": {"type": zone["type"], "name": zone["name"], "hint": zone.get("hint")},
        "distance_m": alert["distance_m"],
        "speed_kmh": alert["speed_kmh"],
        "route_planner_line": None if reroute is None else reroute.get("spoken"),
        "avalanche_rating": "known" if avalanche_known else "unknown for today",
    }


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
    if len(line.split()) >= config.COORDINATOR_MAX_WORDS:  # "under 15 words"
        return None, f"too long ({len(line.split())} words): {line}", ms
    invented = invented_directions(line, facts)
    if invented:
        return None, f"direction not in the facts ({', '.join(invented)}): {line}", ms
    return line, "ok", ms


def _ms(started):
    return round((time.monotonic() - started) * 1000)


async def decide(alert, hazard_map, ability):
    """Choose the spoken line for one geofence alert. Always returns a line."""
    zone = next(z for z in hazard_map["zones"] if z["id"] == alert["zone_id"])
    reroute = reroute_for(hazard_map, ability, alert["zone_id"])
    avalanche = (hazard_map.get("conditions") or {}).get("avalanche") or {}
    avalanche_known = any(avalanche.get(k) is not None for k in ("alpine", "treeline", "below_treeline"))
    exit_name = safe_exit_name(hazard_map, alert["zone_id"], reroute)
    facts = facts_for(alert, zone, reroute, ability, avalanche_known, exit_name)

    if os.environ.get("ANTHROPIC_API_KEY"):
        claude_line, reason, claude_ms = await ask_claude(facts)
    else:
        claude_line, reason, claude_ms = None, "ANTHROPIC_API_KEY not set", 0

    route_line = (reroute or {}).get("spoken")
    if claude_line:
        path, line = "claude", claude_line
    elif route_line:
        path, line = "route_planner", route_line
    else:
        path, line = "template", template_line(alert, reroute, hazard_map)

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
    track = json.loads((geofence.TRACKS_DIR / "scenario_2.json").read_text())
    print(f"model {config.CLAUDE_MODEL}, budget {config.COORDINATOR_TIMEOUT_S} s, "
          f"Route Planner plans: {'yes' if hazard_map.get('complete') else 'not yet'}")
    for alert in geofence.run_track(track, hazard_map):
        decision = await decide(alert, hazard_map, track["ability"])
        print(f"t={decision['t']:4.0f} s {decision['zone_id']:26} path={decision['path']:13} "
              f"claude: {decision['claude_reason']} ({decision['claude_ms']} ms)")
        print(f"           says: \"{decision['line']}\"")


if __name__ == "__main__":
    asyncio.run(_main())
