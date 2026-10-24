// Cairn map: a painted panorama of Blackcomb built from a DEM, with quiet routes on top.
import * as maplibregl from "maplibre-gl";

// [lon, lat], midpoint of OSM way 461517641.
const SPANKYS_LADDER = [-122.8811, 50.0977];

// How many recent GPS fixes the fading skier trail shows.
const TRAIL_LENGTH = 20;

// AWS Terrain Tiles (Tilezen). Free, no key. Attribution lines below are the
// ones that apply to Whistler from https://github.com/tilezen/joerd/blob/master/docs/attribution.md
const DEM_SOURCE = {
  type: "raster-dem",
  tiles: ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"],
  encoding: "terrarium",
  tileSize: 256,
  maxzoom: 15,
  attribution:
    '<a href="https://github.com/tilezen/joerd/blob/master/docs/attribution.md">Terrain Tiles</a>: ' +
    "Canada terrain data contains information licensed under the Open Government Licence – Canada; " +
    "SRTM and GMTED2010 courtesy of the U.S. Geological Survey; ETOPO1 U.S. NOAA",
};

const OSM_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

// Subtle trail map colors, keyed by OSM piste:difficulty.
const RUN_COLOR = [
  "match", ["get", "piste:difficulty"],
  ["novice", "easy"], "#4f7d4c",
  "intermediate", "#4b6894",
  ["advanced", "expert", "freeride"], "#2f2f33",
  "#7a7a7a",
];

const style = {
  version: 8,
  sources: {
    // MapLibre recommends separate sources for terrain and hillshade.
    "dem-terrain": DEM_SOURCE,
    "dem-shade": DEM_SOURCE,
    runs: { type: "geojson", data: "/data/blackcomb_runs.geojson", attribution: OSM_ATTRIBUTION },
    // Built by build_hazard_zones.py from OSM cliffs and closures within 500 m of Spanky's.
    zones: { type: "geojson", data: "/data/hazard_zones.geojson" },
    skier: { type: "geojson", data: emptyCollection() },
    "skier-trail": { type: "geojson", data: emptyCollection() },
    "raw-gps": { type: "geojson", data: emptyCollection() },
    // Safe lines through the bowls (build_safe_lines.py): drawn only when a cliff alert offers one.
    "safe-lines": { type: "geojson", data: "/data/safe_lines.geojson" },
  },
  terrain: { source: "dem-terrain", exaggeration: 1.3 },
  sky: {
    "sky-color": "#c9d6e3",
    "horizon-color": "#f3ede0",
    "fog-color": "#f3ede0",
    "fog-ground-blend": 0.6,
    "horizon-fog-blend": 0.7,
  },
  layers: [
    { id: "paper", type: "background", paint: { "background-color": "#f3ede0" } },
    {
      // Hypsometric tint: sage forest in the valley, snow above treeline.
      id: "relief",
      type: "color-relief",
      source: "dem-shade",
      paint: {
        "color-relief-color": [
          "interpolate", ["linear"], ["elevation"],
          600, "#6f876a",
          1300, "#87a07f",
          1650, "#b3c1aa",
          1850, "#e8e9e2",
          2100, "#f6f3ec",
          2500, "#fdfcf8",
        ],
      },
    },
    {
      id: "hillshade",
      type: "hillshade",
      source: "dem-shade",
      paint: {
        "hillshade-method": "igor",
        "hillshade-exaggeration": 1,
        "hillshade-shadow-color": "#36406a",
        "hillshade-highlight-color": "#fffaf0",
        "hillshade-accent-color": "#6b5d55",
        "hillshade-illumination-anchor": "map",
      },
    },
    {
      id: "runs",
      type: "line",
      source: "runs",
      filter: ["==", ["get", "kind"], "run"],
      layout: { "line-cap": "round" },
      paint: {
        "line-color": RUN_COLOR,
        "line-width": 1.5,
        "line-opacity": 0.6,
        "line-dasharray": [3, 2],
      },
    },
    {
      id: "lifts",
      type: "line",
      source: "runs",
      filter: ["==", ["get", "kind"], "lift"],
      layout: { visibility: "none" },
      paint: { "line-color": "#6e6e6e", "line-width": 1, "line-opacity": 0.7 },
    },
    // Hazard layers are added on style.load, once the hatch image exists (see HAZARD_LAYERS).
    // Invisible wide lines so the thin runs and lifts are easy to hover.
    {
      id: "runs-hover",
      type: "line",
      source: "runs",
      filter: ["==", ["get", "kind"], "run"],
      paint: { "line-width": 12, "line-opacity": 0 },
    },
    {
      id: "lifts-hover",
      type: "line",
      source: "runs",
      filter: ["==", ["get", "kind"], "lift"],
      layout: { visibility: "none" },
      paint: { "line-width": 12, "line-opacity": 0 },
    },
  ],
};

const HAZARD_COLOR = "#b23a3a";
const EXPERT_COLOR = "#2f2f33";

