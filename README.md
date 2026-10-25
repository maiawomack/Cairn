# Cairn

Cairn is a voice safety agent for **advanced and expert skiers at Spanky's Ladder and the Glacier bowls (Garnet, Ruby, Diamond, Sapphire), Whistler Blackcomb**. It briefs them as they enter expert terrain, tells them when a cliff band is on their line and which line to take, and checks on them after a sudden stop. It never tries to talk them out of expert terrain, and it supplements signage and ski patrol; it never replaces them.

Everything runs on simulated GPS replays. There is no on-snow GPS and no real patrol contact (see "What is real vs simulated").

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # then fill in the keys and IDs (never commit .env)
.venv/bin/uvicorn main:app --app-dir backend --port 8000   # then open http://127.0.0.1:8000
.venv/bin/python backend/scenarios.py --seeds 20            # expected vs actual for every scenario, no API calls
```

- The map data is cached in `data/`; `python backend/fetch_osm.py` refetches it from OpenStreetMap, and `build_hazard_zones.py`, `build_safe_lines.py` and `build_safe_exits.py` rebuild what is derived from it.
- On startup the server logs one voice line, and `GET /api/health` reports (without any key or id) whether Sonic, Claude and the check-in agent are configured: `voice: Cartesia Sonic (sonic-3.6), voice id from CARTESIA_VOICE_ID`, or a warning `voice: CARTESIA_VOICE_ID missing in .env -> browser speech fallback (no Sonic)`.
- With no `ANTHROPIC_API_KEY` the spoken lines come from the agents' own lines or fixed templates; with no Cartesia key or voice id the dashboard speaks with the browser's own voice. Nothing is silent.

## Live demo

Public URL: **https://cairn-nvjo.onrender.com/** (Render free tier; it sleeps when idle, so the first load can take 30-50 s).

One Render web service runs the backend and serves the dashboard (`render.yaml`: `pip install -r requirements.txt`, then `uvicorn main:app --app-dir backend --host 0.0.0.0 --port $PORT`). The browser only uses relative URLs, so no CORS or API base URL is needed. GitHub Pages cannot host it (static only).

**Public demo defaults** (`CAIRN_PUBLIC_DEMO=1`, set in `render.yaml`; no secrets are in git):
- The hazard map is the saved one from a real Creao run (`data/hazard_map.cached.json`).
- `POST /api/agents/run` and `POST /api/incident/live-run` answer `403 disabled_in_public_demo`, so no visitor can spend Creao credits. `GET /api/health` shows `public_demo: true`.
- With `ANTHROPIC_API_KEY` unset, lines come from the Route Planner or fixed templates. With the Cartesia key unset, the browser's own voice speaks. With `CARTESIA_CHECKIN_AGENT_ID` blank, the crash check-in uses the mock path and the "Skier says" buttons.
- Browsers block sound until a gesture, so the page shows "Tap anywhere to enable sound" until the first click (a scenario button counts).
- The check-in state is shared by the whole server, so two visitors running a crash scenario at the same moment will interfere.

To add Sonic to the public site, set `CARTESIA_API_KEY` and `CARTESIA_VOICE_ID` in the Render dashboard (each replay then spends Cartesia credits). Leave every `CREAO_*_API_KEY` unset.

**Backup, for judging from a laptop:** `cloudflared tunnel --url http://localhost:8000` prints a temporary public URL for the locally running server, which uses your `.env`. Keep the Creao keys blank (or set `CAIRN_PUBLIC_DEMO=1` in the shell); Cartesia is optional for Sonic and the real check-in agent. The URL changes each run and the laptop must stay awake.

## Creao agents

The hazard map comes from a chain of Creao agents built in the Creao UI (they run before skiing, never in the real time loop): Conditions Analyst, Terrain Scout, Route Planner, and the Incident Responder.

