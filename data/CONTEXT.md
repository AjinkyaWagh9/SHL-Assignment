# data/ — Catalog & Knowledge Base

## Purpose
Single source of truth for what the agent may recommend. Every item the agent emits
must originate here.

## Files
- `raw/shl_product_catalog.json` — original 377-item catalog (provided by SHL).
- `build_catalog.py` — one-shot enrichment. Maps `keys` to single-letter `test_type`,
  buckets `job_levels` into seniority, builds `text_for_embedding`.
- `processed/catalog.json` — output of the enrichment. Loaded at API startup.
- `vector_index/` — FAISS index + BM25 dump, built by `retriever/embeddings.py`.

## Catalog facts (relevant to retrieval/prompting)
- 377 items; all `remote: yes` (do not filter on this).
- `keys` distribution: K(240), P(67), S(43), A(32), C(19), B(17), D(7), E(2).
- 16% of items have no `duration` — treat as soft filter only.
- Some items have multiple `keys`; `test_type` is comma-joined in priority order
  `K,P,A,C,B,S,D,E` (e.g. `K,P` or `C,K`).
- `Global Skills Development Report` has 6 keys — emit them all, do not pick one.

## Test-type letter mapping
```
A = Ability & Aptitude            K = Knowledge & Skills
B = Biodata & Situational Judgmnt P = Personality & Behavior
C = Competencies                  S = Simulations
D = Development & 360             E = Assessment Exercises
```

## Seniority buckets (derived from `job_levels`)
- `junior`     = Entry-Level, Graduate, General Population
- `mid`        = Mid-Professional, Professional Individual Contributor, Supervisor
- `senior`     = Manager, Front Line Manager
- `executive`  = Director, Executive

## Rules
- Never modify item names or URLs. Casing and punctuation matter for the evaluator.
- If you change the enrichment schema, regenerate the index and re-run all evals.
- The raw file has a stray control character (~line 4795); load with `strict=False`.
