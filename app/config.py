import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    #GEMINI EMBEDDINGS
    GEMINI_API_KEY=os.getenv("GEMINI_API_KEY")
    # Must be the SAME model at ingestion time and query time (vector dims must match the collection)
    GEMINI_EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL") or "models/gemini-embedding-2-preview"

    #VECTOR DB (QDRANT)
    QDRANT_URL=os.getenv("QDRANT_CLUSTER_ENDPOINT")
    QDRANT_API_KEY=os.getenv("QDRANT_API_KEY")
    QDRANT_COLLECTION = "enterprise_rag"

    # Reranker relevance cut-off (0..1). Chunks scoring below this are treated as irrelevant;
    # if none pass, the bot replies "not found in the docs" instead of answering from general knowledge.
    # Raise it to be stricter, lower it if real Kubernetes questions get "not found".
    MIN_RERANK_SCORE = float(os.getenv("MIN_RERANK_SCORE") or 0.01)

    # NeMo guardrails (embeddings-only): how closely a message must match a rail's examples (0..1)
    # to trigger it. Lower = catches more paraphrases but risks false blocks; higher = stricter.
    GUARDRAILS_SIMILARITY_THRESHOLD = float(os.getenv("GUARDRAILS_SIMILARITY_THRESHOLD") or 0.75)

    #REASONING ENGINE (GROQ)
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    GROQ_MODEL = "openai/gpt-oss-120b"  # informational; actual routing is in litellm_config.yaml
    GROQ_FALLBACK_API_KEY = os.getenv("GROQ_FALLBACK_API_KEY")

    # --- LLM GATEWAY (LiteLLM Proxy, self-hosted) ---
    # Model names below are *aliases* defined in litellm_config.yaml
    LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL") or "http://localhost:4000"
    LITELLM_MASTER_KEY = os.getenv("LITELLM_MASTER_KEY") or "sk-local-dev-key"
    LLM_MODEL = os.getenv("LLM_MODEL") or "rag-primary"      # groq/openai/gpt-oss-120b (falls back to gpt-oss-20b)
    GUARD_MODEL = os.getenv("GUARD_MODEL") or "guard-model"  # groq/openai/gpt-oss-20b
    LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT") or 60)
    ENVIRONMENT = os.getenv("ENVIRONMENT") or "development"

    # --- CONVERSATION PERSISTENCE ---
    # Postgres connection string. Empty = in-memory (conversations lost when the API restarts).
    DATABASE_URL = os.getenv("DATABASE_URL") or ""
    # How many previous messages are sent to the LLM as context (keeps long chats within token limits)
    MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES") or 10)

    # --- OBSERVABILITY ---
    LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY") or ""
    # Only trace when a key is actually configured (otherwise every LLM call logs 401 errors)
    LANGSMITH_TRACING = "true" if (os.getenv("LANGSMITH_TRACING", "true").lower() == "true" and LANGSMITH_API_KEY) else "false"
    LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT") or "rag_scale_test"
    LANGSMITH_ENDPOINT = os.getenv("LANGSMITH_ENDPOINT") or "https://api.smith.langchain.com"


settings = Settings()

# Apply LangChain environment variables for automatic tracing
os.environ["LANGCHAIN_TRACING_V2"] = settings.LANGSMITH_TRACING
os.environ["LANGSMITH_TRACING"] = settings.LANGSMITH_TRACING
os.environ["LANGCHAIN_API_KEY"] = settings.LANGSMITH_API_KEY
os.environ["LANGCHAIN_PROJECT"] = settings.LANGSMITH_PROJECT
os.environ["LANGCHAIN_ENDPOINT"] = settings.LANGSMITH_ENDPOINT
