# Cairn

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
| Creao agents (Conditions Analyst, Terrain Scout, Route Planner): real runs, saved outputs | The Incident Responder's report: a template filled with the real incident facts, unless a saved Creao run exists |
| Claude wording each spoken line, and Cartesia Sonic speaking it | **Ski patrol: the patrol console is a view in this app. No real patrol and no 911 is ever contacted.** |
| The crash detector and the 30 s countdown with the 10 s reminder, timed exactly | The Cartesia Managed Agent listener: written to the docs, not yet run against a live agent |

Cairn supplements signage and ski patrol and never replaces them.

## Known limitations

- **A late, sharp turn toward a cliff gets a direct warning without guidance.** Guidance needs the skier's path to point over a cliff edge while they are still more than 15 m from its zone. If the turn toward the cliff comes later than that, the first alert is the "Stop now" warning. In testing (scenario 2, 20 random variants on 2026-10-24) this happened in 2 of 20 runs, still 12 s before the edge.
- **Scenario 2 ends on Garnet Bowl Right, where that line exits the bowl (~2,100 m).** Garnet Bowl Left is the lower exit, down to the bowl's lowest point (~2,040 m), and is the line scenarios 1 and 3 ski.
