# agent/ — Orchestrator, State, Validator

## Purpose
Glue between FastAPI, retriever, and LLM. Implements the per-turn pipeline:
```
messages -> state inference -> retrieval -> LLM call -> validate -> JSON response
```

## Files
- `state.py` — `UserState` dataclass + `infer_state(messages)` rule-based extractor.
  Re-derives state from the **full** history every turn (true statelessness).
- `router.py` — single per-turn entrypoint: `respond(messages) -> ChatResponse`.
  Picks the prompt template, runs retrieval, makes one LLM call, returns validated JSON.
- `context_engineer.py` — compresses history into a compact context block for the LLM
  (caps included items + truncates long descriptions).
- `response_formatter.py` — strict catalog validation. Drops items whose `name` or
  `url` does not match a catalog entry byte-for-byte. Fails closed.

## UserState schema
```python
@dataclass
class UserState:
    role: str | None              # "Java developer", "graduate financial analyst"
    seniority: str | None         # one of: junior | mid | senior | executive
    skills: list[str]             # ["Java", "Spring", "stakeholder management"]
    test_types_wanted: list[str]  # ["K", "P"] — additive
    test_types_excluded: list[str]
    duration_max_min: int | None
    language: str | None          # "English (USA)" canonical
    current_shortlist_ids: list[str]  # entity_ids from previous assistant turn
    intent: Literal["clarify","recommend","refine","compare","refuse"]
    clarify_count: int            # number of clarify turns so far
    compare_targets: list[str]    # entity_ids referenced in current user msg
```

## When to route to which prompt
- `intent=refuse` → refuse.md (off-topic / legal / injection)
- `intent=compare` → compare.md (user references ≥2 named items)
- `current_shortlist_ids` non-empty AND user wants edit → refine.md
- Have role + (seniority OR key skill OR test_type) → recommend.md
- Otherwise → clarify.md (cap at 2 clarify turns; auto-recommend after)

## Latency budget per turn
- state inference (regex): <5 ms
- retrieval (BM25 + dense + RRF): <100 ms
- one LLM call: 1–3 s (Gemini Flash) or 0.5–1 s (Groq Llama)
- validation + serialize: <10 ms

Total target: under 5 s p50, under 15 s p99.

## Validator behavior (response_formatter.py)
1. Parse LLM JSON output. Strip markdown code fences if present.
2. For each item in `recommendations`: look up by `name` in catalog. If miss → drop.
3. If `name` matches but `url` differs → overwrite with catalog `url` (defensive).
4. If `recommendations` ends up empty after a `recommend`/`refine` intent → fall back to
   the top-5 retrieval candidates so the user always gets something.
5. Cap `recommendations` at 10 items.
