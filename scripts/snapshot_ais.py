"""Quick AIS snapshot: connect to AISStream for ~30s and dump vessel positions."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from worker.maritime_passages_live import (
    AISSTREAM_URL,
    extract_position_update,
    extract_static_update,
    load_config,
    load_tracker_settings,
    normalize_ship_name,
    map_ais_ship_type,
)

from websockets.asyncio.client import connect


async def snapshot(duration_seconds: int = 45) -> None:
    config = load_config()
    settings = load_tracker_settings(config)
    api_key = os.getenv("AISSTREAM_API_KEY")
    if not api_key:
        print("Set AISSTREAM_API_KEY", file=sys.stderr)
        sys.exit(1)

    vessels: dict[int, dict] = {}
    metadata: dict[int, dict] = {}

    subscription = {
        "APIKey": api_key,
        "BoundingBoxes": [p.bbox.as_aisstream_box() for p in settings.passages],
        "FilterMessageTypes": settings.filter_message_types,
    }

    print(f"Connecting to AISStream for {duration_seconds}s snapshot...", file=sys.stderr)

    async for ws in connect(AISSTREAM_URL, ping_interval=20, ping_timeout=20, open_timeout=45, compression=None):
        try:
            await ws.send(json.dumps(subscription))
            started = asyncio.get_running_loop().time()

            async for msg_json in ws:
                elapsed = asyncio.get_running_loop().time() - started
                if elapsed >= duration_seconds:
                    break

                msg = json.loads(msg_json)
                if not isinstance(msg, dict):
                    continue

                if "error" in msg:
                    print(f"AIS error: {msg['error']}", file=sys.stderr)
                    break

                static = extract_static_update(msg)
                if static is not None:
                    m = static.metadata
                    metadata[m.mmsi] = {
                        "name": m.ship_name,
                        "type_code": m.ship_type_code,
                        "type": m.ship_type,
                    }

                pos = extract_position_update(msg)
                if pos is not None:
                    meta = metadata.get(pos.mmsi, {})
                    vessels[pos.mmsi] = {
                        "mmsi": pos.mmsi,
                        "name": pos.ship_name or meta.get("name"),
                        "type": meta.get("type", "Other"),
                        "lat": round(pos.latitude, 6),
                        "lng": round(pos.longitude, 6),
                        "seen": pos.timestamp.isoformat(),
                    }

                if int(elapsed) % 10 == 0 and int(elapsed) > 0:
                    print(f"  {int(elapsed)}s: {len(vessels)} unique vessels so far", file=sys.stderr)

            break
        except Exception as e:
            print(f"Connection error: {e}", file=sys.stderr)
            break

    vessel_list = sorted(vessels.values(), key=lambda v: v["seen"], reverse=True)

    output = {
        "generated_at": datetime.now(UTC).isoformat(),
        "vessels": vessel_list,
        "crossings": [],
        "stats": {
            "total_vessels": len(vessel_list),
            "total_crossings": 0,
            "vessel_types": {},
        },
    }
    for v in vessel_list:
        t = v["type"] or "Other"
        output["stats"]["vessel_types"][t] = output["stats"]["vessel_types"].get(t, 0) + 1

    out_path = Path(__file__).parent.parent / "site" / "data" / "vessels.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nDone! {len(vessel_list)} vessels written to {out_path}", file=sys.stderr)

    # Also dump a raw lat/lng CSV for analysis
    csv_path = Path(__file__).parent.parent / "site" / "data" / "positions_raw.csv"
    with open(csv_path, "w") as f:
        f.write("mmsi,name,type,lat,lng\n")
        for v in vessel_list:
            f.write(f"{v['mmsi']},{v['name']},{v['type']},{v['lat']},{v['lng']}\n")
    print(f"Raw positions CSV: {csv_path}", file=sys.stderr)


if __name__ == "__main__":
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 45
    asyncio.run(snapshot(duration))
