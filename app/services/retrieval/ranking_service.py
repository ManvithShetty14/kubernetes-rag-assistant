import os
import time
import logfire
from flashrank import Ranker, RerankRequest

# Lazy initialization - Ranker is loaded on first use to ensure logfire.configure() has run
_ranker = None


def _get_ranker() -> Ranker:
    """
    Initializes the FlashRank engine lazily.
    FlashRank uses a local ONNX cross-encoder (default: ms-marco-TinyBERT-L-2-v2) for fast reranking.
    """
    global _ranker
    if _ranker is None:
        logfire.info("🧠 Initializing FlashRank Model (TinyBERT) locally...")
        try:
            # FLASHRANK_CACHE_DIR is set in the Docker image, where the model is pre-downloaded
            _ranker = Ranker(cache_dir=os.getenv("FLASHRANK_CACHE_DIR") or "/tmp/flashrank")
        except Exception:
            _ranker = Ranker()
    return _ranker


def rerank_with_scores(query: str, documents: list[str], top_n: int = 5) -> list[tuple[str, float | None]]:
    """
    Re-scores documents against the query with a cross-encoder.
    Returns [(text, score), ...] sorted best-first. Scores are relevance probabilities (0..1):
    relevant chunks typically score far higher than unrelated ones (e.g. a recipe query
    against Kubernetes docs scores ~0.00002).
    If reranking fails, returns the original order with score=None (so nothing is filtered out).
    """
    if not documents:
        return []

    start_time = time.time()
    logfire.info(f"📡 [Reranker] Sending {len(documents)} docs to FlashRank Cross-Encoder...")

    try:
        ranker = _get_ranker()
        passages = [{"id": i, "text": doc} for i, doc in enumerate(documents)]
        results = ranker.rerank(RerankRequest(query=query, passages=passages))

        ranked = [(res["text"], float(res["score"])) for res in results[:top_n]]

        duration = time.time() - start_time
        top_score = ranked[0][1] if ranked else "N/A"
        logfire.info(f"✅ [Reranker] Done in {duration:.2f}s. Top semantic score: {top_score}")
        return ranked

    except Exception as e:
        logfire.error(f"❌ [Reranker] Semantic Reranking Failed: {e}")
        # Fallback to the original Qdrant order to ensure the user still gets an answer
        return [(doc, None) for doc in documents[:top_n]]


def rerank_documents(query: str, documents: list[str], top_n: int = 5) -> list[str]:
    """Backwards-compatible helper: texts only, best-first."""
    return [text for text, _ in rerank_with_scores(query, documents, top_n)]
