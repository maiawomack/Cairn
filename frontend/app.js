// Cairn map: a painted panorama of Blackcomb built from a DEM, with quiet routes on top.
import * as maplibregl from "maplibre-gl";

// [lon, lat], midpoint of OSM way 461517641.
const SPANKYS_LADDER = [-122.8811, 50.0977];

// Only show OSM hazards this close to Spanky's Ladder (dist_to_spankys_m is set by fetch_osm.py).
const HAZARD_RADIUS_M = 500;

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
    hazards: { type: "geojson", data: "/data/osm_hazards.geojson" },
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

const HAZARD_LAYERS = [
  {
    // Closed areas, plus the one OSM cliff that is mapped as an area.
    id: "hazard-area-hatch",
    type: "fill",
    source: "hazards",
    filter: hazardFilter(null, "Polygon"),
    paint: { "fill-pattern": "hatch" },
  },
  {
    id: "hazard-outline",
    type: "line",
    source: "hazards",
    filter: hazardFilter(null, "Polygon"),
    paint: { "line-color": "#b23a3a", "line-width": 1, "line-opacity": 0.5 },
  },
  {
    id: "cliff-hatch",
    type: "line",
    source: "hazards",
    filter: hazardFilter("cliff", "LineString"),
    paint: { "line-pattern": "hatch", "line-width": 8 },
  },
  {
    id: "cliff-line",
    type: "line",
    source: "hazards",
    filter: hazardFilter("cliff", "LineString"),
    paint: { "line-color": "#b23a3a", "line-width": 0.8, "line-opacity": 0.6 },
  },
];

// Cliffs and closed areas near Spanky's. Ski area boundary is left out.
// geometry-type is "LineString" for both LineString and MultiLineString.
function hazardFilter(kind, geometryType) {
  const filter = [
    "all",
    ["<=", ["get", "dist_to_spankys_m"], HAZARD_RADIUS_M],
    kind ? ["==", ["get", "kind"], kind] : ["in", ["get", "kind"], ["literal", ["cliff", "closed"]]],
  ];
  if (geometryType) filter.push(["==", ["geometry-type"], geometryType]);
  return filter;
}

const LAYER_GROUPS = {
  runs: ["runs", "runs-hover"],
  lifts: ["lifts", "lifts-hover"],
  hazards: HAZARD_LAYERS.map((layer) => layer.id),
};

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

// Soft red diagonal hatching, drawn once on a canvas and used as a fill and line pattern.
function hatchImage() {
  const size = 12;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  ctx.strokeStyle = "rgba(178, 58, 58, 0.55)";
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
  map.addImage("hatch", hatchImage());
  for (const layer of HAZARD_LAYERS) {
    map.addLayer(layer, "runs-hover");
  }
});

new maplibregl.Marker({ element: spankysMarker(), anchor: "left", offset: [-8, 0] })
  .setLngLat(SPANKYS_LADDER)
  .addTo(map);

function spankysMarker() {
  const el = document.createElement("div");
  el.className = "spankys-marker";
  el.innerHTML = '<span class="dot"></span><span class="label">Spanky’s Ladder</span>';
  return el;
}

// Run and lift names on hover only.
const hoverPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, className: "run-name", offset: 8 });

for (const layer of ["runs-hover", "lifts-hover"]) {
  map.on("mousemove", layer, (event) => {
    const name = event.features[0].properties.name;
    if (!name) return hoverPopup.remove();
    map.getCanvas().style.cursor = "pointer";
    hoverPopup.setLngLat(event.lngLat).setText(name).addTo(map);
  });
  map.on("mouseleave", layer, () => {
    map.getCanvas().style.cursor = "";
    hoverPopup.remove();
  });
}

for (const checkbox of document.querySelectorAll(".layer-toggle input")) {
  checkbox.addEventListener("change", () => {
    const visibility = checkbox.checked ? "visible" : "none";
    for (const layer of LAYER_GROUPS[checkbox.dataset.group]) {
      map.setLayoutProperty(layer, "visibility", visibility);
    }
  });
}
