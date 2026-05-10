# api/ — FastAPI Service

## Purpose
Public surface. Stateless. Two endpoints:
- `GET /health` → `{"status": "ok"}`, HTTP 200.
- `POST /chat` → `{"reply", "recommendations", "end_of_conversation"}`.

## Files
- `main.py` — FastAPI app, lifespan-loaded retriever and catalog.
- `schemas.py` — Pydantic v2 models. Request/response shapes are non-negotiable.
- `dependencies.py` — singleton getters for catalog, retriever, LLM client.

## Schema (non-negotiable)
```json
POST /chat
{
  "messages": [
    {"role": "user" | "assistant", "content": "string"}
  ]
}
->
{
  "reply": "string",
  "recommendations": [
    {"name": "string", "url": "string", "test_type": "string"}
  ],
  "end_of_conversation": true | false
}
```
- `recommendations` is `[]` (empty list) during clarify/refuse, **not** `null`.
- `recommendations` has 1–10 items when committing to a shortlist.
- Order matters in `recommendations` — emit most relevant first.

## Cold-start
The evaluator allows up to 2 minutes for the first `/health` to wake the service. We
load the FAISS index and sentence-transformer encoder at FastAPI lifespan startup so
the first `/chat` is warm.

## Error handling
- Schema-invalid request → 422 (FastAPI default).
- Internal exception → catch, log, return a graceful clarify response so the
  evaluator's turn doesn't crash. **Never** return 500 on `/chat`.
