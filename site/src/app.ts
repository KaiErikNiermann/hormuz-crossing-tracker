import type { Map as MLMap, GeoJSONSource, MapMouseEvent, NavigationControl as NavControl, Popup as MLPopup } from "maplibre-gl";

declare const maplibregl: {
  Map: typeof MLMap;
  NavigationControl: typeof NavControl;
  Popup: typeof MLPopup;
};

// === Types ===

interface BBox {
  readonly south: number;
  readonly west: number;
  readonly north: number;
  readonly east: number;
}

type LngLat = readonly [number, number];
type Polygon = readonly LngLat[];

interface Geofence {
  readonly bbox: BBox;
  readonly west: Polygon;
  readonly east: Polygon;
}

interface VesselData {
  readonly mmsi: number;
  readonly name: string | null;
  readonly type: string;
  readonly lat: number;
  readonly lng: number;
  readonly seen: string;
  readonly direction: string | null;
  readonly zone: string | null;
}

interface SnapshotStats {
  readonly total_vessels: number;
  readonly total_crossings: number;
  readonly vessel_types: Readonly<Record<string, number>>;
}

interface Snapshot {
  readonly generated_at: string;
  readonly vessels: readonly VesselData[];
  readonly crossings: readonly unknown[];
  readonly stats: SnapshotStats;
}

// Timeline types
interface VesselMeta {
  readonly mmsi: string | null;
  readonly name: string | null;
  readonly type: string;
  readonly flag: string | null;
}

// Position tuple: [vesselId, lat, lon, bearing, direction, transit, zone, source?]
type PositionTuple = readonly [string, number, number, number | null, string | null, string | null, string | null, string | null];

interface TimelineData {
  readonly generated_at: string;
  readonly source: string;
  readonly date_range: { readonly start: string; readonly end: string };
  readonly dates: readonly string[];
  readonly vessels: Readonly<Record<string, VesselMeta>>;
  readonly positions: Readonly<Record<string, readonly PositionTuple[]>>;
  readonly daily_stats: Readonly<Record<string, Record<string, number>>>;
}

// Build version for cache-busting data fetches (updated by esbuild --define)
declare const __BUILD_VERSION__: string;
const BUILD_VERSION: string = typeof __BUILD_VERSION__ !== "undefined" ? __BUILD_VERSION__ : "";

function dataUrl(path: string): string {
  return BUILD_VERSION ? `${path}?v=${BUILD_VERSION}` : path;
}

// === Constants ===

const STYLES: Readonly<Record<string, string>> = {
  light: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
  dark: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
};

// Real TSS (Traffic Separation Scheme) from IMO/OSM data

const TSS_INBOUND: readonly LngLat[] = [
  [56.38052, 26.52025],
  [56.47858, 26.55841],
  [56.55022, 26.55847],
  [56.61132, 26.46796],
];

const TSS_OUTBOUND: readonly LngLat[] = [
  [56.34518, 26.59506],
  [56.46497, 26.62409],
  [56.59160, 26.62385],
  [56.67343, 26.50350],
];

const TSS_SEPARATION: readonly LngLat[] = [
  [56.35047, 26.58019],
  [56.37335, 26.53309],
  [56.47577, 26.57467],
  [56.55934, 26.57515],
  [56.62601, 26.47652],
  [56.65686, 26.49411],
  [56.58147, 26.60859],
  [56.54750, 26.60898],
  [56.51199, 26.60835],
  [56.48197, 26.60856],
  [56.46594, 26.60817],
  [56.35047, 26.58019],
];

// Short approach stubs — trimmed so they don't cut through the west zone
const APPROACH_INBOUND: readonly LngLat[] = [
  [56.28, 26.48],
  [56.38052, 26.52025],
];

const APPROACH_OUTBOUND: readonly LngLat[] = [
  [56.67343, 26.50350],
  [56.78, 26.46],
];

const GEOFENCE: Geofence = {
  bbox: { south: 25.4, west: 55.0, north: 27.4, east: 57.8 },
  west: [
    [55.55, 26.65],
    [56.15, 26.55],
    [56.25, 26.20],
    [55.65, 26.15],
    [55.55, 26.65],
  ],
  east: [
    [56.68, 26.35],
    [57.10, 26.12],
    [57.05, 25.78],
    [56.62, 25.88],
    [56.68, 26.35],
  ],
};

const VESSEL_COLORS: Readonly<Record<string, string>> = {
  Cargo: "#3b82f6",
  Tanker: "#ef4444",
  Fishing: "#22c55e",
  Passenger: "#f59e0b",
  Other: "#a855f7",
};

// === State ===

const TRAIL_LENGTH = 0.035; // degrees — very short vector-field stub
const MAX_JUMP = 0.4; // degrees — skip unrealistic grid jumps
const ARROW_SIZE = 12; // pixels — arrowhead canvas size

