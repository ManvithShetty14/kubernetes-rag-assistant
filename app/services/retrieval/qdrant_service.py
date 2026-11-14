import logfire
from qdrant_client import QdrantClient
from qdrant_client.http import models
from app.config import settings
from app.services.retrieval.embeddings import embed_query

# Initialize Qdrant Client
client = QdrantClient(
    url=settings.QDRANT_URL,
    api_key=settings.QDRANT_API_KEY
)


_collection_dim: int | None = None


def _get_collection_dim() -> int | None:
    """Vector size of the Qdrant collection (cached). None if it can't be read."""
    global _collection_dim
    if _collection_dim is None:
        try:
            info = client.get_collection(settings.QDRANT_COLLECTION)
            vectors = info.config.params.vectors
            _collection_dim = vectors.size if hasattr(vectors, "size") else None
        except Exception as e:
            logfire.error(
                f"❌ Could not read Qdrant collection '{settings.QDRANT_COLLECTION}': {e}. "
                f"Did you run ingestion? (python -m app.ingestion.processor DATA/true_data true)"
            )
    return _collection_dim


def search_enterprise_knowledge(query: str, limit: int = 8):
    """
    Performs a high-precision search in the enterprise knowledge base.
    Uses the modern query_points interface.
    """
    try:
        query_vector = embed_query(query)

        expected_dim = _get_collection_dim()
        if expected_dim and len(query_vector) != expected_dim:
            logfire.error(
                f"❌ Embedding dimension mismatch: query={len(query_vector)} vs collection={expected_dim}. "
                f"The embedding model used now is not the one used for ingestion "
                f"(e.g. Gemini failed and the 768-dim fallback was loaded). Fix GEMINI_API_KEY "
                f"or re-ingest with --wipe."
            )
            return []

        # Using query_points - the modern standard for Qdrant
        response = client.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            query=query_vector,
            limit=limit,
            with_payload=True  # JSON
        )

        results = []
        for res in response.points:
            results.append({
                "content": res.payload.get("text", ""),
                "source": res.payload.get("source", "Unknown"),
                "score": res.score
            })

        return results
    except Exception as e:
        logfire.error(f"❌ Qdrant Search Failed: {e}")
        return []