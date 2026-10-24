"""Cairn backend. Serves the dashboard and the cached map data.

Run from the repo root (--app-dir backend so the backend modules import each other
by plain name, the same way the scripts do when run as python backend/x.py):

    .venv/bin/uvicorn main:app --app-dir backend --reload --port 8000
"""

import asyncio
import json
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import coordinator
import geofence
import orchestrator
import voice
from events import hub
from replay import load_track, replay

# The demo date for replay mode.
DEMO_DATE = "2026-02-22"

# Coordinator tasks in flight (kept referenced so they aren't garbage collected).
_speaking = set()

# Running replays: session id -> {"speed": chosen playback speed}.
_replays = {}
# Replays slow to 1x from this long before each warning until this long after it,
# so the spoken warning plays at real speed.
REPLAY_SLOW_BEFORE_S = 5
REPLAY_SLOW_AFTER_S = 8

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"
DATA_DIR = ROOT / "data"

app = FastAPI(title="Cairn")

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
                yield f"data: {json.dumps(message)}\n\n"
                if fence:
                    for alert in fence.update(message["data"]):
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


@app.get("/api/events")
async def event_stream():
    """Server Sent Events: the dashboard log (recent history first, then live messages)."""

    async def events():
        async for message in hub.subscribe():
            yield f"data: {json.dumps(message)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/api/agents/run")
def run_agents():
    """Start the Creao chain in the background. Spends credits, so only one chain at a time."""
    if orchestrator.is_running():
        return JSONResponse({"status": "already_running"}, status_code=409)

    def run():
        try:
            orchestrator.run_chain(DEMO_DATE, "replay", publish=hub.publish)
        except Exception:
            pass  # already published to the dashboard log as "failed"

    threading.Thread(target=run, daemon=True).start()
    return JSONResponse({"status": "started", "date": DEMO_DATE}, status_code=202)
