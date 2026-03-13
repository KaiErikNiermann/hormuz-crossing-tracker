"""Shared PostgreSQL configuration helpers."""

from __future__ import annotations

import os


def get_db_config() -> dict[str, str | int]:
    """Return PostgreSQL connection settings from environment variables."""
    return {
        "host": os.getenv("HORMUZ_DB_HOST", "localhost"),
        "port": int(os.getenv("HORMUZ_DB_PORT", "5432")),
        "database": os.getenv("HORMUZ_DB_NAME", "hormuz"),
        "user": os.getenv("HORMUZ_DB_USER", "hormuz"),
        "password": os.getenv("HORMUZ_DB_PASSWORD", "hormuz"),
    }
