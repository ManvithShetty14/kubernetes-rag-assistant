import logfire
import os
from dotenv import load_dotenv

load_dotenv()
logfire.configure(token=os.getenv("LOGFIRE_TOKEN") or None, send_to_logfire="if-token-present")

#Now safe to import app modules - logfire is already active
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Response
from app.agents.graph import rag_agent
from app.config import settings
from app.gateway import check_gateway_health
from app.guardrails import initialize_rails, guard
from app import persistence
from app.persistence import ChatOwnershipError, chat_index, make_title, valid_id

from pydantic import BaseModel
from typing import Optional

@asynccontextmanager
async def lifespan(_app: FastAPI):
    if check_gateway_health():
        logfire.info(f"✅ LiteLLM gateway reachable at {settings.LITELLM_BASE_URL}")
    else:
        logfire.warning(
            f"⚠️ LiteLLM gateway NOT reachable at {settings.LITELLM_BASE_URL}. "
            f"Start it with: docker compose up -d litellm"
        )
    initialize_rails()
    chat_index()  # create the chat index table up front (no-op in memory mode)
    yield
    persistence.close()


# Initialize FastAPI
app = FastAPI(title="Enterprise Agentic RAG API", lifespan=lifespan)


class QueryRequest(BaseModel):
    q: str
    thread_id: Optional[str] = "default_user"
    # Opaque ID of whoever owns this chat (the UI keeps it in the page URL). Chats can only be
    # listed / read / continued by the same owner.
    owner_id: Optional[str] = "anonymous"


def _require_ids(thread_id: str, owner_id: str) -> None:
    if not (valid_id(thread_id) and valid_id(owner_id)):
        raise HTTPException(status_code=400, detail="Invalid chat or owner id.")


def _require_owner(thread_id: str, owner_id: str) -> None:
    _require_ids(thread_id, owner_id)
    owner = chat_index().owner_of(thread_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Chat not found.")
    if owner != owner_id:
        raise HTTPException(status_code=403, detail="This chat belongs to someone else.")


def _record_blocked_turn(config: dict, question: str, answer: str) -> None:
    """Guardrail-blocked messages never enter the graph — save them so they appear in chat history."""
    try:
        rag_agent.update_state(
            config,
            {
                "messages": [{"role": "user", "content": question}, {"role": "assistant", "content": answer}],
                "final_answer": answer,
                "status": "Blocked by guardrails.",
                "plan": ["Intent: Guardrails Fired", "Retrieval: Skipped"],
                "documents": [],
            },
            as_node="refuser",
        )
    except Exception as e:
        logfire.warning(f"⚠️ Could not save blocked turn to chat history: {e}")


@app.get("/")
def home():
    return {"message": "Enterprise LangGraph RAG API is live."}


@app.get("/health")
def health():
    gateway_ok = check_gateway_health()
    return {
        "api": "ok",
        "llm_gateway": "ok" if gateway_ok else "unreachable",
        "gateway_url": settings.LITELLM_BASE_URL,
    }


@app.get("/graph")
def get_graph_image():
    """
    Returns the Mermaid image of the agent's workflow.
    """
    try:
        png_bytes = rag_agent.get_graph().draw_mermaid_png()
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        return {"error": f"Could not generate graph image: {e}"}


@app.post("/query")
def query(request: QueryRequest):
    """
    Executes the LangGraph RAG flow with memory using a POST request.
    """
    q = request.q
    thread_id = request.thread_id
    owner_id = request.owner_id or "anonymous"
    _require_ids(thread_id, owner_id)

    # Register / bump the chat in the owner's chat list (refuses someone else's chat id)
    try:
        chat_index().touch(thread_id, owner_id, make_title(q))
    except ChatOwnershipError:
        raise HTTPException(status_code=403, detail="This chat belongs to someone else.")
    except Exception as e:
        logfire.warning(f"⚠️ Chat index unavailable (chat still works, list may be stale): {e}")

    initial_state = {
        "messages": [{"role": "user", "content": q}],
        "current_query": q,
        "route": "",
        "skipped_part": "",
        "documents": [],
        "plan": ["Start"],
        "status": "Initializing Graph..."
    }

    # Configuration for Memory (Thread ID)
    config = {"configurable": {"thread_id": thread_id}}

    try:
        # Gate 1: NeMo Guardrails — blocks off-topic, jailbreaks, and handles dialog
        rail_fired, rail_response = guard(q)
        if rail_fired:
            logfire.info(f"🛡️ Request blocked by guardrails | thread={thread_id}")
            _record_blocked_turn(config, q, rail_response)
            return {
                "question": q,
                "answer": rail_response,
                "thought_process": ["Intent: Guardrails Fired", "Retrieval: Skipped"],
                "status": "Blocked by guardrails.",
                "sources": []
            }

        # Gate 2: LangGraph RAG pipeline
        # Run the graph synchronously to preserve Logfire context variables
        final_output = rag_agent.invoke(initial_state, config=config)

        return {
            "question": q,
            "answer": final_output.get("final_answer"),
            "thought_process": final_output.get("plan"),
            "status": final_output.get("status"),
            "sources": final_output.get("documents", [])
        }
    except Exception as e:
        logfire.error(f"❌ Backend Execution Failed: {e}")
        return {
            "question": q,
            "answer": "I apologize, but I encountered an internal error while processing your request. Please try again later.",
            "thought_process": ["Error encountered during execution."],
            "status": "error",
            "sources": []
        }


# ---------------------------------------------------------------------------------------
# Chat history
# ---------------------------------------------------------------------------------------
@app.get("/chats")
def list_chats(owner_id: str = Query(...), limit: int = Query(30, ge=1, le=100)):
    """The owner's chats, most recently active first."""
    if not valid_id(owner_id):
        raise HTTPException(status_code=400, detail="Invalid owner id.")
    chats = chat_index().list(owner_id, limit)
    return {
        "chats": [
            {"thread_id": c["thread_id"], "title": c["title"], "updated_at": c["updated_at"].isoformat()}
            for c in chats
        ]
    }


@app.get("/chats/{thread_id}")
def get_chat(thread_id: str, owner_id: str = Query(...)):
    """Full message history of one chat (read from the LangGraph checkpointer)."""
    _require_owner(thread_id, owner_id)
    state = rag_agent.get_state({"configurable": {"thread_id": thread_id}})
    messages = (state.values or {}).get("messages", []) if state else []
    return {
        "thread_id": thread_id,
        "messages": [
            {"role": m.get("role"), "content": m.get("content", "")}
            for m in messages
            if isinstance(m, dict) and m.get("role") in ("user", "assistant")
        ],
    }


@app.delete("/chats/{thread_id}")
def delete_chat(thread_id: str, owner_id: str = Query(...)):
    """Delete a chat: its entry in the list and its stored conversation state."""
    _require_owner(thread_id, owner_id)
    chat_index().delete(thread_id, owner_id)
    try:
        rag_agent.checkpointer.delete_thread(thread_id)
    except Exception as e:
        logfire.warning(f"⚠️ Could not delete checkpoints for {thread_id}: {e}")
    return {"deleted": thread_id}

