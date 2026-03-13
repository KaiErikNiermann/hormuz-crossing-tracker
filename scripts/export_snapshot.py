"""Export current vessel positions and crossing events to JSON for the static site."""

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


def export_vessels(conn: psycopg2.extensions.connection) -> list[dict]:
    """Export current vessel positions from the live state table."""
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

    vessels = [
        {
            "mmsi": row[0],
            "name": row[1],
            "type": row[2],
            "lat": row[3],
            "lng": row[4],
            "seen": row[5].isoformat() if row[5] else None,
            "direction": row[6],
            "zone": row[7],
        }
        for row in rows
    ]
    return vessels


def export_crossings(conn: psycopg2.extensions.connection, days: int = 30) -> list[dict]:
    """Export recent crossing events."""
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

    crossings = [
        {
            "timestamp": row[0].isoformat() if row[0] else None,
            "direction": row[1],
            "mmsi": row[2],
            "name": row[3],
            "type": row[4],
            "from": row[5],
            "to": row[6],
        }
        for row in rows
    ]
    return crossings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    db_config = get_db_config()
    conn = psycopg2.connect(**db_config)  # type: ignore[arg-type]

    try:
        vessels = export_vessels(conn)
        crossings = export_crossings(conn)

        snapshot = {
            "generated_at": datetime.now(UTC).isoformat(),
            "vessels": vessels,
            "crossings": crossings,
            "stats": {
                "total_vessels": len(vessels),
                "total_crossings": len(crossings),
                "vessel_types": {},
            },
        }

        for v in vessels:
            ship_type = v["type"] or "Other"
            snapshot["stats"]["vessel_types"][ship_type] = (
                snapshot["stats"]["vessel_types"].get(ship_type, 0) + 1
            )

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / "vessels.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)

        LOGGER.info(
            "Exported %d vessels, %d crossings to %s",
            len(vessels),
            len(crossings),
            output_path,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
