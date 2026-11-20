from langgraph.graph import StateGraph, END
from app.agents.state import AgentState
from app.agents.scope import ROUTE_CONVERSATIONAL, REFUSAL_ROUTES
from app.agents.nodes.planner import planner_node
from app.agents.nodes.retriever import retrieve_node
from app.agents.nodes.responder import generate_node
from app.agents.nodes.refuser import refuse_node
from app.persistence import create_checkpointer


# 1. Initialize the State Graph
workflow = StateGraph(AgentState)


# 2. Define the Nodes
workflow.add_node("planner", planner_node)
workflow.add_node("retriever", retrieve_node)
workflow.add_node("responder", generate_node)
workflow.add_node("refuser", refuse_node)

# 3. Define the Edges & Routing Logic
def route_planner(state: AgentState):
    """
    Routes the workflow based on the planner's decision.
    """
    route = state.get("route")
    if route in REFUSAL_ROUTES:          # off_topic / unsafe / unsupported_task
        return "refuser"
    if route == ROUTE_CONVERSATIONAL:
        return "responder"
    return "retriever"

workflow.set_entry_point("planner")


# Conditional Edge: Planner -> Router -> (Retriever | Responder | Refuser)
workflow.add_conditional_edges(
    "planner",
    route_planner,
    {
        "retriever": "retriever",
        "responder": "responder",
        "refuser": "refuser",
    }
)


workflow.add_edge("retriever", "responder")
workflow.add_edge("responder", END)
workflow.add_edge("refuser", END)

# --- MEMORY ---
# Conversations are remembered per 'thread_id'. With DATABASE_URL set they are stored in
# Postgres (survive restarts / redeploys); otherwise in process memory (local dev).
checkpointer = create_checkpointer()


# 4. Compile the Graph with Memory
rag_agent = workflow.compile(checkpointer=checkpointer)