// Hazard zones by type: red hatching for cliffs and closures, dark hatching for
// expert terrain, orange dashed line for a boundary.
const HAZARD_LAYERS = [
  {
    id: "zone-hatch",
    type: "fill",
    source: "zones",
    filter: ["in", ["get", "type"], ["literal", ["cliff", "closure"]]],
    paint: { "fill-pattern": "hatch" },
  },
  {
    // Expert terrain is where Cairn's skiers want to be, not a hazard: a faint wash, no hatching.
    id: "expert-hatch",
    type: "fill",
    source: "zones",
    filter: ["==", ["get", "type"], "expert_terrain"],
    paint: { "fill-color": EXPERT_COLOR, "fill-opacity": 0.05 },
  },
  {
    id: "zone-outline",
    type: "line",
    source: "zones",
    filter: ["!=", ["get", "type"], "boundary"],
    paint: {
      "line-color": ["match", ["get", "type"], "expert_terrain", EXPERT_COLOR, HAZARD_COLOR],
      "line-width": 1,
      "line-opacity": 0.55,
    },
  },
  {
    id: "boundary-line",
    type: "line",
    source: "zones",
    filter: ["==", ["get", "type"], "boundary"],
    paint: { "line-color": "#e07b00", "line-width": 2, "line-dasharray": [4, 3] },
  },
];

const ROUTE_COLOR = "#5b3f8c";

// The replayed skier: a dot with a fading trail of recent fixes (plus the safe line a cliff alert offers).
// Added after the hazard layers so they draw on top.
const TOP_LAYERS = [
  {
    // Pulses the scenario's target zone when a replay starts (pulseTargetZone).
    id: "target-pulse",
    type: "line",
    source: "zones",
    filter: ["==", ["get", "id"], ""],
    paint: { "line-color": HAZARD_COLOR, "line-width": 0, "line-opacity": 0.9 },
  },
  {
    // The line a cliff alert offered: dashed while offered, solid with a glow once the skier is on it.
    id: "on-line-glow",
    type: "line",
    source: "safe-lines",
    filter: ["==", ["get", "id"], ""],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": ROUTE_COLOR, "line-width": 12, "line-opacity": 0.18, "line-blur": 4 },
  },
  {
    id: "offered-line",
    type: "line",
    source: "safe-lines",
    filter: ["==", ["get", "id"], ""],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": ROUTE_COLOR, "line-width": 3, "line-opacity": 0.9, "line-dasharray": [2, 1.5] },
  },
  {
    id: "on-line",
    type: "line",
    source: "safe-lines",
    filter: ["==", ["get", "id"], ""],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": ROUTE_COLOR, "line-width": 4, "line-opacity": 0.95 },
  },
  {
    // Raw GPS fixes as faint dots; ones the geofence left out of the fit get a red ring.
    id: "raw-gps",
    type: "circle",
    source: "raw-gps",
    paint: {
      "circle-color": ["case", ["get", "rejected"], "rgba(0, 0, 0, 0)", "#6e6e6e"],
      "circle-radius": ["case", ["get", "rejected"], 5, 2.5],
      "circle-stroke-color": HAZARD_COLOR,
      "circle-stroke-width": ["case", ["get", "rejected"], 1.5, 0],
      "circle-opacity": ["interpolate", ["linear"], ["get", "age"], 1, 0.55, TRAIL_LENGTH, 0],
      "circle-stroke-opacity": ["interpolate", ["linear"], ["get", "age"], 1, 0.9, TRAIL_LENGTH, 0.1],
    },
  },
  {
    id: "skier-trail",
    type: "circle",
    source: "skier-trail",
    paint: {
      "circle-color": ROUTE_COLOR,
      "circle-radius": ["interpolate", ["linear"], ["get", "age"], 0, 4, TRAIL_LENGTH, 1.5],
      "circle-opacity": ["interpolate", ["linear"], ["get", "age"], 0, 0.7, TRAIL_LENGTH, 0],
    },
  },
  {
    id: "skier",
    type: "circle",
    source: "skier",
    paint: {
      "circle-color": ROUTE_COLOR,
      "circle-radius": 7,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 2,
    },
  },
];

// Layers whose features show a label on hover, zones first so they win over runs.
const ZONE_HOVER_LAYERS = ["zone-hatch", "expert-hatch"];
const HOVER_LAYERS = [...ZONE_HOVER_LAYERS, "runs-hover", "lifts-hover"];

const LAYER_GROUPS = {
  runs: ["runs", "runs-hover"],
  lifts: ["lifts", "lifts-hover"],
  hazards: HAZARD_LAYERS.map((layer) => layer.id),
};

function emptyCollection() {
  return { type: "FeatureCollection", features: [] };
}

const map = new maplibregl.Map({
  container: "map",
  style,
  center: SPANKYS_LADDER,
  // Looking south from the Glacier bowls up at Spanky's and the Blackcomb ridge,
  // so the cliff bands below the Ladder face the viewer.
  zoom: 14,
  pitch: 60,
  bearing: 190,
  maxPitch: 75,
  attributionControl: { compact: true },
});

map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");

// Soft diagonal hatching, drawn once on a canvas and used as a fill pattern.
function hatchImage(color) {
  const size = 12;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  // Three strokes so the diagonal continues across tile edges.
  for (const offset of [-size, 0, size]) {
    ctx.moveTo(offset, size);
    ctx.lineTo(offset + size, 0);
  }
  ctx.stroke();
  return ctx.getImageData(0, 0, size, size);
}

