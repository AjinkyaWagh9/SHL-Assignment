# prompts/ — Agent Prompt Library

## Purpose
All natural-language instructions to the LLM live here. Code loads them by name; never
inline a prompt in `.py`.

## Structure (Jake 5-part + Claude leak patterns)
Every prompt has these sections, in order:
1. `<identity>` — who the agent is right now.
2. `<task>` — the action to take this turn.
3. `<context>` — state snapshot + retrieved items (filled by code, not authored here).
4. `<constraints>` — what to avoid; grounding rules.
5. `<output_format>` — strict JSON schema.

We also use:
- `<thinking>` block for one-shot CoT before final output (kept terse — token budget).
- `<self_check>` rule listing failure modes the model must verify before emitting.

## Files
- `system_base.md` — global identity + hard rules. Prepended to every call.
- `router.md` — decision table (clarify | recommend | refine | compare | refuse).
- `clarify.md` — ask exactly **one** targeted question; never recommend in the same turn.
- `recommend.md` — produce 1–10 items from supplied retrieval candidates.
- `refine.md` — targeted edit of the previous shortlist; preserve unchanged items.
- `compare.md` — ground the comparison in `description` + `keys` only; keep prior shortlist.
- `refuse.md` — legal / general hiring / off-topic. Brief, redirect, no leak.
- `safety_guardrails.md` — prompt-injection patterns and refusal phrasing.

## Tone (calibrated to C1–C10 traces)
- Terse. 1–3 sentences in `reply` is typical.
- Cite catalog facts when justifying ("Advanced covers concurrency, JVM internals…").
- Push back when the user's request would degrade quality (C8 turn 2: agent declined
  to swap OPQ32r for a "shorter" personality test because none exists).
- Never use marketing language. Never invent percentages or claims.

## Authoring rules
- Quote a catalog field when you need to make a claim about an item. If a field isn't
  there, say "I don't have that data".
- All URLs in `recommendations` come from retrieved candidates — code enforces this in
  the response validator. Don't bother instructing the LLM to "make sure URLs are real";
  it cannot improve over the validator.
