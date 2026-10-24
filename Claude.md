# Cairn

Cairn is a voice safety agent for advanced and expert skiers. It helps them ski Spanky's Ladder and the Glacier bowls (Garnet, Ruby, Diamond, Sapphire) on Blackcomb safely: it briefs them as they enter expert terrain, and when a cliff band is on their line it names the hazard and the safe line in time to change course. It never tries to talk them out of expert terrain. Built solo by Maia at Cal Hacks 13 (submission due Sunday Oct 25, 10am PT).

Why it exists: two years ago a family friend skied off a cliff at Spanky's Ladder on Whistler Blackcomb and broke his jaw in four places. The demo is scoped to that area.

## Who it's for and how it talks

- Community: advanced and expert skiers who chose this terrain. Cairn is a partner on the mountain, not a gatekeeper.
- Tone: calm, brief, specific. Name the hazard and the line; never lecture, never call terrain too hard, never suggest leaving it.
- Entering expert terrain: a short, calm briefing (today's conditions, the main hazards in the area). Never a warning.
- Cliffs: guidance on trajectory, naming the hazard and the safe line ("Cliff band below. Move to the Garnet Bowl Left line."). "Stop now" only when the skier is inside the cliff zone or within 15 m of it.
- Closures and the ski area boundary: hard warnings.
- Unknown avalanche rating: say it isn't available; never substitute one.

## How to work with me

- Make small, step by step changes. Never rewrite a whole file when an edit will do.
- Before a change, say in one or two sentences what you will change and why. After, say what changed.
- Ask before adding a new dependency or changing the architecture below.
- Prefer simple, readable code over clever code. Plain Python and plain JavaScript are preferred.
- Never invent API fields, endpoints, model names or voice IDs. If unsure, read the docs linked below or ask me.
- Never put API keys in code, the frontend, or git. Keys live in `.env` only. Keep `.env.example` updated with empty values.
- Commit small, working steps with clear messages.

## The one thing that must always work

Scenario 2: a simulated advanced skier in Garnet Bowl drifts toward a cliff band in flat light and hears guidance naming the hazard and the safe line, early enough to change line. Protect this flow above everything else.

## Architecture

Two layers.

**1. Creao agents (slow, the core intelligence).** Four role agents built and saved by Maia in the agent.creao.ai UI, chained in a workflow. Runs take minutes and do not stream, so they run before skiing (or on a schedule), never inside the real time loop.

1. Conditions Analyst: forecast, avalanche rating, closures for the day
2. Terrain Scout: marks today's hazard zones around Spanky's Ladder
3. Route Planner: produces safe zones and safe exits for each ability level
4. Incident Responder: after a crash check in fails, writes the incident report and sends it to the patrol console (simulated)

The Route Planner's output is the hazard map JSON that the fast path runs on. Without it Cairn cannot warn anyone. This is the Creao judging requirement: a Creao agent does the core work.

**2. Fast path (our code, real time).**

GPS replay → smoothing → geofence check against the hazard map → Claude writes the spoken warning and reroute → Cartesia Sonic speaks it → dashboard shows everything.

Hard rule: a cliff on the skier's trajectory ALWAYS triggers a spoken alert (guidance, or a "stop" warning inside the zone or within 15 m), even if Claude fails, is slow, or disagrees. Claude chooses the wording and the safe line to name, never whether a cliff exists. If Claude takes longer than about 1.5 seconds, speak a template line from the hazard zone instead.

Hard rule: every zone in `data/hazard_zones.geojson` always stays in the hazard map. Agents (Creao or Claude) can annotate zones (severity, applies_to, hint) but can never remove a zone, so no agent can make a cliff disappear. The merge step enforces this.

## Stack

- Backend: Python, FastAPI
- Frontend: one HTML page with plain JavaScript and MapLibre GL JS (v6, ES module from unpkg, pinned exact version) for the 3D map
- Map data: OpenStreetMap (via Overpass) for Blackcomb as cached GeoJSON, plus hazard zone polygons generated from the OSM cliffs and closures near Spanky's Ladder
- Live updates to the dashboard: WebSocket or Server Sent Events from the backend
- Claude: Anthropic API, called from the backend only. Use a fast current model; check the Anthropic docs for the exact model name rather than guessing.
- Voice: Cartesia (rules below)
- Creao: API Trigger, called from the backend only (rules below)

## Suggested layout

```
cairn/
  backend/
    main.py            FastAPI app, websocket to dashboard
    replay.py          plays a GPS track in real time
    tracks.py          generates scenario tracks with speed changes and GPS noise
    geofence.py        smoothing + distance to hazard zones
    coordinator.py     Claude call that writes warning text and reroute
    voice.py           Cartesia Sonic calls
    creao_client.py    triggers Creao runs, polls or receives webhooks
    scenarios.py       runs the test scenarios and prints pass/fail
    fetch_osm.py       rerunnable Overpass fetch, writes the cached OSM GeoJSON below
    build_hazard_zones.py  builds hazard_zones.geojson from the OSM cache
  data/
    blackcomb_runs.geojson   runs and lifts (generated by fetch_osm.py)
    osm_hazards.geojson      raw OSM cliffs, closures, ski area boundary (generated by fetch_osm.py)
    hazard_zones.geojson     hazard zone polygons (generated by build_hazard_zones.py, do not edit)
    hazard_map.cached.json   saved output of a real Creao run, used for the demo
    tracks/            generated scenario tracks
  frontend/
    index.html
    app.js
  .env.example
  README.md
```

## Data formats

Hazard map (`data/hazard_map.json`, copied to `hazard_map.cached.json` for demo mode). The merge step owns this schema, not any single agent. It combines the Conditions Analyst output, the Terrain Scout zone annotations, the Route Planner's route output, and the geometry from `hazard_zones.geojson` and `safe_exits.geojson`:

```json
{
  "generated_at": "ISO timestamp",
  "date": "2026-02-22",
  "creao_run_link": "share link for the README",
  "creao_runs": [
    { "role": "conditions | terrain | route", "run_id": "uuid", "started_at": "ISO", "finished_at": "ISO", "duration_s": 0 }
  ],
  "conditions": { "generated_at": "ISO", "mode": "replay | live", "date": "YYYY-MM-DD", "resort_operating": null, "weather": {}, "avalanche": { "alpine": null, "treeline": null, "below_treeline": null, "problems": [] }, "closures": [], "notes_for_terrain_scout": "...", "risk_summary": "...", "sources": [] },
  "planner_notes": "Conditions Analyst notes on sources and gaps",
  "zones": [
    {
      "id": "spankys-cliff-band-1",
      "type": "cliff | closure | expert_terrain | boundary",
      "name": "from hazard_zones.geojson",
      "active": true,
      "severity": "high | medium (Terrain Scout)",
      "applies_to": ["beginner", "intermediate"],
      "hint": "short description of the danger (Terrain Scout)",
      "polygon": [[lat, lon], "..."]
    }
  ],
  "safe_exits": [
    { "id": "spankys-cliff-band-1-exit", "from_zone": "spankys-cliff-band-1", "name": "Exit to Rock Alley", "path": [[lat, lon], "..."] }
  ],
  "route": {
    "bowls": [{ "zone_id": "garnet-bowl", "recommended_line_id": "garnet-bowl-right-line", "why": "..." }],
    "cliff_guidance": [{ "zone_id": "garnet-cliff-band-1", "line_id": null, "spoken": "words to say" }],
    "hard_limits": [{ "zone_id": "spankys-closure-1", "spoken": "words to say" }]
  },
  "notes_for_incident_responder": "string (Route Planner)"
}
```

Agent outputs, each the last ```json block in the run's result text:
- Conditions Analyst: the full conditions document is the run's JSON artifact (`result.artifacts`, type application/json): `{ generated_at, mode, date, resort_operating, weather, avalanche, closures, notes_for_terrain_scout, risk_summary, sources }`. It becomes `conditions` (real nulls) and is what the Terrain Scout receives. `result.text` varies between runs (sometimes only flat form fields with the string "null", sometimes also a smaller `conditions_json` and `planner_notes`), so it is only used for `planner_notes` when present. Download artifacts with the key only to agent.creao.ai; follow the 302 to storage without the key.
- Terrain Scout (v1.1.0): `{ date, based_on_conditions, zones: [{ id, active, severity, hint, briefing }], notes_for_route_planner }`, merged into `zones` by id (`briefing` is the spoken entry briefing used when Claude's is rejected). Zone ids the Terrain Scout invents are ignored. A cliff stays `active: true` even if the Terrain Scout says otherwise.

Avalanche rating: never substitute a rating. When the Conditions Analyst returns null (for example, no Avalanche Canada archive for a replay date), the rating stays null, the dashboard shows "Avalanche rating: not available for this date", and spoken wording may treat the unknown rating as a reason for caution. Unknown never changes which zones warn.
- Route Planner (safe lines version): `{ date, bowls: [{ bowl_id, recommended_line_id, why, line_note }], cliff_guidance: [{ zone_id, line_id, spoken, line_note }], hard_limits: [{ zone_id, spoken }], notes_for_incident_responder }`, merged into `route` and `notes_for_incident_responder`. `bowl_id` may be the bowl's name or id. Entries naming an unknown zone or safe line are dropped (logged), never invented. Its spoken lines stand in for Claude's when they pass the same checks. A bowl's recommended line is the line for every cliff in that line's `for_zones`: the merge fills or overrides `cliff_guidance.line_id` with it (logged), so guidance never contradicts the recommendation. `line_note` (max 10 words, e.g. "Wind loaded today, ski it with care.") carries a concern about the recommended line; longer notes are dropped. A line is never called risky without the safest available one being offered.

Where the deliverable lands moves between runs of the same agent (seen 2026-10-24): a JSON artifact, a ```json block in a markdown artifact, a ```json block in `chatText`, or a JSON string inside `result.text`. `result.text` is often just the agent's memory `persist_fields`. `creao_client.agent_output` takes the first JSON object with the role's required keys (conditions: weather + avalanche; terrain: zones; route: bowls + cliff_guidance) and fails loudly otherwise, so a wrong shape never reaches the merge. (The Route Planner's markdown-only output was fixed in Creao on 2026-10-24; it now returns JSON.)

Agent inputs (textarea fields are JSON strings): Conditions `{mode, date}`; Terrain Scout `{conditions_json, hazard_zones: [{id, type, name}]}`; Route Planner `{terrain_json, safe_lines_json: [{id, name, bowl, for_zones}], zone_types: {id: type}}`. The orchestrator saves each agent's parsed output in `data/agent_outputs/`, so `python backend/hazard_map.py` can rebuild the map without spending credits.

Geometry (zone polygons, exit paths) always comes from the geojson files, never from an agent. Every zone in `hazard_zones.geojson` appears in the map even if the Terrain Scout leaves it out, so a cliff can never disappear because an agent skipped it. The fast path reads only this merged shape.

Hazard zones (`data/hazard_zones.geojson`) are generated by `backend/build_hazard_zones.py` from the OSM cliffs and closures within 500 m of Spanky's Ladder, plus one expert_terrain zone for the Ladder entrance. Each feature has `id`, `type` (cliff | closure | expert_terrain | boundary), `name` and `osm_id`. Never edit the geojson by hand; a rerun overwrites it. Manual changes (ids, names, types, which OSM ways become zones) go in the `ZONES` id/name table in that script, then rerun it. Keep ids stable once they are in Creao.

Geofence (`backend/geofence.py`, tuning in `backend/config.py`): a least squares fit over the last 5 fixes gives position, speed and heading. GPS bursts are left out of the fit: a fix whose reported accuracy is worse than 15 m, or that lands more than 20 m from where the fit predicts, can't alert and doesn't move the fit; after 4 left out in a row they are accepted as real movement (never blind for more than 3 s). The replay stream carries each raw fix with the smoothed position and why it was left out, and the dashboard draws raw fixes as faint dots beside the smoothed track ("GPS jump ignored"). The zone type decides how a zone alerts, for every ability (the Route Planner's plan only supplies wording): expert terrain gives a `briefing` once on entry; cliffs give `guidance` on trajectory and a `warning` only inside the cliff zone or within `CLIFF_STOP_M` (15 m); closures and the boundary give hard `warning`s. Cliffs are directional: OSM draws them with the drop on the right, so a cliff alerts only when the path ahead (30 m + speed x 8 s, rays at +-6 degrees) crosses its line from the high side, only the first cliff crossed counts, and being on the high side within `CLIFF_EDGE_M` (8 m) always alerts. Below a cliff is never an alert. After any warning, guidance for other cliffs waits 8 s (`CLIFF_QUIET_AFTER_WARNING_S`); closures and the boundary still speak. When a cliff alert offered a safe line and the skier comes back onto it (off the line since the alert, then within 12 m for 2 fixes, no longer heading over the cliff), an `on_line` alert speaks a fixed, calm "You're on <line>." and the dashboard turns the dashed offered line solid; that cliff can alert again afterwards. Closures need a sustained heading (5 fixes), a 15 m + speed x 4 s look ahead, 5 m near and no path margin. `spankys-ladder-entrance` has a 25 m Rock Alley corridor cut out (`build_hazard_zones.py`).

Coordinator (`backend/coordinator.py`): on each alert, Claude (`CLAUDE_MODEL`, default `claude-haiku-5-5`, effort low, thinking off, no retries) gets facts for the alert level and returns one spoken line: a `briefing` (at most 30 words: conditions and up to two main hazards of the area), `guidance` (the hazard and the safe line from `safe_lines.geojson`, never "stop"), or a `warning` (may say "Stop now", under 15 words). A line is rejected if it is too long, says "stop" outside a warning, names or knows a safe line without offering it ("move to", "take"...), or uses a direction word (left, right, uphill, back, ...) that isn't in its facts. If Claude hasn't answered in 1.5 s, errors, refuses, or its line is rejected, the Route Planner's line is used if it passes the same checks, otherwise a fixed template. Every decision is posted to the dashboard log as `coordinator` / `decision` with `path` (claude | route_planner | template) and `claude_reason`.

Voice (`backend/voice.py`): the coordinator's line streams through Sonic (`sonic-3.6`, cartesia SDK 4.2.0, `cartesia-version` header) over the WebSocket TTS API as raw `pcm_s16le` at 24 kHz, relayed to the dashboard as `voice` / `chunk` messages (live only, not kept in the log history) and played gaplessly with Web Audio. Warnings: the alert firm and quick (`determined`, speed 1.2, volume 1.4), the final safe line or exit sentence calm. Guidance: clear and confident (`confident`, speed 1.05, volume 1.25). Briefings: calm. A short attention chime plays before the speech (`config.VOICE_CHIME`, synthesized in the browser, sent as `chime` on each voice message): warnings and guidance get the normal chime, briefings a softer one, confirmations ("You're on ...") none. Styles live in `config.VOICE_STYLES`. If Cartesia is not configured or errors, the dashboard speaks the line with the browser's speech synthesis, so a warning is never silent. The banner on the map shows the exact words from the coordinator decision.

Safe lines (`backend/build_safe_lines.py`, `data/safe_lines.geojson`): OSM downhill runs mostly inside a Glacier bowl that cross no cliff line and stay 5 m clear of every cliff and closure zone. Each cliff gets the nearest safe line in a bowl that borders it; guidance names it. These replace exits like Rock Alley for cliffs. They are proposals: review them before relying on them.

Replay presentation: scenario tracks are trimmed by `tracks.py` to start 25 s before their first alert (a track with no alert keeps its first 30 s). The dashboard follows the skier (zoom 15.8, smoothed), has 1x / 2x / 4x speed buttons (POST `/api/replay/session/{id}/speed`), and the server drops to 1x from 5 s before each warning until 8 s after, using warning times computed up front with the same geofence. When a scenario starts, its target zone pulses and is labeled by name.

Agent messages on the dashboard log: `{ "from": "skier | terrain | coordinator | voice | creao:<role>", "type": "string", "data": {}, "time": "ISO timestamp" }`.

Units are metric (meters, km/h).

## Crash check in flow

Triggered when the skier was moving fast, then stops suddenly and stays still (tune thresholds in one config place).

1. The Cartesia Managed Agent speaks: "Hey, it looks like you stopped suddenly. Are you okay? If I don't hear from you in 30 seconds, I'll alert ski patrol."
2. A visible countdown starts on the dashboard.
3. At 10 seconds left, it speaks once more: "Alerting ski patrol in 10 seconds unless you tell me you're okay."
4. Skier says they're okay → cancel, log it, say "Glad you're okay. I'll keep watching."
5. Skier asks for help, or the countdown ends with no reply → escalate immediately: say "Alerting ski patrol now with your location. Stay where you are." Then trigger the Incident Responder and post the incident to the patrol console.
6. Any movement back to normal skiing speed also cancels the countdown.

Keep the 30 and 10 second values in config. This voice filter is the pitch: unlike phone crash detection, Cairn only escalates after the skier fails to respond.

## Patrol console (small feature, build after the core loop)

A second view on the same dashboard, toggled with a "Skier view / Patrol view" switch, fed by the same backend stream. Clearly labeled "Simulated: no real patrol is contacted."

- Map of the area with incident pins and active countdowns
- Incident cards: time, location, last speed, what the skier said (or "no response"), the Incident Responder's report, status (countdown, cancelled, escalated)
- Near misses: skiers who got a cliff warning, as small markers

Keep it to one panel and one list. No auth, no history beyond the current session.

## Dashboard design

Vibe: a painted panoramic trail map. The terrain is the star; routes are thin and quiet on top. Calm by default, loud only during a warning, matching the voice design. Reference: a painted panoramic resort trail map, as a style reference only; never use the resort's trail map artwork as a layer.

- Base map: MapLibre GL JS with 3D terrain, no raster basemap. The painterly look comes from the DEM itself: a `color-relief` layer (sage forest in the valley to snow above treeline) under a soft `igor` hillshade with blue-violet shadows, on a cream background.
- DEM: AWS Terrain Tiles (Tilezen terrarium, `s3.amazonaws.com/elevation-tiles-prod`), free with no key. Required attribution: Canada OGL, USGS SRTM/GMTED2010 and NOAA ETOPO1 lines from the Tilezen attribution list, plus "© OpenStreetMap contributors" for the runs and hazards.
- Camera: pitch about 60°, Spanky's Ladder centered, looking south from the Glacier bowls up at the Blackcomb ridge, so the cliff bands below the Ladder face the viewer.
- Palette (define once as CSS variables): paper cream, snow white, forest sage, shadow blue-violet, hazard red, route purple, slate text. Avoid bright UI blues.
- Runs: thin (1.5px), dashed, semi-transparent lines, subtly colored by difficulty. No labels; the name shows on hover.
- Lifts: thin gray lines, hidden by default.
- Hazards: soft red diagonal hatching (a canvas pattern added with `map.addImage`), never solid fills. Cliffs are hatched bands with a fine red line; closed areas are hatched with a faint outline. Only OSM cliffs and closed areas within about 500 m of Spanky's Ladder are shown.
- Data: all features are clipped to the fetch box in `fetch_osm.py`. The box itself is not drawn.
- One clear marker for Spanky's Ladder. No other labels by default.
- A small layer toggle (runs, lifts, hazards) in a corner.
- Routes: the skier's track and safe exits as clean purple lines with small direction arrows, like hiking map routes.
- Skier: a small dot with a fading trail of recent positions.
- Type: a crisp serif for map labels and headings, monospace for the agent log.
- Agent log as a field notebook: side panel on the same paper, one entry per message, small icon per agent, timestamp, short text.
- Agent org chart: a small panel showing the Creao roles (Conditions Analyst → Terrain Scout → Route Planner → Incident Responder) with Maia as founder on top, each lighting up when it runs.
- Warning state: the hazard zone pulses red, a banner shows the exact words being spoken, and the safe exit line draws itself in.
- Check in state: a large, readable countdown ring in the corner.
- Layout: map takes most of the screen, notebook log on the right, org chart and banners overlaid on the map. Must read well on a projector.

## Demo mode

The demo must never wait minutes for Creao. Demo mode loads `hazard_map.cached.json` from a real earlier run and replays a track. A separate button or command triggers a live Creao run in the background to show it really works.

## Test scenarios

`scenarios.py` runs each and prints expected vs actual (`python backend/scenarios.py --seeds 20` also runs 20 random variants of each).

1. Advanced skier on a clean line down Garnet Bowl Left → only the Garnet Bowl entry briefing
2. Advanced skier on Garnet Bowl Right drifts toward `garnet-cliff-band-1` in flat light → entry briefing, guidance naming the hazard and the safe line at least 12 s before the edge, a "stop" warning as they get within 15 m of the zone; they follow it back onto Garnet Bowl Right, the line highlights and a calm "You're on Garnet Bowl Right." confirms it; nothing else
3. Scenario 1's line with a 2-3 s GPS burst that jumps the reported position 20-40 m toward `garnet-cliff-band-1` (accuracy degrades with it) → only the Garnet Bowl briefing, no guidance or warning from the burst
4. Fast run, sudden stop, no movement → voice check in with 30 second countdown, then escalate to Incident Responder and patrol console
4b. Same stop, skier says "I'm okay" → countdown cancelled, no escalation
5. Closed area ahead → hard warning before the closure

The intermediate scenarios were dropped when Cairn's community changed to advanced and expert skiers (2026-10-24).

## Cartesia rules

- Read https://docs.cartesia.ai/llms.txt before writing Cartesia code. Do not invent fields or voice IDs.
- Base URL https://api.cartesia.ai (WebSockets wss://). Send the Cartesia-Version header on every request; default `2026-08-14`.
- Use the official Python SDK (`pip install cartesia`) and model `sonic-3.6` for text to speech.
- Stream warnings over the WebSocket TTS API so speech starts before generation finishes.
- Use `generation_config` for speed, volume and emotion: cliff warnings firm and quick, reroutes calm.
- The crash check in uses a Cartesia Managed Agent (two way voice). Do NOT use the Line SDK; it is being retired.
- Keys stay on the backend. If the browser ever calls Cartesia directly, the backend mints a short lived access token.

## Creao rules

- Docs: https://community.creao.ai/CalHacks2026 (Quickstart, Agents, API Trigger, Workflows, Secrets). Read them before writing Creao code.
- Read docs/creao/ before writing any Creao code. These are the source of truth for endpoints and request shapes.
- Agents are created and saved by Maia in the agent.creao.ai UI. The API only runs agents.
- Runs take minutes and do not stream. Use async runs with webhooks or polling, show progress in the UI, never hold a request open.
- Every run spends credits. Never let an agent trigger itself, and never trigger runs in a loop.
- The `capi_` key is server side only, never in frontend or repo code.

## README must include

- One line on who it's for: advanced and expert skiers at Spanky's Ladder and the Glacier bowls, Whistler Blackcomb
- Creao run share link and every agent ID
- Cartesia features used and known limitations
- Where Claude is used and how it could go wrong (GPS drift, outdated map, overconfidence) and the mitigations
- What is real vs simulated
- Setup steps
- Note that Cairn supplements signage and ski patrol and never replaces them

## Out of scope

Native mobile app, real on snow GPS, other resorts, accounts or login, group tracking, contacting real ski patrol or 911. Patrol contact is simulated in the patrol console only.