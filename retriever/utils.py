"""Retrieval helpers: RRF fusion and minor utilities."""

from __future__ import annotations

from collections import defaultdict


def reciprocal_rank_fusion(
    rankings: list[list[str]], k: int = 60
) -> list[tuple[str, float]]:
    """Combine multiple ranked lists of doc-ids into one list with RRF scores.

    RRF formula: score(d) = sum over rankers of 1 / (k + rank_in_ranker(d)).
    Parameter-free across heterogeneous score scales — robust default for hybrid search.
    """
    fused: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            fused[doc_id] += 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
