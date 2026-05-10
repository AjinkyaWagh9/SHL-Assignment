"""LLM-output validator. Hard contract: only catalog items survive.

Behavior:
  1. Parse the LLM's JSON. Strip markdown fences if present.
  2. For each item in `recommendations`: byte-match `name` against the catalog. If miss,
     try a case-insensitive match. If still miss, drop the item.
  3. If `name` matches but `url` differs from catalog → overwrite with catalog `url`.
  4. Force `test_type` to the catalog's value (LLM-derived letters can drift).
  5. Cap to 10 items.
  6. If `recommend`/`refine` ended up with 0 items, fall back to the top retrieval items
     so the user always gets something — but only when the orchestrator passes them in.
"""

from __future__ import annotations

import json
import re

from api.dependencies import get_catalog_by_name
from api.schemas import ChatResponse, Recommendation


CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?|\n?```\s*$", re.MULTILINE)


def _strip_fence(text: str) -> str:
    return CODE_FENCE_RE.sub("", text).strip()


def parse_llm_json(raw: str) -> dict:
    """Parse a JSON object from the LLM, tolerant of fences + leading/trailing text."""
    text = _strip_fence(raw)
    # Find first '{' and matching last '}'.
    first = text.find("{")
    last = text.rfind("}")
    if first == -1 or last == -1 or last < first:
        raise ValueError(f"No JSON object found in LLM output: {raw[:200]!r}")
    return json.loads(text[first : last + 1])


def validate_recommendations(items: list[dict]) -> list[Recommendation]:
    """Drop any item not in the catalog. Force exact `url` and `test_type` from catalog."""
    catalog_by_name = get_catalog_by_name()
    catalog_lookup_ci = {k.lower(): v for k, v in catalog_by_name.items()}

    out: list[Recommendation] = []
    seen_urls: set[str] = set()
    for it in items:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        catalog_item = catalog_by_name.get(name) or catalog_lookup_ci.get(name.lower())
        if not catalog_item:
            continue  # not in catalog → drop, no exceptions
        if catalog_item["url"] in seen_urls:
            continue  # dedupe
        seen_urls.add(catalog_item["url"])
        out.append(
            Recommendation(
                name=catalog_item["name"],
                url=catalog_item["url"],
                test_type=catalog_item["test_type"],
            )
        )
        if len(out) >= 10:
            break
    return out


def build_response(
    parsed: dict,
    fallback_items: list[dict] | None = None,
) -> ChatResponse:
    intent = parsed.get("intent", "clarify")
    reply = (parsed.get("reply") or "").strip()
    recs_raw = parsed.get("recommendations") or []
    eoc = bool(parsed.get("end_of_conversation", False))

    # Hard rule: clarify and refuse always emit empty recommendations.
    if intent in ("clarify", "refuse"):
        recs: list[Recommendation] = []
        eoc = False if intent == "refuse" else eoc
    else:
        recs = validate_recommendations(recs_raw)
        # Fallback: if the LLM dropped everything during recommend/refine, use top
        # retrieval candidates so the user is never empty-handed.
        if not recs and intent in ("recommend", "refine") and fallback_items:
            recs = validate_recommendations(fallback_items[:5])

    # Defensive: never set eoc on an empty-recs commit turn.
    if intent in ("clarify", "refuse"):
        eoc = False

    return ChatResponse(reply=reply, recommendations=recs, end_of_conversation=eoc)