let currentTheme: "light" | "dark" = "light";
let map: MLMap;
let timelineData: TimelineData | null = null;
let currentDateIndex = -1;
let animationTimer: ReturnType<typeof setInterval> | null = null;
let sourceFilter: "all" | "gfw" | "ais" = "all";


// === Helpers ===

function bboxToPolygon(box: BBox): [number, number][] {
  return [
    [box.west, box.south],
    [box.east, box.south],
    [box.east, box.north],
    [box.west, box.north],
    [box.west, box.south],
  ];
}

function polygonCenter(coords: Polygon): [number, number] {
  const pts = coords.slice(0, -1);
  const lng = pts.reduce((s, p) => s + p[0], 0) / pts.length;
  const lat = pts.reduce((s, p) => s + p[1], 0) / pts.length;
  return [lng, lat];
}

// === Directional trail stubs ===

function buildTrailFeatures(positions: readonly PositionTuple[]): GeoJSON.Feature[] {
  if (!timelineData) return [];

  const features: GeoJSON.Feature[] = [];

  for (const p of positions) {
    const bearing = p[3];
    if (bearing === null) continue;

    const lat = p[1];
    const lon = p[2];
    const meta = timelineData.vessels[p[0]];
    const vtype = meta?.type ?? "Other";
    const color = VESSEL_COLORS[vtype] ?? VESSEL_COLORS["Other"]!;

    // Compute tail point: go backward from current position (opposite of bearing)
    const reverseBearing = ((bearing + 180) % 360) * (Math.PI / 180);
    const tailLat = lat + TRAIL_LENGTH * Math.cos(reverseBearing);
    const tailLon = lon + TRAIL_LENGTH * Math.sin(reverseBearing) / Math.cos(lat * Math.PI / 180);

    // Skip if the jump looks unrealistic
    if (Math.abs(tailLat - lat) > MAX_JUMP || Math.abs(tailLon - lon) > MAX_JUMP) continue;

    features.push({
      type: "Feature",
      geometry: {
        type: "LineString",
        coordinates: [
          [tailLon, tailLat],
          [lon, lat],
        ],
      },
      properties: { color },
    });
  }

  return features;
}

// === Arrow images ===

function createArrowImage(color: string, size: number): ImageData {
  const canvas = document.createElement("canvas");
  const s = size * 2; // retina
  canvas.width = s;
  canvas.height = s;
  const ctx = canvas.getContext("2d")!;

  // Draw a filled triangle pointing up (bearing 0 = north)
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(s / 2, 1);           // tip (top center)
  ctx.lineTo(s * 0.82, s * 0.85); // bottom right
  ctx.lineTo(s / 2, s * 0.6);     // notch
  ctx.lineTo(s * 0.18, s * 0.85); // bottom left
  ctx.closePath();
  ctx.fill();

  return ctx.getImageData(0, 0, s, s);
}

function addArrowImages(): void {
  for (const [name, color] of Object.entries(VESSEL_COLORS)) {
    const id = `arrow-${name}`;
    if (!map.hasImage(id)) {
      const img = createArrowImage(color, ARROW_SIZE);
      map.addImage(id, img, { pixelRatio: 2 });
    }
  }
}

// === Map initialization ===

function initMap(): void {
  map = new maplibregl.Map({
    container: "map",
    style: STYLES[currentTheme]!,
    center: [56.35, 26.25],
    zoom: 8,
    attributionControl: {},
  });

  map.addControl(new maplibregl.NavigationControl(), "bottom-right");

  map.on("load", () => {
    addArrowImages();
    addGeofenceLayers();
    addTSSLayers();
    loadVesselData();
    startHeartbeat();
  });
}

// === TSS shipping lane layers ===

