# retriever/ — Hybrid Metadata + Semantic Retrieval

## Purpose
Given the inferred `UserState` and the latest user turn, return up to N catalog items
that are eligible for the agent to consider. The LLM never sees items this layer didn't
return.

## Pipeline (in `hybrid_retriever.py`)
1. **Metadata pre-filter** (`metadata_filters.py`) — drop items whose seniority,
   language, or excluded test_type contradicts state. Pre-filter is permissive: if a
   field is unknown in state, do not filter on it.
2. **BM25** over `name + description` for exact-name hits ("OPQ32r", "Java 8").
3. **Dense** cosine similarity over `text_for_embedding` (sentence-transformers
   `all-MiniLM-L6-v2`, pre-encoded at index build time).
4. **Fusion** via Reciprocal Rank Fusion (RRF, k=60). RRF is parameter-free and beats
   weighted-sum on heterogeneous score scales.
5. **Test-type boost**: if state lists `test_types_wanted`, items matching get +0.1
   added after fusion (small, doesn't override semantic relevance).

## Output
A list of dicts shaped like the catalog item, plus a `score` field. Default top-N = 25
(LLM picks 1–10 from this).

## Why hybrid over pure dense
- "Java 8" should hit "Java 8 (New)" exactly — dense alone is noisy on short names.
- Catalog has 240 K-tests, many near-duplicates ("Core Java Advanced" vs "Java 8") —
  metadata + BM25 disambiguates by intent.

## Files
- `hybrid_retriever.py` — `Retriever.search(state, query, k=25)`.
- `metadata_filters.py` — `apply_filters(items, state)`.
- `embeddings.py` — `build_index()` and `load_index()`. Run `build_index()` once after
  enrichment; index files land in `data/vector_index/`.
- `utils.py` — RRF fusion, score normalization.

## Performance
Index loads in <2 s on cold start; query <50 ms. Pre-encode at build time to keep the
sentence-transformer model out of the request path if memory-constrained.
