"""Supervisor for long-running datasource collectors."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

LOGGER = logging.getLogger(__name__)

DEFAULT_RESTART_DELAY_SECONDS = 5
WORKER_COMMANDS: dict[str, list[str]] = {
    "maritime-passages-live": ["python", "-m", "worker.maritime_passages_live"],
    "gfw-periodic-fetch": ["python", "-m", "worker.gfw_periodic_fetch"],
}


@dataclass(frozen=True)
class WorkerSpec:
    """A named long-running worker command."""

    name: str
    command: list[str]


def configure_logging() -> None:
    """Configure worker supervisor logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def parse_worker_names(raw_value: str | None) -> list[str]:
    """Parse a comma-separated list of worker names from the environment."""
    if raw_value is None:
        return []

    return [name.strip() for name in raw_value.split(",") if name.strip()]


def resolve_workers(worker_names: list[str]) -> list[WorkerSpec]:
    """Resolve worker names to command specs."""
    workers: list[WorkerSpec] = []
    unknown_workers = [name for name in worker_names if name not in WORKER_COMMANDS]
    if unknown_workers:
        unknown = ", ".join(sorted(unknown_workers))
        raise ValueError(f"Unknown persistent workers: {unknown}")

    for name in worker_names:
        workers.append(WorkerSpec(name=name, command=WORKER_COMMANDS[name]))

    return workers


async def stop_process(process: asyncio.subprocess.Process) -> None:
    """Terminate a subprocess gracefully, then force-kill if needed."""
    if process.returncode is not None:
        return

    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except TimeoutError:
        process.kill()
        await process.wait()


async def run_worker(
    worker: WorkerSpec,
    restart_delay_seconds: int,
) -> None:
    """Run and restart a worker process indefinitely."""
    backoff_seconds = restart_delay_seconds

    while True:
        LOGGER.info(
            "Starting persistent worker name=%s command=%s",
            worker.name,
            " ".join(worker.command),
        )
        process = await asyncio.create_subprocess_exec(*worker.command)
        try:
            returncode = await process.wait()
        except asyncio.CancelledError:
            await stop_process(process)
            raise

        LOGGER.warning(
            "Worker exited name=%s returncode=%s; restarting in %s seconds",
            worker.name,
            returncode,
            backoff_seconds,
        )
        await asyncio.sleep(backoff_seconds)
        backoff_seconds = min(backoff_seconds * 2, 60)


async def run_supervisor() -> None:
    """Run all configured persistent workers."""
    configure_logging()

    worker_names = parse_worker_names(os.getenv("PERSISTENT_TRACKERS"))
    if not worker_names:
        raise ValueError("PERSISTENT_TRACKERS is empty; no workers configured")

    workers = resolve_workers(worker_names)
    restart_delay_seconds = max(
        int(
            os.getenv("PERSISTENT_RESTART_DELAY_SECONDS", DEFAULT_RESTART_DELAY_SECONDS)
        ),
        1,
    )

    tasks = [
        asyncio.create_task(run_worker(worker, restart_delay_seconds))
        for worker in workers
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    """Entry point for the persistent worker supervisor."""
    asyncio.run(run_supervisor())


if __name__ == "__main__":
    main()
