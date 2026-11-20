import logfire
from app.agents.state import AgentState
from app.config import settings
from app.services.retrieval.qdrant_service import search_enterprise_knowledge
from app.services.retrieval.ranking_service import rerank_with_scores


def retrieve_node(state: AgentState):
    """
    Performs vector search and semantic reranking for technical queries.
    Chunks whose reranker score is below MIN_RERANK_SCORE are dropped, so an
    unanswerable question reaches the responder with NO context (-> "not found"),
    instead of with 5 irrelevant chunks.
    """
    query = state["current_query"]

    with logfire.span("🔍 Knowledge Retrieval"):
        logfire.info(f"Searching Qdrant for: {query}")
        raw_results = search_enterprise_knowledge(query, limit=15)
        logfire.info(f"Retrieved {len(raw_results)} candidates from Vector DB")

        doc_contents = [doc['content'] for doc in raw_results]

        with logfire.span("⚖️ Semantic Reranking"):
            ranked = rerank_with_scores(query, doc_contents, top_n=5)

        threshold = settings.MIN_RERANK_SCORE
        # score is None only when the reranker itself failed -> keep the vector-search order
        relevant = [text for text, score in ranked if score is None or score >= threshold]
        top_score = ranked[0][1] if ranked else None
        logfire.info(
            f"Kept {len(relevant)}/{len(ranked)} chunks above relevance threshold {threshold} "
            f"(top score: {top_score})"
        )

        formatted_docs = [f"CONTENT: {doc}" for doc in relevant]

    if not formatted_docs:
        return {
            "documents": [],
            "status": "No relevant documentation found.",
            "plan": state["plan"] + ["Context: No relevant documents found"]
        }

    return {
        "documents": formatted_docs,
        "status": "Found technical context.",
        "plan": state["plan"] + ["Context Retrieved"]
    }
