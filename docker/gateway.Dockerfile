# ==============================================================================
# LiteLLM gateway — official image (pinned) + this project's routing config baked in
# Build from the repo root:  docker build -f docker/gateway.Dockerfile -t rag-gateway .
# ==============================================================================
FROM ghcr.io/berriai/litellm:v1.108.0

COPY litellm_config.yaml /app/config.yaml

EXPOSE 4000
CMD ["--config", "/app/config.yaml", "--port", "4000"]
