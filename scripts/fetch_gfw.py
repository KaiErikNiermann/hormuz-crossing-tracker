"""Fetch vessel presence and SAR detections from Global Fishing Watch for the Strait of Hormuz."""

from __future__ import annotations

import json
import logging
import math
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

LOGGER = logging.getLogger(__name__)

GFW_BASE = "https://gateway.api.globalfishingwatch.org/v3"
OUTPUT_DIR = Path(__file__).parent.parent / "site" / "data"

# Strait of Hormuz bounding box as GeoJSON
HORMUZ_GEOJSON = {
    "type": "Polygon",
    "coordinates": [[
        [55.0, 25.4],
        [57.8, 25.4],
        [57.8, 27.4],
        [55.0, 27.4],
        [55.0, 25.4],
    ]],
}

# Vessel type mapping
VESSEL_TYPE_MAP = {
    "cargo": "Cargo",
    "carrier": "Cargo",
    "tanker": "Tanker",
    "bunker": "Tanker",
    "fishing": "Fishing",
    "passenger": "Passenger",
}


def map_vessel_type(raw_type: str) -> str:
    """Map GFW vessel types to our display categories."""
    lower = raw_type.lower()
    for key, mapped in VESSEL_TYPE_MAP.items():
        if key in lower:
            return mapped
    return "Other"


