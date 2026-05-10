"""Reranker: LLM stage that selects an ordered shortlist from retrieved candidates.

This stage is purpose-built for selection quality. It gets ~30 candidates with metadata
and returns 2-8 ordered names. Decoupling selection from response prose lets the model
focus on the harder part — picking the right items, including SHL-curated defaults that
naive vector search would miss.

Hard rules enforced via prompt + post-validation:
  - Output names must be drawn from the candidate list (byte-exact).
  - Items flagged `default_injected: True` whose role bucket aligns with the user's
    stated role MUST appear in output, unless the user excluded their test_type.
  - For `refine` intent, items in `state.current_shortlist_names` survive unless the
    user explicitly asked to drop them.

Falls back to original retrieval order on parse failure or LLM error so the orchestrator
never crashes on a bad rerank turn.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from agent.llm_client import get_llm
from agent.response_formatter import parse_llm_json
from agent.state import UserState

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

DESC_CAP_RERANK = 110  # tighter than composer's 140 — selection doesn't need fluff
INDUSTRIAL_TRIGGERS = (
    "safety", "industrial", "manufact", "factory", "plant", "operator", "warehouse",
)


@lru_cache(maxsize=1)
def _load_rerank_prompt() -> str:
    return (PROMPTS_DIR / "rerank.md").read_text()


def _format_state_for_rerank(state: UserState) -> str:
    return json.dumps(
        {
            "intent": state.intent,
            "role": state.role,
            "seniority": state.seniority,
            "skills": state.skills,
            "test_types_wanted": state.test_types_wanted,
            "test_types_excluded": state.test_types_excluded,
            "language": state.language,
            "prior_shortlist": state.current_shortlist_names,
        },
        indent=2,
    )


def _format_candidates(candidates: list[dict]) -> str:
    """Format candidates so the name is on its own line (no inline flags).

    Flags live on a separate `flags:` line. The LLM has been observed appending the
    flag back into `ranked_names`; keeping flags off the name line avoids that and
    lets `_clean_name` recover from any residual flag-suffix anyway.
    """
    lines = []
    for i, c in enumerate(candidates, 1):
        flags = []
        if c.get("default_injected"):
            flags.append("DEFAULT_INJECTED")
        if c.get("from_prior_shortlist"):
            flags.append("PRIOR_SHORTLIST")
        flag_line = f"   flags: {', '.join(flags)}\n" if flags else ""
        desc = (c.get("description") or "")
        if len(desc) > DESC_CAP_RERANK:
            desc = desc[:DESC_CAP_RERANK].rsplit(" ", 1)[0] + "…"
        keys = ", ".join((c.get("keys") or [])[:5])
        lines.append(
            f"{i}. {c['name']}\n"
            f"{flag_line}"
            f"   test_type: {c.get('test_type', '')}\n"
            f"   keys: {keys}\n"
            f"   desc: {desc}"
        )
    return "\n".join(lines)


_FLAG_SUFFIX_RE = None  # lazy


def _clean_name(raw: str) -> str:
    """Strip residual `[FLAG]` suffixes the LLM sometimes copies into ranked_names."""
    import re
    global _FLAG_SUFFIX_RE
    if _FLAG_SUFFIX_RE is None:
        _FLAG_SUFFIX_RE = re.compile(r"\s*\[(?:DEFAULT_INJECTED|PRIOR_SHORTLIST)(?:,\s*[A-Z_]+)*\]\s*$")
    return _FLAG_SUFFIX_RE.sub("", raw).strip()


def _is_industrial(state: UserState) -> bool:
    haystack = " ".join(filter(None, [
        (state.role or "").lower(),
        " ".join(state.skills).lower(),
        state.last_user_msg.lower(),
    ]))
    return any(t in haystack for t in INDUSTRIAL_TRIGGERS)


def _max_output_size(state: UserState) -> int:
    # Industrial / safety roles are well-served by a tight 4-5 item shortlist (DSI,
    # Workplace Health and Safety, Manufac. & Indust. Safety + role defaults).
    return 5 if _is_industrial(state) else 8


def rerank(state: UserState, candidates: list[dict], intent: str) -> list[dict]:
    """Return an ordered subset of candidates (2-8 typical, 4-5 for industrial roles).

    For clarify/refuse intents, returns candidates unchanged (orchestrator drops them
    on those intents anyway). Falls back to original order on any error.
    """
    if intent not in ("recommend", "refine") or not candidates:
        return candidates

    user_block = (
        f"<state>\n{_format_state_for_rerank(state)}\n</state>\n\n"
        f"<max_output_size>{_max_output_size(state)}</max_output_size>\n\n"
        f"<candidates>\n{_format_candidates(candidates)}\n</candidates>"
    )

    try:
        llm = get_llm()
        raw = llm.complete(
            system=_load_rerank_prompt(),
            user=user_block,
            max_tokens=300,
        )
        parsed = parse_llm_json(raw)
    except Exception:
        log.exception("rerank: LLM/parse failure; falling back to retrieval order")
        return candidates

    # Index-based output is robust against name-mangling (the LLM has been observed
    # stripping `(New)` suffixes and similar). Each entry is the 1-based index in the
    # candidate list. Tolerate string-form names in `ranked_names` as a fallback.
    out: list[dict] = []
    seen: set[str] = set()

    indices = parsed.get("ranked_indices") or []
    for idx in indices:
        try:
            i = int(idx)
        except (TypeError, ValueError):
            continue
        if not (1 <= i <= len(candidates)):
            continue
        item = candidates[i - 1]
        if item["name"] in seen:
            continue
        seen.add(item["name"])
        out.append(item)

    # Backwards compatibility: if the LLM ignored the index spec and emitted names,
    # try to recover via byte-exact and flag-stripped match.
    if not out:
        by_name = {c["name"]: c for c in candidates}
        for name in parsed.get("ranked_names") or []:
            if not isinstance(name, str):
                continue
            cleaned = _clean_name(name)
            item = by_name.get(cleaned)
            if item is None or item["name"] in seen:
                continue
            seen.add(item["name"])
            out.append(item)

    # Safety net: if the reranker dropped everything, return the top of original
    # retrieval so the composer still has material to work with.
    if not out:
        log.warning("rerank: empty output; falling back to top retrieval candidates")
        return candidates[: _max_output_size(state)]

    # Deterministic post-check: any DEFAULT_INJECTED candidate the LLM dropped is
    # force-restored. The reranker prompt asks for inclusion but smaller models
    # occasionally omit defaults; we don't trust the model with this rule.
    excluded_letters = set(state.test_types_excluded)
    out_names = {it["name"] for it in out}
    forced: list[dict] = []
    for c in candidates:
        if not c.get("default_injected"):
            continue
        if c["name"] in out_names:
            continue
        item_letters = set((c.get("test_type") or "").split(","))
        if item_letters & excluded_letters:
            continue  # user asked to skip this test_type
        forced.append(c)
    if forced:
        log.info("rerank: force-restored %d default-injected items", len(forced))
        out = out + forced

    # Force-include prior_shortlist items that the LLM dropped, on refine intent.
    if intent == "refine":
        prior = set(state.current_shortlist_names)
        if prior:
            # Heuristic: detect whether user asked to drop a prior item this turn.
            last = (state.last_user_msg or "").lower()
            drop_words = ("drop", "remove", "skip", "without", "no ", "exclude", "not ")
            user_wants_drop = any(w in last for w in drop_words)
            for c in candidates:
                if c["name"] not in prior or c["name"] in out_names:
                    continue
                if user_wants_drop and c["name"].lower() in last:
                    continue  # user explicitly named this item alongside a drop word
                out.append(c)

    # Cap at max_output_size to honor the prompt's hard limit.
    return out[: _max_output_size(state)]
