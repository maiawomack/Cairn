"""Cairn backend. Serves the dashboard and the cached map data.

Run from the repo root (--app-dir backend so the backend modules import each other
by plain name, the same way the scripts do when run as python backend/x.py):

    .venv/bin/uvicorn main:app --app-dir backend --reload --port 8000
"""

import asyncio
import json
import logging
import os
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import checkin_agent
import config
import coordinator
import crashcheck
import geofence
import incident
import orchestrator
import voice
from datetime import datetime, timezone

from events import hub
from replay import load_track, replay

# The demo date for replay mode.
DEMO_DATE = "2026-02-22"

# Coordinator tasks in flight (kept referenced so they aren't garbage collected).
_speaking = set()

# ---- Crash check-in (one at a time). Everything in here happens inside the event loop. ----
# SIMULATED patrol: escalation only creates a record in the dashboard's patrol view.
_checkin = {"machine": None, "incident": None, "stop": None, "hazard": None}


def _checkin_active():
    machine = _checkin["machine"]
    return machine is not None and machine.state == "asking"


def _spawn(coroutine):
    task = asyncio.create_task(coroutine)
    _speaking.add(task)
    task.add_done_callback(_speaking.discard)


async def _say(kind, level):
    """A scripted check-in line: shown as the banner, then spoken (chime rules in config.VOICE_CHIME)."""
    line = crashcheck.lines()[kind]
    hub.publish("checkin", "say", {"line": line, "kind": kind, "level": level})
    await voice.speak(line, "checkin", level, hub.publish)


def _on_checkin_event(kind, data):
    inc = _checkin["incident"]
    hub.publish("checkin", kind, data)
    if kind == "ask":
        _spawn(_say("ask", "checkin_ask"))
    elif kind == "reminder":
        _spawn(_say("reminder", "checkin_reminder"))
    elif kind == "cancelled":
        # Not an incident: nothing goes to the patrol console, the ring just closes.
        _spawn(_say("moving" if data["by"] == "moving" else "ok", "checkin_ok"))
        _checkin["stop"].set()
    elif kind == "escalated":
        _spawn(_say("escalated", "checkin_escalated"))
        # The Incident Responder's report: cached or simulated, never a Creao run from here.
        report, source = incident.report_for(inc, _checkin["hazard"], data["reason"])
        incident.update(inc, status="escalated", skier_said=data["said"], report=report, report_source=source)
        incident.register(inc)
        hub.publish("patrol", "incident", inc)  # the only patrol message: an escalation actually happened
        _checkin["stop"].set()


async def _checkin_ticker(machine):
    while machine.state == "asking":
        machine.tick()
        await asyncio.sleep(0.1)


async def _scripted_reply(machine, after_s, said):
    await asyncio.sleep(after_s)
    machine.reply(said)


def _start_checkin(crash, position, hazard, track):
    if _checkin_active():
        return
    inc = incident.create(position, crash["peak_kmh"], hazard, datetime.now(timezone.utc).isoformat())
    machine = crashcheck.CheckinMachine(_on_checkin_event)
    _checkin.update(machine=machine, incident=inc, stop=asyncio.Event(), hazard=hazard)
    machine.start({"mode": "agent" if checkin_agent.configured() else "mock", "peak_kmh": crash["peak_kmh"]})
    _spawn(_checkin_ticker(machine))
    reply = track.get("reply")
    if reply:  # scenario 4b: the skier answers on cue
        _spawn(_scripted_reply(machine, reply["after_ask_s"], reply["said"]))
    if checkin_agent.configured():
        _spawn(checkin_agent.run_session(lambda: machine.reply("okay"), lambda: machine.reply("help"),
                                         _checkin["stop"], hub.publish))


# Running replays: session id -> {"speed": chosen playback speed}.
_replays = {}
# Replays slow to 1x from this long before each warning until this long after it,
# so the spoken warning plays at real speed.
REPLAY_SLOW_BEFORE_S = 5
REPLAY_SLOW_AFTER_S = 8

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"
DATA_DIR = ROOT / "data"