| | |
| --- | --- |
| Creao run share link | **TODO: paste the share link** |
| Conditions Analyst agent ID | **TODO** (`CREAO_CONDITIONS_AGENT_ID`) |
| Terrain Scout agent ID | **TODO** (`CREAO_TERRAIN_AGENT_ID`) |
| Route Planner agent ID | **TODO** (`CREAO_ROUTE_AGENT_ID`) |
| Incident Responder agent ID | **TODO** (`CREAO_INCIDENT_AGENT_ID`) |

The keys are server side only (`CREAO_*_API_KEY` in `.env`). The demo uses `data/hazard_map.cached.json` from a real run; the dashboard's "Run agents" button runs the chain live (credits).

## Cartesia

- **Voice:** Sonic (`sonic-3.6`) over the WebSocket TTS API, streamed to the dashboard. The voice, model and API version come from `.env` (`CARTESIA_VOICE_ID`, `CARTESIA_TTS_MODEL`, `CARTESIA_VERSION`); no voice id is hard-coded. Warnings are firm and quick, guidance clear and confident, briefings calm, with a short attention chime before warnings and guidance (a softer one before briefings, none before confirmations). Styles are in `config.VOICE_STYLES`.
- **Managed Agent for the crash check-in:** written to the docs, not yet run against a live agent. It only listens; Sonic speaks the scripted lines, because the API has no event to make the agent say exact words.

| | |
| --- | --- |
| Check-in agent ID | **TODO** (`CARTESIA_CHECKIN_AGENT_ID`; blank = the mock path below) |
| Check-in tool IDs allowed on that agent | **TODO** (`CHECKIN_ALLOWED_TOOL_IDS`, comma separated; the tools are named `skier_ok` and `request_help`) |

## Crash check-in

A fast run followed by a sudden stop starts a check-in: Cairn asks "are you okay?", counts down 30 s with a reminder at 10 s left (timed in our code, exact to 0.1 s), and escalates on a help request or when the countdown runs out. "I'm okay", or normal skiing again, cancels it.

- **Mock path (default, and whenever `CARTESIA_CHECKIN_AGENT_ID` is blank):** the scripted lines are spoken by Sonic or the browser, and the dashboard's "Skier says: I'm okay" and "Skier says: help" buttons stand in for the skier's reply. There is no microphone input yet.
- **Keeping a demo on the mock path:** blank `CARTESIA_CHECKIN_AGENT_ID` in `.env` (`CARTESIA_CHECKIN_AGENT_ID=`) and restart. No agent session is opened and no agent config is read; Sonic still speaks the scripted lines (blank `CARTESIA_API_KEY` too for browser speech only). `GET /api/health` shows `checkin_agent.mode` as `mock` or `agent`. Live check (2026-10-24, scenario 4b): the agent connected, its config was read from `/v1/agents/{id}` and accepted (`agent_listening`), the ask was spoken by Sonic, and "I'm okay" (dashboard button) cancelled the check-in. Not tested: microphone audio and the agent calling `skier_ok` itself.
- **Patrol view only:** the patrol console ("Simulated: no real patrol is contacted") shows an incident only after a check-in escalates. A cancelled check-in never appears there, and the Skier view shows no incident cards or pins; during a countdown the Skier view shows the ring.
- **Report:** the Incident Responder's report on escalation is `data/incident_report.cached.json` if a saved run exists, otherwise a template filled with the real incident facts and today's conditions. It is never a live Creao run; the "Live Incident Responder run" button is separate and not wired yet.
- **Thresholds (`config.py`):** a fast run is 25 km/h or more, a sudden stop is a drop below `STOP_KMH` (10) within 3 s, still for 4 s; normal skiing again is `NORMAL_KMH` (15) for 3 fixes. **`STOP_KMH` is 10, not 3, and resume is 15,** because speed is derived from noisy GPS positions: a perfectly still skier reads about 4 km/h (90th percentile 8, 99th 11, max 14 over 40 simulated seeds). At 10, the detector caught 38 of 40 simulated crashes with 0 false triggers on 60 calm runs (12 caught 40/40 with 1 false trigger).
- **Safety:** Cairn never contacts real patrol or 911. A configured check-in agent is refused (the mock is used) if it enables `transfer_to_number` or `send_dtmf`, has any tool not in `CHECKIN_ALLOWED_TOOL_IDS`, or its config can't be read.

