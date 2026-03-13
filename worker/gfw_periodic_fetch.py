"""Periodic Global Fishing Watch data fetcher.

Runs fetch_gfw.py on a configurable interval (default: every 5 days).
Designed to be managed by the persistent worker supervisor.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time

LOGGER = logging.getLogger(__name__)

DEFAULT_INTERVAL_HOURS = 5 * 24  # 5 days


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    interval_hours = int(os.getenv("GFW_FETCH_INTERVAL_HOURS", DEFAULT_INTERVAL_HOURS))
    interval_seconds = interval_hours * 3600

    LOGGER.info("GFW periodic fetcher starting — interval=%dh (%dd)", interval_hours, interval_hours // 24)

    while True:
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
        except Exception:
            LOGGER.exception("GFW fetch failed — will retry next cycle")

        LOGGER.info("Sleeping %d hours until next fetch", interval_hours)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