// style.load, not load: load waits for every terrain tile, which can take a while.
map.on("style.load", () => {
  map.addImage("hatch", hatchImage("rgba(178, 58, 58, 0.55)"));
  for (const layer of HAZARD_LAYERS) {
    map.addLayer(layer, "runs-hover");
  }
  for (const layer of TOP_LAYERS) {
    map.addLayer(layer);
  }
  // ?replay=scenario_2&speed=4 starts a replay on load, for demos.
  const params = new URLSearchParams(location.search);
  if (params.has("replay")) {
    chosenSpeed = Number(params.get("speed")) || 1;
    startReplay(params.get("replay"), chosenSpeed);
  }
});

// Replay a scenario track streamed by the backend (Server Sent Events).
let replaySource = null;
let replaySession = null;
let chosenSpeed = 1;

// Follow cam: zoomed in on the Spanky's area, keeping the default tilt and bearing.
const FOLLOW_ZOOM = 15.8;
const CAMERA_SMOOTHING = 0.35; // 0..1, how far the camera moves toward each new GPS fix
const TARGET_PULSE_SECONDS = 6;

function startReplay(scenarioId, speed = chosenSpeed) {
  if (replaySource) replaySource.close();
  const trail = [];
  const raw = []; // recent raw GPS fixes, newest first
  let camera = null;
  setReplayButton(scenarioId);
  setSpeedButtons(speed);
  clearSafeLine();

  replaySource = new EventSource(`/api/replay/${encodeURIComponent(scenarioId)}?speed=${speed}`);
  replaySource.addEventListener("session", (event) => {
    const session = JSON.parse(event.data);
    replaySession = session.session;
    if (session.target_zone) pulseTargetZone(session.target_zone);
  });
  replaySource.onmessage = (event) => {
    const { lat: rawLat, lon: rawLon, smoothed, rejected, playback_speed: playing } = JSON.parse(event.data).data;
    // The skier is drawn on the geofence's smoothed track; raw GPS fixes are faint dots beside it.
    const [lat, lon] = smoothed || [rawLat, rawLon];
    trail.unshift([lon, lat]);
    trail.length = Math.min(trail.length, TRAIL_LENGTH + 1);
    raw.unshift({ coordinates: [rawLon, rawLat], rejected: Boolean(rejected) });
    raw.length = Math.min(raw.length, TRAIL_LENGTH);
    map.getSource("skier").setData(pointCollection([trail[0]]));
    map.getSource("skier-trail").setData(pointCollection(trail.slice(1)));
    map.getSource("raw-gps").setData(rawCollection(raw));
    showIgnoredJump(rejected ? [rawLon, rawLat] : null, rejected);
    showPlaybackSpeed(playing);

    // Smooth the camera so GPS noise doesn't shake the view.
    camera = camera
      ? [camera[0] + (lon - camera[0]) * CAMERA_SMOOTHING, camera[1] + (lat - camera[1]) * CAMERA_SMOOTHING]
      : [lon, lat];
    map.easeTo({ center: camera, zoom: FOLLOW_ZOOM, duration: 1000 / playing, easing: (t) => t });
  };
  // The server sends "end" when the track is done; close so EventSource doesn't reconnect and replay again.
  replaySource.addEventListener("end", stopReplay);
  replaySource.onerror = stopReplay;
}

function rawCollection(fixes) {
  return {
    type: "FeatureCollection",
    features: fixes.map((fix, age) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: fix.coordinates },
      properties: { age: age + 1, rejected: fix.rejected },
    })),
  };
}

// A tag on a raw fix the geofence left out of the smoothed track (a GPS jump).
let ignoredTag = null;

function showIgnoredJump(coordinates, reason) {
  if (!coordinates) {
    ignoredTag?.remove();
    ignoredTag = null;
    return;
  }
  if (!ignoredTag) {
    const el = Object.assign(document.createElement("div"), { className: "ignored-tag" });
    ignoredTag = new maplibregl.Marker({ element: el, anchor: "left", offset: [8, 0] }).setLngLat(coordinates).addTo(map);
  }
  ignoredTag.setLngLat(coordinates);
  ignoredTag.getElement().textContent = `GPS jump ignored (${reason})`;
}

function stopReplay() {
  showIgnoredJump(null);
  replaySource?.close();
  replaySession = null;
  setReplayButton(null);
  showPlaybackSpeed(null);
}

// Speed buttons apply to the running replay and to the next one.
for (const button of document.querySelectorAll("[data-speed]")) {
  button.addEventListener("click", () => {
    chosenSpeed = Number(button.dataset.speed);
    setSpeedButtons(chosenSpeed);
    if (replaySession) fetch(`/api/replay/session/${replaySession}/speed?speed=${chosenSpeed}`, { method: "POST" });
  });
}

function setSpeedButtons(speed) {
  for (const button of document.querySelectorAll("[data-speed]")) {
    button.classList.toggle("playing", Number(button.dataset.speed) === speed);
  }
}

function showPlaybackSpeed(playing) {
  const label = document.getElementById("playback-speed");
  // The server drops to 1x around each warning so the spoken line plays at real speed.
  label.textContent = playing == null ? "" : playing < chosenSpeed ? "1x near warning" : "";
}

