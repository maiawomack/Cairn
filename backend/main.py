"""Cairn backend. Serves the dashboard and the cached map data.

Run from the repo root:

    .venv/bin/uvicorn backend.main:app --reload --port 8000
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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
