"""
Catalog enrichment: raw SHL JSON -> processed catalog.json.

Single-pass transform that:
  1. Maps full-name `keys` to single-letter test_type codes.
  2. Normalizes job_levels into a coarse seniority bucket.
  3. Builds a compact `text_for_embedding` field per item.
  4. Drops items missing required fields (name, link).

Output: data/processed/catalog.json (a list of enriched item dicts).

Run: `python data/build_catalog.py`
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_PATH = ROOT / "raw" / "shl_product_catalog.json"
OUT_PATH = ROOT / "processed" / "catalog.json"

# Maps catalog `keys` (full names) to SHL's single-letter test_type codes.
# Source: SHL Solution Type taxonomy. K & P are the most common in this catalog.
KEY_TO_LETTER: dict[str, str] = {
    "Ability & Aptitude": "A",
    "Biodata & Situational Judgment": "B",
    "Competencies": "C",
    "Development & 360": "D",
    "Assessment Exercises": "E",
    "Knowledge & Skills": "K",
    "Personality & Behavior": "P",
    "Simulations": "S",
}

# Order in which to emit letters when an item has multiple keys.
# Tracked separately from the dict so output is stable across runs.
LETTER_ORDER: list[str] = ["K", "P", "A", "C", "B", "S", "D", "E"]

# Coarse seniority bucket -> canonical job_levels in catalog that map to it.
# An item that lists ANY of these levels is tagged with the bucket.
SENIORITY_BUCKETS: dict[str, set[str]] = {
    "junior": {"Entry-Level", "Graduate", "General Population"},
    "mid": {"Mid-Professional", "Professional Individual Contributor", "Supervisor"},
    "senior": {"Manager", "Front Line Manager"},
    "executive": {"Director", "Executive"},
}


def keys_to_test_type(keys: list[str]) -> str:
    """Comma-join single-letter codes for all `keys`, in priority order."""
    letters = {KEY_TO_LETTER[k] for k in keys if k in KEY_TO_LETTER}
    ordered = [l for l in LETTER_ORDER if l in letters]
    return ",".join(ordered)


def normalize_seniority(job_levels: list[str]) -> list[str]:
    """Return the seniority buckets this item is calibrated for."""
    out = []
    for bucket, members in SENIORITY_BUCKETS.items():
        if any(jl in members for jl in job_levels):
            out.append(bucket)
    return out


def build_text_for_embedding(item: dict) -> str:
    """Compact natural-language blob for dense retrieval.

    We deliberately repeat the name in the description because BM25 hits on
    name are by far the strongest signal for "find me OPQ32r"-style queries.
    """
    parts = [
        item.get("name", ""),
        item.get("description", ""),
        "Test types: " + ", ".join(item.get("keys", [])),
        "Job levels: " + ", ".join(item.get("job_levels", [])),
    ]
    if item.get("duration"):
        parts.append(f"Duration: {item['duration']}")
    if item.get("languages"):
        parts.append("Languages: " + ", ".join(item["languages"][:6]))
    return " | ".join(p for p in parts if p)


def enrich(raw_items: list[dict]) -> list[dict]:
    enriched = []
    for item in raw_items:
        # Required fields: drop anything missing them — we cannot recommend it.
        if not item.get("name") or not item.get("link"):
            continue
        keys = item.get("keys", []) or []
        out = {
            "entity_id": item.get("entity_id"),
            "name": item["name"],
            "url": item["link"],
            "test_type": keys_to_test_type(keys),
            "keys": keys,
            "description": item.get("description", "") or "",
            "job_levels": item.get("job_levels", []) or [],
            "seniority": normalize_seniority(item.get("job_levels", []) or []),
            "languages": item.get("languages", []) or [],
            "duration": item.get("duration", "") or "",
            "remote": item.get("remote", "") or "",
            "adaptive": item.get("adaptive", "") or "",
        }
        out["text_for_embedding"] = build_text_for_embedding(out)
        enriched.append(out)
    return enriched


MOJIBAKE_FIXES = {
    "â€“": "–",  # en-dash: UTF-8 bytes interpreted as Latin-1 round-trip
    "â€”": "—",  # em-dash
    "â€™": "’",  # right single quote
    "â€˜": "‘",  # left single quote
    "â€œ": "“",  # left double quote
    "â€": "”",  # right double quote
}


def _fix_mojibake(s: str) -> str:
    for bad, good in MOJIBAKE_FIXES.items():
        s = s.replace(bad, good)
    return s


def _fix_strings_in(obj):
    if isinstance(obj, dict):
        return {k: _fix_strings_in(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fix_strings_in(v) for v in obj]
    if isinstance(obj, str):
        return _fix_mojibake(obj)
    return obj


def main() -> None:
    # The raw file has a stray control character on line 4795 — load lenient.
    raw_text = RAW_PATH.read_text(encoding="utf-8", errors="replace")
    raw = json.loads(raw_text, strict=False)
    raw = _fix_strings_in(raw)
    enriched = enrich(raw)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(enriched, ensure_ascii=False, indent=2))
    print(f"Wrote {len(enriched)} items -> {OUT_PATH.relative_to(ROOT.parent)}")


if __name__ == "__main__":
    main()
