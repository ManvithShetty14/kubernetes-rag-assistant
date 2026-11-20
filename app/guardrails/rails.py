import logfire
from nemoguardrails import RailsConfig, LLMRails

from app.config import settings
from app.gateway import get_langchain_llm
from app.guardrails.colang_rules import COLANG_CONTENT, RAIL_INDICATORS, RAIL_PASS_MESSAGE, build_yaml

_rails: LLMRails | None = None


def initialize_rails() -> None:
    """
    Build the NeMo LLMRails singleton at app startup.
    Intent matching is embeddings-only (local model, no LLM calls). The guard LLM is still
    passed in because NeMo requires one, but the configured flows never need to call it.
    """
    global _rails

    # Routed through the LiteLLM gateway ("guard-model" alias -> groq/openai/gpt-oss-20b)
    guard_llm = get_langchain_llm(feature="guardrails", model=settings.GUARD_MODEL, temperature=0)

    config = RailsConfig.from_content(
        colang_content=COLANG_CONTENT,
        yaml_content=build_yaml(settings.GUARDRAILS_SIMILARITY_THRESHOLD)
    )

    _rails = LLMRails(config, llm=guard_llm)
    logfire.info(
        f"🛡️ NeMo Guardrails initialised (embeddings-only, "
        f"threshold={settings.GUARDRAILS_SIMILARITY_THRESHOLD})."
    )


def guard(message: str) -> tuple[bool, str | None]:
    """
    Run a user message through the NeMo rails gate.

    Returns:
        (True,  rail_response) — a rail fired; return this response immediately,
                                skip the RAG pipeline entirely.
        (False, None)          — message is clean; proceed to LangGraph.
    """
    if _rails is None:
        logfire.warning("⚠️ Guardrails not initialised — skipping gate.")
        return False, None

    with logfire.span("🛡️ Guardrails Check"):
        result = _rails.generate(messages=[{"role": "user", "content": message}])

        # NeMo returns {'role': 'assistant', 'content': '...'} — extract text
        content = result.get("content", "") if isinstance(result, dict) else str(result)

        if content.strip() == RAIL_PASS_MESSAGE:
            logfire.info("✅ Guardrails passed (no rail matched).")
            return False, None

        if any(indicator in content for indicator in RAIL_INDICATORS):
            logfire.info(f"🛡️ Guardrails fired | query='{message[:80]}'")
            return True, content

        # Neither a known rail reply nor the pass-through sentinel: NeMo took an unexpected path
        # (e.g. generated its own text). Don't silently call that "passed" — log it loudly.
        # We still continue, because the planner applies the scope/safety checks next.
        logfire.warning(
            f"⚠️ Guardrails returned unexpected output — continuing to planner checks. "
            f"output='{content[:120]}'"
        )
        return False, None
