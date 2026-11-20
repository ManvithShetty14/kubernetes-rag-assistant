# syntax=docker/dockerfile:1
# ==============================================================================
# Streamlit chat UI — tiny image, only what the UI needs (see ui/requirements.txt)
# Build from the repo root:  docker build -f docker/ui.Dockerfile -t rag-ui .
# ==============================================================================
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 1000 appuser
WORKDIR /app

COPY ui/requirements.txt ./ui/requirements.txt
RUN pip install -r ui/requirements.txt

COPY --chown=appuser:appuser ui ./ui

USER appuser
EXPOSE 8080

CMD ["sh", "-c", "streamlit run ui/app.py --server.port=${PORT:-8080} --server.address=0.0.0.0 --server.headless=true --browser.gatherUsageStats=false"]
