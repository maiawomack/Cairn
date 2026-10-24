# Cairn

## Where Claude could go wrong

Claude writes the words of each spoken warning (`backend/coordinator.py`). It never decides whether a hazard exists: the geofence fires on code-owned zone geometry, and a cliff warning is always spoken, whatever Claude returns.

### Invented directions

In live testing (scenario 2, three runs on 2026-10-24), Claude returned short, fast lines but added directions that nothing it was given supported: "Turn left, stay on the traverse." and "Turn back uphill." No fact said which way was safe, and a guessed "left" could point a skier toward the cliff.

Mitigations:
- Claude now gets the zone's code-owned safe exit by name (for example "Rock Alley"), so it has a real place to send the skier.
- The prompt forbids directions that aren't in the facts.
- Code rejects any line containing a direction word (left, right, uphill, downhill, back, across, straight, north, south, east, west) that doesn't appear in the facts Claude was given. A rejected line falls back to the Route Planner's spoken line, or a fixed template from the zone type and safe exit.

After the fix, six live runs all named the safe exit and none invented a direction.

### Other ways a line can fail, and the fallback

Every failure falls back to the Route Planner's line or the template, so something is always spoken:
- Claude takes longer than 1.5 s (seen once: 1,533 ms on the first call in a fresh process).
- The line is 15 words or longer (seen: a 16-word line before the prompt asked for 6 to 10 words).
- An API error, refusal, or empty reply.

Each decision is logged on the dashboard with the path used (claude, route_planner, or template) and the reason.
