"""Periodic Global Fishing Watch data fetcher.

Polls daily and only triggers an export/build when new dates appear
in the GFW timeline. Designed to be managed by the persistent worker supervisor.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

LOGGER = logging.getLogger(__name__)

DEFAULT_INTERVAL_HOURS = 24
TIMELINE_PATH = Path(__file__).parent.parent / "site" / "data" / "gfw_timeline.json"


def read_existing_dates() -> set[str]:
    """Read the set of dates currently in gfw_timeline.json."""
    if not TIMELINE_PATH.exists():
        return set()
    try:
        with open(TIMELINE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("dates", []))
    except (json.JSONDecodeError, OSError) as e:
        LOGGER.warning("Could not read existing timeline (%s)", e)
        return set()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    interval_hours = int(os.getenv("GFW_FETCH_INTERVAL_HOURS", DEFAULT_INTERVAL_HOURS))
    interval_seconds = interval_hours * 3600

    LOGGER.info("GFW periodic fetcher starting — interval=%dh", interval_hours)

    while True:
        dates_before = read_existing_dates()

        LOGGER.info("Running GFW fetch...")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "scripts.fetch_gfw"],
                check=True,
                capture_output=False,
            )
            LOGGER.info("GFW fetch completed (exit=%d)", result.returncode)
        except subprocess.CalledProcessError as e:
            LOGGER.error("GFW fetch failed (exit=%d) — will retry next cycle", e.returncode)
            LOGGER.info("Sleeping %d hours until next fetch", interval_hours)
            time.sleep(interval_seconds)
            continue
        except Exception:
            LOGGER.exception("GFW fetch failed — will retry next cycle")
            LOGGER.info("Sleeping %d hours until next fetch", interval_hours)
            time.sleep(interval_seconds)
            continue

        dates_after = read_existing_dates()
        new_dates = sorted(dates_after - dates_before)

        if new_dates:
            LOGGER.info(
                "New GFW dates available: %s — running export merge",
                ", ".join(new_dates),
            )
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "scripts.export_snapshot"],
                    check=True,
                    capture_output=False,
                )
                LOGGER.info("Export merge completed (exit=%d)", result.returncode)
            except subprocess.CalledProcessError as e:
                LOGGER.error("Export merge failed (exit=%d)", e.returncode)
            except Exception:
                LOGGER.exception("Export merge failed")
        else:
            LOGGER.info("No new dates in GFW data — skipping export, will poll again in %dh", interval_hours)

        LOGGER.info("Sleeping %d hours until next fetch", interval_hours)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
