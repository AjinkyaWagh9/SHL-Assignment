"""Diagnostic: do the expected items even appear in the top-K retrieval pool?

If an expected item is missing from top-25 candidates, no prompt or LLM fix can recover
it — the LLM never sees it. This is the upstream check before tuning prompts.

For each trace we simulate the persona's initial user message (same logic as the
replay simulator) and run the hybrid retriever. Then we check coverage of the
trace's `expected_names` against the candidate pool.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.state import infer_state
from api.schemas import Message
from retriever.hybrid_retriever import get_retriever

ROOT = Path(__file__).resolve().parent
TRACES_DIR = ROOT / "traces"
K = 25


def _initial_query(persona: dict) -> str:
    role = persona.get("role") or persona.get("audience") or persona.get("context")
    if role:
        return f"Hiring for {role}. What do you recommend?"
    return "I need to recommend an assessment."


def _enriched_query(persona: dict) -> str:
    """Worst-case: simulator answered every clarify question. Single fat query."""
    bits = []
    role = persona.get("role") or persona.get("audience") or persona.get("context")
    if role:
        bits.append(f"Hiring for {role}.")
    if persona.get("seniority"):
        bits.append(persona["seniority"])
    if persona.get("experience"):
        bits.append(persona["experience"])
    for k in ("priority", "industry", "stack_primary", "language", "accent"):
        v = persona.get(k)
        if v:
            bits.append(f"{k}: {', '.join(v) if isinstance(v, list) else v}")
    return " ".join(bits) if bits else "I need to recommend an assessment."


def audit() -> None:
    retriever = get_retriever()
    rows = []
    print(f"{'trace':<5} {'expected':<8} {'in_top25':<10} {'enriched_top25':<15} miss_initial")
    print("-" * 110)
    for tf in sorted(TRACES_DIR.glob("C*.json")):
        trace = json.loads(tf.read_text())
        persona = trace["persona_facts"]
        expected = trace["expected_names"]

        # Pass 1: initial query only (turn 1).
        q1 = _initial_query(persona)
        msgs1 = [Message(role="user", content=q1)]
        state1 = infer_state(msgs1)
        top1 = [r["name"] for r in retriever.search(state1, state1.last_user_msg, k=K)]
        hit1 = sum(1 for e in expected if e in top1)

        # Pass 2: enriched query (simulator volunteered everything).
        q2 = _enriched_query(persona)
        msgs2 = [Message(role="user", content=q2)]
        state2 = infer_state(msgs2)
        top2 = [r["name"] for r in retriever.search(state2, state2.last_user_msg, k=K)]
        hit2 = sum(1 for e in expected if e in top2)

        missing_initial = [e for e in expected if e not in top1]
        rows.append(
            {
                "trace": trace["id"],
                "expected": len(expected),
                "in_top25_initial": hit1,
                "in_top25_enriched": hit2,
                "missing_initial": missing_initial,
            }
        )
        print(
            f"{trace['id']:<5} {len(expected):<8} "
            f"{hit1}/{len(expected):<8} "
            f"{hit2}/{len(expected):<13} "
            f"{', '.join(missing_initial) if missing_initial else '-'}"
        )

    print()
    total_expected = sum(r["expected"] for r in rows)
    total_hit_initial = sum(r["in_top25_initial"] for r in rows)
    total_hit_enriched = sum(r["in_top25_enriched"] for r in rows)
    print(
        f"Coverage (top-{K}): initial query "
        f"{total_hit_initial}/{total_expected} = {total_hit_initial/total_expected:.0%}"
    )
    print(
        f"Coverage (top-{K}): enriched query "
        f"{total_hit_enriched}/{total_expected} = {total_hit_enriched/total_expected:.0%}"
    )
    print()
    print("Interpretation:")
    print("- enriched coverage = retrieval ceiling (best case if simulator told us everything).")
    print("- initial coverage = realistic floor (turn-1 retrieval before clarification).")
    print("- gap between the two = how much clarification helps retrieval.")
    print("- items missing from BOTH = retrieval problem (BM25/dense/RRF need work).")


if __name__ == "__main__":
    audit()