// When a scenario starts, pulse its target zone and label it by name.
async function pulseTargetZone(target) {
  map.setFilter("target-pulse", ["==", ["get", "id"], target.id]);
  const zones = await (await fetch("/data/hazard_zones.geojson")).json();
  const ring = zones.features.find((f) => f.properties.id === target.id)?.geometry.coordinates[0] || [];
  let label = null;
  if (ring.length) {
    const center = [ring.reduce((s, p) => s + p[0], 0) / ring.length, ring.reduce((s, p) => s + p[1], 0) / ring.length];
    const el = Object.assign(document.createElement("div"), { className: "target-label", textContent: target.name });
    label = new maplibregl.Marker({ element: el }).setLngLat(center).addTo(map);
  }
  const started = performance.now();
  const frame = (now) => {
    const t = (now - started) / 1000;
    if (t > TARGET_PULSE_SECONDS) {
      map.setPaintProperty("target-pulse", "line-width", 0);
      label?.remove();
      return;
    }
    const wave = 0.5 + 0.5 * Math.sin(t * Math.PI * 2);
    map.setPaintProperty("target-pulse", "line-width", 2 + 5 * wave);
    map.setPaintProperty("target-pulse", "line-opacity", 0.35 + 0.55 * wave);
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

function pointCollection(coordinates) {
  return {
    type: "FeatureCollection",
    features: coordinates.map((coordinate, age) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: coordinate },
      properties: { age: age + 1 },
    })),
  };
}

function setReplayButton(playingId) {
  for (const button of document.querySelectorAll("[data-scenario]")) {
    button.classList.toggle("playing", button.dataset.scenario === playingId);
  }
}

for (const button of document.querySelectorAll("[data-scenario]")) {
  button.addEventListener("click", () => startReplay(button.dataset.scenario));
}

new maplibregl.Marker({ element: spankysMarker(), anchor: "left", offset: [-8, 0] })
  .setLngLat(SPANKYS_LADDER)
  .addTo(map);

function spankysMarker() {
  const el = document.createElement("div");
  el.className = "spankys-marker";
  el.innerHTML = '<span class="dot"></span><span class="label">Spanky’s Ladder</span>';
  return el;
}

// Zone ids and run or lift names on hover only.
const hoverPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, className: "hover-label", offset: 8 });

map.on("mousemove", (event) => {
  const layers = HOVER_LAYERS.filter((id) => map.getLayer(id));
  const features = map.queryRenderedFeatures(event.point, { layers });
  const zone = features.find((f) => ZONE_HOVER_LAYERS.includes(f.layer.id));
  const named = zone || features.find((f) => f.properties.name);
  if (!named) {
    map.getCanvas().style.cursor = "";
    hoverPopup.remove();
    return;
  }
  map.getCanvas().style.cursor = "pointer";
  hoverPopup.setLngLat(event.lngLat).setDOMContent(hoverLabel(named, Boolean(zone))).addTo(map);
});

function hoverLabel(feature, isZone) {
  const el = document.createElement("div");
  if (isZone) {
    const id = document.createElement("b");
    id.textContent = feature.properties.id;
    const name = document.createElement("div");
    name.textContent = feature.properties.name;
    el.append(id, name);
  } else {
    el.textContent = feature.properties.name;
  }
  return el;
}

for (const checkbox of document.querySelectorAll(".layer-toggle input")) {
  checkbox.addEventListener("change", () => {
    const visibility = checkbox.checked ? "visible" : "none";
    for (const layer of LAYER_GROUPS[checkbox.dataset.group]) {
      map.setLayoutProperty(layer, "visibility", visibility);
    }
  });
}

// ---- Creao agents panel: run button, org chart, conditions, log ----

const LOG_LENGTH = 8;
const PAGE_OPENED_AT = Date.now();
const runButton = document.getElementById("run-agents");

runButton.addEventListener("click", async () => {
  runButton.disabled = true;
  const response = await fetch("/api/agents/run", { method: "POST" });
  if (response.status === 409) addLogLine({ from: "creao:orchestrator", type: "already running", time: new Date().toISOString() });
});

// Agent messages arrive over Server Sent Events, recent history first.
const agentEvents = new EventSource("/api/events");
agentEvents.onmessage = (event) => {
  const message = JSON.parse(event.data);
  // Creao messages are "creao:<role>"; our code's are "terrain" (geofence), "coordinator" and "voice".
  // "terrain" is also a Creao role, so tell them apart by the prefix.
  const isCreao = message.from.startsWith("creao:");
  const role = message.from.replace("creao:", "");
  // History replays on page load; only light boxes, show and speak what happens now.
  const fresh = Date.parse(message.time) > PAGE_OPENED_AT - 2000;
  if (isCreao && role in agentState) updateOrgBox(role, message);
  if (isCreao && role === "orchestrator") {
    runButton.disabled = message.type === "started";
    if (message.type === "finished") loadConditions();
  }
  if (!isCreao) updateCodeBox(message, fresh);
  if (message.from === "checkin") handleCheckin(message, fresh);
  if (message.from === "patrol") handlePatrol(message);
  if (fresh && role === "coordinator" && message.type === "decision") {
    showBanner(message.data);
    showSafeLine(message.data);
  }
  if (fresh && role === "voice") handleVoice(message);
  const liveOnly = role === "voice" && ["start", "chunk", "end"].includes(message.type);
  if (message.type !== "output" && !liveOnly) addLogLine(message);
};

