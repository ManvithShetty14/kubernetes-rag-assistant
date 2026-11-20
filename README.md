# kubernetes-rag-assistant

Enterprise agentic RAG: **FastAPI + LangGraph** (planner → retriever → responder) with **Qdrant** vector search,
**Gemini** embeddings, **FlashRank** reranking, **NeMo Guardrails**, and **Groq** LLMs behind a self-hosted
**LiteLLM gateway** (fallbacks, retries, response cache). Streamlit chat UI + RAGAS/DeepEval eval suite.

```
Streamlit UI (:8501) ──► FastAPI (:8000) ──► LiteLLM gateway (:4000) ──► Groq
                              │
                              └──► Gemini embeddings ──► Qdrant Cloud ──► FlashRank
```

## 1. Prerequisites
- Python 3.11 + [uv](https://docs.astral.sh/uv/)
- Docker Desktop (for the LiteLLM gateway)
- Free API keys: Groq, Gemini (Google AI Studio), Qdrant Cloud cluster URL + key

## 2. Configure
```bash
cp .env.example .env      # skip if you already have .env — then just fill in the values
```
Required: `GROQ_API_KEY`, `GEMINI_API_KEY`, `QDRANT_CLUSTER_ENDPOINT`, `QDRANT_API_KEY`, `LITELLM_MASTER_KEY`.

## 3. Install
```bash
uv sync                   # re-locks automatically (portkey-ai removed, openai added)
```

## 4. Start the LLM gateway (terminal 1)
```bash
docker compose up -d litellm
curl http://localhost:4000/health/liveliness          # -> "I'm alive!"
```
<details><summary>No Docker?</summary>

Run LiteLLM in its own isolated env (never inside the app venv; pin the version):
```bash
set -a; source .env; set +a
uvx --from 'litellm[proxy]==1.108.0' litellm --config litellm_config.yaml --port 4000
```
</details>

## 5. Ingest documents into Qdrant (once, or after changing the embedding model)
```bash
uv run python test_qdrant.py                                   # check Qdrant credentials
uv run python -m app.ingestion.processor DATA/true_data true --wipe
# optional noise corpus (large, slow, uses a lot of Gemini quota):
# uv run python -m app.ingestion.processor DATA/noisy_data noisy
```

## 6. Run the API (terminal 2)
```bash
uv run uvicorn app.main:app --reload --port 8000
curl http://localhost:8000/health
curl -X POST http://localhost:8000/query -H 'Content-Type: application/json' \
     -d '{"q": "How do I create a CronJob in Kubernetes?", "thread_id": "test"}'
```

## 7. Run the UI (terminal 3)
```bash
uv run streamlit run ui/app.py          # http://localhost:8501
```

## 8. Evals (optional; needs the API running)
```bash
uv run streamlit run evals/app.py
```

## Chat history
Conversations are saved in Postgres (LangGraph `PostgresSaver`) when `DATABASE_URL` is set, and
listed in the UI sidebar ("Your chats"). Chats are tied to the page link (`?u=<owner>&chat=<chat>`),
so bookmark it to return later. Without `DATABASE_URL`, memory is in-process (lost on restart).
Local database: `docker compose up -d postgres` and
`DATABASE_URL=postgresql://rag:rag@localhost:5432/rag` in `.env`.

## 9. Run the whole stack in Docker (production images)
```bash
docker compose --profile full up --build     # UI -> http://localhost:8501, API -> http://localhost:8000
```

## 10. Deploy to Google Cloud Run
Three Cloud Run services (UI → private API → LiteLLM gateway), secrets in Secret Manager,
least-privilege service accounts, and CI/CD via Cloud Build on every push to `main`.
Step-by-step guide: **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `Connection refused :4000` / answers are the generic error | Gateway not running → `docker compose up -d litellm`, check `docker logs litellm-gateway` |
| `401` from gateway | `LITELLM_MASTER_KEY` must be identical for the app and the container, and start with `sk-` |
| Answers have no sources | Collection empty or embedding-dimension mismatch (see API logs) → re-run ingestion with `--wipe` |
| Groq 429 rate limits | Free tier; the gateway retries then falls back to `rag-fallback`. A second key in `GROQ_FALLBACK_API_KEY` helps |
