"use strict";
(() => {
  var __create = Object.create;
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __getProtoOf = Object.getPrototypeOf;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __require = /* @__PURE__ */ ((x) => typeof require !== "undefined" ? require : typeof Proxy !== "undefined" ? new Proxy(x, {
    get: (a, b) => (typeof require !== "undefined" ? require : a)[b]
  }) : x)(function(x) {
    if (typeof require !== "undefined") return require.apply(this, arguments);
    throw Error('Dynamic require of "' + x + '" is not supported');
  });
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from === "object" || typeof from === "function") {
      for (let key of __getOwnPropNames(from))
        if (!__hasOwnProp.call(to, key) && key !== except)
          __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    }
    return to;
  };
  var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
    // If the importer is in node compatibility mode or this is not an ESM
    // file that has been converted to a CommonJS file using a Babel-
    // compatible transform (i.e. "__esModule" has not been set), then set
    // "default" to the CommonJS "module.exports" for node compatibility.
    isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
    mod
  ));

  // src/app.ts
  var STYLES = {
    light: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
    dark: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
  };
  var TSS_INBOUND = [
    [56.38052, 26.52025],
    [56.47858, 26.55841],
    [56.55022, 26.55847],
    [56.61132, 26.46796]
  ];
  var TSS_OUTBOUND = [
    [56.34518, 26.59506],
    [56.46497, 26.62409],
    [56.5916, 26.62385],
    [56.67343, 26.5035]
  ];
  var TSS_SEPARATION = [
    [56.35047, 26.58019],
    [56.37335, 26.53309],
    [56.47577, 26.57467],
    [56.55934, 26.57515],
    [56.62601, 26.47652],
    [56.65686, 26.49411],
    [56.58147, 26.60859],
    [56.5475, 26.60898],
    [56.51199, 26.60835],
    [56.48197, 26.60856],
    [56.46594, 26.60817],
    [56.35047, 26.58019]
  ];
  var APPROACH_INBOUND = [
    [56.28, 26.48],
    [56.38052, 26.52025]
  ];
  var APPROACH_OUTBOUND = [
    [56.67343, 26.5035],
    [56.78, 26.46]
  ];
  var GEOFENCE = {
    bbox: { south: 25.4, west: 55, north: 27.4, east: 57.8 },
    west: [
      [55.55, 26.65],
      [56.15, 26.55],
      [56.25, 26.2],
      [55.65, 26.15],
      [55.55, 26.65]
    ],
    east: [
      [56.68, 26.35],
      [57.1, 26.12],
      [57.05, 25.78],
      [56.62, 25.88],
      [56.68, 26.35]
    ]
  };
  var VESSEL_COLORS = {
    Cargo: "#3b82f6",
    Tanker: "#ef4444",
    Fishing: "#22c55e",
    Passenger: "#f59e0b",
    Other: "#a855f7"
  };
  var TRAIL_LENGTH = 0.035;
  var MAX_JUMP = 0.4;
  var ARROW_SIZE = 12;
  var currentTheme = "light";
  var map;
  var timelineData = null;
  var currentDateIndex = -1;
  var animationTimer = null;
  var sourceFilter = "all";
  function bboxToPolygon(box) {
    return [
      [box.west, box.south],
      [box.east, box.south],
      [box.east, box.north],
      [box.west, box.north],
      [box.west, box.south]
    ];
  }
  function polygonCenter(coords) {
    const pts = coords.slice(0, -1);
    const lng = pts.reduce((s, p) => s + p[0], 0) / pts.length;
    const lat = pts.reduce((s, p) => s + p[1], 0) / pts.length;
    return [lng, lat];
  }
  function buildTrailFeatures(positions) {
    if (!timelineData) return [];
    const features = [];
    for (const p of positions) {
      const bearing = p[3];
      if (bearing === null) continue;
      const lat = p[1];
      const lon = p[2];
      const meta = timelineData.vessels[p[0]];
      const vtype = meta?.type ?? "Other";
      const color = VESSEL_COLORS[vtype] ?? VESSEL_COLORS["Other"];
      const reverseBearing = (bearing + 180) % 360 * (Math.PI / 180);
      const tailLat = lat + TRAIL_LENGTH * Math.cos(reverseBearing);
      const tailLon = lon + TRAIL_LENGTH * Math.sin(reverseBearing) / Math.cos(lat * Math.PI / 180);
      if (Math.abs(tailLat - lat) > MAX_JUMP || Math.abs(tailLon - lon) > MAX_JUMP) continue;
      features.push({
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: [
            [tailLon, tailLat],
            [lon, lat]
          ]
        },
        properties: { color }
      });
    }
    return features;
  }
  function createArrowImage(color, size) {
    const canvas = document.createElement("canvas");
    const s = size * 2;
    canvas.width = s;
    canvas.height = s;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(s / 2, 1);
    ctx.lineTo(s * 0.82, s * 0.85);
    ctx.lineTo(s / 2, s * 0.6);
    ctx.lineTo(s * 0.18, s * 0.85);
    ctx.closePath();
    ctx.fill();
    return ctx.getImageData(0, 0, s, s);
  }
  function addArrowImages() {
    for (const [name, color] of Object.entries(VESSEL_COLORS)) {
      const id = `arrow-${name}`;
      if (!map.hasImage(id)) {
        const img = createArrowImage(color, ARROW_SIZE);
        map.addImage(id, img, { pixelRatio: 2 });
      }
    }
  }
  function initMap() {
    map = new maplibregl.Map({
      container: "map",
      style: STYLES[currentTheme],
      center: [56.35, 26.25],
      zoom: 8,
      attributionControl: {}
    });
    map.addControl(new maplibregl.NavigationControl(), "bottom-right");
    map.on("load", () => {
      addArrowImages();
      addGeofenceLayers();
      addTSSLayers();
      loadVesselData();
      checkLiveStatus();
    });
  }
  function addTSSLayers() {
    map.addSource("tss-separation", {
      type: "geojson",
      data: {
        type: "Feature",
        properties: {},
        geometry: { type: "Polygon", coordinates: [TSS_SEPARATION] }
      }
    });
    map.addLayer({
      id: "tss-separation-fill",
      type: "fill",
      source: "tss-separation",
      paint: { "fill-color": "#ff9800", "fill-opacity": 0.12 }
    });
    map.addLayer({
      id: "tss-separation-line",
      type: "line",
      source: "tss-separation",
      paint: { "line-color": "#ff9800", "line-width": 1.5, "line-dasharray": [3, 3] }
    });
    map.addSource("tss-inbound", {
      type: "geojson",
      data: {
        type: "Feature",
        properties: {},
        geometry: {
          type: "LineString",
          coordinates: [...APPROACH_INBOUND, ...TSS_INBOUND]
        }
      }
    });
    map.addLayer({
      id: "tss-inbound-line",
      type: "line",
      source: "tss-inbound",
      paint: { "line-color": "#4caf50", "line-width": 2, "line-opacity": 0.7 }
    });
    map.addSource("tss-outbound", {
      type: "geojson",
      data: {
        type: "Feature",
        properties: {},
        geometry: {
          type: "LineString",
          coordinates: [...TSS_OUTBOUND, ...APPROACH_OUTBOUND]
        }
      }
    });
    map.addLayer({
      id: "tss-outbound-line",
      type: "line",
      source: "tss-outbound",
      paint: { "line-color": "#f44336", "line-width": 2, "line-opacity": 0.7 }
    });
    map.addSource("tss-labels", {
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features: [
          {
            type: "Feature",
            properties: { label: "INBOUND" },
            geometry: { type: "Point", coordinates: [56.48, 26.53] }
          },
          {
            type: "Feature",
            properties: { label: "OUTBOUND" },
            geometry: { type: "Point", coordinates: [56.52, 26.63] }
          },
          {
            type: "Feature",
            properties: { label: "TSS" },
            geometry: { type: "Point", coordinates: [56.5, 26.58] }
          }
        ]
      }
    });
    map.addLayer({
      id: "tss-labels",
      type: "symbol",
      source: "tss-labels",
      layout: {
        "text-field": ["get", "label"],
        "text-size": 10,
        "text-font": ["Open Sans Regular", "Arial Unicode MS Regular"],
        "text-allow-overlap": true
      },
      paint: {
        "text-color": currentTheme === "dark" ? "#ff980090" : "#e6850090",
        "text-halo-color": currentTheme === "dark" ? "#000000" : "#ffffff",
        "text-halo-width": 1.5
      }
    });
  }
  function addGeofenceLayers() {
    map.addSource("bbox", {
      type: "geojson",
      data: {
        type: "Feature",
        properties: {},
        geometry: { type: "Polygon", coordinates: [bboxToPolygon(GEOFENCE.bbox)] }
      }
    });
    map.addLayer({
      id: "bbox-fill",
      type: "fill",
      source: "bbox",
      paint: { "fill-color": "#ffc107", "fill-opacity": 0.05 }
    });
    map.addLayer({
      id: "bbox-line",
      type: "line",
      source: "bbox",
      paint: { "line-color": "#ffc107", "line-width": 1.5, "line-dasharray": [6, 4] }
    });
    map.addSource("zone-west", {
      type: "geojson",
      data: {
        type: "Feature",
        properties: {},
        geometry: { type: "Polygon", coordinates: [GEOFENCE.west] }
      }
    });
    map.addLayer({
      id: "zone-west-fill",
      type: "fill",
      source: "zone-west",
      paint: { "fill-color": "#4caf50", "fill-opacity": 0.12 }
    });
    map.addLayer({
      id: "zone-west-line",
      type: "line",
      source: "zone-west",
      paint: { "line-color": "#4caf50", "line-width": 2 }
    });
    map.addSource("zone-east", {
      type: "geojson",
      data: {
        type: "Feature",
        properties: {},
        geometry: { type: "Polygon", coordinates: [GEOFENCE.east] }
      }
    });
    map.addLayer({
      id: "zone-east-fill",
      type: "fill",
      source: "zone-east",
      paint: { "fill-color": "#2196f3", "fill-opacity": 0.12 }
    });
    map.addLayer({
      id: "zone-east-line",
      type: "line",
      source: "zone-east",
      paint: { "line-color": "#2196f3", "line-width": 2 }
    });
    map.addSource("zone-labels", {
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features: [
          {
            type: "Feature",
            properties: { label: "WEST ZONE" },
            geometry: { type: "Point", coordinates: polygonCenter(GEOFENCE.west) }
          },
          {
            type: "Feature",
            properties: { label: "EAST ZONE" },
            geometry: { type: "Point", coordinates: polygonCenter(GEOFENCE.east) }
          }
        ]
      }
    });
    map.addLayer({
      id: "zone-labels",
      type: "symbol",
      source: "zone-labels",
      layout: {
        "text-field": ["get", "label"],
        "text-size": 12,
        "text-font": ["Open Sans Bold", "Arial Unicode MS Bold"],
        "text-allow-overlap": true
      },
      paint: {
        "text-color": currentTheme === "dark" ? "#ffffff" : "#333333",
        "text-halo-color": currentTheme === "dark" ? "#000000" : "#ffffff",
        "text-halo-width": 2
      }
    });
  }
  function loadVesselData() {
    fetch("data/vessels_timeline.json").then((r) => {
      if (!r.ok) throw new Error("No timeline data");
      return r.json();
    }).then((data) => {
      timelineData = data;
      initTimeline(data);
    }).catch(() => {
      fetch("data/vessels.json").then((r) => {
        if (!r.ok) throw new Error("No vessel data available yet");
        return r.json();
      }).then((data) => {
        renderVessels(data.vessels);
        updateStats(data);
        updateSnapshotTime(data.generated_at);
      }).catch(() => {
        document.getElementById("vessel-count").textContent = "0";
        document.getElementById("crossing-count").textContent = "0";
        document.getElementById("snapshot-time").textContent = "No data yet";
      });
    });
  }
  function filterBySource(positions) {
    if (sourceFilter === "all") return positions;
    return positions.filter((p) => {
      const src = p[7] ?? "gfw";
      if (sourceFilter === "gfw") return src === "gfw" || src === "both";
      return src === "ais" || src === "both";
    });
  }
  function renderVesselsForDate(date) {
    if (!timelineData) return;
    const rawPositions = timelineData.positions[date];
    if (!rawPositions) return;
    const positions = filterBySource(rawPositions);
    const features = positions.filter((p) => p[1] && p[2]).map((p) => {
      const meta = timelineData.vessels[p[0]];
      const vtype = meta?.type ?? "Other";
      return {
        type: "Feature",
        geometry: { type: "Point", coordinates: [p[2], p[1]] },
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
          color: VESSEL_COLORS[vtype] ?? VESSEL_COLORS["Other"]
        }
      };
    });
    const geojson = { type: "FeatureCollection", features };
    renderTrails(positions);
    const existing = map.getSource("vessels");
    if (existing) {
      existing.setData(geojson);
    } else {
      map.addSource("vessels", { type: "geojson", data: geojson });
      addVesselLayers();
    }
    const stats = timelineData.daily_stats[date];
    document.getElementById("vessel-count").textContent = String(positions.length);
    document.getElementById("crossing-count").textContent = String(stats?.crossings ?? 0);
    let inbound = 0;
    let outbound = 0;
    for (const p of positions) {
      if (p[5] === "inbound") inbound++;
      else if (p[5] === "outbound") outbound++;
    }
    const dirStats = document.getElementById("direction-stats");
    dirStats.classList.remove("hidden");
    document.getElementById("inbound-count").textContent = String(inbound);
    document.getElementById("outbound-count").textContent = String(outbound);
    updateSnapshotTime(date);
  }
  function renderVessels(vessels) {
    const features = vessels.filter((v) => v.lat && v.lng).map((v) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [v.lng, v.lat] },
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
        color: VESSEL_COLORS[v.type] ?? VESSEL_COLORS["Other"]
      }
    }));
    const geojson = { type: "FeatureCollection", features };
    const existing = map.getSource("vessels");
    if (existing) {
      existing.setData(geojson);
      return;
    }
    map.addSource("vessels", { type: "geojson", data: geojson });
    addVesselLayers();
  }
  function renderTrails(positions) {
    const trailFeatures = buildTrailFeatures(positions);
    const trailGeojson = { type: "FeatureCollection", features: trailFeatures };
    const existingTrails = map.getSource("vessel-trails");
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
          "line-opacity": 0.35
        },
        layout: {
          "line-cap": "round"
        }
      });
    }
  }
  function addVesselLayers() {
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
        "circle-opacity": 0.7
      }
    });
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
        "icon-ignore-placement": true
      },
      paint: {
        "icon-opacity": 0.85
      }
    });
    const vesselClickHandler = (e) => {
      const feature = e.features?.[0];
      if (!feature) return;
      const props = feature.properties ?? {};
      const coords = feature.geometry.coordinates.slice();
      const transitHtml = props.transit !== "-" ? `<br>Transit: <strong>${props.transit}</strong>` : "";
      const zoneHtml = props.zone && props.zone !== "-" ? `<br>Zone: ${props.zone}` : "";
      new maplibregl.Popup({ offset: 12 }).setLngLat(coords).setHTML(
        `<div class="popup-title">${props.name}</div>
         <div class="popup-detail">
           Type: ${props.type}<br>
           MMSI: ${props.mmsi}<br>
           Flag: ${props.flag}<br>
           Direction: ${props.direction}${transitHtml}${zoneHtml}
         </div>`
      ).addTo(map);
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
  function updateStats(data) {
    document.getElementById("vessel-count").textContent = String(data.stats?.total_vessels ?? data.vessels?.length ?? 0);
    document.getElementById("crossing-count").textContent = String(data.stats?.total_crossings ?? data.crossings?.length ?? 0);
  }
  function updateSnapshotTime(isoString) {
    const el = document.getElementById("snapshot-time");
    if (!isoString) {
      el.textContent = "Unknown";
      return;
    }
    if (isoString.length === 10) {
      el.textContent = isoString;
    } else {
      el.textContent = new Date(isoString).toLocaleString();
    }
  }
  function initSourceFilter(data) {
    const container = document.getElementById("source-filter");
    if (!container) return;
    const sources = data.sources;
    if (!sources || sources.length < 2) {
      container.classList.add("hidden");
      return;
    }
    container.classList.remove("hidden");
    for (const btn of document.querySelectorAll(".source-btn")) {
      btn.addEventListener("click", () => {
        sourceFilter = btn.dataset.source;
        for (const b of document.querySelectorAll(".source-btn")) b.classList.remove("active");
        btn.classList.add("active");
        if (timelineData && currentDateIndex >= 0) {
          const date = timelineData.dates[currentDateIndex];
          renderVesselsForDate(date);
          updateChartHighlight(timelineData);
        }
      });
    }
  }
  function initTimeline(data) {
    const bar = document.getElementById("timeline-bar");
    const slider = document.getElementById("timeline-slider");
    const dateLabel = document.getElementById("timeline-date");
    const countLabel = document.getElementById("timeline-count");
    const playBtn = document.getElementById("timeline-play");
    const maxIdx = data.dates.length - 1;
    slider.min = "0";
    slider.max = String(maxIdx);
    const startIdx = Math.max(0, maxIdx - 5);
    slider.value = String(startIdx);
    currentDateIndex = startIdx;
    bar.classList.remove("hidden");
    initSourceFilter(data);
    initChart(data);
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
    function renderDateAtIndex(idx) {
      const date = data.dates[idx];
      const stats = data.daily_stats[date];
      dateLabel.textContent = date;
      countLabel.textContent = `${stats?.total ?? 0} vessels`;
      renderVesselsForDate(date);
      updateChartHighlight(data);
    }
    function startAnimation() {
      playBtn.innerHTML = "&#9646;&#9646;";
      animationTimer = setInterval(() => {
        currentDateIndex = (currentDateIndex + 1) % data.dates.length;
        slider.value = String(currentDateIndex);
        renderDateAtIndex(currentDateIndex);
      }, 800);
    }
    function stopAnimation() {
      if (animationTimer) {
        clearInterval(animationTimer);
        animationTimer = null;
      }
      playBtn.innerHTML = "&#9654;";
    }
  }
  var chartVisible = false;
  var chartMode = "direction";
  var Plot = null;
  function initChart(data) {
    const toggleBtn = document.getElementById("timeline-chart-toggle");
    const panel = document.getElementById("chart-panel");
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
    for (const tab of document.querySelectorAll(".chart-tab")) {
      tab.addEventListener("click", () => {
        chartMode = tab.dataset.mode;
        for (const t of document.querySelectorAll(".chart-tab")) t.classList.remove("active");
        tab.classList.add("active");
        if (Plot) renderChart(data);
      });
    }
  }
  function loadAndRenderChart(data) {
    const container = document.getElementById("chart-container");
    container.innerHTML = '<div class="chart-loading">Loading chart\u2026</div>';
    import("https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6/+esm").then((mod) => {
      Plot = mod;
      renderChart(data);
    }).catch((err) => {
      container.innerHTML = '<div class="chart-loading">Failed to load chart</div>';
      console.error("Failed to load Observable Plot:", err);
    });
  }
  function loess(rows, bandwidth) {
    const n = rows.length;
    const result = [];
    const span = Math.max(2, Math.floor(n * bandwidth));
    for (let i = 0; i < n; i++) {
      let sumW = 0, sumWX = 0, sumWY = 0, sumWXX = 0, sumWXY = 0;
      const maxDist = span;
      for (let j = 0; j < n; j++) {
        const dist = Math.abs(i - j);
        if (dist > maxDist) continue;
        const u = dist / (maxDist + 1);
        const w = Math.pow(1 - u * u * u, 3);
        const x = rows[j].idx;
        const y = rows[j].total;
        sumW += w;
        sumWX += w * x;
        sumWY += w * y;
        sumWXX += w * x * x;
        sumWXY += w * x * y;
      }
      const xi = rows[i].idx;
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
  var TYPE_COLORS = {
    Cargo: "#3b82f6",
    Tanker: "#ef4444",
    Fishing: "#22c55e",
    Passenger: "#f59e0b",
    Other: "#a855f7"
  };
  var VESSEL_TYPE_KEYS = ["Cargo", "Tanker", "Fishing", "Passenger", "Other"];
  var chartTooltipEl = null;
  function getChartTooltip() {
    if (!chartTooltipEl) {
      chartTooltipEl = document.createElement("div");
      chartTooltipEl.className = "chart-tooltip hidden";
      document.getElementById("chart-panel").appendChild(chartTooltipEl);
    }
    return chartTooltipEl;
  }
  function attachChartTooltip(chart, rows, data) {
    const svg = chart.tagName === "svg" ? chart : chart.querySelector("svg");
    if (!svg) return;
    const tooltip = getChartTooltip();
    const panel = document.getElementById("chart-panel");
    const barRects = svg.querySelectorAll('g[aria-label="bar"] rect');
    if (!barRects.length) return;
    const bandMap = [];
    const seen = /* @__PURE__ */ new Set();
    barRects.forEach((rect) => {
      const x = parseFloat(rect.getAttribute("x") ?? "0");
      if (!seen.has(x)) {
        seen.add(x);
        bandMap.push({
          x,
          width: parseFloat(rect.getAttribute("width") ?? "0"),
          idx: bandMap.length
        });
      }
    });
    bandMap.sort((a, b) => a.x - b.x);
    svg.addEventListener("pointermove", (e) => {
      const pe = e;
      const svgRect = svg.getBoundingClientRect();
      const mx = pe.clientX - svgRect.left;
      let closest = bandMap[0];
      let minDist = Infinity;
      for (const band of bandMap) {
        const center = band.x + band.width / 2;
        const dist = Math.abs(mx - center);
        if (dist < minDist) {
          minDist = dist;
          closest = band;
        }
      }
      if (minDist > closest.width * 1.5) {
        tooltip.classList.add("hidden");
        return;
      }
      const row = rows[closest.idx];
      if (!row) {
        tooltip.classList.add("hidden");
        return;
      }
      let html = `<div class="popup-title">${row.dateStr}</div><div class="popup-detail">`;
      html += `Total: <strong>${row.total}</strong>`;
      if (chartMode === "direction") {
        html += `<br>Inbound: <strong>${row.inbound}</strong>`;
        html += `<br>Outbound: <strong>${row.outbound}</strong>`;
      } else {
        const stats = data.daily_stats[row.dateStr] ?? {};
        for (const t of VESSEL_TYPE_KEYS) {
          const count = stats[`cx_${t}`] ?? 0;
          if (count > 0) html += `<br>${t}: <strong>${count}</strong>`;
        }
      }
      html += `</div>`;
      tooltip.innerHTML = html;
      tooltip.classList.remove("hidden");
      const panelRect = panel.getBoundingClientRect();
      const tipX = pe.clientX - panelRect.left + 12;
      const tipY = pe.clientY - panelRect.top - 10;
      const tipWidth = tooltip.offsetWidth;
      if (tipX + tipWidth > panelRect.width - 8) {
        tooltip.style.left = `${tipX - tipWidth - 24}px`;
      } else {
        tooltip.style.left = `${tipX}px`;
      }
      tooltip.style.top = `${Math.max(4, tipY - tooltip.offsetHeight / 2)}px`;
    });
    svg.addEventListener("pointerleave", () => {
      tooltip.classList.add("hidden");
    });
  }
  function renderChart(data) {
    if (!Plot) return;
    const container = document.getElementById("chart-container");
    container.innerHTML = "";
    const rows = data.dates.map((d, i) => {
      const stats = data.daily_stats[d] ?? {};
      return {
        date: new Date(d),
        dateStr: d,
        inbound: stats.crossings_inbound ?? 0,
        outbound: stats.crossings_outbound ?? 0,
        total: stats.crossings ?? 0,
        idx: i,
        cx_Cargo: stats.cx_Cargo ?? 0,
        cx_Tanker: stats.cx_Tanker ?? 0,
        cx_Fishing: stats.cx_Fishing ?? 0,
        cx_Passenger: stats.cx_Passenger ?? 0,
        cx_Other: stats.cx_Other ?? 0
      };
    });
    const smoothed = loess(rows, 0.25);
    const smoothLine = rows.map((r, i) => ({
      date: r.date,
      value: smoothed[i]
    }));
    const isDark = currentTheme === "dark";
    const fg = isDark ? "#e0e0e0" : "#1a1a2e";
    const mutedFg = isDark ? "#666" : "#bbb";
    let stackedData;
    let colorDomain;
    let colorRange;
    if (chartMode === "direction") {
      stackedData = rows.flatMap((r) => [
        { date: r.date, dateStr: r.dateStr, crossings: r.inbound, category: "Inbound", total: r.total },
        { date: r.date, dateStr: r.dateStr, crossings: r.outbound, category: "Outbound", total: r.total }
      ]);
      colorDomain = ["Inbound", "Outbound"];
      colorRange = ["#4caf50", "#f44336"];
    } else {
      stackedData = rows.flatMap((r) => {
        const stats = data.daily_stats[r.dateStr] ?? {};
        return VESSEL_TYPE_KEYS.map((t) => ({
          date: r.date,
          dateStr: r.dateStr,
          crossings: stats[`cx_${t}`] ?? 0,
          category: t,
          total: r.total
        }));
      });
      colorDomain = [...VESSEL_TYPE_KEYS];
      colorRange = VESSEL_TYPE_KEYS.map((t) => TYPE_COLORS[t]);
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
        tickFormat: (d) => `${d.getMonth() + 1}/${d.getDate()}`,
        tickRotate: -45,
        tickSize: 0
      },
      y: {
        label: null,
        grid: true,
        tickSize: 0
      },
      color: {
        domain: colorDomain,
        range: colorRange
      },
      style: {
        background: "transparent",
        color: fg,
        fontSize: "10px"
      },
      marks: [
        Plot.barY(stackedData, {
          x: "date",
          y: "crossings",
          fill: "category"
        }),
        Plot.ruleX(rows, Plot.pointerX({
          x: "date",
          y: "total",
          stroke: isDark ? "#ffffff40" : "#00000020",
          strokeWidth: 1
        })),
        Plot.line(smoothLine, {
          x: "date",
          y: "value",
          stroke: isDark ? "#fbbf24" : "#d97706",
          strokeWidth: 2,
          strokeDasharray: "4,3"
        }),
        Plot.ruleY([0], { stroke: mutedFg, strokeWidth: 0.5 }),
        ...currentDateIndex >= 0 ? [
          Plot.ruleX([new Date(data.dates[currentDateIndex])], {
            stroke: isDark ? "#ffffff80" : "#00000040",
            strokeWidth: 1.5,
            strokeDasharray: "2,2"
          })
        ] : []
      ]
    });
    container.appendChild(chart);
    attachChartTooltip(chart, rows, data);
  }
  function updateChartHighlight(data) {
    if (chartVisible && Plot) {
      renderChart(data);
    }
  }
  function checkLiveStatus() {
    fetch("data/heartbeat.json").then((r) => {
      if (!r.ok) throw new Error("No heartbeat");
      return r.json();
    }).then((hb) => {
      const age = Date.now() - new Date(hb.timestamp).getTime();
      const badge = document.getElementById("live-badge");
      if (age < 12e4) {
        badge.classList.remove("hidden");
      } else {
        badge.classList.add("hidden");
      }
    }).catch(() => {
      document.getElementById("live-badge").classList.add("hidden");
    });
    setInterval(() => checkLiveStatus(), 3e4);
  }
  function toggleTheme() {
    const theme = currentTheme === "light" ? "dark" : "light";
    currentTheme = theme;
    document.documentElement.setAttribute("data-theme", theme);
    document.getElementById("icon-sun").classList.toggle("hidden", theme === "dark");
    document.getElementById("icon-moon").classList.toggle("hidden", theme === "light");
    if (map) {
      map.setStyle(STYLES[theme]);
      map.once("idle", () => {
        addArrowImages();
        addGeofenceLayers();
        addTSSLayers();
        if (timelineData && currentDateIndex >= 0) {
          const date = timelineData.dates[currentDateIndex];
          renderVesselsForDate(date);
        } else {
          loadVesselData();
        }
      });
    }
  }
  window["toggleTheme"] = toggleTheme;
  initMap();
})();
//# sourceMappingURL=app.js.map
