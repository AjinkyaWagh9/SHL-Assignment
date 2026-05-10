# Single-stage build. sentence-transformers pulls torch (~700MB), so multi-stage
# offers little benefit — copying site-packages between stages costs almost as
# much as it saves. Slim base + non-root user is enough.
FROM python:3.11-slim

# System deps: faiss-cpu and torch wheels are self-contained, but sentence-
# transformers occasionally shells out and curl is useful for health checks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces runs containers as UID 1000 with HOME=/home/user. Setting
# this up early lets the same image work on Spaces, Render, Fly, and Railway
# without per-platform tweaks.
RUN useradd --create-home --uid 1000 --shell /bin/bash user

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/user \
    HF_HOME=/home/user/.cache/huggingface \
    TRANSFORMERS_CACHE=/home/user/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/home/user/.cache/huggingface/sentence-transformers \
    PYTHONPATH=/home/user/app

WORKDIR /home/user/app

# Install deps first for better layer caching.
COPY --chown=user:user requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# Copy app source.
COPY --chown=user:user . .

# Bake the catalog + vector index into the image. Building at image-build time
# means cold-start = load numpy arrays from disk (fast), not download MiniLM
# and re-encode 377 items. Run as root, then chown the resulting files so the
# unprivileged user can read them.
RUN python data/build_catalog.py \
    && python retriever/embeddings.py \
    && mkdir -p /home/user/.cache/huggingface \
    && chown -R user:user /home/user

USER user

# 7860 is Hugging Face Spaces' default app port. Render/Railway inject $PORT
# and will override; Fly's fly.toml [env] sets PORT=8000 explicitly.
EXPOSE 7860

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