function addTSSLayers(): void {
  map.addSource("tss-separation", {
    type: "geojson",
    data: {
      type: "Feature",
      properties: {},
      geometry: { type: "Polygon", coordinates: [TSS_SEPARATION as unknown as number[][]] },
    },
  });
  map.addLayer({
    id: "tss-separation-fill",
    type: "fill",
    source: "tss-separation",
    paint: { "fill-color": "#ff9800", "fill-opacity": 0.12 },
  });
  map.addLayer({
    id: "tss-separation-line",
    type: "line",
    source: "tss-separation",
    paint: { "line-color": "#ff9800", "line-width": 1.5, "line-dasharray": [3, 3] },
  });

  map.addSource("tss-inbound", {
    type: "geojson",
    data: {
      type: "Feature",
      properties: {},
      geometry: {
        type: "LineString",
        coordinates: [...APPROACH_INBOUND, ...TSS_INBOUND] as unknown as number[][],
      },
    },
  });
  map.addLayer({
    id: "tss-inbound-line",
    type: "line",
    source: "tss-inbound",
    paint: { "line-color": "#4caf50", "line-width": 2, "line-opacity": 0.7 },
  });

  map.addSource("tss-outbound", {
    type: "geojson",
    data: {
      type: "Feature",
      properties: {},
      geometry: {
        type: "LineString",
        coordinates: [...TSS_OUTBOUND, ...APPROACH_OUTBOUND] as unknown as number[][],
      },
    },
  });
  map.addLayer({
    id: "tss-outbound-line",
    type: "line",
    source: "tss-outbound",
    paint: { "line-color": "#f44336", "line-width": 2, "line-opacity": 0.7 },
  });

  map.addSource("tss-labels", {
    type: "geojson",
    data: {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: { label: "INBOUND" },
          geometry: { type: "Point", coordinates: [56.48, 26.53] },
        },
        {
          type: "Feature",
          properties: { label: "OUTBOUND" },
          geometry: { type: "Point", coordinates: [56.52, 26.63] },
        },
        {
          type: "Feature",
          properties: { label: "TSS" },
          geometry: { type: "Point", coordinates: [56.50, 26.58] },
        },
      ],
    },
  });
  map.addLayer({
    id: "tss-labels",
    type: "symbol",
    source: "tss-labels",
    layout: {
      "text-field": ["get", "label"],
      "text-size": 10,
      "text-font": ["Open Sans Regular", "Arial Unicode MS Regular"],
      "text-allow-overlap": true,
    },
    paint: {
      "text-color": currentTheme === "dark" ? "#ff980090" : "#e6850090",
      "text-halo-color": currentTheme === "dark" ? "#000000" : "#ffffff",
      "text-halo-width": 1.5,
    },
  });
}

// === Geofence zone layers ===

function addGeofenceLayers(): void {
  map.addSource("bbox", {
    type: "geojson",
    data: {
      type: "Feature",
      properties: {},
      geometry: { type: "Polygon", coordinates: [bboxToPolygon(GEOFENCE.bbox)] },
    },
  });
  map.addLayer({
    id: "bbox-fill",
    type: "fill",
    source: "bbox",
    paint: { "fill-color": "#ffc107", "fill-opacity": 0.05 },
  });
  map.addLayer({
    id: "bbox-line",
    type: "line",
    source: "bbox",
    paint: { "line-color": "#ffc107", "line-width": 1.5, "line-dasharray": [6, 4] },
  });

  map.addSource("zone-west", {
    type: "geojson",
    data: {
      type: "Feature",
      properties: {},
      geometry: { type: "Polygon", coordinates: [GEOFENCE.west as unknown as number[][]] },
    },
  });
  map.addLayer({
    id: "zone-west-fill",
    type: "fill",
    source: "zone-west",
    paint: { "fill-color": "#4caf50", "fill-opacity": 0.12 },
  });
  map.addLayer({
    id: "zone-west-line",
    type: "line",
    source: "zone-west",
    paint: { "line-color": "#4caf50", "line-width": 2 },
  });

  map.addSource("zone-east", {
    type: "geojson",
    data: {
      type: "Feature",
      properties: {},
      geometry: { type: "Polygon", coordinates: [GEOFENCE.east as unknown as number[][]] },
    },
  });
  map.addLayer({
    id: "zone-east-fill",
    type: "fill",
    source: "zone-east",
    paint: { "fill-color": "#2196f3", "fill-opacity": 0.12 },
  });
  map.addLayer({
    id: "zone-east-line",
    type: "line",
    source: "zone-east",
    paint: { "line-color": "#2196f3", "line-width": 2 },
  });

  map.addSource("zone-labels", {
    type: "geojson",
    data: {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: { label: "WEST ZONE" },
          geometry: { type: "Point", coordinates: polygonCenter(GEOFENCE.west) },
        },
        {
          type: "Feature",
          properties: { label: "EAST ZONE" },
          geometry: { type: "Point", coordinates: polygonCenter(GEOFENCE.east) },
        },
      ],
    },
  });
  map.addLayer({
    id: "zone-labels",
    type: "symbol",
    source: "zone-labels",
    layout: {
      "text-field": ["get", "label"],
      "text-size": 12,
      "text-font": ["Open Sans Bold", "Arial Unicode MS Bold"],
      "text-allow-overlap": true,
    },
    paint: {
      "text-color": currentTheme === "dark" ? "#ffffff" : "#333333",
      "text-halo-color": currentTheme === "dark" ? "#000000" : "#ffffff",
      "text-halo-width": 2,
    },
  });
}

// === Vessel data ===

function loadVesselData(): void {
  // Try timeline data first (has daily positions + direction)
  fetch(dataUrl("data/vessels_timeline.json"))
    .then((r) => {
      if (!r.ok) throw new Error("No timeline data");
      return r.json() as Promise<TimelineData>;
    })
    .then((data) => {
      timelineData = data;
      initTimeline(data);
    })
    .catch(() => {
      // Fall back to single snapshot
      fetch(dataUrl("data/vessels.json"))
        .then((r) => {
          if (!r.ok) throw new Error("No vessel data available yet");
          return r.json() as Promise<Snapshot>;
        })
        .then((data) => {
          renderVessels(data.vessels);
          updateStats(data);
          updateSnapshotTime(data.generated_at);
        })
        .catch(() => {
          document.getElementById("vessel-count")!.textContent = "0";
          document.getElementById("crossing-count")!.textContent = "0";
          document.getElementById("snapshot-time")!.textContent = "No data yet";
        });
    });
}

