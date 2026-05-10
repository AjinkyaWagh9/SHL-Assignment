"""LLM client wrappers with provider fallback.

Provider order is governed by `SHL_LLM_PROVIDER`:
- `fallback` (default): OpenAI primary (paid, reliable) -> Groq -> Gemini.
  Failover protects against one provider's downtime / rate-limit during evaluation.
  Each provider has a tight per-call timeout so the chain stays well inside the 30 s
  `/chat` budget.
- `openai`, `groq`, `gemini`, `stub` (offline tests): single provider, no fallback.

We deliberately avoid in-provider retries: a slow provider would burn the whole
budget on retries before failover. Failing over IS the retry.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Protocol

log = logging.getLogger("shl_recommender.llm")

# Per-provider hard timeouts. Sum stays under ~30 s so retrieval + formatting + network
# overhead fit inside the 30 s evaluator cap.
OPENAI_TIMEOUT_S = 20.0
GROQ_TIMEOUT_S = 10.0
GEMINI_TIMEOUT_S = 12.0


class LLM(Protocol):
    def complete(self, system: str, user: str, max_tokens: int = 800) -> str: ...


class StubLLM:
    """Deterministic stub for offline tests. Returns a minimal valid clarify."""

    def complete(self, system: str, user: str, max_tokens: int = 800) -> str:
        return json.dumps(
            {
                "intent": "clarify",
                "reply": "Could you tell me more about the role you're hiring for?",
                "recommendations": [],
                "end_of_conversation": False,
            }
        )


class OpenAILLM:
    """Paid OpenAI client. Default model is configurable via `OPENAI_MODEL`."""

    def __init__(self, model: str | None = None) -> None:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError("pip install openai") from e
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        # max_retries=0: our FallbackLLM is the retry. Letting the SDK retry too
        # would burn the 30s /chat budget on a single slow provider.
        self._client = OpenAI(api_key=api_key, timeout=OPENAI_TIMEOUT_S, max_retries=0)
        self._model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    def complete(self, system: str, user: str, max_tokens: int = 800) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content or ""


class GeminiLLM:
    def __init__(self, model: str = "gemini-2.5-flash") -> None:
        try:
            import google.generativeai as genai
        except ImportError as e:
            raise RuntimeError("pip install google-generativeai") from e
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model)

    def complete(self, system: str, user: str, max_tokens: int = 800) -> str:
        prompt = f"{system}\n\n{user}"
        resp = self._model.generate_content(
            prompt,
            generation_config={
                "temperature": 0,
                "max_output_tokens": max_tokens,
                "response_mime_type": "application/json",
            },
            request_options={"timeout": GEMINI_TIMEOUT_S},
        )
        return resp.text or ""


class GroqLLM:
    def __init__(self, model: str = "llama-3.3-70b-versatile") -> None:
        try:
            from groq import Groq
        except ImportError as e:
            raise RuntimeError("pip install groq") from e
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY not set")
        self._client = Groq(api_key=api_key, timeout=GROQ_TIMEOUT_S)
        self._model = model

    def complete(self, system: str, user: str, max_tokens: int = 800) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content or ""


class FallbackLLM:
    """Tries providers in order. First non-empty response wins."""

    def __init__(self, providers: list[LLM]) -> None:
        if not providers:
            raise ValueError("FallbackLLM needs at least one provider")
        self._providers = providers

    def complete(self, system: str, user: str, max_tokens: int = 800) -> str:
        last_err: Exception | None = None
        for provider in self._providers:
            name = type(provider).__name__
            try:
                out = provider.complete(system, user, max_tokens=max_tokens)
                if out and out.strip():
                    return out
                log.warning("%s returned empty response; falling through", name)
            except Exception as e:
                log.warning("%s failed (%s); falling through", name, e)
                last_err = e
        raise RuntimeError(f"All LLM providers failed; last error: {last_err}")


def _build_fallback() -> LLM:
    """Build whatever providers are usable given the env. Skip those without keys.

    Order: OpenAI (paid, reliable) -> Groq (fast, free TPD) -> Gemini (free RPM).
    """
    chain: list[LLM] = []
    if os.environ.get("OPENAI_API_KEY"):
        try:
            chain.append(OpenAILLM())
        except Exception as e:
            log.warning("OpenAI init skipped: %s", e)
    if os.environ.get("GROQ_API_KEY"):
        try:
            chain.append(GroqLLM())
        except Exception as e:
            log.warning("Groq init skipped: %s", e)
    if os.environ.get("GEMINI_API_KEY"):
        try:
            chain.append(GeminiLLM())
        except Exception as e:
            log.warning("Gemini init skipped: %s", e)
    if not chain:
        raise RuntimeError(
            "SHL_LLM_PROVIDER=fallback but no provider keys are set "
            "(OPENAI_API_KEY / GROQ_API_KEY / GEMINI_API_KEY)"
        )
    return FallbackLLM(chain)


def get_llm() -> LLM:
    provider = os.environ.get("SHL_LLM_PROVIDER", "fallback").lower()
    if provider == "stub":
        return StubLLM()
    if provider == "openai":
        return OpenAILLM()
    if provider == "groq":
        return GroqLLM()
    if provider == "gemini":
        return GeminiLLM()
    if provider == "fallback":
        return _build_fallback()
    raise ValueError(f"Unknown SHL_LLM_PROVIDER: {provider}")
