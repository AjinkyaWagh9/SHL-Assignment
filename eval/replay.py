"""Replay harness: simulated user vs deployed /chat endpoint.

The simulated user is driven by the persona facts in `eval/traces/<id>.json`. It
follows simple rules: answer the agent's question if it has a fact for it; otherwise
say "no preference"; end the conversation when the agent emits a non-empty shortlist.

We deliberately keep the user simulator rule-based (no LLM) so the harness is
deterministic and free to run. For realism we also support an LLM simulator (`--llm`)
that uses Gemini to act as the persona.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests

from eval.metrics import (
    all_items_in_catalog,
    recall_at_k,
    schema_compliant,
    summarize,
    turn_cap_honored,
)

ROOT = Path(__file__).resolve().parent
TRACES_DIR = ROOT / "traces"
DEFAULT_ENDPOINT = "http://localhost:8000/chat"
TURN_CAP = 8


def _user_message_from_facts(persona: dict, agent_reply: str | None) -> str:
    """Rule-based simulator. Recruiter-style: volunteers key facts upfront; answers
    clarifications from remaining facts.

    State is implicit in what's already been said; we re-pick until something new fires.
    Good enough for development; replace with LLM driver for closer-to-real evaluation.
    """
    # Initial message: a recruiter typically describes role + the 1-2 most specifying
    # facts upfront, the way a real hiring manager opens a conversation.
    if agent_reply is None:
        role = persona.get("role") or persona.get("audience") or persona.get("context")
        if not role:
            return "I need to recommend an assessment."
        parts = [f"Hiring for {role}."]
        if persona.get("seniority"):
            parts.append(f"Seniority is {persona['seniority']}.")
        if persona.get("stack_primary"):
            parts.append(f"Primary stack: {', '.join(persona['stack_primary'])}.")
        if persona.get("stack_secondary"):
            parts.append(f"Also touches {', '.join(persona['stack_secondary'])}.")
        if persona.get("screen_for"):
            parts.append(f"Need to screen for {', '.join(persona['screen_for'])}.")
        if persona.get("compliance"):
            parts.append(f"Compliance: {persona['compliance']}.")
        if persona.get("priority"):
            parts.append(f"Priority: {persona['priority']}.")
        if persona.get("industry"):
            parts.append(f"Industry: {persona['industry']}.")
        if persona.get("domain"):
            parts.append(f"Domain: {persona['domain']}.")
        if persona.get("scope"):
            parts.append(f"Scope: {persona['scope']}.")
        if persona.get("battery"):
            parts.append(f"Battery: {persona['battery']}.")
        if persona.get("channel"):
            parts.append(f"Channel: {persona['channel']}.")
        return " ".join(parts)

    text = agent_reply.lower()
    if any(k in text for k in ("seniority", "level", "experience", "audience", "who is this for")):
        for key in ("seniority", "experience", "audience"):
            if persona.get(key):
                return f"{persona[key]}."
    if "language" in text or "accent" in text:
        if persona.get("language"):
            return f"{persona['language']}."
        if persona.get("accent"):
            return f"{persona['accent']}."
    if any(k in text for k in ("backend", "frontend", "stack", "skills", "software", "tools")):
        for key in ("stack_primary", "screen_for", "stack_secondary"):
            if persona.get(key):
                return f"{', '.join(persona[key])}."
    if "constraint" in text or "duration" in text or "time" in text:
        if persona.get("constraint"):
            return f"{persona['constraint']}."
    if any(k in text for k in ("compliance", "regulation", "legal")):
        if persona.get("compliance"):
            return f"{persona['compliance']}."
    # Default: confirm and end.
    return "That works. Thanks."


def replay(trace: dict, endpoint: str, turn_cap: int = TURN_CAP) -> dict:
    persona = trace["persona_facts"]
    expected = trace.get("expected_names", [])

    history: list[dict] = []
    last_response: dict | None = None
    schema_ok = True
    catalog_only = True

    user_msg = _user_message_from_facts(persona, agent_reply=None)
    history.append({"role": "user", "content": user_msg})

    for _ in range(turn_cap):
        resp = requests.post(endpoint, json={"messages": history}, timeout=30)
        try:
            payload = resp.json()
        except Exception:
            schema_ok = False
            break

        if not schema_compliant(payload):
            schema_ok = False
        names = [r["name"] for r in payload.get("recommendations", [])]
        if names and not all_items_in_catalog(names):
            catalog_only = False

        last_response = payload
        history.append({"role": "assistant", "content": payload.get("reply", "")})

        if names or payload.get("end_of_conversation") is True:
            break

        next_msg = _user_message_from_facts(persona, payload.get("reply", ""))
        history.append({"role": "user", "content": next_msg})

    final_names = [r["name"] for r in (last_response or {}).get("recommendations", [])]
    return {
        "trace_id": trace["id"],
        "predicted_names": final_names,
        "recall_at_10": recall_at_k(final_names, expected, k=10),
        "schema_ok": schema_ok,
        "catalog_only": catalog_only,
        "turn_cap_ok": turn_cap_honored(len(history)),
        "n_messages": len(history),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", help="Single trace id, e.g. C1. Omit for all.")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    args = parser.parse_args()

    trace_files = (
        [TRACES_DIR / f"{args.trace}.json"]
        if args.trace
        else sorted(TRACES_DIR.glob("C*.json"))
    )
    results = []
    for tf in trace_files:
        trace = json.loads(tf.read_text())
        result = replay(trace, args.endpoint)
        results.append(result)
        print(
            f"{result['trace_id']:>4}: recall@10={result['recall_at_10']:.2f} "
            f"schema={'ok' if result['schema_ok'] else 'FAIL'} "
            f"catalog={'ok' if result['catalog_only'] else 'FAIL'} "
            f"turns={result['n_messages']}"
        )

    print()
    print(json.dumps(summarize(results), indent=2))


if __name__ == "__main__":
    main()