function filterBySource(positions: readonly PositionTuple[]): readonly PositionTuple[] {
  if (sourceFilter === "all") return positions;
  return positions.filter((p) => {
    const src = p[7] ?? "gfw";
    if (sourceFilter === "gfw") return src === "gfw" || src === "both";
    return src === "ais" || src === "both";
  });
}

function renderVesselsForDate(date: string): void {
  if (!timelineData) return;

  const rawPositions = timelineData.positions[date];
  if (!rawPositions) return;
  const positions = filterBySource(rawPositions);

  const features = positions
    .filter((p) => p[1] && p[2])
    .map((p) => {
      const meta = timelineData!.vessels[p[0]];
      const vtype = meta?.type ?? "Other";
      return {
        type: "Feature" as const,
        geometry: { type: "Point" as const, coordinates: [p[2], p[1]] },
        properties: {
          vesselId: p[0],
          mmsi: meta?.mmsi ?? "-",
          name: meta?.name ?? `Vessel ${p[0].slice(0, 8)}`,
          type: vtype,
          flag: meta?.flag ?? "-",
          bearing: p[3] ?? 0,
          hasBearing: p[3] !== null ? 1 : 0,
          direction: p[4] ?? "unknown",
          transit: p[5] ?? "-",
          zone: p[6] ?? "-",
          icon: `arrow-${vtype}`,
          color: VESSEL_COLORS[vtype] ?? VESSEL_COLORS["Other"]!,
        },
      };
    });

  const geojson = { type: "FeatureCollection" as const, features };

  // Render trails first (so they appear below dots)
  renderTrails(positions);

  const existing = map.getSource("vessels") as GeoJSONSource | undefined;
  if (existing) {
    existing.setData(geojson);
  } else {
    map.addSource("vessels", { type: "geojson", data: geojson });
    addVesselLayers();
  }

  // Update stats (use filtered count, not precomputed total)
  const stats = timelineData.daily_stats[date];
  document.getElementById("vessel-count")!.textContent = String(positions.length);
  document.getElementById("crossing-count")!.textContent = String(stats?.crossings ?? 0);

  // Update direction stats
  let inbound = 0;
  let outbound = 0;
  for (const p of positions) {
    if (p[5] === "inbound") inbound++;
    else if (p[5] === "outbound") outbound++;
  }
  const dirStats = document.getElementById("direction-stats")!;
  dirStats.classList.remove("hidden");
  document.getElementById("inbound-count")!.textContent = String(inbound);
  document.getElementById("outbound-count")!.textContent = String(outbound);

  updateSnapshotTime(date);
}

function renderVessels(vessels: readonly VesselData[]): void {
  const features = vessels
    .filter((v) => v.lat && v.lng)
    .map((v) => ({
      type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: [v.lng, v.lat] },
      properties: {
        mmsi: v.mmsi,
        name: v.name ?? `MMSI ${v.mmsi}`,
        type: v.type || "Other",
        direction: v.direction ?? "unknown",
        bearing: 0,
        hasBearing: 0,
        transit: "-",
        zone: v.zone ?? "-",
        seen: v.seen || "-",
        flag: "-",
        icon: `arrow-${v.type || "Other"}`,
        color: VESSEL_COLORS[v.type] ?? VESSEL_COLORS["Other"]!,
      },
    }));

  const geojson = { type: "FeatureCollection" as const, features };

  const existing = map.getSource("vessels") as GeoJSONSource | undefined;
  if (existing) {
    existing.setData(geojson);
    return;
  }

  map.addSource("vessels", { type: "geojson", data: geojson });
  addVesselLayers();
}

function renderTrails(positions: readonly PositionTuple[]): void {
  const trailFeatures = buildTrailFeatures(positions);
  const trailGeojson = { type: "FeatureCollection" as const, features: trailFeatures };

  const existingTrails = map.getSource("vessel-trails") as GeoJSONSource | undefined;
  if (existingTrails) {
    existingTrails.setData(trailGeojson);
  } else {
    map.addSource("vessel-trails", { type: "geojson", data: trailGeojson });
    map.addLayer({
      id: "vessel-trails-line",
      type: "line",
      source: "vessel-trails",
      paint: {
        "line-color": ["get", "color"],
        "line-width": 1.5,
        "line-opacity": 0.35,
      },
      layout: {
        "line-cap": "round",
      },
    });
  }
}

