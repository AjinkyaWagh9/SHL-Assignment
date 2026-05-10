"""Evaluation metrics: Recall@K, schema compliance, catalog-only check."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "data" / "processed" / "catalog.json"


def _catalog_names() -> set[str]:
    return {item["name"] for item in json.loads(CATALOG_PATH.read_text())}


def recall_at_k(
    predicted_names: list[str], expected_names: list[str], k: int = 10
) -> float:
    """Fraction of `expected_names` present in the first `k` of `predicted_names`."""
    if not expected_names:
        return 0.0
    top_k = set(predicted_names[:k])
    hits = sum(1 for e in expected_names if e in top_k)
    return hits / len(expected_names)


def all_items_in_catalog(predicted_names: list[str]) -> bool:
    catalog = _catalog_names()
    return all(n in catalog for n in predicted_names)


def schema_compliant(response_obj: dict) -> bool:
    """Match the SHL response schema strictly."""
    if not isinstance(response_obj, dict):
        return False
    if set(response_obj.keys()) != {"reply", "recommendations", "end_of_conversation"}:
        return False
    if not isinstance(response_obj.get("reply"), str):
        return False
    if not isinstance(response_obj.get("end_of_conversation"), bool):
        return False
    recs = response_obj.get("recommendations")
    if not isinstance(recs, list):
        return False
    if len(recs) > 10:
        return False
    for r in recs:
        if not isinstance(r, dict):
            return False
        if set(r.keys()) != {"name", "url", "test_type"}:
            return False
        if not all(isinstance(r.get(f), str) for f in ("name", "url", "test_type")):
            return False
    return True


def turn_cap_honored(message_count: int, cap: int = 8) -> bool:
    return message_count <= cap


def summarize(results: list[dict]) -> dict:
    """Aggregate per-trace results into a single report."""
    if not results:
        return {"recall_at_10": 0.0, "schema_pass": 0.0, "catalog_only_pass": 0.0}
    n = len(results)
    return {
        "n_traces": n,
        "mean_recall_at_10": sum(r["recall_at_10"] for r in results) / n,
        "schema_pass_rate": sum(1 for r in results if r["schema_ok"]) / n,
        "catalog_only_pass_rate": sum(1 for r in results if r["catalog_only"]) / n,
        "turn_cap_pass_rate": sum(1 for r in results if r["turn_cap_ok"]) / n,
    }
