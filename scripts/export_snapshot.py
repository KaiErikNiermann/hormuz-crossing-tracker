"""Export merged GFW + live AIS data to vessels_timeline.json for the static site."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).parent.parent))
from worker.db import get_db_config

LOGGER = logging.getLogger(__name__)
OUTPUT_DIR = Path(__file__).parent.parent / "site" / "data"

VESSEL_TYPE_MAP: dict[str, str] = {
    "cargo": "Cargo",
    "tanker": "Tanker",
    "fishing": "Fishing",
    "passenger": "Passenger",
}


def map_vessel_type(raw: str | None) -> str:
    if not raw:
        return "Other"
    lower = raw.lower()
    for key, label in VESSEL_TYPE_MAP.items():
        if key in lower:
            return label
    return "Other"


def load_gfw_timeline() -> dict | None:
    """Load the GFW-only timeline produced by fetch_gfw.py."""
    path = OUTPUT_DIR / "gfw_timeline.json"
    if not path.exists():
        LOGGER.warning("No GFW timeline at %s", path)
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def query_ais_vessels(conn: psycopg2.extensions.connection) -> list[dict]:
    """Get current vessel positions from the live AIS tracker."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            mmsi,
            ship_name,
            ship_type,
            last_latitude,
            last_longitude,
            last_seen_at,
            last_direction,
            pending_zone
        FROM maritime_passage_live_state
        WHERE last_latitude IS NOT NULL
          AND last_longitude IS NOT NULL
        ORDER BY last_seen_at DESC
        """
    )
    rows = cur.fetchall()
    cur.close()

    return [
        {
            "mmsi": str(row[0]),
            "name": row[1],
            "type": map_vessel_type(row[2]),
            "lat": float(row[3]),
            "lng": float(row[4]),
            "seen": row[5].isoformat() if row[5] else None,
            "date": row[5].strftime("%Y-%m-%d") if row[5] else None,
            "direction": row[6],
            "zone": row[7],
        }
        for row in rows
    ]


def query_ais_crossings(conn: psycopg2.extensions.connection, days: int = 30) -> list[dict]:
    """Get recent crossing events from the live AIS tracker."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            event_timestamp,
            direction,
            mmsi,
            ship_name,
            ship_type,
            zone_from,
            zone_to
        FROM maritime_passage_live_events
        WHERE event_timestamp >= NOW() - INTERVAL '%s days'
        ORDER BY event_timestamp DESC
        """,
        (days,),
    )
    rows = cur.fetchall()
    cur.close()

    return [
        {
            "timestamp": row[0].isoformat() if row[0] else None,
            "direction": row[1],
            "mmsi": str(row[2]),
            "name": row[3],
            "type": map_vessel_type(row[4]),
            "from": row[5],
            "to": row[6],
        }
        for row in rows
    ]


def merge_timeline(gfw_timeline: dict | None, ais_vessels: list[dict]) -> dict:
    """Merge GFW timeline with live AIS positions.

    - Adds source field ("gfw", "ais", "both") to vessel metadata
    - Extends position tuples to 8 elements with source as the 8th
    - Deduplicates by MMSI where both sources provide it
    """
    if gfw_timeline is None:
        # No GFW data — build AIS-only timeline
        gfw_timeline = {
            "dates": [],
            "vessels": {},
            "positions": {},
            "daily_stats": {},
        }

    vessels: dict[str, dict] = dict(gfw_timeline.get("vessels", {}))
    positions: dict[str, list] = {
        date: list(pos_list)
        for date, pos_list in gfw_timeline.get("positions", {}).items()
    }
    dates: list[str] = list(gfw_timeline.get("dates", []))
    daily_stats: dict[str, dict] = dict(gfw_timeline.get("daily_stats", {}))

    # Tag existing GFW position tuples with source (append 8th element)
    for date in positions:
        positions[date] = [
            list(p) + ["gfw"] if len(p) == 7 else list(p)
            for p in positions[date]
        ]

    # Build MMSI → GFW vesselId cross-reference
    mmsi_to_gfw_vid: dict[str, str] = {}
    for vid, meta in vessels.items():
        mmsi = meta.get("mmsi")
        if mmsi:
            mmsi_to_gfw_vid[str(mmsi)] = vid

    # Tag all existing GFW vessel metadata with source
    for vid in vessels:
        if "source" not in vessels[vid]:
            vessels[vid] = {**vessels[vid], "source": "gfw"}

    # Group AIS vessels by date
    ais_by_date: dict[str, list[dict]] = {}
    for v in ais_vessels:
        d = v.get("date")
        if d:
            ais_by_date.setdefault(d, []).append(v)

    ais_added = 0
    deduped = 0

    for date, day_vessels in sorted(ais_by_date.items()):
        # Ensure date exists in timeline
        if date not in positions:
            positions[date] = []
            if date not in dates:
                dates.append(date)
                dates.sort()

        # Track which GFW vesselIds already have a position on this date
        existing_vids_on_date: set[str] = set()
        for p in positions[date]:
            existing_vids_on_date.add(p[0])

        for v in day_vessels:
            mmsi = v["mmsi"]
            gfw_vid = mmsi_to_gfw_vid.get(mmsi)

            if gfw_vid:
                # MMSI matches a GFW vessel — tag as "both"
                vessels[gfw_vid] = {**vessels[gfw_vid], "source": "both"}
                deduped += 1

                # If GFW already has a position for this date, update source tag
                if gfw_vid in existing_vids_on_date:
                    for p in positions[date]:
                        if p[0] == gfw_vid and len(p) >= 8:
                            p[7] = "both"
                    continue

                # Add AIS position under the GFW vesselId
                vid = gfw_vid
            else:
                # AIS-only vessel — create new entry
                vid = f"ais-{mmsi}"
                if vid not in vessels:
                    vessels[vid] = {
                        "mmsi": mmsi,
                        "name": v.get("name"),
                        "type": v.get("type", "Other"),
                        "flag": None,
                        "source": "ais",
                    }

            # Build position tuple: [vid, lat, lon, bearing, direction, transit, zone, source]
            source = "both" if gfw_vid else "ais"
            pos_tuple = [
                vid,
                v["lat"],
                v["lng"],
                None,  # bearing (no consecutive positions to compute from)
                v.get("direction"),
                None,  # transit
                v.get("zone"),
                source,
            ]
            positions[date].append(pos_tuple)
            ais_added += 1

    # Recompute daily_stats for dates with AIS data
    for date in ais_by_date:
        pos_list = positions.get(date, [])
        stats = daily_stats.get(date, {})
        stats["total"] = len(pos_list)

        # Count by source
        src_counts: dict[str, int] = {"gfw": 0, "ais": 0, "both": 0}
        for p in pos_list:
            src = p[7] if len(p) > 7 else "gfw"
            src_counts[src] = src_counts.get(src, 0) + 1
        stats["src_gfw"] = src_counts["gfw"]
        stats["src_ais"] = src_counts["ais"]
        stats["src_both"] = src_counts["both"]

        daily_stats[date] = stats

    LOGGER.info(
        "Merge complete: %d AIS positions added, %d MMSI deduped, %d total vessels",
        ais_added,
        deduped,
        len(vessels),
    )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "Global Fishing Watch + AIS Live",
        "sources": ["gfw", "ais"],
        "date_range": gfw_timeline.get("date_range", {}),
        "dates": dates,
        "vessels": vessels,
        "positions": positions,
        "daily_stats": daily_stats,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Load GFW timeline
    gfw_timeline = load_gfw_timeline()

    # Query live AIS from PostgreSQL
    ais_vessels: list[dict] = []
    ais_crossings: list[dict] = []
    try:
        db_config = get_db_config()
        conn = psycopg2.connect(**db_config)  # type: ignore[arg-type]
        try:
            ais_vessels = query_ais_vessels(conn)
            ais_crossings = query_ais_crossings(conn)
            LOGGER.info("Got %d AIS vessels, %d crossings from DB", len(ais_vessels), len(ais_crossings))
        finally:
            conn.close()
    except Exception:
        LOGGER.warning("Could not connect to PostgreSQL — using GFW data only")

    # Merge
    merged = merge_timeline(gfw_timeline, ais_vessels)

    # Write merged timeline
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timeline_path = OUTPUT_DIR / "vessels_timeline.json"
    with open(timeline_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, separators=(",", ":"))
    size_mb = timeline_path.stat().st_size / 1024 / 1024
    LOGGER.info(
        "Wrote merged timeline: %d dates, %d vessels, %.1f MB to %s",
        len(merged["dates"]),
        len(merged["vessels"]),
        size_mb,
        timeline_path,
    )

    # Also write flat snapshot for backward compatibility
    flat_vessels = [
        {
            "mmsi": meta.get("mmsi"),
            "name": meta.get("name"),
            "type": meta.get("type", "Other"),
            "lat": None,
            "lng": None,
            "seen": None,
            "direction": None,
            "zone": None,
            "source": meta.get("source", "gfw"),
        }
        for meta in merged["vessels"].values()
    ]
    snapshot = {
        "generated_at": merged["generated_at"],
        "vessels": flat_vessels,
        "crossings": ais_crossings,
        "stats": {
            "total_vessels": len(flat_vessels),
            "total_crossings": len(ais_crossings),
            "vessel_types": {},
        },
    }
    for v in flat_vessels:
        t = v["type"]
        snapshot["stats"]["vessel_types"][t] = snapshot["stats"]["vessel_types"].get(t, 0) + 1

    snapshot_path = OUTPUT_DIR / "vessels.json"
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)
    LOGGER.info("Wrote snapshot: %d vessels to %s", len(flat_vessels), snapshot_path)


if __name__ == "__main__":
    main()