def compute_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute bearing in degrees from point 1 to point 2."""
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2_r)
    y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def bearing_to_direction(bearing: float) -> str:
    """Convert bearing to compass direction and inbound/outbound classification."""
    # In the Strait of Hormuz, westbound (toward Persian Gulf) is inbound,
    # eastbound (toward Gulf of Oman) is outbound
    if 45 <= bearing < 135:
        return "eastbound"
    if 135 <= bearing < 225:
        return "southbound"
    if 225 <= bearing < 315:
        return "westbound"
    return "northbound"


def classify_transit(bearing: float) -> str | None:
    """Classify as inbound/outbound based on bearing through the strait."""
    # Strait runs roughly WSW-ENE. Inbound = toward Persian Gulf (west), outbound = toward Oman (east)
    if 180 <= bearing < 360:
        return "inbound"
    if 0 <= bearing < 180:
        return "outbound"
    return None


# Geofence zone polygons (matching site/src/app.ts)
ZONE_WEST = [
    (55.55, 26.65), (56.15, 26.55), (56.25, 26.20),
    (55.65, 26.15), (55.55, 26.65),
]
ZONE_EAST = [
    (56.68, 26.35), (57.10, 26.12), (57.05, 25.78),
    (56.62, 25.88), (56.68, 26.35),
]


def point_in_polygon(lon: float, lat: float, polygon: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test. Polygon is [(lon, lat), ...]."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def classify_zone(lat: float, lon: float) -> str | None:
    """Return 'west', 'east', or None based on which geofence zone a point falls in."""
    if point_in_polygon(lon, lat, ZONE_WEST):
        return "west"
    if point_in_polygon(lon, lat, ZONE_EAST):
        return "east"
    return None


def get_token() -> str:
    token = os.getenv("GFW_API_ACCESS_TOKEN", "")
    if not token:
        print("Set GFW_API_ACCESS_TOKEN env var. Get one at https://globalfishingwatch.org/our-apis/tokens", file=sys.stderr)
        sys.exit(1)
    return token


def gfw_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def fetch_vessel_presence(
    token: str,
    start_date: str,
    end_date: str,
    temporal_resolution: str = "DAILY",
    spatial_resolution: str = "HIGH",
) -> dict:
    """Fetch AIS vessel presence gridded data for the Hormuz bbox."""
    resp = requests.post(
        f"{GFW_BASE}/4wings/report",
        headers=gfw_headers(token),
        params={
            "datasets[0]": "public-global-presence:latest",
            "format": "JSON",
            "temporal-resolution": temporal_resolution,
            "spatial-resolution": spatial_resolution,
            "group-by": "VESSEL_ID",
            "date-range": f"{start_date},{end_date}",
        },
        json={"geojson": HORMUZ_GEOJSON},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_sar_detections(
    token: str,
    start_date: str,
    end_date: str,
    temporal_resolution: str = "DAILY",
    spatial_resolution: str = "HIGH",
) -> dict:
    """Fetch SAR satellite vessel detections for the Hormuz bbox."""
    resp = requests.post(
        f"{GFW_BASE}/4wings/report",
        headers=gfw_headers(token),
        params={
            "datasets[0]": "public-global-sar-presence:latest",
            "format": "JSON",
            "temporal-resolution": temporal_resolution,
            "spatial-resolution": spatial_resolution,
            "group-by": "VESSEL_ID",
            "date-range": f"{start_date},{end_date}",
        },
        json={"geojson": HORMUZ_GEOJSON},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def search_vessel(token: str, query: str) -> dict:
    """Search for a vessel by MMSI, IMO, name, or callsign."""
    resp = requests.get(
        f"{GFW_BASE}/vessels/search",
        headers=gfw_headers(token),
        params={
            "query": query,
            "datasets[0]": "public-global-vessel-identity:latest",
            "limit": 10,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def extract_vessel_positions(presence_data: dict) -> list[dict]:
    """Extract individual vessel positions from the 4Wings presence report."""
    vessels: dict[str, dict] = {}

    for entry in presence_data.get("entries", []):
        for dataset_records in entry.values():
            if not isinstance(dataset_records, list):
                continue
            for rec in dataset_records:
                vid = rec.get("vesselId") or rec.get("vessel_id") or rec.get("shipName", "unknown")
                lat = rec.get("lat")
                lon = rec.get("lon")
                if lat is None or lon is None:
                    continue

                existing = vessels.get(vid)
                rec_date = rec.get("date", "")
                if existing is None or rec_date > existing.get("_date", ""):
                    vessels[vid] = {
                        "mmsi": rec.get("mmsi") or rec.get("ssvid"),
                        "name": rec.get("shipName") or rec.get("shipname"),
                        "type": map_vessel_type(rec.get("vesselType") or rec.get("vessel_type") or ""),
                        "lat": lat,
                        "lng": lon,
                        "seen": rec_date,
                        "direction": None,
                        "zone": None,
                        "source": "gfw_ais",
                        "_date": rec_date,
                    }

    return [{k: v for k, v in vessel.items() if k != "_date"} for vessel in vessels.values()]


def extract_sar_positions(sar_data: dict) -> list[dict]:
    """Extract vessel detections from SAR data."""
    detections: list[dict] = []

    for entry in sar_data.get("entries", []):
        for dataset_records in entry.values():
            if not isinstance(dataset_records, list):
                continue
            for rec in dataset_records:
                lat = rec.get("lat")
                lon = rec.get("lon")
                if lat is None or lon is None:
                    continue

                detections.append({
                    "mmsi": rec.get("mmsi") or rec.get("ssvid"),
                    "name": rec.get("shipName") or rec.get("shipname"),
                    "type": "Other",
                    "lat": lat,
                    "lng": lon,
                    "seen": rec.get("date", ""),
                    "direction": None,
                    "zone": None,
                    "source": "gfw_sar",
                })

    return detections


def extract_vessel_timeline(presence_data: dict) -> dict:
    """Extract daily vessel positions with direction data for the timeline slider.

    Returns a compact structure with vessel metadata stored once and positions
    keyed by date as [vesselId, lat, lon, bearing, direction] tuples.
    """
    # Group all records by (vesselId, date), keeping the one with most hours
    vessel_day: dict[tuple[str, str], dict] = {}
    vessel_meta: dict[str, dict] = {}

    for entry in presence_data.get("entries", []):
        for dataset_records in entry.values():
            if not isinstance(dataset_records, list):
                continue
            for rec in dataset_records:
                vid = rec.get("vesselId")
                if not vid:
                    continue
                lat = rec.get("lat")
                lon = rec.get("lon")
                date = rec.get("date", "")
                if lat is None or lon is None or not date:
                    continue

                hours = rec.get("hours", 0)
                key = (vid, date)
                existing = vessel_day.get(key)
                if existing is None or hours > existing.get("hours", 0):
                    vessel_day[key] = {"lat": lat, "lon": lon, "hours": hours}

                # Update vessel metadata (latest wins)
                if vid not in vessel_meta or date > vessel_meta[vid].get("_date", ""):
                    vessel_meta[vid] = {
                        "mmsi": rec.get("mmsi"),
                        "name": rec.get("shipName"),
                        "type": map_vessel_type(rec.get("vesselType") or ""),
                        "flag": rec.get("flag"),
                        "_date": date,
                    }

    # Build ordered date list
    all_dates = sorted({date for _, date in vessel_day})

    # Build per-vessel position history for direction computation
    vessel_positions: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    for (vid, date), pos in vessel_day.items():
        vessel_positions[vid].append((date, pos["lat"], pos["lon"]))
    for positions in vessel_positions.values():
        positions.sort(key=lambda x: x[0])

    # Compute bearings from consecutive days
    vessel_bearings: dict[tuple[str, str], tuple[float, str, str | None]] = {}
    for vid, positions in vessel_positions.items():
        for i in range(1, len(positions)):
            _, prev_lat, prev_lon = positions[i - 1]
            curr_date, curr_lat, curr_lon = positions[i]
            bearing = compute_bearing(prev_lat, prev_lon, curr_lat, curr_lon)
            direction = bearing_to_direction(bearing)
            transit = classify_transit(bearing)
            vessel_bearings[(vid, curr_date)] = (round(bearing, 1), direction, transit)
        # First day gets no bearing (or inherit from day 2 if available)
        if len(positions) >= 2:
            first_date = positions[0][0]
            second_key = (vid, positions[1][0])
            if second_key in vessel_bearings:
                vessel_bearings[(vid, first_date)] = vessel_bearings[second_key]

    # Classify zones for display
    vessel_zones: dict[tuple[str, str], str | None] = {}
    for (vid, date), pos in vessel_day.items():
        vessel_zones[(vid, date)] = classify_zone(pos["lat"], pos["lon"])

    # Detect strait crossings using a longitude transit line through the narrows.
    # The TSS runs roughly 56.35-56.65°E. We use 56.5°E as the dividing line.
    # A crossing = vessel on one side on day N and the other side on day N+1.
    # We also require the vessel to be within the strait latitude band (25.5-27°N)
    # to avoid counting port-hopping vessels outside the strait.
    TRANSIT_LON = 56.5
    STRAIT_LAT_MIN = 25.5
    STRAIT_LAT_MAX = 27.4

    crossings_by_date: dict[str, list[dict]] = defaultdict(list)
    total_crossings = 0
    for vid, positions in vessel_positions.items():
        for i in range(1, len(positions)):
            prev_date, prev_lat, prev_lon = positions[i - 1]
            curr_date, curr_lat, curr_lon = positions[i]

            # Both positions must be in the strait latitude band
            if not (STRAIT_LAT_MIN <= prev_lat <= STRAIT_LAT_MAX and
                    STRAIT_LAT_MIN <= curr_lat <= STRAIT_LAT_MAX):
                continue

            prev_side = "west" if prev_lon < TRANSIT_LON else "east"
            curr_side = "west" if curr_lon < TRANSIT_LON else "east"

            if prev_side != curr_side:
                # inbound = moving west (into Persian Gulf), outbound = moving east
                crossing_type = "inbound" if curr_side == "west" else "outbound"
                crossings_by_date[curr_date].append({
                    "vid": vid,
                    "from": prev_side,
                    "to": curr_side,
                    "type": crossing_type,
                })
                total_crossings += 1

    LOGGER.info("Detected %d strait crossings across %d days", total_crossings, len(crossings_by_date))

    # Build positions dict keyed by date
    # Each entry: [vesselId, lat, lon, bearing, direction, transit, zone]
    positions_by_date: dict[str, list] = {}
    daily_stats: dict[str, dict[str, int]] = {}

    for date in all_dates:
        day_positions: list = []
        type_counts: dict[str, int] = defaultdict(int)

        day_crossings = crossings_by_date.get(date, [])
        inbound_crossings = sum(1 for c in day_crossings if c["type"] == "inbound")
        outbound_crossings = sum(1 for c in day_crossings if c["type"] == "outbound")

        # Crossing counts by vessel type
        crossing_types: dict[str, int] = defaultdict(int)
        for c in day_crossings:
            vtype = vessel_meta.get(c["vid"], {}).get("type", "Other")
            crossing_types[vtype] += 1

        for vid in vessel_positions:
            key = (vid, date)
            if key not in vessel_day:
                continue

            pos = vessel_day[key]
            bearing_info = vessel_bearings.get(key)
            bearing = bearing_info[0] if bearing_info else None
            direction = bearing_info[1] if bearing_info else None
            transit = bearing_info[2] if bearing_info else None
            zone = vessel_zones.get(key)

            day_positions.append([vid, pos["lat"], pos["lon"], bearing, direction, transit, zone])

            vtype = vessel_meta.get(vid, {}).get("type", "Other")
            type_counts[vtype] += 1

        positions_by_date[date] = day_positions
        daily_stats[date] = {
            "total": len(day_positions),
            "crossings": len(day_crossings),
            "crossings_inbound": inbound_crossings,
            "crossings_outbound": outbound_crossings,
            "cx_Cargo": crossing_types.get("Cargo", 0),
            "cx_Tanker": crossing_types.get("Tanker", 0),
            "cx_Fishing": crossing_types.get("Fishing", 0),
            "cx_Passenger": crossing_types.get("Passenger", 0),
            "cx_Other": crossing_types.get("Other", 0),
            **dict(type_counts),
        }

    # Clean vessel metadata (remove internal fields)
    clean_meta = {
        vid: {k: v for k, v in meta.items() if not k.startswith("_")}
        for vid, meta in vessel_meta.items()
    }

    return {
        "dates": all_dates,
        "vessels": clean_meta,
        "positions": positions_by_date,
        "daily_stats": daily_stats,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    token = get_token()

    # Fetch last 30 days of data (GFW has ~5 day lag)
    end_date = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%d")
    start_date = (datetime.now(UTC) - timedelta(days=35)).strftime("%Y-%m-%d")

    presence: dict = {}
    LOGGER.info("Fetching GFW vessel presence for %s to %s", start_date, end_date)
    try:
        presence = fetch_vessel_presence(token, start_date, end_date)
        ais_vessels = extract_vessel_positions(presence)
        LOGGER.info("Got %d AIS vessel positions from GFW", len(ais_vessels))
    except requests.HTTPError as e:
        LOGGER.error("GFW presence request failed: %s", e)
        ais_vessels = []

    LOGGER.info("Fetching GFW SAR detections for %s to %s", start_date, end_date)
    try:
        sar_data = fetch_sar_detections(token, start_date, end_date)
        sar_vessels = extract_sar_positions(sar_data)
        LOGGER.info("Got %d SAR vessel detections from GFW", len(sar_vessels))
    except requests.HTTPError as e:
        LOGGER.error("GFW SAR request failed: %s", e)
        sar_vessels = []

    # Merge: AIS vessels take priority, SAR fills gaps
    seen_positions: set[tuple[float, float]] = set()
    merged: list[dict] = []

    for v in ais_vessels:
        key = (round(v["lat"], 2), round(v["lng"], 2))
        seen_positions.add(key)
        merged.append(v)

    for v in sar_vessels:
        key = (round(v["lat"], 2), round(v["lng"], 2))
        if key not in seen_positions:
            seen_positions.add(key)
            merged.append(v)

    # Build snapshot
    type_counts: dict[str, int] = {}
    for v in merged:
        t = v.get("type", "Other")
        type_counts[t] = type_counts.get(t, 0) + 1

    snapshot = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "Global Fishing Watch",
        "date_range": {"start": start_date, "end": end_date},
        "vessels": merged,
        "crossings": [],
        "stats": {
            "total_vessels": len(merged),
            "total_crossings": 0,
            "ais_vessels": len(ais_vessels),
            "sar_detections": len(sar_vessels),
            "vessel_types": type_counts,
        },
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Write GFW-specific data
    gfw_path = OUTPUT_DIR / "gfw_vessels.json"
    with open(gfw_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)
    LOGGER.info("Wrote %d vessels to %s", len(merged), gfw_path)

    # Also write as the main vessels.json for the site
    site_path = OUTPUT_DIR / "vessels.json"
    with open(site_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)
    LOGGER.info("Updated site data at %s", site_path)

    # Build and write timeline data
    if presence:
        LOGGER.info("Building vessel timeline with direction data...")
        timeline = extract_vessel_timeline(presence)
        timeline["generated_at"] = datetime.now(UTC).isoformat()
        timeline["source"] = "Global Fishing Watch"
        timeline["date_range"] = {"start": start_date, "end": end_date}

        timeline_path = OUTPUT_DIR / "vessels_timeline.json"
        with open(timeline_path, "w", encoding="utf-8") as f:
            json.dump(timeline, f, separators=(",", ":"))
        size_mb = timeline_path.stat().st_size / 1024 / 1024
        LOGGER.info(
            "Wrote timeline: %d dates, %d vessels, %.1f MB to %s",
            len(timeline["dates"]),
            len(timeline["vessels"]),
            size_mb,
            timeline_path,
        )

        # Dump raw data for inspection
        raw_path = OUTPUT_DIR / "gfw_raw_presence.json"
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(presence, f, indent=2)

    LOGGER.info("Done. %d AIS + %d SAR = %d total vessels", len(ais_vessels), len(sar_vessels), len(merged))


if __name__ == "__main__":
    main()
