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
    // Proposed by build_safe_exits.py, not yet reviewed.
    exits: { type: "geojson", data: "/data/safe_exits.geojson" },
    skier: { type: "geojson", data: emptyCollection() },
    "skier-trail": { type: "geojson", data: emptyCollection() },
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

// Proposed safe exits, and the replayed skier: a dot with a fading trail of recent fixes.
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
    id: "exits",
    type: "line",
    source: "exits",
    layout: { visibility: "none", "line-cap": "round" },
    paint: { "line-color": ROUTE_COLOR, "line-width": 2.5, "line-opacity": 0.85 },
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
  exits: ["exits"],
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
  let camera = null;
  setReplayButton(scenarioId);
  setSpeedButtons(speed);

  replaySource = new EventSource(`/api/replay/${encodeURIComponent(scenarioId)}?speed=${speed}`);
  replaySource.addEventListener("session", (event) => {
    const session = JSON.parse(event.data);
    replaySession = session.session;
    if (session.target_zone) pulseTargetZone(session.target_zone);
  });
  replaySource.onmessage = (event) => {
    const { lat, lon, playback_speed: playing } = JSON.parse(event.data).data;
    trail.unshift([lon, lat]);
    trail.length = Math.min(trail.length, TRAIL_LENGTH + 1);
    map.getSource("skier").setData(pointCollection([trail[0]]));
    map.getSource("skier-trail").setData(pointCollection(trail.slice(1)));
    showPlaybackSpeed(playing);

    // Smooth the camera so GPS noise doesn't shake the view.
    camera = camera
      ? [camera[0] + (lon - camera[0]) * CAMERA_SMOOTHING, camera[1] + (lat - camera[1]) * CAMERA_SMOOTHING]
      : [lon, lat];
    map.easeTo({ center: camera, zoom: FOLLOW_ZOOM, duration: 1000 / playing, easing: (t) => t });  };
  // The server sends "end" when the track is done; close so EventSource doesn't reconnect and replay again.
  replaySource.addEventListener("end", stopReplay);
  replaySource.onerror = stopReplay;
}

function stopReplay() {
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
  const role = message.from.replace("creao:", "");
  const node = document.querySelector(`.org-chart [data-role="${role}"]`);
  if (node) updateOrgNode(node, message);
  if (role === "orchestrator") {
    if (message.type === "started") {
      for (const li of document.querySelectorAll(".org-chart [data-role]")) setOrgState(li, "", "");
    }
    runButton.disabled = message.type === "started";
    if (message.type === "finished") loadConditions();
  }
  // History replays on page load; only show and speak warnings that happen now.
  const fresh = Date.parse(message.time) > PAGE_OPENED_AT - 2000;
  if (fresh && role === "coordinator" && message.type === "decision") showBanner(message.data);
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
    utterances[data.utterance] = { sampleRate: data.sample_rate, nextTime: 0, leftover: null };
  } else if (type === "chunk") {
    playChunk(utterances[data.utterance], data.audio);
  } else if (type === "end") {
    delete utterances[data.utterance];
  } else if (type === "unavailable" || type === "error") {
    // Never silent: speak the line with the browser's own voice instead.
    speakWithBrowser(data.line);
  }
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

function updateOrgNode(node, message) {
  const { type, data } = message;
  if (type === "started") setOrgState(node, "running", "starting");
  if (type === "status") setOrgState(node, "running", `${data.status} ${Math.round(data.elapsed_s)} s`);
  if (type === "finished") setOrgState(node, "done", `${data.duration_s ?? data.wall_s} s`);
  if (type === "failed") setOrgState(node, "failed", "failed");
}

function setOrgState(node, state, label) {
  node.classList.remove("running", "done", "failed");
  if (state) node.classList.add(state);
  node.querySelector("small").textContent = label;
}

function addLogLine(message) {
  const list = document.getElementById("agent-log");
  const item = document.createElement("li");
  const time = document.createElement("time");
  time.textContent = new Date(message.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const data = message.data || {};
  const detail = data.status
    ? `${data.status}${data.first_audio_ms != null ? `, first audio ${data.first_audio_ms} ms` : ""}${data.reason ? `: ${data.reason}` : ""}`
    : data.line
    ? `"${data.line}" (${data.path}${data.path === "claude" ? `, ${data.claude_ms} ms` : `: ${data.claude_reason}`})`
    : data.zone_id
      ? `${data.zone_id} ${data.distance_m} m (${data.reason}, ${data.speed_kmh} km/h)`
      : data.error || data.status || "";
  item.append(time, `${message.from.replace("creao:", "")} ${message.type} ${detail}`.trim());
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
