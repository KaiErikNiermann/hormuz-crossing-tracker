"""Estimate live maritime passage crossings from AIS position streams."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg2
import yaml
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from worker.db import get_db_config

DB_CONFIG = get_db_config()

CONFIG_FILE = Path(__file__).parent.parent / "config.yaml"
AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"
SOURCE_NAME = "AISStream live estimate"
DEFAULT_API_KEY_ENV = "AISSTREAM_API_KEY"
POSITION_MESSAGE_TYPES = {
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
}
STATIC_MESSAGE_TYPES = {
    "ShipStaticData",
    "StaticDataReport",
}
DEFAULT_FILTER_MESSAGE_TYPES = sorted(POSITION_MESSAGE_TYPES | STATIC_MESSAGE_TYPES)

LOGGER = logging.getLogger(__name__)

CREATE_EVENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS maritime_passage_live_events (
    id SERIAL PRIMARY KEY,
    event_timestamp TIMESTAMPTZ NOT NULL,
    passage VARCHAR(64) NOT NULL,
    direction VARCHAR(32) NOT NULL,
    mmsi BIGINT NOT NULL,
    ship_name VARCHAR(128),
    ship_type_code INT,
    ship_type VARCHAR(32) NOT NULL,
    source VARCHAR(128) NOT NULL,
    zone_from VARCHAR(16) NOT NULL,
    zone_to VARCHAR(16) NOT NULL,
    UNIQUE (event_timestamp, passage, mmsi, direction)
);
CREATE INDEX IF NOT EXISTS idx_maritime_passage_live_events_time
    ON maritime_passage_live_events(event_timestamp);
CREATE INDEX IF NOT EXISTS idx_maritime_passage_live_events_passage
    ON maritime_passage_live_events(passage);
CREATE INDEX IF NOT EXISTS idx_maritime_passage_live_events_ship_type
    ON maritime_passage_live_events(ship_type);
"""

CREATE_STATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS maritime_passage_live_state (
    passage VARCHAR(64) NOT NULL,
    mmsi BIGINT NOT NULL,
    ship_name VARCHAR(128),
    ship_type_code INT,
    ship_type VARCHAR(32) NOT NULL,
    pending_zone VARCHAR(16),
    pending_zone_seen_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ NOT NULL,
    last_latitude DOUBLE PRECISION,
    last_longitude DOUBLE PRECISION,
    last_direction VARCHAR(32),
    last_event_at TIMESTAMPTZ,
    PRIMARY KEY (passage, mmsi)
);
CREATE INDEX IF NOT EXISTS idx_maritime_passage_live_state_last_seen
    ON maritime_passage_live_state(last_seen_at);
"""

DROP_SCHEMA_SQL = """
DROP TABLE IF EXISTS maritime_passage_live_events;
DROP TABLE IF EXISTS maritime_passage_live_state;
"""


@dataclass(frozen=True)
class BoundingBox:
    """A rectangular geofence using latitude / longitude bounds."""

    south: float
    west: float
    north: float
    east: float

    def contains(self, latitude: float, longitude: float) -> bool:
        """Return whether a point falls inside the bounding box."""
        return (
            self.south <= latitude <= self.north and self.west <= longitude <= self.east
        )

    def as_aisstream_box(self) -> list[list[float]]:
        """Return the box in AISStream subscription format."""
        return [[self.south, self.west], [self.north, self.east]]


@dataclass(frozen=True)
class PassageConfig:
    """Configuration for one live-tracked maritime passage."""

    name: str
    bbox: BoundingBox
    zone_a_name: str
    zone_a_box: BoundingBox
    zone_b_name: str
    zone_b_box: BoundingBox

    def classify_zone(self, latitude: float, longitude: float) -> str | None:
        """Return the approach zone containing a point, if any."""
        if self.zone_a_box.contains(latitude, longitude):
            return self.zone_a_name
        if self.zone_b_box.contains(latitude, longitude):
            return self.zone_b_name
        return None


@dataclass(frozen=True)
class TrackerSettings:
    """Runtime settings for the live AIS tracker."""

    api_key_env: str
    run_seconds: int
    state_ttl: timedelta
    crossing_timeout: timedelta
    min_event_gap: timedelta
    passages: list[PassageConfig]
    filter_message_types: list[str]


@dataclass(frozen=True)
class VesselMetadata:
    """Static or semi-static metadata for a vessel."""

    mmsi: int
    ship_name: str | None
    ship_type_code: int | None
    ship_type: str


@dataclass
class VesselState:
    """Persistent in-memory state for a vessel / passage pair."""

    passage: str
    mmsi: int
    ship_name: str | None
    ship_type_code: int | None
    ship_type: str
    pending_zone: str | None
    pending_zone_seen_at: datetime | None
    last_seen_at: datetime
    last_latitude: float | None
    last_longitude: float | None
    last_direction: str | None
    last_event_at: datetime | None


@dataclass(frozen=True)
class PositionUpdate:
    """A live AIS position update."""

    timestamp: datetime
    mmsi: int
    latitude: float
    longitude: float
    ship_name: str | None


@dataclass(frozen=True)
class StaticUpdate:
    """A live AIS static metadata update."""

    metadata: VesselMetadata


@dataclass(frozen=True)
class LiveCrossingEvent:
    """A derived passage crossing event."""

    event_timestamp: datetime
    passage: str
    direction: str
    mmsi: int
    ship_name: str | None
    ship_type_code: int | None
    ship_type: str
    source: str
    zone_from: str
    zone_to: str


def configure_logging() -> None:
    """Configure module logging once for CLI runs."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def load_config() -> dict[str, Any]:
    """Load the live maritime tracker configuration block from config.yaml."""
    if not CONFIG_FILE.exists():
        return {}

    try:
        with open(CONFIG_FILE, encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file)
            return config.get("maritime_live_tracker", {}) if config else {}
    except yaml.YAMLError as exc:
        LOGGER.warning("Unable to parse config.yaml: %s", exc)
        return {}