function addVesselLayers(): void {
  // Fallback circles for vessels without bearing (no arrow rotation)
  map.addLayer({
    id: "vessels-circle",
    type: "circle",
    source: "vessels",
    filter: ["==", ["get", "hasBearing"], 0],
    paint: {
      "circle-radius": 3,
      "circle-color": ["get", "color"],
      "circle-stroke-width": 0.5,
      "circle-stroke-color": currentTheme === "dark" ? "#ffffff30" : "#00000020",
      "circle-opacity": 0.7,
    },
  });

  // Arrow symbols for vessels with bearing data
  map.addLayer({
    id: "vessels-arrows",
    type: "symbol",
    source: "vessels",
    filter: ["==", ["get", "hasBearing"], 1],
    layout: {
      "icon-image": ["get", "icon"],
      "icon-size": 0.9,
      "icon-rotate": ["get", "bearing"],
      "icon-rotation-alignment": "map",
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
    },
    paint: {
      "icon-opacity": 0.85,
    },
  });

  const vesselClickHandler = (e: MapMouseEvent & { features?: GeoJSON.Feature[] }): void => {
    const feature = e.features?.[0];
    if (!feature) return;
    const props = feature.properties ?? {};
    const coords = (feature.geometry as GeoJSON.Point).coordinates.slice() as [number, number];

    const transitHtml = props.transit !== "-"
      ? `<br>Transit: <strong>${props.transit as string}</strong>`
      : "";
    const zoneHtml = props.zone && props.zone !== "-"
      ? `<br>Zone: ${props.zone as string}`
      : "";

    new maplibregl.Popup({ offset: 12 })
      .setLngLat(coords)
      .setHTML(
        `<div class="popup-title">${props.name as string}</div>
         <div class="popup-detail">
           Type: ${props.type as string}<br>
           MMSI: ${props.mmsi as string}<br>
           Flag: ${props.flag as string}<br>
           Direction: ${props.direction as string}${transitHtml}${zoneHtml}
         </div>`
      )
      .addTo(map);
  };

  for (const layerId of ["vessels-arrows", "vessels-circle"]) {
    map.on("click", layerId, vesselClickHandler);
    map.on("mouseenter", layerId, () => {
      map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", layerId, () => {
      map.getCanvas().style.cursor = "";
    });
  }
}

function updateStats(data: Snapshot): void {
  document.getElementById("vessel-count")!.textContent =
    String(data.stats?.total_vessels ?? data.vessels?.length ?? 0);
  document.getElementById("crossing-count")!.textContent =
    String(data.stats?.total_crossings ?? data.crossings?.length ?? 0);
}

function updateSnapshotTime(isoString: string | null): void {
  const el = document.getElementById("snapshot-time")!;
  if (!isoString) {
    el.textContent = "Unknown";
    return;
  }
  // If it's just a date (YYYY-MM-DD), show as-is
  if (isoString.length === 10) {
    el.textContent = isoString;
  } else {
    el.textContent = new Date(isoString).toLocaleString();
  }
}

// === Timeline slider ===

function initSourceFilter(data: TimelineData): void {
  const container = document.getElementById("source-filter");
  if (!container) return;

  // Only show if timeline has multiple sources
  const sources = (data as unknown as Record<string, unknown>).sources as string[] | undefined;
  if (!sources || sources.length < 2) {
    container.classList.add("hidden");
    return;
  }
  container.classList.remove("hidden");

  for (const btn of document.querySelectorAll<HTMLButtonElement>(".source-btn")) {
    btn.addEventListener("click", () => {
      sourceFilter = btn.dataset.source as "all" | "gfw" | "ais";
      for (const b of document.querySelectorAll(".source-btn")) b.classList.remove("active");
      btn.classList.add("active");
      if (timelineData && currentDateIndex >= 0) {
        const date = timelineData.dates[currentDateIndex]!;
        renderVesselsForDate(date);
        updateChartHighlight(timelineData);
      }
    });
  }
}

function initTimeline(data: TimelineData): void {
  const bar = document.getElementById("timeline-bar")!;
  const slider = document.getElementById("timeline-slider") as HTMLInputElement;
  const dateLabel = document.getElementById("timeline-date")!;
  const countLabel = document.getElementById("timeline-count")!;
  const playBtn = document.getElementById("timeline-play")!;

  const maxIdx = data.dates.length - 1;
  slider.min = "0";
  slider.max = String(maxIdx);

  // Start at latest full-data date (skip the ~5 sparse trailing days)
  const startIdx = Math.max(0, maxIdx - 5);
  slider.value = String(startIdx);
  currentDateIndex = startIdx;

  bar.classList.remove("hidden");
  initSourceFilter(data);
  initChart(data);

  // Render initial date
  renderDateAtIndex(startIdx);

  slider.addEventListener("input", () => {
    const idx = parseInt(slider.value, 10);
    currentDateIndex = idx;
    renderDateAtIndex(idx);
  });

  playBtn.addEventListener("click", () => {
    if (animationTimer) {
      stopAnimation();
    } else {
      startAnimation();
    }
  });

  function renderDateAtIndex(idx: number): void {
    const date = data.dates[idx]!;
    const stats = data.daily_stats[date];
    dateLabel.textContent = date;
    countLabel.textContent = `${stats?.total ?? 0} vessels`;
    renderVesselsForDate(date);
    updateChartHighlight(data);
  }

  function startAnimation(): void {
    playBtn.innerHTML = "&#9646;&#9646;";
    animationTimer = setInterval(() => {
      currentDateIndex = (currentDateIndex + 1) % data.dates.length;
      slider.value = String(currentDateIndex);
      renderDateAtIndex(currentDateIndex);
    }, 800);
  }

  function stopAnimation(): void {
    if (animationTimer) {
      clearInterval(animationTimer);
      animationTimer = null;
    }
    playBtn.innerHTML = "&#9654;";
  }
}

