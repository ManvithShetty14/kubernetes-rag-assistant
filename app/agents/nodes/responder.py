import logfire
from app.agents.state import AgentState
from app.config import settings
from app.agents.scope import CONVERSATIONAL, NOT_FOUND_MESSAGE, skipped_note
from app.gateway import chat_completion


def generate_node(state: AgentState):
    """
    Synthesizes a response using both Documentation Context AND Conversation History.
    Uses the native OpenAI SDK against the LiteLLM gateway (not LangChain) so we can
    read the x-litellm-cache-key response header and surface Cache: Hit in the UI.
    """
    query = state["current_query"]

    history_str = ""
    for msg in state["messages"][:-1][-settings.MAX_HISTORY_MESSAGES:]:
        role = "User" if msg["role"] == "user" else "Assistant"
        history_str += f"{role}: {msg['content']}\n"

    user_msg = state["messages"][-1]["content"] if state["messages"] else ""

    # Mixed question (e.g. "create a CronJob, and give me a biryani recipe"): answer ONLY the
    # Kubernetes part the planner extracted, then append a fixed note about what was skipped.
    skipped = state.get("skipped_part") or ""
    question = query if (skipped and query != CONVERSATIONAL) else user_msg
    note = skipped_note(skipped) if skipped else ""

    if query == CONVERSATIONAL:
        logfire.info("Generating conversational response using memory.")
        prompt = f"""
        You are a friendly Kubernetes documentation assistant.
        Reply to the user's latest message using the CONVERSATION HISTORY below.
        Keep it to greetings, thanks, and questions about this conversation.
        If the user asks for anything that is not about Kubernetes (recipes, jokes, poems,
        general knowledge, etc.), politely say you can only help with Kubernetes questions.

        CONVERSATION HISTORY:
        {history_str}

        LATEST MESSAGE:
        "{user_msg}"
        """
    else:
        # Nothing relevant was retrieved -> fixed reply, no LLM call (prevents answers from
        # the model's general knowledge that are not backed by our documentation)
        if not state["documents"]:
            logfire.info("No relevant context — returning 'not found' without calling the LLM.")
            answer = NOT_FOUND_MESSAGE + note
            return {
                "final_answer": answer,
                "status": "No relevant documentation found.",
                "plan": state["plan"],
                "messages": [{"role": "assistant", "content": answer}],
            }

        logfire.info("Generating technical RAG response.")
        max_context_chars = 25000
        full_context = ""

        for doc in state["documents"]:
            if len(full_context) + len(doc) < max_context_chars:
                full_context += doc + "\n\n"
            else:
                logfire.warning("Context truncated to fit Groq TPM limits.")
                break

        prompt = f"""
        You are a Kubernetes documentation assistant.
        Answer the user's question using ONLY the TECHNICAL CONTEXT below (excerpts from our docs).
        Rules:
        - Do not use outside knowledge to add facts that are not in the context.
        - If the context does not contain the answer, say plainly that the documentation
          you have does not cover it — do not guess.
        - If the question is not about Kubernetes, say you can only help with Kubernetes.
        - Be clear and concise; include commands or YAML from the context when useful.

        TECHNICAL CONTEXT:
        {full_context}

        CONVERSATION HISTORY:
        {history_str}

        USER QUESTION:
        "{question}"
        """

    with logfire.span("✍️ LLM Synthesis"):
        try:
            content, is_cache_hit = chat_completion(
                messages=[{"role": "user", "content": prompt}],
                feature="responder",
                temperature=0.1,
            )

            if is_cache_hit:
                logfire.info("⚡ Gateway Cache Hit — response served from LiteLLM cache.")
                plan_update = state["plan"] + ["Cache: Hit ⚡"]
                status = "Cache hit — instant response."
            else:
                logfire.info("✅ Response synthesised via LLM.")
                plan_update = state["plan"]
                status = "Response generated."

            content = (content or "").rstrip() + note
            return {
                "final_answer": content,
                "status": status,
                "plan": plan_update,
                "messages": [{"role": "assistant", "content": content}]
            }

        except Exception as e:
            logfire.error(f"LLM Generation failed: {e}")
            raise e