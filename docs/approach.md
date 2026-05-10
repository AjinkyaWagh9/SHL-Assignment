# Approach — SHL Assessment Recommender

## Problem framing

A hiring manager describes a role in plain language; the agent must commit to a 1–10 item shortlist drawn **only** from SHL's 377-item catalog, byte-exact. The hard contract: stateless `/chat`, ≤8 turns, ≤30 s, fixed JSON schema. The scoring contract: schema/catalog/turn-cap binary gate, mean Recall@10 across traces, behavior-probe pass rate.

Two signal asymmetries shaped the design:

1. The catalog uses internal vocabulary ("Dependability and Safety Instrument", "OPQ32r", "Manufac. & Indust. – Safety & Dependability 8.0") that almost never appears in user phrasing.
2. SHL's own conventions inject standard items (OPQ32r personality, Verify G+ cognitive, plus role-typical defaults) that semantic search cannot find from a role-only query.

Both effects mean that pure BM25 + dense retrieval, however well-tuned, structurally undercounts the right items. The architecture therefore separates **retrieval** (find candidates), **default injection** (force-include role-bucket defaults), and **selection** (a dedicated reranker stage) so each can be solved with the right tool.

## Architecture

```
messages
  ├─ infer_state (regex)        → role / seniority / skills / intent / closure
  ├─ expand_query (LLM #1)      → BM25/dense-friendly keyword string
  ├─ hybrid_retriever.search    → BM25 + dense (RRF k=60) → metadata filter →
  │     type-boost → top-30 → DEFAULT_BY_ROLE injection (catalog-derived,
  │     validated at module load)
  ├─ rerank (LLM #2)            → ordered subset 2–8 items, index-based output,
  │     deterministic post-check force-restores any DEFAULT_INJECTED /
  │     PRIOR_SHORTLIST item the model dropped
  └─ compose (LLM #3)           → reply text + final JSON; existing
        validate_recommendations enforces catalog-only + URL byte-equality
```

Three LLM calls per recommend/refine turn (one for clarify/refuse). Total p50 ≈ 7 s, p99 ≈ 18 s.

The reranker is the load-bearing accuracy lever. It receives candidates as a numbered list (index-based output prevents the model from mangling names by stripping `(New)` suffixes — a real failure mode we observed). The post-validation does NOT trust the model on the must-include rule: any flagged candidate the model dropped is force-restored unless the user excluded its `test_type`.

`DEFAULT_BY_ROLE` is generated from `data/processed/catalog.json` at module load with a startup assertion that every curated name resolves byte-exact. This protects against catalog drift (a renamed item would crash boot, not silently lose recall at eval time). Hand-coded role categories drive the *triggers* and *patterns*; items are derived from catalog metadata to limit holdout overfitting.

## Eval results

Mean Recall@10 across the 10 public traces:

| | Baseline | After refactor |
|---|---|---|
| Mean R@10 | 0.525 | **0.86–0.91** (run-to-run) |
| Schema pass | 1.00 | 1.00 |
| Catalog-only | 1.00 | 1.00 |
| Turn cap | 1.00 | 1.00 |
| Behavior probes | 8/9 | **9/9** |

Per-trace: C1 0.67, C2 1.00, C3 1.00, C4 0.80, C5 1.00, C6 0.50–1.00, C7 0.80, C8 1.00, C9 0.86, C10 1.00. Worst trace (C4) loses two items the user did not name explicitly; trace C6's variance comes from gpt-4o-mini's residual non-determinism at temp=0 (a known OpenAI floor).

All probes pass: vague-turn-1 doesn't recommend, off-topic / legal / prompt-injection are refused without echoing forbidden vocabulary, schema is exact (no extra keys), URLs byte-equal the catalog, refine honors drops, closure sets `end_of_conversation: true`.

## What didn't work / measured

- **Trusting the LLM on the must-include rule.** The first cut put "DEFAULT_INJECTED items must appear" in the prompt only. gpt-4o-mini ignored it on ~30% of turns — typically dropping `OPQ32r` and `Verify G+` when retrieval rank was low. Fix: deterministic post-check restores them.
- **Name-based reranker output.** Initial design had the reranker emit `ranked_names`. The model stripped `(New)` suffixes and appended `[DEFAULT_INJECTED]` flag text into names, breaking byte-exact lookup; the parser then fell back to retrieval order — which put manufacturing items at the top for a Rust query. Fix: index-based output (`ranked_indices`).
- **Trigger detection on the expanded query.** `_inject_defaults` originally read the LLM-expanded query for bucket triggers. The expander legitimately adds adjacent vocabulary like "customer service" or "manufacturing" — which falsely fired the contact-center / industrial buckets on unrelated roles. Fix: trigger detection runs on `state.last_user_msg`, not the expanded query.
- **Hand-curated role lists.** The original `DEFAULT_BY_ROLE` was a literal list of names, with no validation that they resolved in the catalog. Rewritten to derive from catalog metadata at module load with a hard assertion.
- **Composer second-guessing the reranker.** With the reranker producing 5–8 items and the composer prompt saying "Pick 4–8 by default," the composer dropped reranker-vetted items. Fix: composer prompt now instructs "Copy every item in `<retrieved_items>` as-is."
- **Relaxed schema probe.** The original `schema_well_formed` probe only checked field types, not the exact key set, so a response with an extra `debug` field would silently pass. Tightened to assert exact keys + recommendation-key set + length 0–10. The original `all_urls_from_catalog` probe only checked `"shl.com" in url` — also tightened to byte-equality against `catalog[name].url`.

## Latency, cost, reliability

- Provider chain: OpenAI (paid) → Groq → Gemini, no in-provider retry (failover IS the retry).
- Per-stage timeouts sum under 30 s with overhead.
- Three LLM calls per recommend turn ≈ ~$0.0006 on gpt-4o-mini.
- Catalog + SentenceTransformer warmed in FastAPI lifespan so the first `/chat` after boot doesn't eat the encoder load.

## AI tools used

- Claude Code (Opus 4.7) for design, refactoring, prompt iteration, and eval-loop debugging.
- Production runtime uses gpt-4o-mini (primary) with structured `json_object` output. Groq Llama 3.3 70B and Gemini 2.5 Flash are configured as fallback providers; we did not need them during eval.
