"""Cairn backend. Serves the dashboard and the cached map data.

Run from the repo root:

    .venv/bin/uvicorn backend.main:app --reload --port 8000
"""

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.replay import load_track, replay

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

    async def events():
        async for message in replay(track, speed):
            yield f"data: {json.dumps(message)}\n\n"
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