// === Crossings chart ===

let chartVisible = false;
let chartMode: "direction" | "type" = "direction";
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let Plot: any = null;

interface ChartRow {
  date: Date;
  dateStr: string;
  inbound: number;
  outbound: number;
  total: number;
  idx: number;
  cx_Cargo: number;
  cx_Tanker: number;
  cx_Fishing: number;
  cx_Passenger: number;
  cx_Other: number;
}

function initChart(data: TimelineData): void {
  const toggleBtn = document.getElementById("timeline-chart-toggle")!;
  const panel = document.getElementById("chart-panel")!;

  toggleBtn.addEventListener("click", () => {
    chartVisible = !chartVisible;
    toggleBtn.classList.toggle("active", chartVisible);
    if (chartVisible) {
      panel.classList.remove("hidden");
      if (!Plot) {
        loadAndRenderChart(data);
      } else {
        renderChart(data);
      }
    } else {
      panel.classList.add("hidden");
    }
  });

  // Tab switching
  for (const tab of document.querySelectorAll<HTMLButtonElement>(".chart-tab")) {
    tab.addEventListener("click", () => {
      chartMode = tab.dataset.mode as "direction" | "type";
      for (const t of document.querySelectorAll(".chart-tab")) t.classList.remove("active");
      tab.classList.add("active");
      if (Plot) renderChart(data);
    });
  }
}

function loadAndRenderChart(data: TimelineData): void {
  const container = document.getElementById("chart-container")!;
  container.innerHTML = '<div class="chart-loading">Loading chart\u2026</div>';

  import("https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6/+esm" as string)
    .then((mod) => {
      Plot = mod;
      renderChart(data);
    })
    .catch((err) => {
      container.innerHTML = '<div class="chart-loading">Failed to load chart</div>';
      console.error("Failed to load Observable Plot:", err);
    });
}

function loess(rows: ChartRow[], bandwidth: number): number[] {
  // LOESS: locally weighted scatterplot smoothing
  const n = rows.length;
  const result: number[] = [];
  const span = Math.max(2, Math.floor(n * bandwidth));

  for (let i = 0; i < n; i++) {
    // Tricube weight based on distance from point i
    let sumW = 0, sumWX = 0, sumWY = 0, sumWXX = 0, sumWXY = 0;
    const maxDist = span;
    for (let j = 0; j < n; j++) {
      const dist = Math.abs(i - j);
      if (dist > maxDist) continue;
      const u = dist / (maxDist + 1);
      const w = Math.pow(1 - u * u * u, 3); // tricube kernel
      const x = rows[j]!.idx;
      const y = rows[j]!.total;
      sumW += w;
      sumWX += w * x;
      sumWY += w * y;
      sumWXX += w * x * x;
      sumWXY += w * x * y;
    }
    // Local linear fit
    const xi = rows[i]!.idx;
    const denom = sumW * sumWXX - sumWX * sumWX;
    if (Math.abs(denom) < 1e-12) {
      result.push(sumWY / sumW);
    } else {
      const a = (sumWXX * sumWY - sumWX * sumWXY) / denom;
      const b = (sumW * sumWXY - sumWX * sumWY) / denom;
      result.push(Math.max(0, a + b * xi));
    }
  }
  return result;
}

const TYPE_COLORS: Readonly<Record<string, string>> = {
  Cargo: "#3b82f6",
  Tanker: "#ef4444",
  Fishing: "#22c55e",
  Passenger: "#f59e0b",
  Other: "#a855f7",
};

const VESSEL_TYPE_KEYS = ["Cargo", "Tanker", "Fishing", "Passenger", "Other"] as const;

let chartTooltipEl: HTMLDivElement | null = null;
let chartTooltipAbort: AbortController | null = null;

function getChartTooltip(): HTMLDivElement {
  if (!chartTooltipEl) {
    chartTooltipEl = document.createElement("div");
    chartTooltipEl.className = "chart-tooltip hidden";
    document.getElementById("chart-panel")!.appendChild(chartTooltipEl);
  }
  return chartTooltipEl;
}

