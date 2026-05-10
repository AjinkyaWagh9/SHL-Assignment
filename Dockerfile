# Single-stage build. sentence-transformers pulls torch (~700MB), so multi-stage
# offers little benefit — copying site-packages between stages costs almost as
# much as it saves. Slim base + non-root user is enough.
FROM python:3.11-slim

# System deps: faiss-cpu and torch wheels are self-contained, but sentence-
# transformers occasionally shells out and curl is useful for health checks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Cache Hugging Face weights inside /app so platforms with persistent disks
# (Fly volumes, Railway volumes) reuse the ~90MB MiniLM download across boots.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/app/.cache/huggingface \
    TRANSFORMERS_CACHE=/app/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/app/.cache/huggingface/sentence-transformers

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# Copy app source.
COPY . .

# Non-root user. Create after copy so we can chown the cache dir.
RUN useradd --create-home --shell /bin/bash app \
    && mkdir -p /app/.cache/huggingface \
    && chown -R app:app /app
USER app

EXPOSE 8000

# Bind to PORT if the platform injects it (Render/Railway/Heroku style),
# else default to 8000 (Fly forwards 8080 -> internal_port via fly.toml).
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
