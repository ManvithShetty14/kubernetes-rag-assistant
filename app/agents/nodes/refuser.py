import logfire
from app.agents.state import AgentState
from app.agents.scope import REFUSAL_MESSAGES, OFF_TOPIC_MESSAGE


def refuse_node(state: AgentState):
    """Fixed reply chosen by the refusal reason: no vector search, no LLM call."""
    route = state.get("route") or ""
    message = REFUSAL_MESSAGES.get(route, OFF_TOPIC_MESSAGE)
    logfire.info(f"🚫 Returning refusal for route={route!r}.")
    return {
        "final_answer": message,
        "documents": [],
        "status": state.get("status") or "Request refused.",
        "messages": [{"role": "assistant", "content": message}],
    }