def voice_status():
    """Where spoken audio will come from, from the environment (.env). Never includes a key or id."""
    key, voice_id = bool(os.environ.get("CARTESIA_API_KEY")), bool(os.environ.get("CARTESIA_VOICE_ID"))
    if key and voice_id:
        mode, note = "sonic", f"voice: Cartesia Sonic ({config.CARTESIA_TTS_MODEL}), voice id from CARTESIA_VOICE_ID"
    else:
        missing = " and ".join(name for name, ok in (("CARTESIA_API_KEY", key), ("CARTESIA_VOICE_ID", voice_id)) if not ok)
        mode, note = "browser_speech", f"voice: {missing} missing in .env -> browser speech fallback (no Sonic)"
    return {"mode": mode, "model": config.CARTESIA_TTS_MODEL, "api_key_set": key, "voice_id_set": voice_id, "note": note}


@asynccontextmanager
async def lifespan(app):
    status = voice_status()
    log = logging.getLogger("uvicorn.error")
    (log.info if status["mode"] == "sonic" else log.warning)(status["note"])
    yield


app = FastAPI(title="Cairn", lifespan=lifespan)

# Map data is read from the cached GeoJSON files written by fetch_osm.py.
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/replay/{scenario_id}")
async def replay_stream(scenario_id: str, speed: float = Query(1.0, gt=0, le=20)):
    """Server Sent Events: a "session" event, one skier position message per GPS fix, then an "end" event.

    Playback speed can change mid-replay (POST /api/replay/session/{id}/speed). Near a
    warning it drops to 1x: the warning times are known up front by running the same
    geofence over the same fixes.
    """
    try:
        track = load_track(scenario_id)
    except FileNotFoundError:
        raise HTTPException(404, f"unknown scenario {scenario_id}")

    # Geofence the replayed fixes against the current hazard map; warnings go to the dashboard log.
    fence = hazard = None
    if geofence.HAZARD_MAP_FILE.exists():
        hazard = geofence.load_hazard_map()
        fence = geofence.Geofence(hazard, track["ability"])
    else:
        hub.publish("terrain", "no_hazard_map", {"error": "data/hazard_map.json missing; run agents first"})

    warning_times = [a["t"] for a in geofence.run_track(track, hazard)] if hazard else []
    detector = crashcheck.CrashDetector()
    session_id = uuid.uuid4().hex[:8]
    session = _replays[session_id] = {"speed": speed}

    def speed_at(t):
        near_warning = any(w - REPLAY_SLOW_BEFORE_S <= t <= w + REPLAY_SLOW_AFTER_S for w in warning_times)
        return 1.0 if near_warning else session["speed"]

    target = None
    if hazard and track.get("target_zone"):
        zone = next((z for z in hazard["zones"] if z["id"] == track["target_zone"]), None)
        if zone:
            target = {"id": zone["id"], "name": zone["name"], "type": zone["type"]}

    async def speak(alert):
        # Runs beside the replay so the skier keeps moving while Claude words the line.
        decision = await coordinator.decide(alert, hazard, track["ability"])
        hub.publish("coordinator", "decision", {**decision, "zone_type": alert["zone_type"]})
        await voice.speak(decision["line"], alert["zone_type"], alert["level"], hub.publish)

    async def events():
        start = {"session": session_id, "scenario": track["id"], "description": track.get("description"),
                 "target_zone": target, "warning_times": warning_times, "speed": speed}
        yield f"event: session\ndata: {json.dumps(start)}\n\n"
        try:
            async for message in replay(track, speed_at):
                alerts = fence.update(message["data"]) if fence else []
                if fence and fence.speed_ms is not None and not fence.last_rejected:
                    # Smoothed speed of accepted fixes only: the crash check-in's input.
                    kmh = fence.speed_ms * 3.6
                    if _checkin_active():
                        _checkin["machine"].movement(kmh)
                    crash = detector.update(message["data"]["t"], kmh)
                    if crash:
                        _start_checkin(crash, fence.smoothed, hazard, track)
                if fence:
                    # The dashboard draws the skier on the smoothed track and the raw fix beside it.
                    message["data"]["smoothed"] = fence.smoothed
                    message["data"]["rejected"] = fence.last_rejected
                yield f"data: {json.dumps(message)}\n\n"
                for alert in alerts:
                    hub.publish("terrain", alert["level"], alert)
                    task = asyncio.create_task(speak(alert))
                    _speaking.add(task)
                    task.add_done_callback(_speaking.discard)
            yield "event: end\ndata: {}\n\n"
        finally:
            _replays.pop(session_id, None)

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/api/replay/session/{session_id}/speed")
def set_replay_speed(session_id: str, speed: float = Query(..., gt=0, le=20)):
    """Change a running replay's speed (the dashboard's 1x / 2x / 4x buttons)."""
    if session_id not in _replays:
        raise HTTPException(404, "no such replay")
    _replays[session_id]["speed"] = speed
    return {"session": session_id, "speed": speed}


