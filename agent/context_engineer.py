"""History compression + retrieved-item formatting for the LLM context.

Goal: keep the LLM payload small enough to fit a 1–3 s call. Two levers:
  1. Truncate per-item description to 240 chars (most catalog descriptions are short
     anyway; long ones are reports with templated boilerplate).
  2. Cap retrieved items to 25 — the LLM picks 1–10 from there.
"""

from __future__ import annotations

import json

from agent.state import UserState
from api.schemas import Message

DESC_CAP = 140  # short enough to fit a wider candidate pool; long enough to disambiguate
HISTORY_USER_TURN_CAP = 6  # only carry the last N user turns into context


def format_state(state: UserState) -> str:
    return json.dumps(
        {
            "role": state.role,
            "seniority": state.seniority,
            "skills": state.skills,
            "test_types_wanted": state.test_types_wanted,
            "test_types_excluded": state.test_types_excluded,
            "language": state.language,
            "current_shortlist_names": state.current_shortlist_names,
            "intent": state.intent,
            "clarify_count": state.clarify_count,
            "compare_targets": state.compare_targets,
            "closure_signal": state.closure_signal,
        },
        indent=2,
    )


def format_retrieved(items: list[dict]) -> str:
    """Compact JSON list the LLM can copy `name`, `url`, `test_type` from."""
    compact = []
    for it in items:
        desc = it.get("description", "") or ""
        if len(desc) > DESC_CAP:
            desc = desc[:DESC_CAP].rsplit(" ", 1)[0] + "…"
        compact.append(
            {
                "name": it["name"],
                "url": it["url"],
                "test_type": it["test_type"],
                "keys": it.get("keys", []),
                "job_levels": it.get("job_levels", []),
                "duration": it.get("duration", ""),
                "languages": (it.get("languages") or [])[:4],
                "description": desc,
            }
        )
    return json.dumps(compact, indent=2)


def format_history(messages: list[Message]) -> str:
    """Last N user turns + the most recent assistant turn for refinement context."""
    user_turns = [m for m in messages if m.role == "user"][-HISTORY_USER_TURN_CAP:]
    last_assistant = next(
        (m for m in reversed(messages) if m.role == "assistant"), None
    )
    lines = []
    for m in user_turns:
        lines.append(f"USER: {m.content}")
    if last_assistant is not None and any(m.role == "assistant" for m in messages):
        lines.append(f"\nLAST_ASSISTANT (for refinement only): {last_assistant.content[:400]}")
    return "\n".join(lines)