## Where Claude could go wrong

Claude writes the words of each spoken warning (`backend/coordinator.py`). It never decides whether a hazard exists: the geofence fires on code-owned zone geometry, and a cliff warning is always spoken, whatever Claude returns.

### Invented directions

In live testing (scenario 2, three runs on 2026-10-24), Claude returned short, fast lines but added directions that nothing it was given supported: "Turn left, stay on the traverse." and "Turn back uphill." No fact said which way was safe, and a guessed "left" could point a skier toward the cliff.

Mitigations:
- Claude now gets a code-owned place to send the skier by name: for a cliff, the safe line through the bowl (for example "Garnet Bowl Right", from `data/safe_lines.geojson`); for a closure, its safe exit.
- The prompt forbids directions that aren't in the facts.
- Code rejects any line containing a direction word (left, right, uphill, downhill, back, across, straight, north, south, east, west) that doesn't appear in the facts Claude was given. A rejected line falls back to the Route Planner's spoken line, or a fixed template from the zone type and safe exit.

After the fix, six live runs all named the safe exit and none invented a direction.

### Other ways a line can fail, and the fallback

Every failure falls back to the Route Planner's line or the template, so something is always spoken:
- Claude takes longer than 1.5 s (seen once: 1,533 ms on the first call in a fresh process).
- The line is 15 words or longer (seen: a 16-word line before the prompt asked for 6 to 10 words).
- An API error, refusal, or empty reply.

Each decision is logged on the dashboard with the path used (claude, route_planner, or template) and the reason.

## What is real vs simulated

| Real | Simulated |
| --- | --- |
| Hazard zones, runs and cliffs built from OpenStreetMap, elevations from AWS Terrain Tiles | GPS tracks: generated, with realistic speed and GPS noise (no on-snow GPS) |
| The geofence, speed-scaled warning distances and GPS-burst rejection, run on those tracks | The skier's replies to the crash check-in: dashboard buttons (no microphone yet) |
| Creao agents (Conditions Analyst, Terrain Scout, Route Planner): real runs, saved outputs | The Incident Responder's report on escalation: a template filled with the real incident facts, unless a saved Creao run exists |
| Claude wording each spoken line, and Cartesia Sonic speaking it | **Ski patrol: the patrol console is a view in this app. No real patrol and no 911 is ever contacted.** |
| The crash detector and the 30 s countdown with the 10 s reminder, timed exactly | The Cartesia Managed Agent listener: written to the docs, not yet run against a live agent |

Cairn supplements signage and ski patrol and never replaces them.

## Known limitations

- **A late, sharp turn toward a cliff gets a direct warning without guidance.** Guidance needs the skier's path to point over a cliff edge while they are still more than 15 m from its zone. If the turn toward the cliff comes later than that, the first alert is the "Stop now" warning. In testing (scenario 2, 20 random variants on 2026-10-24) this happened in 2 of 20 runs, still 12 s before the edge.
- **Scenario 2 ends on Garnet Bowl Right, at that line's real east exit from the bowl (~2,100 m; the track stops 4 m short, inside GPS accuracy).** Garnet Bowl Left is the lower exit, down to the bowl's lowest point (~2,040 m), and is the line scenarios 1 and 3 ski. Ending exactly on the exit makes the geofence, correctly, give cliff guidance again, because the mapped line ends beside the north tip of `garnet-cliff-band-1`.
- **The crash detector can mistake a fast but controlled stop for a crash.** A 6 s controlled stop is hard to tell apart from a crash through GPS-derived speed; the cost is a harmless "are you okay?".