function attachChartTooltip(chart: Element, rows: ChartRow[], data: TimelineData): void {
  // Abort previous listeners to prevent leaks on re-render
  if (chartTooltipAbort) chartTooltipAbort.abort();
  chartTooltipAbort = new AbortController();
  const { signal } = chartTooltipAbort;

  const svg = chart.tagName === "svg" ? chart : chart.querySelector("svg");
  if (!svg) return;

  const tooltip = getChartTooltip();
  const panel = document.getElementById("chart-panel")!;

  // Find the x-axis band scale info from bar rects
  const barRects = svg.querySelectorAll('g[aria-label="bar"] rect');
  if (!barRects.length) return;

  // Build a map of x-position → date index
  const bandMap: { x: number; width: number; idx: number }[] = [];
  const seen = new Set<number>();
  barRects.forEach((rect) => {
    const x = parseFloat(rect.getAttribute("x") ?? "0");
    if (!seen.has(x)) {
      seen.add(x);
      bandMap.push({
        x,
        width: parseFloat(rect.getAttribute("width") ?? "0"),
        idx: bandMap.length,
      });
    }
  });
  bandMap.sort((a, b) => a.x - b.x);

  svg.addEventListener("pointermove", (e: Event) => {
    const pe = e as PointerEvent;
    const svgRect = svg.getBoundingClientRect();
    const mx = pe.clientX - svgRect.left;

    // Find nearest band
    let closest = bandMap[0]!;
    let minDist = Infinity;
    for (const band of bandMap) {
      const center = band.x + band.width / 2;
      const dist = Math.abs(mx - center);
      if (dist < minDist) {
        minDist = dist;
        closest = band;
      }
    }

    // Only show if reasonably close
    if (minDist > closest.width * 1.5) {
      tooltip.classList.add("hidden");
      return;
    }

    const row = rows[closest.idx];
    if (!row) { tooltip.classList.add("hidden"); return; }

    // Build tooltip content
    let html = `<div class="popup-title">${row.dateStr}</div><div class="popup-detail">`;
    html += `Total: <strong>${row.total}</strong>`;
    if (chartMode === "direction") {
      html += `<br>Inbound: <strong>${row.inbound}</strong>`;
      html += `<br>Outbound: <strong>${row.outbound}</strong>`;
    } else {
      const stats = data.daily_stats[row.dateStr] ?? {};
      for (const t of VESSEL_TYPE_KEYS) {
        const count = (stats[`cx_${t}`] as number | undefined) ?? 0;
        if (count > 0) html += `<br>${t}: <strong>${count}</strong>`;
      }
    }
    html += `</div>`;
    tooltip.innerHTML = html;
    tooltip.classList.remove("hidden");

    // Position relative to the chart panel
    const panelRect = panel.getBoundingClientRect();
    const tipX = pe.clientX - panelRect.left + 12;
    const tipY = pe.clientY - panelRect.top - 10;

    // Flip left if would overflow right edge
    const tipWidth = tooltip.offsetWidth;
    if (tipX + tipWidth > panelRect.width - 8) {
      tooltip.style.left = `${tipX - tipWidth - 24}px`;
    } else {
      tooltip.style.left = `${tipX}px`;
    }
    tooltip.style.top = `${Math.max(4, tipY - tooltip.offsetHeight / 2)}px`;
  }, { signal });

  svg.addEventListener("pointerleave", () => {
    tooltip.classList.add("hidden");
  }, { signal });
}

