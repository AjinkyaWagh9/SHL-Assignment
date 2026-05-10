"""Metadata pre-filter: cheap, deterministic gating before BM25 + dense.

We keep this PERMISSIVE on purpose. If a state field is unknown (None or empty), do not
filter on it — that would silently drop candidates the user might still want.
"""

from __future__ import annotations

from agent.state import UserState


def _has_overlap(a: list[str], b: list[str]) -> bool:
    return any(x in b for x in a)


def apply_filters(items: list[dict], state: UserState) -> list[dict]:
    """Return a subset of `items` consistent with `state`.

    Filters applied (each only if state has the relevant signal):
      - seniority: keep items whose seniority bucket includes the user's level OR items
        with no seniority data (catalog has 0 such, but be defensive).
      - language: keep items that list this language OR items with empty languages
        (some reports have empty languages — don't drop them).
      - test_types_excluded: drop items whose test_type contains an excluded letter.

    Notes:
      - We deliberately do NOT filter on `test_types_wanted` here. That's better
        applied as a post-fusion boost so the LLM still sees nearby candidates.
      - We do NOT filter on duration unless the user gave a specific cap and the
        item has a parseable duration.
    """
    filtered = []
    for item in items:
        if state.seniority and item.get("seniority"):
            if state.seniority not in item["seniority"]:
                continue

        if state.language and item.get("languages"):
            if state.language not in item["languages"]:
                continue

        if state.test_types_excluded:
            item_letters = set(item.get("test_type", "").split(","))
            if item_letters & set(state.test_types_excluded):
                continue

        filtered.append(item)

    # Safety net: if filters wiped everything out, return the original list.
    # Better to give the LLM something noisy than nothing at all.
    return filtered if filtered else items
