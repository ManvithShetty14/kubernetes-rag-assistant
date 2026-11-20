# syntax=docker/dockerfile:1
# ==============================================================================
# FastAPI backend (LangGraph agent + NeMo Guardrails + Qdrant retrieval)
# Build from the repo root:  docker build -f docker/api.Dockerfile -t rag-api .
# ==============================================================================

# ---------- Stage 1: install dependencies + download models ----------
FROM python:3.11-slim AS builder

# Compilers only in the build stage (some packages may build from source)
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.10.7 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    FASTEMBED_CACHE_PATH=/app/.models/fastembed \
    FLASHRANK_CACHE_DIR=/app/.models/flashrank

# Dependencies first (cached layer). Eval-only and UI-only groups are excluded:
# no PyTorch, no RAGAS, no Streamlit in the API image.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-group evals --no-group ui

# Bake the local models into the image so cold starts don't download them:
#  - all-MiniLM-L6-v2 (FastEmbed): NeMo Guardrails' intent matching
#  - ms-marco-TinyBERT-L-2-v2 (FlashRank): reranker
RUN /app/.venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')" \
 && /app/.venv/bin/python -c "import os; from flashrank import Ranker; Ranker(cache_dir=os.environ['FLASHRANK_CACHE_DIR'])"


# ---------- Stage 2: slim runtime image ----------
FROM python:3.11-slim

RUN useradd --create-home --uid 1000 appuser
WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FASTEMBED_CACHE_PATH=/app/.models/fastembed \
    FLASHRANK_CACHE_DIR=/app/.models/flashrank \
    ENVIRONMENT=production

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appuser /app/.models /app/.models
COPY --chown=appuser:appuser app ./app

USER appuser
EXPOSE 8080

# Cloud Run injects $PORT (default 8080). One worker: the agent's conversation memory is in-process.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --timeout-keep-alive 75"]