// ---- Spoken warnings: banner + streamed Sonic audio ----

const BANNER_SECONDS = 7;
let bannerTimer = null;

function showBanner(decision) {
  const banner = document.getElementById("spoken-banner");
  // Loud only for warnings; guidance gets a red accent, briefings stay calm.
  banner.className = `spoken-banner ${decision.level}`;
  const via = Object.assign(document.createElement("small"), {
    textContent: `${decision.zone_id} · words by ${decision.path.replace("_", " ")}`,
  });
  banner.replaceChildren(decision.line, via);
  banner.hidden = false;
  clearTimeout(bannerTimer);
  bannerTimer = setTimeout(() => {
    banner.classList.add("fading");
    setTimeout(() => { banner.hidden = true; }, 600);
  }, BANNER_SECONDS * 1000);
}

// The offered safe line: dashed when a cliff alert names it, solid once the skier is on it.
function showSafeLine(decision) {
  const id = decision.safe_line_id;
  if (!id) return;
  if (decision.level === "on_line") {
    map.setFilter("offered-line", ["==", ["get", "id"], ""]);
    map.setFilter("on-line", ["==", ["get", "id"], id]);
    map.setFilter("on-line-glow", ["==", ["get", "id"], id]);
  } else if (decision.level === "guidance" || decision.level === "warning") {
    map.setFilter("offered-line", ["==", ["get", "id"], id]);
  }
}

function clearSafeLine() {
  for (const layer of ["offered-line", "on-line", "on-line-glow"]) map.setFilter(layer, ["==", ["get", "id"], ""]);
}

// Browsers only allow audio after a click, so the context starts on the first one.
let audioContext = null;
document.addEventListener("click", () => {
  audioContext ??= new AudioContext();
  audioContext.resume();
});

const utterances = {}; // utterance id -> { sampleRate, nextTime, leftover }

function handleVoice(message) {
  const { type, data } = message;
  if (type === "start") {
    // The chime goes first; the speech is queued to start right after it.
    const chimeEnds = playChime(data.chime);
    utterances[data.utterance] = { sampleRate: data.sample_rate, nextTime: chimeEnds, leftover: null };
  } else if (type === "chunk") {
    playChunk(utterances[data.utterance], data.audio);
  } else if (type === "end") {
    delete utterances[data.utterance];
  } else if (type === "unavailable" || type === "error") {
    // Never silent: chime, then speak the line with the browser's own voice instead.
    const chimeSeconds = playChime(data.chime) - (audioContext ? audioContext.currentTime : 0);
    setTimeout(() => speakWithBrowser(data.line), Math.max(0, chimeSeconds) * 1000);
  }
}

// Attention chime: two short sine notes with a soft attack and decay. "normal" is clear and
// bright; "soft" is lower and much quieter; anything else (confirmations) plays nothing.
// Returns the audio clock time at which the chime (and a short gap) is over.
const CHIMES = {
  normal: { notes: [880, 1174.7], gain: 0.22, noteSeconds: 0.17 },
  soft: { notes: [587.3, 784], gain: 0.07, noteSeconds: 0.2 },
};
const CHIME_GAP_SECONDS = 0.15;

function playChime(kind) {
  const chime = CHIMES[kind];
  const now = audioContext ? audioContext.currentTime : 0;
  if (!chime || !audioContext) return now;
  chime.notes.forEach((frequency, i) => {
    const start = now + 0.02 + i * chime.noteSeconds * 0.8;
    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    oscillator.type = "sine";
    oscillator.frequency.value = frequency;
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.linearRampToValueAtTime(chime.gain, start + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + chime.noteSeconds + 0.12);
    oscillator.connect(gain).connect(audioContext.destination);
    oscillator.start(start);
    oscillator.stop(start + chime.noteSeconds + 0.15);
  });
  return now + 0.02 + chime.noteSeconds * 0.8 + chime.noteSeconds + 0.12 + CHIME_GAP_SECONDS;
}

function playChunk(utterance, base64) {
  if (!utterance || !audioContext) return;
  let bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  if (utterance.leftover !== null) {
    // A 16 bit sample can straddle two chunks.
    bytes = Uint8Array.from([utterance.leftover, ...bytes]);
    utterance.leftover = null;
  }
  if (bytes.length % 2) {
    utterance.leftover = bytes[bytes.length - 1];
    bytes = bytes.subarray(0, bytes.length - 1);
  }
  const samples = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.length / 2);
  if (!samples.length) return;
  const buffer = audioContext.createBuffer(1, samples.length, utterance.sampleRate);
  const channel = buffer.getChannelData(0);
  for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 32768;
  const source = audioContext.createBufferSource();
  source.buffer = buffer;
  source.connect(audioContext.destination);
  // Queue each chunk right after the previous one so playback is gapless.
  const startAt = Math.max(audioContext.currentTime + 0.02, utterance.nextTime);
  source.start(startAt);
  utterance.nextTime = startAt + buffer.duration;
}

function speakWithBrowser(line) {
  if (!line || !("speechSynthesis" in window)) return;
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(line));
}

// ---- Agent org chart ----

