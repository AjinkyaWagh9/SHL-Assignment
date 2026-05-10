"""LLM-based query expansion for retrieval.

The user message ("Hiring plant operators in chemicals") is short on the catalog's
own vocabulary ("safety", "dependability", "manufacturing"). One small, cheap LLM
call rewrites the user intent into a richer keyword string before BM25 + dense run.

This is the highest-leverage retrieval improvement: catalog items get surfaced via
their actual descriptive vocabulary, not the user's role-only phrasing.

Cost: ~500 input tokens + ~80 output = ~$0.0001 per call on gpt-4o-mini.
Latency: ~1-2 s, parallelizable in principle but currently sequential.

Skipped on clarify/refuse turns (no retrieval is used downstream anyway).
"""

from __future__ import annotations

import json
import logging

from agent.llm_client import get_llm
from agent.state import UserState

log = logging.getLogger("shl_recommender.query_expander")


_SYSTEM = (
    "You expand a hiring manager's request into a richer search query for an "
    "assessment catalog. Output ONLY the JSON object specified."
)


def _build_prompt(state: UserState, last_msg: str) -> str:
    return (
        "<task>\n"
        "Rewrite the user's hiring need into a single dense keyword string that "
        "would surface the right SHL catalog items via BM25 + semantic search. "
        "Include adjacent vocabulary the catalog itself uses: skill names, test "
        "categories (knowledge, personality, cognitive, situational, simulation), "
        "domain terms (HIPAA, manufacturing, customer service), and seniority cues.\n"
        "Stay under 50 words. No prose, no markdown, no explanation.\n"
        "</task>\n\n"
        f"<user_message>\n{last_msg}\n</user_message>\n\n"
        f"<extracted_state>\n"
        f"role={state.role}; seniority={state.seniority}; "
        f"skills={state.skills}; language={state.language}; "
        f"test_types_wanted={state.test_types_wanted}\n"
        f"</extracted_state>\n\n"
        "<output_format>\n"
        '{"query": "<keyword string>"}\n'
        "</output_format>"
    )


def expand_query(state: UserState, last_msg: str) -> str:
    """Return the expanded query (original + LLM keywords) or original on failure.

    Only fires for recommend / refine intents — clarify / refuse don't use retrieval.
    """
    if state.intent not in ("recommend", "refine"):
        return last_msg

    try:
        llm = get_llm()
        raw = llm.complete(
            system=_SYSTEM,
            user=_build_prompt(state, last_msg),
            max_tokens=120,
        )
        parsed = json.loads(raw)
        expanded = (parsed.get("query") or "").strip()
        if not expanded:
            return last_msg
        # Combine: original message dominates BM25 exact-match; expansion adds breadth.
        return f"{last_msg} | {expanded}"
    except Exception as e:
        log.warning("query expansion failed (%s); falling back to raw query", e)
        return last_msg
