"""Cairn backend. Serves the dashboard and the cached map data.

Run from the repo root (--app-dir backend so the backend modules import each other
by plain name, the same way the scripts do when run as python backend/x.py):

    .venv/bin/uvicorn main:app --app-dir backend --reload --port 8000
"""

import json
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import geofence
import orchestrator
from events import hub
from replay import load_track, replay

# The demo date for replay mode.
DEMO_DATE = "2026-02-22"

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
    """Server Sent Events: one skier position message per GPS fix, then an "end" event."""
    try:
        track = load_track(scenario_id)
    except FileNotFoundError:
        raise HTTPException(404, f"unknown scenario {scenario_id}")

    # Geofence the replayed fixes against the current hazard map; warnings go to the dashboard log.
    fence = None
    if geofence.HAZARD_MAP_FILE.exists():
        fence = geofence.Geofence(geofence.load_hazard_map(), track["ability"])
    else:
        hub.publish("terrain", "no_hazard_map", {"error": "data/hazard_map.json missing; run agents first"})

    async def events():
        async for message in replay(track, speed):
            yield f"data: {json.dumps(message)}\n\n"
            if fence:
                for alert in fence.update(message["data"]):
                    hub.publish("terrain", alert["level"], alert)
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


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
