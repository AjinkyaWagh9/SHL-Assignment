---
title: SHL Assessment Recommender
emoji: 🧪
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: shl recommender system
---

# SHL Assessment Recommender

Conversational agent over the SHL Individual Test Solutions catalog. Built for the SHL
AI Intern take-home. FastAPI + hybrid retrieval + one-LLM-call orchestrator.

## Quickstart

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. Build catalog + index (one-shot)
python data/build_catalog.py
python retriever/embeddings.py

# 3. Configure LLM provider (.env loaded automatically at startup)
cp .env.example .env
# then edit .env and set GROQ_API_KEY and/or GEMINI_API_KEY
# Default SHL_LLM_PROVIDER=fallback uses Groq -> Gemini failover.
# Use SHL_LLM_PROVIDER=stub for offline wiring tests (no API key needed).

# 4. Run the API
uvicorn api.main:app --host 0.0.0.0 --port 8000

# 5. Sanity check
curl http://localhost:8000/health
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Hiring a mid-level Java developer with Spring."}]}'
```

## Layout (Jake Van Clief style)

```
shl-assessment-recommender/
├── CLAUDE.md              # Global identity, hard rules, routing map
├── data/                  # Catalog + index
│   ├── CONTEXT.md
│   ├── build_catalog.py   # raw -> processed/catalog.json
│   ├── raw/shl_product_catalog.json
│   ├── processed/catalog.json
│   └── vector_index/      # built by retriever/embeddings.py
├── retriever/             # Hybrid metadata + BM25 + dense + RRF
│   ├── CONTEXT.md
│   ├── hybrid_retriever.py
│   ├── metadata_filters.py
│   ├── embeddings.py
│   └── utils.py
├── prompts/               # All LLM instructions
│   ├── CONTEXT.md
│   ├── system_base.md
│   ├── safety_guardrails.md
│   ├── router.md
│   ├── clarify.md
│   ├── recommend.md
│   ├── refine.md
│   ├── compare.md
│   └── refuse.md
├── agent/                 # State, orchestrator, validator
│   ├── CONTEXT.md
│   ├── state.py           # rule-based UserState extraction
│   ├── router.py          # one LLM call per turn
│   ├── context_engineer.py
│   ├── response_formatter.py
│   └── llm_client.py      # Gemini / Groq / Stub
├── api/                   # FastAPI service
│   ├── CONTEXT.md
│   ├── main.py
│   ├── schemas.py
│   └── dependencies.py
├── eval/                  # Replay harness + probes
│   ├── CONTEXT.md
│   ├── traces/C1.json … C10.json
│   ├── replay.py
│   ├── metrics.py
│   └── behavior_probes.py
├── docs/approach.md       # 2-page submission document (TODO)
├── tests/
├── requirements.txt
└── README.md
```

## Evaluation

```bash
# Run all 10 public traces against the live endpoint
python eval/replay.py --endpoint http://localhost:8000/chat

# Behavior probes (refuse, prompt-injection, refine, schema, …)
python eval/behavior_probes.py --endpoint http://localhost:8000/chat
```

## Design notes

- **Stateless API.** Every `/chat` call carries the full history. State is re-derived
  each turn from `agent/state.py` (regex + keyword extraction; no LLM call).
- **Hybrid retrieval.** Metadata pre-filter (seniority, language, excluded test types)
  → BM25 + dense (`all-MiniLM-L6-v2`) → RRF fusion → optional test-type boost. Top 25
  candidates feed the LLM, which picks 1–10.
- **One LLM call per turn.** Latency budget is 30 s and we want headroom. State
  inference and retrieval are pure Python; only response generation calls the model.
- **Provider fallback.** `agent/llm_client.py` defaults to Groq (Llama 3.3 70B) →
  Gemini 2.5 Flash failover with tight per-provider timeouts (10 s / 12 s). Failing
  over IS the retry — no in-provider retry loop that would burn the latency budget.
- **Hard grounding.** `agent/response_formatter.py` byte-matches every recommended
  `name` against the catalog and overwrites `url` from catalog; mismatches are dropped.
- **Behavior calibrated to traces.** Prompts in `prompts/` cite exemplar phrasings from
  C1–C10 so the agent's tone matches what the evaluator expects.

## Where to look first
- `CLAUDE.md` — high-level routing.
- `prompts/system_base.md` + `prompts/safety_guardrails.md` — global rules.
- `agent/router.py` — request flow, top-down.