def parse_bounding_box(raw_box: object) -> BoundingBox:
    """Parse a configured bounding box object."""
    if not isinstance(raw_box, dict):
        raise ValueError("Bounding box must be a mapping.")

    try:
        south = float(raw_box["south"])
        west = float(raw_box["west"])
        north = float(raw_box["north"])
        east = float(raw_box["east"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid bounding box values: {raw_box}") from exc

    if south >= north or west >= east:
        raise ValueError(f"Invalid bounding box ordering: {raw_box}")

    return BoundingBox(south=south, west=west, north=north, east=east)


def parse_passage_config(raw_passage: object) -> PassageConfig:
    """Parse one configured passage definition."""
    if not isinstance(raw_passage, dict):
        raise ValueError("Passage config must be a mapping.")

    name = str(raw_passage.get("name") or "").strip()
    zone_a_name = str(raw_passage.get("zone_a_name") or "").strip()
    zone_b_name = str(raw_passage.get("zone_b_name") or "").strip()
    if not name or not zone_a_name or not zone_b_name:
        raise ValueError(f"Passage config is missing required fields: {raw_passage}")

    return PassageConfig(
        name=name,
        bbox=parse_bounding_box(raw_passage.get("bbox")),
        zone_a_name=zone_a_name,
        zone_a_box=parse_bounding_box(raw_passage.get("zone_a")),
        zone_b_name=zone_b_name,
        zone_b_box=parse_bounding_box(raw_passage.get("zone_b")),
    )


def resolve_positive_int(value: object, default_value: int) -> int:
    """Resolve a positive integer setting."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default_value

    return max(number, 0)


def load_tracker_settings(config: dict[str, Any]) -> TrackerSettings:
    """Normalize live tracker settings from config.yaml."""
    raw_passages = config.get("passages") or [
        {
            "name": "Hormuz",
            "bbox": {
                "south": 25.4,
                "west": 55.0,
                "north": 26.9,
                "east": 57.8,
            },
            "zone_a_name": "west",
            "zone_a": {
                "south": 26.0,
                "west": 55.85,
                "north": 26.75,
                "east": 56.35,
            },
            "zone_b_name": "east",
            "zone_b": {
                "south": 25.95,
                "west": 56.95,
                "north": 26.65,
                "east": 57.45,
            },
        }
    ]

    passages = [parse_passage_config(raw_passage) for raw_passage in raw_passages]
    filter_message_types = [
        str(message_type)
        for message_type in (
            config.get("filter_message_types") or DEFAULT_FILTER_MESSAGE_TYPES
        )
    ]

    return TrackerSettings(
        api_key_env=str(config.get("api_key_env") or DEFAULT_API_KEY_ENV),
        run_seconds=resolve_positive_int(config.get("run_seconds"), 0),
        state_ttl=timedelta(
            hours=resolve_positive_int(config.get("state_ttl_hours"), 48),
        ),
        crossing_timeout=timedelta(
            hours=resolve_positive_int(config.get("crossing_timeout_hours"), 18),
        ),
        min_event_gap=timedelta(
            hours=resolve_positive_int(config.get("min_event_gap_hours"), 6),
        ),
        passages=passages,
        filter_message_types=filter_message_types,
    )


def normalize_ship_name(value: object) -> str | None:
    """Normalize AIS ship names by stripping padding markers."""
    if not isinstance(value, str):
        return None

    normalized = value.replace("@", " ").strip()
    return normalized or None


def map_ais_ship_type(ship_type_code: int | None) -> str:
    """Map AIS ship type codes into the ONS-aligned categories."""
    if ship_type_code is None:
        return "Other"

    if 70 <= ship_type_code <= 79:
        return "Cargo"
    if 80 <= ship_type_code <= 89:
        return "Tanker"
    return "Other"


def get_metadata_object(ais_message: dict[str, Any]) -> dict[str, Any]:
    """Return the metadata object from an AISStream message."""
    metadata = ais_message.get("MetaData")
    if isinstance(metadata, dict):
        return metadata

    metadata = ais_message.get("Metadata")
    if isinstance(metadata, dict):
        return metadata

    return {}


def parse_metadata_timestamp(metadata: dict[str, Any]) -> datetime | None:
    """Parse the stream metadata timestamp if present."""
    value = metadata.get("time_utc")
    if not isinstance(value, str):
        return None

    for fmt in ("%Y-%m-%d %H:%M:%S.%f %z UTC", "%Y-%m-%d %H:%M:%S %z UTC"):
        try:
            return datetime.strptime(value, fmt).astimezone(UTC)
        except ValueError:
            continue

    return None


def extract_message_body(
    ais_message: dict[str, Any],
    message_type: str,
) -> dict[str, Any] | None:
    """Extract the message body matching `MessageType`."""
    message = ais_message.get("Message")
    if not isinstance(message, dict):
        return None

    body = message.get(message_type)
    if not isinstance(body, dict):
        return None

    return body


def extract_position_update(ais_message: dict[str, Any]) -> PositionUpdate | None:
    """Extract a position update from an AISStream message."""
    message_type = ais_message.get("MessageType")
    if not isinstance(message_type, str) or message_type not in POSITION_MESSAGE_TYPES:
        return None

    body = extract_message_body(ais_message, message_type)
    if body is None:
        return None

    metadata = get_metadata_object(ais_message)

    try:
        mmsi = int(body["UserID"])
        latitude = float(body["Latitude"])
        longitude = float(body["Longitude"])
    except (KeyError, TypeError, ValueError):
        return None

    timestamp = parse_metadata_timestamp(metadata) or datetime.now(UTC)
    ship_name = normalize_ship_name(
        metadata.get("ShipName") or metadata.get("ship_name"),
    )
    return PositionUpdate(
        timestamp=timestamp,
        mmsi=mmsi,
        latitude=latitude,
        longitude=longitude,
        ship_name=ship_name,
    )


def extract_static_update(ais_message: dict[str, Any]) -> StaticUpdate | None:
    """Extract static vessel metadata from an AISStream message."""
    message_type = ais_message.get("MessageType")
    if not isinstance(message_type, str) or message_type not in STATIC_MESSAGE_TYPES:
        return None

    body = extract_message_body(ais_message, message_type)
    if body is None:
        return None

    metadata = get_metadata_object(ais_message)

    try:
        mmsi = int(body["UserID"])
    except (KeyError, TypeError, ValueError):
        return None

    ship_name = normalize_ship_name(
        metadata.get("ShipName") or metadata.get("ship_name")
    )
    ship_type_code: int | None = None

    if message_type == "ShipStaticData":
        ship_name = normalize_ship_name(body.get("Name")) or ship_name
        raw_ship_type = body.get("Type")
        if raw_ship_type is not None:
            try:
                ship_type_code = int(raw_ship_type)
            except (TypeError, ValueError):
                ship_type_code = None
    elif message_type == "StaticDataReport":
        report_a = body.get("ReportA")
        if isinstance(report_a, dict):
            ship_name = normalize_ship_name(report_a.get("Name")) or ship_name

        report_b = body.get("ReportB")
        if isinstance(report_b, dict):
            raw_ship_type = report_b.get("ShipType")
            if raw_ship_type is not None:
                try:
                    ship_type_code = int(raw_ship_type)
                except (TypeError, ValueError):
                    ship_type_code = None

    return StaticUpdate(
        metadata=VesselMetadata(
            mmsi=mmsi,
            ship_name=ship_name,
            ship_type_code=ship_type_code,
            ship_type=map_ais_ship_type(ship_type_code),
        )
    )


class PassageTracker:
    """Stateful passage-crossing detector built from AIS positions."""

    def __init__(self, settings: TrackerSettings) -> None:
        self.settings = settings
        self.metadata_by_mmsi: dict[int, VesselMetadata] = {}
        self.states: dict[tuple[str, int], VesselState] = {}

    def load_states(self, rows: list[tuple[Any, ...]], now: datetime) -> None:
        """Load persisted vessel state rows into memory."""
        for row in rows:
            state = VesselState(
                passage=str(row[0]),
                mmsi=int(row[1]),
                ship_name=row[2],
                ship_type_code=row[3],
                ship_type=str(row[4]),
                pending_zone=row[5],
                pending_zone_seen_at=row[6],
                last_seen_at=row[7],
                last_latitude=row[8],
                last_longitude=row[9],
                last_direction=row[10],
                last_event_at=row[11],
            )

            if now - state.last_seen_at > self.settings.state_ttl:
                continue

            self.states[(state.passage, state.mmsi)] = state
            self.metadata_by_mmsi[state.mmsi] = VesselMetadata(
                mmsi=state.mmsi,
                ship_name=state.ship_name,
                ship_type_code=state.ship_type_code,
                ship_type=state.ship_type,
            )

    def process_static_update(self, update: StaticUpdate) -> list[VesselState]:
        """Merge new static metadata into tracker state."""
        metadata = update.metadata
        self.metadata_by_mmsi[metadata.mmsi] = metadata

        changed_states: list[VesselState] = []
        for state in self.states.values():
            if state.mmsi != metadata.mmsi:
                continue

            state.ship_name = metadata.ship_name or state.ship_name
            state.ship_type_code = metadata.ship_type_code
            state.ship_type = metadata.ship_type
            changed_states.append(state)

        return changed_states

    def process_position_update(
        self,
        update: PositionUpdate,
    ) -> tuple[list[LiveCrossingEvent], list[VesselState]]:
        """Process one position update and emit any derived crossing events."""
        metadata = self.metadata_by_mmsi.get(update.mmsi)
        if update.ship_name is not None:
            metadata = VesselMetadata(
                mmsi=update.mmsi,
                ship_name=update.ship_name,
                ship_type_code=metadata.ship_type_code if metadata else None,
                ship_type=metadata.ship_type if metadata else "Other",
            )
            self.metadata_by_mmsi[update.mmsi] = metadata

        events: list[LiveCrossingEvent] = []
        changed_states: list[VesselState] = []

        for passage in self.settings.passages:
            if not passage.bbox.contains(update.latitude, update.longitude):
                continue

            key = (passage.name, update.mmsi)
            state = self.states.get(key)
            if state is None:
                state = VesselState(
                    passage=passage.name,
                    mmsi=update.mmsi,
                    ship_name=metadata.ship_name if metadata else update.ship_name,
                    ship_type_code=metadata.ship_type_code if metadata else None,
                    ship_type=metadata.ship_type if metadata else "Other",
                    pending_zone=None,
                    pending_zone_seen_at=None,
                    last_seen_at=update.timestamp,
                    last_latitude=update.latitude,
                    last_longitude=update.longitude,
                    last_direction=None,
                    last_event_at=None,
                )
                self.states[key] = state

            if metadata is not None:
                state.ship_name = metadata.ship_name or state.ship_name
                state.ship_type_code = metadata.ship_type_code
                state.ship_type = metadata.ship_type

            if update.timestamp - state.last_seen_at > self.settings.state_ttl:
                state.pending_zone = None
                state.pending_zone_seen_at = None

            state.last_seen_at = update.timestamp
            state.last_latitude = update.latitude
            state.last_longitude = update.longitude

            zone = passage.classify_zone(update.latitude, update.longitude)
            if zone is None:
                changed_states.append(state)
                continue

            if state.pending_zone is None:
                state.pending_zone = zone
                state.pending_zone_seen_at = update.timestamp
                changed_states.append(state)
                continue

            if zone == state.pending_zone:
                state.pending_zone_seen_at = update.timestamp
                changed_states.append(state)
                continue

            is_fresh_crossing = (
                state.pending_zone_seen_at is not None
                and update.timestamp - state.pending_zone_seen_at
                <= self.settings.crossing_timeout
            )
            respects_gap = (
                state.last_event_at is None
                or update.timestamp - state.last_event_at >= self.settings.min_event_gap
            )
            previous_zone = state.pending_zone

            if is_fresh_crossing and respects_gap:
                direction = f"{previous_zone}_to_{zone}"
                events.append(
                    LiveCrossingEvent(
                        event_timestamp=update.timestamp,
                        passage=passage.name,
                        direction=direction,
                        mmsi=update.mmsi,
                        ship_name=state.ship_name,
                        ship_type_code=state.ship_type_code,
                        ship_type=state.ship_type,
                        source=SOURCE_NAME,
                        zone_from=previous_zone,
                        zone_to=zone,
                    )
                )
                state.last_event_at = update.timestamp
                state.last_direction = direction

            state.pending_zone = zone
            state.pending_zone_seen_at = update.timestamp
            changed_states.append(state)

        return events, changed_states


def create_schema(conn: psycopg2.extensions.connection, reset: bool = False) -> None:
    """Create the live tracker tables."""
    cur = conn.cursor()
    if reset:
        cur.execute(DROP_SCHEMA_SQL)
    cur.execute(CREATE_EVENTS_TABLE_SQL)
    cur.execute(CREATE_STATE_TABLE_SQL)
    conn.commit()
    cur.close()


def load_persisted_states(
    conn: psycopg2.extensions.connection,
    tracker: PassageTracker,
) -> None:
    """Load persisted vessel state from PostgreSQL into the tracker."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            passage,
            mmsi,
            ship_name,
            ship_type_code,
            ship_type,
            pending_zone,
            pending_zone_seen_at,
            last_seen_at,
            last_latitude,
            last_longitude,
            last_direction,
            last_event_at
        FROM maritime_passage_live_state
        """
    )
    rows = cur.fetchall()
    cur.close()
    tracker.load_states(rows, now=datetime.now(UTC))


def cleanup_stale_state(
    conn: psycopg2.extensions.connection,
    settings: TrackerSettings,
) -> None:
    """Delete stale persisted vessel state rows."""
    cutoff = datetime.now(UTC) - settings.state_ttl
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM maritime_passage_live_state
        WHERE last_seen_at < %s
        """,
        (cutoff,),
    )
    conn.commit()
    cur.close()


def upsert_state(
    conn: psycopg2.extensions.connection,
    states: list[VesselState],
) -> None:
    """Persist updated vessel state rows."""
    if not states:
        return

    cur = conn.cursor()
    for state in states:
        cur.execute(
            """
            INSERT INTO maritime_passage_live_state
                (
                    passage,
                    mmsi,
                    ship_name,
                    ship_type_code,
                    ship_type,
                    pending_zone,
                    pending_zone_seen_at,
                    last_seen_at,
                    last_latitude,
                    last_longitude,
                    last_direction,
                    last_event_at
                )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (passage, mmsi)
            DO UPDATE SET
                ship_name = EXCLUDED.ship_name,
                ship_type_code = EXCLUDED.ship_type_code,
                ship_type = EXCLUDED.ship_type,
                pending_zone = EXCLUDED.pending_zone,
                pending_zone_seen_at = EXCLUDED.pending_zone_seen_at,
                last_seen_at = EXCLUDED.last_seen_at,
                last_latitude = EXCLUDED.last_latitude,
                last_longitude = EXCLUDED.last_longitude,
                last_direction = EXCLUDED.last_direction,
                last_event_at = EXCLUDED.last_event_at
            """,
            (
                state.passage,
                state.mmsi,
                state.ship_name,
                state.ship_type_code,
                state.ship_type,
                state.pending_zone,
                state.pending_zone_seen_at,
                state.last_seen_at,
                state.last_latitude,
                state.last_longitude,
                state.last_direction,
                state.last_event_at,
            ),
        )
    conn.commit()
    cur.close()


def insert_events(
    conn: psycopg2.extensions.connection,
    events: list[LiveCrossingEvent],
) -> int:
    """Insert derived live crossing events."""
    if not events:
        return 0

    cur = conn.cursor()
    inserted = 0
    for event in events:
        cur.execute(
            """
            INSERT INTO maritime_passage_live_events
                (
                    event_timestamp,
                    passage,
                    direction,
                    mmsi,
                    ship_name,
                    ship_type_code,
                    ship_type,
                    source,
                    zone_from,
                    zone_to
                )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_timestamp, passage, mmsi, direction)
            DO NOTHING
            """,
            (
                event.event_timestamp,
                event.passage,
                event.direction,
                event.mmsi,
                event.ship_name,
                event.ship_type_code,
                event.ship_type,
                event.source,
                event.zone_from,
                event.zone_to,
            ),
        )
        if cur.rowcount > 0:
            inserted += 1
    conn.commit()
    cur.close()
    return inserted


def build_subscription_message(
    settings: TrackerSettings,
    api_key: str,
) -> dict[str, Any]:
    """Build the AISStream subscription payload."""
    return {
        "APIKey": api_key,
        "BoundingBoxes": [
            passage.bbox.as_aisstream_box() for passage in settings.passages
        ],
        "FilterMessageTypes": settings.filter_message_types,
    }


async def consume_live_stream(
    conn: psycopg2.extensions.connection,
    tracker: PassageTracker,
    settings: TrackerSettings,
    api_key: str,
) -> None:
    """Consume the live AIS stream and persist derived passage events."""
    started_at = asyncio.get_running_loop().time()
    message_count = 0
    event_count = 0

    async for websocket in connect(
        AISSTREAM_URL,
        ping_interval=20,
        ping_timeout=20,
        max_queue=256,
        open_timeout=45,
        compression=None,
    ):
        try:
            await websocket.send(
                json.dumps(build_subscription_message(settings, api_key))
            )
            async for message_json in websocket:
                if settings.run_seconds > 0:
                    elapsed = asyncio.get_running_loop().time() - started_at
                    if elapsed >= settings.run_seconds:
                        LOGGER.info(
                            "Stopping live tracker after %s seconds",
                            settings.run_seconds,
                        )
                        return

                message_count += 1
                ais_message = json.loads(message_json)
                if isinstance(ais_message, dict) and "error" in ais_message:
                    raise RuntimeError(str(ais_message["error"]))

                if not isinstance(ais_message, dict):
                    continue

                static_update = extract_static_update(ais_message)
                if static_update is not None:
                    changed_states = tracker.process_static_update(static_update)
                    upsert_state(conn, changed_states)

                position_update = extract_position_update(ais_message)
                if position_update is not None:
                    events, changed_states = tracker.process_position_update(
                        position_update
                    )
                    upsert_state(conn, changed_states)
                    inserted = insert_events(conn, events)
                    if inserted > 0:
                        event_count += inserted
                        LOGGER.info(
                            "Recorded %s new live crossings; total inserted this run=%s",
                            inserted,
                            event_count,
                        )

                if message_count % 500 == 0:
                    LOGGER.info(
                        "Processed %s AIS messages and recorded %s live crossings",
                        message_count,
                        event_count,
                    )
        except ConnectionClosed:
            LOGGER.warning("AISStream connection closed; reconnecting")
            continue


def run_live(reset: bool) -> None:
    """Run the live AIS-based passage tracker."""
    configure_logging()
    config = load_config()
    settings = load_tracker_settings(config)

    LOGGER.info("Connecting to PostgreSQL")
    conn = psycopg2.connect(**DB_CONFIG)  # type: ignore[arg-type]

    try:
        create_schema(conn, reset=reset)
        api_key = os.getenv(settings.api_key_env)
        if not api_key:
            LOGGER.error(
                "Missing %s environment variable; created schema but cannot start live AIS tracker",
                settings.api_key_env,
            )
            return

        cleanup_stale_state(conn, settings)

        tracker = PassageTracker(settings)
        load_persisted_states(conn, tracker)

        LOGGER.info(
            "Starting live tracker for %s passage(s); run_seconds=%s",
            len(settings.passages),
            settings.run_seconds,
        )
        asyncio.run(consume_live_stream(conn, tracker, settings, api_key))
    finally:
        conn.close()

    LOGGER.info("Done")


def main() -> None:
    """Main entry point: run the live AIS-based passage tracker."""
    run_live(reset=False)


def reset_and_seed() -> None:
    """Reset live tables and start the live AIS-based passage tracker."""
    run_live(reset=True)


if __name__ == "__main__":
    main()
