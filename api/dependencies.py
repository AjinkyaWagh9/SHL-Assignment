"""Singleton getters loaded once at FastAPI lifespan startup.

Keeps the request path free of disk I/O and model loads.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "data" / "processed" / "catalog.json"


@lru_cache(maxsize=1)
def get_catalog() -> list[dict]:
    """Enriched catalog. 377 items expected."""
    return json.loads(CATALOG_PATH.read_text())


@lru_cache(maxsize=1)
def get_catalog_by_name() -> dict[str, dict]:
    """Lookup table for the response validator (byte-exact name match)."""
    return {item["name"]: item for item in get_catalog()}


@lru_cache(maxsize=1)
def get_catalog_by_url() -> dict[str, dict]:
    return {item["url"]: item for item in get_catalog()}
