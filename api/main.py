"""FastAPI service: GET /health, POST /chat.

Stateless. Each /chat call carries the full message history.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv

# Load .env before any module reads os.environ. Production hosts (Render/Fly/Railway)
# inject env vars directly; load_dotenv is a no-op there if .env is absent.
load_dotenv()

from fastapi import FastAPI

from api.dependencies import get_catalog
from api.schemas import ChatRequest, ChatResponse, HealthResponse
from agent.router import respond

log = logging.getLogger("shl_recommender")
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm all singletons so the first request is hot. Without this, the first /chat
    # eats the SentenceTransformer load (~10s on cold cache) and risks the 30s cap.
    catalog = get_catalog()
    log.info("Loaded catalog: %d items", len(catalog))
    from retriever.hybrid_retriever import get_retriever
    retriever = get_retriever()
    # Touch the encoder once — the first encode is a few seconds even on warm model.
    retriever.index.encode_query("warmup")
    log.info("Retriever ready: index loaded and encoder warm")
    yield


app = FastAPI(title="SHL Assessment Recommender", lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    try:
        return respond(request.messages)
    except Exception:
        # The evaluator's turn must not crash. Fall back to a graceful clarify.
        log.exception("/chat failed; returning fallback")
        return ChatResponse(
            reply="Could you share a bit more about the role you're hiring for?",
            recommendations=[],
            end_of_conversation=False,
        )