const AGENT_TITLES = {
  conditions: "Conditions Analyst",
  terrain: "Terrain Scout",
  route: "Route Planner",
  incident: "Incident Responder",
};
// Per role: { lastRun: {duration_s, finished_at, run_id}, output } from /api/agents/state,
// kept up to date by the live agent messages.
const agentState = { conditions: {}, terrain: {}, route: {}, incident: {} };

function orgBox(role) {
  return document.querySelector(`.org-box[data-role="${role}"]`);
}

function setOrgBox(role, state, runText) {
  const box = orgBox(role);
  box.classList.remove("running", "done", "failed");
  if (state) box.classList.add(state);
  box.querySelector(".org-run").textContent = runText;
}

function lastRunText(role) {
  const run = agentState[role].lastRun;
  if (!run) return role === "incident" ? "standby" : "not run yet";
  if (run.run_id === "simulated") {
    return `simulated report · ${new Date(run.finished_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}`;
  }
  const when = new Date(run.finished_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  return `last run ${run.duration_s} s · ${when}`;
}

function updateOrgBox(role, message) {
  const { type, data } = message;
  if (type === "started") setOrgBox(role, "running", "running…");
  if (type === "status") setOrgBox(role, "running", `${data.status} · ${Math.round(data.elapsed_s)} s`);
  if (type === "finished") {
    agentState[role].lastRun = data;
    setOrgBox(role, "done", lastRunText(role));
  }
  if (type === "output") agentState[role].output = role === "conditions" ? { conditions: data } : data;
  if (type === "failed") setOrgBox(role, "failed", `failed: ${(data.error || "").slice(0, 40)}`);
}

// ---- Our code's lane: Geofence -> Coordinator (Claude) -> Voice (Sonic) ----
// State comes only from the in-memory event history (and live messages), nothing on disk,
// so after a server restart the boxes read "no alerts yet".

const CODE_TITLES = { geofence: "Geofence", coordinator: "Coordinator (Claude)", voice: "Voice (Sonic)" };
const GEOFENCE_TYPES = ["briefing", "guidance", "warning", "on_line"];
const VOICE_DONE_TYPES = ["spoken", "unavailable", "error"];
const MIN_LIT_MS = 700; // a box stays lit at least this long, so a fast step is still visible
const codeState = { geofence: null, coordinator: null, voice: null }; // latest event per box
const lit = {};

function codeBox(name) {
  return document.querySelector(`.org-box[data-code="${name}"]`);
}

function lightCode(name, on) {
  const box = codeBox(name);
  const state = (lit[name] ??= { since: 0, timer: null });
  clearTimeout(state.timer);
  if (on) {
    state.since = performance.now();
    box.classList.add("running");
    return;
  }
  const wait = Math.max(0, MIN_LIT_MS - (performance.now() - state.since));
  state.timer = setTimeout(() => box.classList.remove("running"), wait);
}

function codeRunText(name) {
  const event = codeState[name];
  if (!event) return "no alerts yet";
  const when = new Date(event.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const data = event.data;
  if (name === "geofence") return `last alert ${when} · ${event.type}`;
  if (name === "coordinator") {
    const claude = data.claude_ms ? `Claude ${data.claude_ms} ms` : "no Claude call";
    return `last alert ${when} · ${claude}${data.path === "claude" ? "" : ` · ${data.path}`}`;
  }
  return `last alert ${when} · ${data.status}`;
}

function setCodeEvent(name, message) {
  codeState[name] = message;
  codeBox(name).querySelector(".org-run").textContent = codeRunText(name);
}

function updateCodeBox(message, fresh) {
  const { from, type } = message;
  if (from === "terrain" && GEOFENCE_TYPES.includes(type)) {
    setCodeEvent("geofence", message);
    if (fresh) {
      lightCode("geofence", true);
      lightCode("geofence", false); // lit for the minimum time
      lightCode("coordinator", true); // working from the alert's arrival until its decision
    }
  } else if (from === "coordinator" && type === "decision") {
    setCodeEvent("coordinator", message);
    if (fresh) lightCode("coordinator", false);
  } else if (from === "voice") {
    if (type === "start" && fresh) lightCode("voice", true);
    if (type === "end" && fresh) lightCode("voice", false);
    if (VOICE_DONE_TYPES.includes(type)) {
      setCodeEvent("voice", message);
      if (fresh) {
        if (type !== "spoken") lightCode("voice", true); // unavailable / error: no stream, a short pulse
        lightCode("voice", false);
      }
    }
  }
}

for (const name of Object.keys(CODE_TITLES)) codeBox(name).querySelector(".org-run").textContent = codeRunText(name);

for (const box of document.querySelectorAll(".org-box[data-code]")) {
  box.addEventListener("click", () => {
    const name = box.dataset.code;
    const event = codeState[name];
    document.getElementById("agent-output-title").textContent = CODE_TITLES[name];
    document.getElementById("agent-output-meta").textContent = event
      ? `${event.from} ${event.type} · ${new Date(event.time).toLocaleString()}`
      : codeRunText(name);
    document.getElementById("agent-output-body").textContent = event ? JSON.stringify(event.data, null, 2) : "No alerts yet.";
    outputDialog.showModal();
  });
}

// Collapse the card down to its header (default open).
const orgCard = document.getElementById("org-card");
const orgToggle = document.getElementById("org-toggle");
orgToggle.addEventListener("click", () => {
  const collapsed = orgCard.classList.toggle("collapsed");
  orgToggle.setAttribute("aria-expanded", String(!collapsed));
  orgToggle.textContent = collapsed ? "▸" : "▾";
  orgToggle.title = collapsed ? "Expand" : "Collapse";
});

async function loadAgentState() {
  const state = await (await fetch("/api/agents/state", { cache: "no-store" })).json();
  for (const [name, event] of Object.entries(state.code || {})) {
    if (event) setCodeEvent(name, event);
  }
  for (const [role, { last_run: lastRun, output }] of Object.entries(state.roles)) {
    agentState[role] = { lastRun, output };
    setOrgBox(role, lastRun ? "done" : "", lastRunText(role));
  }
  runButton.disabled = state.running;
}

// Click a box to read that agent's last output.
const outputDialog = document.getElementById("agent-output");
for (const box of document.querySelectorAll(".org-box[data-role]")) {
  box.addEventListener("click", () => {
    const role = box.dataset.role;
    const { lastRun, output } = agentState[role];
    document.getElementById("agent-output-title").textContent = AGENT_TITLES[role];
    document.getElementById("agent-output-meta").textContent = lastRun && lastRun.run_id === "simulated"
      ? `${lastRunText(role)} (no Creao run)`
      : lastRun
      ? `run ${lastRun.run_id.slice(0, 8)} · ${lastRun.duration_s} s · finished ${new Date(lastRun.finished_at).toLocaleString()}`
      : lastRunText(role);
    document.getElementById("agent-output-body").textContent = output ? JSON.stringify(output, null, 2) : "No output yet.";
    outputDialog.showModal();
  });
}
document.getElementById("agent-output-close").addEventListener("click", () => outputDialog.close());

loadAgentState();

function addLogLine(message) {
  const list = document.getElementById("agent-log");
  const item = document.createElement("li");
  const time = document.createElement("time");
  time.textContent = new Date(message.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const data = message.data || {};
  const chimeNote = message.from === "voice" && "chime" in data ? ` · chime ${data.chime || "none"}` : "";
  const detail = data.status
    ? `${data.status}${data.first_audio_ms != null ? `, first audio ${data.first_audio_ms} ms` : ""}${data.reason ? `: ${data.reason}` : ""}`
    : data.line
    ? `"${data.line}"${data.path ? ` (${data.path}${data.path === "claude" ? `, ${data.claude_ms} ms` : `: ${data.claude_reason}`})` : ""}`
    : data.reasons
      ? `refused: ${data.reasons.join("; ")}`
    : data.zone_id
      ? `${data.zone_id} ${data.distance_m} m (${data.reason}, ${data.speed_kmh} km/h)`
      : data.error || data.status || "";
  item.append(time, `${message.from.replace("creao:", "")} ${message.type} ${detail}${chimeNote}`.trim());
  list.prepend(item);
  while (list.children.length > LOG_LENGTH) list.lastChild.remove();
}

// Conditions from the latest hazard map, falling back to the demo copy.
async function loadConditions() {
  let hazardMap = null;
  for (const url of ["/data/hazard_map.json", "/data/hazard_map.cached.json"]) {
    const response = await fetch(url, { cache: "no-store" });
    if (response.ok) {
      hazardMap = await response.json();
      break;
    }
  }
  const box = document.getElementById("conditions");
  if (!hazardMap) {
    box.textContent = "No hazard map yet. Run agents.";
    return;
  }
  const { date, weather = {}, avalanche = {} } = hazardMap.conditions;
  const ratings = [avalanche.alpine, avalanche.treeline, avalanche.below_treeline];
  // Never substitute a rating: unknown stays unknown (CLAUDE.md).
  const avalancheLine = ratings.every((r) => r == null)
    ? "Avalanche rating: not available for this date"
    : `Avalanche: alpine ${avalanche.alpine ?? "n/a"}, treeline ${avalanche.treeline ?? "n/a"}, below treeline ${avalanche.below_treeline ?? "n/a"}`;
  const lines = [
    `${date} (${hazardMap.conditions.mode})`,
    `${weather.temp_c ?? "?"} °C, wind ${weather.wind_kmh ?? "?"} km/h, gusts ${weather.wind_gust_kmh ?? "?"} km/h, new snow ${weather.new_snow_cm ?? "?"} cm`,
    avalancheLine,
  ];
  box.replaceChildren(...lines.map((line) => Object.assign(document.createElement("div"), { textContent: line })));
}

loadConditions();


// ---- Crash check-in: countdown ring, demo reply buttons, simulated patrol console ----
// SIMULATED: nothing here contacts real patrol or 911. The reply buttons stand in for a microphone.

const checkinBox = document.getElementById("checkin");
const RING_LENGTH = 377; // 2 * pi * r (r = 60)
const checkinRun = { active: false, startedAt: 0, countdown: 30 };
let checkinHideTimer = null;

function checkinLeft() {
  return Math.max(0, checkinRun.countdown - (Date.now() - checkinRun.startedAt) / 1000);
}

function drawCheckin() {
  if (checkinRun.active) {
    const left = checkinLeft();
    document.getElementById("ring-fill").style.strokeDashoffset = String(RING_LENGTH * (1 - left / checkinRun.countdown));
    document.getElementById("checkin-count").textContent = String(Math.ceil(left));
    requestAnimationFrame(drawCheckin);
  }
}

function endCheckin(title, kind) {
  checkinRun.active = false;
  checkinBox.classList.remove("urgent");
  checkinBox.classList.add("done");
  document.getElementById("checkin-title").textContent = title;
  document.getElementById("checkin-count").textContent = kind === "cancelled" ? "✓" : "!";
  clearTimeout(checkinHideTimer);
  checkinHideTimer = setTimeout(() => { checkinBox.hidden = true; }, 6000);
}

function handleCheckin(message, fresh) {
  const { type, data } = message;
  if (type === "say" && fresh) {
    // The scripted line as the banner: calm for the question and the cancel, urgent only for the escalation.
    showBanner({ level: data.kind === "escalated" ? "warning" : data.kind === "reminder" ? "guidance" : "briefing",
                 line: data.line, zone_id: "check-in", path: "scripted" });
  }
  if (!fresh) return;
  if (type === "ask") {
    clearTimeout(checkinHideTimer);
    checkinRun.active = true;
    checkinRun.startedAt = Date.parse(message.time);
    checkinRun.countdown = data.countdown_s;
    checkinBox.classList.remove("urgent", "done");
    checkinBox.hidden = false;
    document.getElementById("checkin-title").textContent = "Are you okay?";
    requestAnimationFrame(drawCheckin);
  } else if (type === "reminder") {
    checkinBox.classList.add("urgent");
  } else if (type === "cancelled") {
    endCheckin(data.by === "moving" ? "Moving again" : "Glad you're okay", "cancelled");
  } else if (type === "escalated") {
    endCheckin("Patrol alerted (simulated)", "escalated");
  }
}

for (const button of document.querySelectorAll(".checkin-buttons button")) {
  button.addEventListener("click", () => fetch(`/api/checkin/reply?said=${button.dataset.said}`, { method: "POST" }));
}

// Patrol console.
const incidents = new Map(); // id -> incident
const incidentPins = new Map(); // id -> maplibre marker

function handlePatrol(message) {
  // The server only publishes an incident when a check-in escalates; cancelled check-ins never get here.
  const incident = message.data;
  incidents.set(incident.id, incident);
  renderPatrol();
  if (incident.report) {
    // The Incident Responder box reads "simulated report" with the report as its last output.
    agentState.incident = { lastRun: { run_id: "simulated", duration_s: 0, finished_at: new Date().toISOString() },
                            output: { incident: incident.id, source: incident.report_source, report: incident.report } };
    setOrgBox("incident", "done", lastRunText("incident"));
  }
}

function dl(rows) {
  const list = document.createElement("dl");
  for (const [term, value] of rows) {
    list.append(Object.assign(document.createElement("dt"), { textContent: term }),
                Object.assign(document.createElement("dd"), { textContent: value }));
  }
  return list;
}

function renderPatrol() {
  const container = document.getElementById("incident-cards");
  const cards = [...incidents.values()].sort((a, b) => b.id - a.id).map((incident) => {
    const card = document.createElement("div");
    card.className = `incident-card ${incident.status}`;
    const header = document.createElement("header");
    const when = new Date(incident.started_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
    header.append(Object.assign(document.createElement("b"), { textContent: `Incident ${incident.id} · ${when}` }),
                  Object.assign(document.createElement("span"), { className: "chip", textContent: incident.status }));
    const loc = incident.location;
    card.append(header, dl([
      ["Status", incident.status],
      ["Where", `${loc.area || "Glacier bowls"} (${loc.lat.toFixed(5)}, ${loc.lon.toFixed(5)})`],
      ["Last speed", `${incident.last_speed_kmh} km/h`],
      ["Skier said", incident.skier_said || "(waiting)"],
    ]));
    if (incident.report) {
      card.append(Object.assign(document.createElement("pre"), { textContent: incident.report }),
                  Object.assign(document.createElement("div"), { className: "source", textContent: `Report: ${incident.report_source}` }));
    }
    return card;
  });
  container.replaceChildren(...cards);
  document.getElementById("patrol-empty").hidden = incidents.size > 0;
  for (const incident of incidents.values()) {
    let pin = incidentPins.get(incident.id);
    if (!pin) {
      pin = new maplibregl.Marker({ element: Object.assign(document.createElement("div"), { className: "incident-pin" }) })
        .setLngLat([incident.location.lon, incident.location.lat]).addTo(map);
      incidentPins.set(incident.id, pin);
    }
    pin.getElement().className = `incident-pin ${incident.status}`;
  }
}

fetch("/api/patrol/incidents").then((r) => r.json()).then((list) => {
  for (const incident of list) incidents.set(incident.id, incident);
  renderPatrol();
});

document.getElementById("incident-live-run").addEventListener("click", async () => {
  const response = await fetch("/api/incident/live-run", { method: "POST" });
  const body = await response.json();
  addLogLine({ from: "creao:incident", type: body.status, data: { error: body.reason }, time: new Date().toISOString() });
});

// Skier view / Patrol view.
for (const button of document.querySelectorAll("[data-view]")) {
  button.addEventListener("click", () => {
    document.body.classList.toggle("patrol", button.dataset.view === "patrol");
    for (const other of document.querySelectorAll("[data-view]")) other.classList.toggle("playing", other === button);
  });
}