@app.post("/api/checkin/reply")
async def checkin_reply(said: str = Query(..., pattern="^(okay|help)$")):
    """The skier's reply during a check-in: the dashboard's demo buttons call this."""
    if not _checkin_active():
        raise HTTPException(409, "no check-in is running")
    _checkin["machine"].reply(said)
    return {"said": said}


@app.get("/api/patrol/incidents")
def patrol_incidents():
    """The simulated patrol console's incidents: escalated check-ins only, this session only."""
    return incident.INCIDENTS


PUBLIC_DEMO = os.environ.get("CAIRN_PUBLIC_DEMO", "").strip().lower() in ("1", "true", "yes")


def _disabled_in_public_demo():
    """The answer for every endpoint that would spend Creao credits when CAIRN_PUBLIC_DEMO is set."""
    return JSONResponse({"status": "disabled_in_public_demo",
                         "reason": "Live agent runs are turned off in the public demo (they spend credits). "
                                   "The demo uses the saved hazard map from a real Creao run."}, status_code=403)


@app.post("/api/incident/live-run")
def incident_live_run():
    """A live Incident Responder run on Creao would spend credits, so it is its own explicit action.
    Not wired yet: it needs the agent's input fields (its config.yaml)."""
    if PUBLIC_DEMO:
        return _disabled_in_public_demo()
    return JSONResponse({"status": "not_wired", "reason": "A live Creao Incident Responder run needs that agent's "
                         "input fields (config.yaml); nothing was run and no credits were spent."}, status_code=501)


@app.get("/api/health")
def health():
    """What is configured, as booleans and model names only (no keys, no ids)."""
    return {
        "public_demo": PUBLIC_DEMO,
        "voice": voice_status(),
        "claude": {"api_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")), "model": config.CLAUDE_MODEL},
        "checkin_agent": {"mode": "agent" if checkin_agent.configured() else "mock"},
        "hazard_map": {"present": geofence.HAZARD_MAP_FILE.exists()},
    }


@app.get("/api/events")
async def event_stream():
    """Server Sent Events: the dashboard log (recent history first, then live messages)."""

    async def events():
        async for message in hub.subscribe():
            yield f"data: {json.dumps(message)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/api/agents/state")
def agents_state():
    """Org chart data: last run and output per Creao role (data/agent_outputs/), last event per code box."""
    outputs_dir = DATA_DIR / "agent_outputs"
    runs_file = outputs_dir / "creao_runs.json"
    runs = {r["role"]: r for r in json.loads(runs_file.read_text())} if runs_file.exists() else {}
    state = {}
    for role in ("conditions", "terrain", "route", "incident"):
        output_file = outputs_dir / f"{role}.json"
        state[role] = {
            "last_run": runs.get(role),
            "output": json.loads(output_file.read_text()) if output_file.exists() else None,
        }
    # Our code's boxes: the latest event in the in-memory history (last 200), nothing on disk,
    # so they read "no alerts yet" after a restart.
    code = {
        "geofence": hub.latest("terrain", ("briefing", "guidance", "warning", "on_line")),
        "coordinator": hub.latest("coordinator", ("decision",)),
        "voice": hub.latest("voice", ("spoken", "unavailable", "error")),
    }
    return {"running": orchestrator.is_running(), "roles": state, "code": code}


@app.post("/api/agents/run")
def run_agents():
    """Start the Creao chain in the background. Spends credits, so only one chain at a time."""
    if PUBLIC_DEMO:
        return _disabled_in_public_demo()
    if orchestrator.is_running():
        return JSONResponse({"status": "already_running"}, status_code=409)

    def run():
        try:
            orchestrator.run_chain(DEMO_DATE, "replay", publish=hub.publish)
        except Exception:
            pass  # already published to the dashboard log as "failed"

    threading.Thread(target=run, daemon=True).start()
    return JSONResponse({"status": "started", "date": DEMO_DATE}, status_code=202)
