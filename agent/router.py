"""Per-turn orchestrator. One LLM call per turn.

Pipeline:
  messages -> infer_state -> retrieval -> compose prompt -> LLM -> validate -> response
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from agent.context_engineer import format_history, format_retrieved, format_state
from agent.llm_client import get_llm
from agent.query_expander import expand_query
from agent.reranker import rerank
from agent.response_formatter import build_response, parse_llm_json
from agent.state import UserState, infer_state
from api.dependencies import get_catalog_by_name
from api.schemas import ChatResponse, Message
from retriever.hybrid_retriever import get_retriever

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@lru_cache(maxsize=16)
def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text()


def _select_prompt(intent: str) -> str:
    return _load_prompt(intent)  # clarify / recommend / refine / compare / refuse


def _build_user_block(
    state: UserState, retrieved: list[dict], messages: list[Message]
) -> str:
    """Assemble the per-turn user-side prompt block.

    Token-saver: clarify and refuse turns never use the retrieval candidates
    (recommendations are always []), so we skip the ~3k-token <retrieved_items>
    block on those intents.
    """
    catalog_by_name = get_catalog_by_name()
    shortlist_lookup = {
        n: catalog_by_name[n]
        for n in state.current_shortlist_names
        if n in catalog_by_name
    }
    parts = [
        f"<state>\n{format_state(state)}\n</state>",
        f"<history>\n{format_history(messages)}\n</history>",
    ]
    if state.intent not in ("clarify", "refuse"):
        parts.append(f"<retrieved_items>\n{format_retrieved(retrieved)}\n</retrieved_items>")
    parts.append(
        f"<catalog_lookup>\n{format_retrieved(list(shortlist_lookup.values()))}\n</catalog_lookup>"
    )
    return "\n\n".join(parts) + "\n"


def respond(messages: list[Message]) -> ChatResponse:
    state = infer_state(messages)
    retriever = get_retriever()

    # Query expansion (one cheap LLM call) only fires for recommend/refine. Returns
    # the original message verbatim for clarify/refuse, so retrieval stays consistent.
    search_query = expand_query(state, state.last_user_msg)
    retrieved = retriever.search(state, search_query, k=30)

    # On refine, ensure prior-shortlist items are in the candidate pool so the reranker
    # can preserve them. Retrieval may not return them (different query), but the user
    # expects them to survive unless explicitly dropped.
    if state.intent == "refine" and state.current_shortlist_names:
        catalog_by_name = get_catalog_by_name()
        existing_names = {it["name"] for it in retrieved}
        for name in state.current_shortlist_names:
            if name in existing_names:
                continue
            item = catalog_by_name.get(name)
            if item:
                retrieved.append({**item, "score": 0.0, "from_prior_shortlist": True})

    # Reranker (LLM stage 2): selects an ordered subset for recommend/refine intents.
    # Falls back to original retrieval order on any failure, so this can only help.
    retrieved = rerank(state, retrieved, state.intent)

    system = _load_prompt("system_base") + "\n\n" + _load_prompt("safety_guardrails")
    behavior = _select_prompt(state.intent)
    user_block = _build_user_block(state, retrieved, messages)
    full_user = behavior + "\n\n" + user_block

    llm = get_llm()
    # 900 covers a worst-case 10-item shortlist with full justification without truncating
    # mid-JSON (which silently drops items and tanks recall on traces with long
    # expected-name lists like C5/C7). gpt-4o is fast enough that the extra cap costs
    # negligible latency in practice.
    raw = llm.complete(system=system, user=full_user, max_tokens=900)

    try:
        parsed = parse_llm_json(raw)
    except Exception:
        # Last-ditch: degrade to a clarify so the conversation continues.
        parsed = {
            "intent": "clarify",
            "reply": "Could you share a bit more about the role and the level you're hiring for?",
            "recommendations": [],
            "end_of_conversation": False,
        }

    # Honor the rule-inferred intent over the LLM's self-report. The LLM occasionally
    # tries to recommend during a clarify turn; the rules know better.
    parsed["intent"] = state.intent

    # Closure signal overrides: if user clearly closed AND we have a shortlist
    # (recommend or refine intent), set end_of_conversation true.
    if state.closure_signal and state.intent in ("recommend", "refine"):
        parsed["end_of_conversation"] = True

    return build_response(parsed, fallback_items=retrieved)
