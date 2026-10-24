// Cairn map. Plain Leaflet; styling here is a placeholder until the dashboard design pass.

const SPANKYS_LADDER = [50.0977, -122.8811];

const map = L.map("map").setView(SPANKYS_LADDER, 15);

L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
  maxZoom: 17,
  attribution:
    'Map data: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, ' +
    '<a href="http://viewfinderpanoramas.org">SRTM</a> | Map style: &copy; ' +
    '<a href="https://opentopomap.org">OpenTopoMap</a> (<a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA</a>)',
}).addTo(map);

// North American trail map colors, keyed by OSM piste:difficulty.
const DIFFICULTY_COLORS = {
  novice: "#2e8b3a",
  easy: "#2e8b3a",
  intermediate: "#2b5fa8",
  advanced: "#222222",
  expert: "#222222",
  freeride: "#222222",
};

function runStyle(feature) {
  const p = feature.properties;
  if (p.kind === "lift") {
    return { color: "#5b3a29", weight: 2, dashArray: "6 4" };
  }
  const color = DIFFICULTY_COLORS[p["piste:difficulty"]] || "#888888";
  const offPiste = p["piste:type"] !== "downhill" || p["piste:grooming"] === "backcountry";
  return {
    color,
    weight: p.kind === "run" && feature.geometry.type === "Polygon" ? 1 : 3,
    fillOpacity: 0.08,
    dashArray: offPiste ? "2 5" : null,
  };
}

function hazardStyle(feature) {
  const kind = feature.properties.kind;
  if (kind === "ski_area_boundary") {
    return { color: "#e07b00", weight: 2, dashArray: "8 6", fill: false };
  }
  return { color: "#c62828", weight: kind === "cliff" ? 4 : 2, fillOpacity: 0.2 };
}

// OSM tag values are user edited text, so escape them before putting them in HTML.
function escapeHTML(value) {
  const div = document.createElement("div");
  div.textContent = value;
  return div.innerHTML;
}

function popup(feature, layer) {
  const p = Object.fromEntries(
    Object.entries(feature.properties).map(([k, v]) => [k, escapeHTML(String(v))])
  );
  const lines = [
    `<b>${p.name || "(unnamed)"}</b>`,
    p.kind,
    p["piste:type"] && `piste:type=${p["piste:type"]}`,
    p["piste:difficulty"] && `difficulty=${p["piste:difficulty"]}`,
    p.aerialway && `aerialway=${p.aerialway}`,
    p.description,
    `<a href="https://www.openstreetmap.org/${p.osm_id}" target="_blank">${p.osm_id}</a>`,
  ];
  layer.bindPopup(lines.filter(Boolean).join("<br>"));
}

async function loadGeoJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

async function main() {
  const [runs, hazards] = await Promise.all([
    loadGeoJSON("/data/blackcomb_runs.geojson"),
    loadGeoJSON("/data/osm_hazards.geojson"),
  ]);

  const runsLayer = L.geoJSON(runs, { style: runStyle, onEachFeature: popup }).addTo(map);
  const hazardsLayer = L.geoJSON(hazards, { style: hazardStyle, onEachFeature: popup }).addTo(map);

  // The Overpass fetch box, so we can see what is and isn't covered.
  const [west, south, east, north] = runs.bbox;
  const boxLayer = L.rectangle([[south, west], [north, east]], {
    color: "#6a1b9a", weight: 1, fill: false, dashArray: "4 4",
  }).addTo(map);

  const spankysLayer = L.circleMarker(SPANKYS_LADDER, { radius: 6, color: "#6a1b9a" })
    .bindTooltip("Spanky's Ladder", { permanent: true, direction: "right" })
    .addTo(map);

  L.control.layers(null, {
    "Runs and lifts": runsLayer,
    "OSM hazards": hazardsLayer,
    "Fetch box": boxLayer,
    "Spanky's Ladder": spankysLayer,
  }).addTo(map);

  map.fitBounds(boxLayer.getBounds());
}

main().catch((error) => {
  console.error(error);
  alert(`Could not load map data: ${error.message}`);
});