function renderChart(data: TimelineData): void {
  if (!Plot) return;

  const container = document.getElementById("chart-container")!;
  container.innerHTML = "";

  const rows: ChartRow[] = data.dates.map((d, i) => {
    const stats = data.daily_stats[d] ?? {};
    return {
      date: new Date(d),
      dateStr: d,
      inbound: (stats.crossings_inbound as number | undefined) ?? 0,
      outbound: (stats.crossings_outbound as number | undefined) ?? 0,
      total: (stats.crossings as number | undefined) ?? 0,
      idx: i,
      cx_Cargo: (stats.cx_Cargo as number | undefined) ?? 0,
      cx_Tanker: (stats.cx_Tanker as number | undefined) ?? 0,
      cx_Fishing: (stats.cx_Fishing as number | undefined) ?? 0,
      cx_Passenger: (stats.cx_Passenger as number | undefined) ?? 0,
      cx_Other: (stats.cx_Other as number | undefined) ?? 0,
    };
  });

  const smoothed = loess(rows, 0.25);
  const smoothLine = rows.map((r, i) => ({
    date: r.date,
    value: smoothed[i]!,
  }));

  const isDark = currentTheme === "dark";
  const fg = isDark ? "#e0e0e0" : "#1a1a2e";
  const mutedFg = isDark ? "#666" : "#bbb";

  // Build stacked data based on mode
  let stackedData: { date: Date; dateStr: string; crossings: number; category: string; total: number }[];
  let colorDomain: string[];
  let colorRange: string[];

  if (chartMode === "direction") {
    stackedData = rows.flatMap((r) => [
      { date: r.date, dateStr: r.dateStr, crossings: r.inbound, category: "Inbound", total: r.total },
      { date: r.date, dateStr: r.dateStr, crossings: r.outbound, category: "Outbound", total: r.total },
    ]);
    colorDomain = ["Inbound", "Outbound"];
    colorRange = ["#4caf50", "#f44336"];
  } else {
    stackedData = rows.flatMap((r) => {
      const stats = data.daily_stats[r.dateStr] ?? {};
      return VESSEL_TYPE_KEYS.map((t) => ({
        date: r.date,
        dateStr: r.dateStr,
        crossings: (stats[`cx_${t}`] as number | undefined) ?? 0,
        category: t,
        total: r.total,
      }));
    });
    colorDomain = [...VESSEL_TYPE_KEYS];
    colorRange = VESSEL_TYPE_KEYS.map((t) => TYPE_COLORS[t]!);
  }

  const chart = Plot.plot({
    width: container.clientWidth,
    height: 164,
    marginTop: 8,
    marginBottom: 48,
    marginLeft: 32,
    marginRight: 8,
    x: {
      type: "band",
      label: null,
      tickFormat: (d: Date) => `${d.getMonth() + 1}/${d.getDate()}`,
      tickRotate: -45,
      tickSize: 0,
    },
    y: {
      label: null,
      grid: true,
      tickSize: 0,
    },
    color: {
      domain: colorDomain,
      range: colorRange,
    },
    style: {
      background: "transparent",
      color: fg,
      fontSize: "10px",
    },
    marks: [
      Plot.barY(stackedData, {
        x: "date",
        y: "crossings",
        fill: "category",
      }),
      Plot.ruleX(rows, Plot.pointerX({
        x: "date",
        y: "total",
        stroke: isDark ? "#ffffff40" : "#00000020",
        strokeWidth: 1,
      })),
      Plot.line(smoothLine, {
        x: "date",
        y: "value",
        stroke: isDark ? "#fbbf24" : "#d97706",
        strokeWidth: 2,
        strokeDasharray: "4,3",
      }),
      Plot.ruleY([0], { stroke: mutedFg, strokeWidth: 0.5 }),
      ...(currentDateIndex >= 0 ? [
        Plot.ruleX([new Date(data.dates[currentDateIndex]!)], {
          stroke: isDark ? "#ffffff80" : "#00000040",
          strokeWidth: 1.5,
          strokeDasharray: "2,2",
        }),
      ] : []),
    ],
  });

  container.appendChild(chart);

  // Custom HTML tooltip — matches vessel popup styling
  attachChartTooltip(chart, rows, data);
}

function updateChartHighlight(data: TimelineData): void {
  if (chartVisible && Plot) {
    renderChart(data);
  }
}

// === Live status detection ===

function checkLiveStatus(): void {
  fetch(dataUrl("data/heartbeat.json"))
    .then((r) => {
      if (!r.ok) throw new Error("No heartbeat");
      return r.json() as Promise<{ timestamp: string }>;
    })
    .then((hb) => {
      const age = Date.now() - new Date(hb.timestamp).getTime();
      const badge = document.getElementById("live-badge")!;
      if (age < 120_000) {
        badge.classList.remove("hidden");
      } else {
        badge.classList.add("hidden");
      }
    })
    .catch(() => {
      document.getElementById("live-badge")!.classList.add("hidden");
    });

}

// Single heartbeat interval — started once in initMap
let heartbeatTimer: ReturnType<typeof setInterval> | null = null;

function startHeartbeat(): void {
  if (heartbeatTimer) return;
  checkLiveStatus();
  heartbeatTimer = setInterval(checkLiveStatus, 30_000);
}

// === Theme switching ===

function toggleTheme(): void {
  const theme = currentTheme === "light" ? "dark" : "light";
  currentTheme = theme;
  document.documentElement.setAttribute("data-theme", theme);

  // Toggle sun/moon icons
  document.getElementById("icon-sun")!.classList.toggle("hidden", theme === "dark");
  document.getElementById("icon-moon")!.classList.toggle("hidden", theme === "light");

  if (map) {
    map.setStyle(STYLES[theme]!);
    map.once("idle", () => {
      addArrowImages();
      addGeofenceLayers();
      addTSSLayers();
      if (timelineData && currentDateIndex >= 0) {
        const date = timelineData.dates[currentDateIndex]!;
        renderVesselsForDate(date);
      } else {
        loadVesselData();
      }
    });
  }
}

// Expose toggleTheme globally for the HTML onclick handler
(window as unknown as Record<string, unknown>)["toggleTheme"] = toggleTheme;

initMap();
