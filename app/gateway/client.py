"""
LLM Gateway client — LiteLLM Proxy (open source, self-hosted, free).

The LiteLLM proxy runs as a separate service (see litellm_config.yaml / docker-compose.yml)
and exposes an OpenAI-compatible API at LITELLM_BASE_URL (default http://localhost:4000).

All routing logic lives in litellm_config.yaml, NOT in Python:
  - "rag-primary"  -> groq/openai/gpt-oss-120b
  - "rag-fallback" -> groq/openai/gpt-oss-20b    (automatic fallback on failure / 429)
  - "guard-model"  -> groq/openai/gpt-oss-20b    (fast model for NeMo Guardrails)
  - retries + in-memory response cache

Because the proxy speaks the OpenAI protocol, we use:
  - ChatOpenAI (LangChain) for nodes that want a LangChain chat model
  - the native OpenAI SDK for the responder, so we can read response headers
    (LiteLLM sets `x-litellm-cache-key` on cache hits).
"""
import logfire
import requests
from openai import OpenAI
from langchain_openai import ChatOpenAI

from app.config import settings

_GATEWAY_URL = settings.LITELLM_BASE_URL.rstrip("/")

# Native OpenAI client pointed at the LiteLLM proxy.
# max_retries=0: retries + fallbacks are handled by the gateway itself.
gateway_client = OpenAI(
    api_key=settings.LITELLM_MASTER_KEY,
    base_url=_GATEWAY_URL,
    timeout=settings.LLM_TIMEOUT,
    max_retries=0,
)


def _metadata(feature: str) -> dict:
    # LiteLLM reads `metadata` from the request body and attaches it to its logs/callbacks.
    return {
        "metadata": {
            "tags": [feature, "rag-system"],
            "feature": feature,
            "environment": settings.ENVIRONMENT,
        }
    }


def get_langchain_llm(feature: str = "rag", model: str | None = None, temperature: float = 0) -> ChatOpenAI:
    """
    Returns a LiteLLM-backed ChatOpenAI — a drop-in replacement for ChatGroq in LangChain.
    `model` is a model *alias* defined in litellm_config.yaml (default: settings.LLM_MODEL).
    """
    return ChatOpenAI(
        api_key=settings.LITELLM_MASTER_KEY,
        base_url=_GATEWAY_URL,
        model=model or settings.LLM_MODEL,
        temperature=temperature,
        timeout=settings.LLM_TIMEOUT,
        max_retries=0,
        extra_body=_metadata(feature),
    )


def chat_completion(messages: list[dict], feature: str = "rag", temperature: float = 0.1,
                    model: str | None = None) -> tuple[str, bool]:
    """
    Calls the gateway with the native OpenAI SDK.
    Returns (content, is_cache_hit).
    """
    kwargs = dict(
        model=model or settings.LLM_MODEL,
        messages=messages,
        temperature=temperature,
        extra_body=_metadata(feature),
    )
    raw_api = getattr(gateway_client.chat.completions, "with_raw_response", None)
    if raw_api is None:  # defensive: SDK without raw-response support -> no cache info
        completion = gateway_client.chat.completions.create(**kwargs)
        return completion.choices[0].message.content or "", False

    raw = raw_api.create(**kwargs)
    completion = raw.parse()
    content = completion.choices[0].message.content or ""

    # LiteLLM only returns x-litellm-cache-key when the response was served from cache.
    is_cache_hit = bool(raw.headers.get("x-litellm-cache-key"))

    fallbacks = raw.headers.get("x-litellm-attempted-fallbacks")
    if fallbacks and fallbacks != "0":
        logfire.warning(f"Gateway used fallback model (attempted_fallbacks={fallbacks}).")

    return content, is_cache_hit


def check_gateway_health() -> bool:
    """Liveness probe against the LiteLLM proxy (no auth required)."""
    try:
        r = requests.get(f"{_GATEWAY_URL}/health/liveliness", timeout=3)
        return r.status_code == 200
    except Exception:
        return False
