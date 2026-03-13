"""Prepare data release assets: cumulative archive + rolling 30-day batch."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "site" / "data"
RELEASE_DIR = Path(__file__).parent.parent / "release"
LICENSE_FILE = Path(__file__).parent.parent / "DATA_LICENSE.md"


def filter_last_n_days(timeline: dict, days: int) -> dict:
    """Return a copy of the timeline with only the last N days of data."""
    cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
    recent_dates = [d for d in timeline.get("dates", []) if d >= cutoff]

    # Collect vessel IDs referenced in the recent positions
    referenced_vids: set[str] = set()
    recent_positions: dict[str, list] = {}
    for d in recent_dates:
        pos_list = timeline.get("positions", {}).get(d, [])
        recent_positions[d] = pos_list
        for p in pos_list:
            referenced_vids.add(p[0])

    recent_vessels = {
        vid: meta
        for vid, meta in timeline.get("vessels", {}).items()
        if vid in referenced_vids
    }

    recent_stats = {
        d: timeline.get("daily_stats", {}).get(d, {}) for d in recent_dates
    }

    return {
        "generated_at": timeline.get("generated_at"),
        "source": timeline.get("source"),
        "sources": timeline.get("sources"),
        "date_range": {
            "start": recent_dates[0] if recent_dates else None,
            "end": recent_dates[-1] if recent_dates else None,
        },
        "dates": recent_dates,
        "vessels": recent_vessels,
        "positions": recent_positions,
        "daily_stats": recent_stats,
    }


def main() -> None:
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)

    # Copy cumulative files
    for name in ["vessels_timeline.json", "gfw_timeline.json", "vessels.json"]:
        src = DATA_DIR / name
        if src.exists():
            shutil.copy2(src, RELEASE_DIR / name)
            size_mb = src.stat().st_size / 1024 / 1024
            print(f"Copied {name} ({size_mb:.1f} MB)")

    # Generate 30-day rolling batch
    timeline_path = DATA_DIR / "vessels_timeline.json"
    if timeline_path.exists():
        with open(timeline_path, encoding="utf-8") as f:
            full_timeline = json.load(f)

        batch = filter_last_n_days(full_timeline, 30)
        batch_path = RELEASE_DIR / "vessels_timeline_30d.json"
        with open(batch_path, "w", encoding="utf-8") as f:
            json.dump(batch, f, separators=(",", ":"))
        size_mb = batch_path.stat().st_size / 1024 / 1024
        print(f"Created 30-day batch: {len(batch['dates'])} dates, {len(batch['vessels'])} vessels, {size_mb:.1f} MB")

    # Copy data license
    if LICENSE_FILE.exists():
        shutil.copy2(LICENSE_FILE, RELEASE_DIR / "DATA_LICENSE.md")
        print("Included DATA_LICENSE.md")

    print(f"Release assets ready in {RELEASE_DIR}")


if __name__ == "__main__":
    main()
