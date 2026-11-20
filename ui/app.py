import os
import re
import time
import uuid

import logfire
import requests
import streamlit as st
from dotenv import load_dotenv

# Load environment variables explicitly from the root directory
env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
load_dotenv(dotenv_path=env_path)

# Initialize Logfire
try:
    token = os.getenv("LOGFIRE_TOKEN")
    if not token:
        print("ERROR: LOGFIRE_TOKEN is empty or None!")
    logfire.configure(token=token or None, send_to_logfire="if-token-present")
    # logfire.instrument_requests() # Disabled due to OpenTelemetry bug on Windows: MeterProvider.get_meter() got multiple values for argument 'version'
    LOGFIRE_STATUS = "Connected & Tracing"
except Exception as e:
    print(f"Logfire Init Error in UI: {e}")
    LOGFIRE_STATUS = f"Standby (Error: {e})"

# `or` (not a getenv default): BACKEND_URL="" in .env must still fall back
BASE_URL = (os.getenv("BACKEND_URL") or "http://localhost:8000").rstrip("/")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@st.cache_data(ttl=3000, show_spinner=False)
def _id_token(audience: str) -> str:
    """Google-signed ID token for the private API (cached ~50 min; tokens last 1 hour)."""
    import google.auth.transport.requests
    import google.oauth2.id_token
    return google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), audience)


def _backend_headers(base_url: str) -> dict:
    """
    On Cloud Run the API service is private (no public access). The UI's service account
    has the Cloud Run Invoker role, so we attach a Google-signed ID token whose audience is
    the API's URL. Locally (BACKEND_AUTH unset) no auth header is sent.
    """
    if (os.getenv("BACKEND_AUTH") or "").lower() != "google":
        return {}
    return {"Authorization": f"Bearer {_id_token(base_url)}"}


def api(method: str, path: str, timeout: float = 30, **kwargs) -> dict:
    response = requests.request(
        method, f"{BASE_URL}{path}", timeout=timeout, headers=_backend_headers(BASE_URL), **kwargs
    )
    response.raise_for_status()
    return response.json()


def _valid_id(value) -> bool:
    return isinstance(value, str) and bool(_ID_RE.match(value))


def _new_id() -> str:
    return uuid.uuid4().hex


# --- PAGE CONFIG ---
st.set_page_config(
    page_title="Enterprise Agentic RAG",
    page_icon="🤖",
    layout="wide",
)

# --- AVATARS ---
AI_AVATAR = "🤖"
USER_AVATAR = "👤"


# --- CHAT HISTORY HELPERS ---
def load_history(thread_id: str) -> list[dict]:
    """Messages of a saved chat; [] if it doesn't exist yet (e.g. a brand-new chat link)."""
    try:
        data = api("GET", f"/chats/{thread_id}", params={"owner_id": st.session_state.owner_id})
        return data.get("messages", [])
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else None
        if code == 403:
            st.warning("That chat belongs to someone else — starting a new chat.")
            st.session_state.session_id = _new_id()
        elif code != 404:
            st.warning(f"Couldn't load this chat's history ({e}).")
        return []
    except Exception as e:
        st.warning(f"Couldn't load this chat's history ({e}).")
        return []


def open_chat(thread_id: str) -> None:
    st.session_state.session_id = thread_id
    st.session_state.messages = load_history(thread_id)
    logfire.info(f"📂 Opened chat {thread_id}")


def new_chat() -> None:
    st.session_state.session_id = _new_id()
    st.session_state.messages = []
    logfire.info(f"✨ New chat {st.session_state.session_id}")


# --- SESSION MANAGEMENT ---
# Both IDs live in the page URL (?u=<owner>&chat=<chat>), so refreshing the page or opening a
# bookmarked link restores your chat list and the open conversation. Streamlit cannot set
# cookies natively, so the URL is the most reliable place to keep them.
if "owner_id" not in st.session_state:
    url_owner = st.query_params.get("u")
    st.session_state.owner_id = url_owner if _valid_id(url_owner) else _new_id()

if "session_id" not in st.session_state:
    url_chat = st.query_params.get("chat")
    if _valid_id(url_chat):
        open_chat(url_chat)
    else:
        new_chat()

st.query_params["u"] = st.session_state.owner_id
st.query_params["chat"] = st.session_state.session_id


def render_sidebar() -> None:
    # Rendered at the END of the script so a chat created by the message just sent already
    # appears in the list.
    with st.sidebar:
        st.title("🧠 Agent OS")

        if st.button("➕ New chat", width="stretch", type="primary"):
            new_chat()
            st.rerun()

        st.markdown("#### 💬 Your chats")
        try:
            chats = api("GET", "/chats", params={"owner_id": st.session_state.owner_id}).get("chats", [])
        except Exception as e:
            chats = []
            st.caption(f"Couldn't load your chats ({e}).")

        if not chats:
            st.caption("No saved chats yet — ask a question to start one.")
        for chat in chats:
            is_current = chat["thread_id"] == st.session_state.session_id
            label = ("▶ " if is_current else "") + chat["title"]
            if st.button(label, key=f"chat_{chat['thread_id']}", width="stretch", disabled=is_current):
                open_chat(chat["thread_id"])
                st.rerun()

        st.markdown("---")
        if st.session_state.messages and st.button("🗑️ Delete this chat", width="stretch"):
            try:
                api("DELETE", f"/chats/{st.session_state.session_id}",
                    params={"owner_id": st.session_state.owner_id})
                logfire.warn(f"🗑️ Chat deleted: {st.session_state.session_id}")
            except Exception as e:
                st.error(f"Couldn't delete the chat ({e}).")
            new_chat()
            st.rerun()

        st.caption("🔖 **Bookmark this page** to come back to your chats later — "
                   "your chat list is tied to this link.")
        st.success(f"Logfire: {LOGFIRE_STATUS}")
        st.info(f"Chat ID: {st.session_state.session_id[:8]}")


def render_sources(sources: list[str]) -> None:
    if sources:
        with st.expander(f"📄 View Retrieved Context ({len(sources)} sources)"):
            for i, source in enumerate(sources):
                preview = source[:100].replace("\n", " ") + "..."
                st.markdown(f"**Chunk {i + 1}:** {preview}")
                st.info(source)


# --- MAIN CHAT ---
st.title("🤖 Enterprise Agentic Assistant")

# Display history (reasoning steps / sources are kept for answers given in this browser session)
for message in st.session_state.messages:
    avatar = AI_AVATAR if message["role"] == "assistant" else USER_AVATAR
    with st.chat_message(message["role"], avatar=avatar):
        if message.get("steps"):
            with st.status("✅ Answer Synthesized", state="complete", expanded=False):
                for step in message["steps"]:
                    st.write(f"⚙️ {step}")
        render_sources(message.get("sources") or [])
        st.markdown(message["content"])

# Chat Input
if prompt := st.chat_input("Ask about your documentation..."):
    # START TRACE: User Interaction
    with logfire.span("💬 User Chat Interaction", user_query=prompt, session_id=st.session_state.session_id):

        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user", avatar=USER_AVATAR):
            st.markdown(prompt)

        # Assistant Response
        with st.chat_message("assistant", avatar=AI_AVATAR):
            with st.status("🔍 Agent is thinking...", expanded=True) as status:
                try:
                    # DISTRIBUTED TRACE: Calling Backend
                    with logfire.span("📡 Calling RAG Backend"):
                        payload = {
                            "q": prompt,
                            "thread_id": st.session_state.session_id,
                            "owner_id": st.session_state.owner_id,
                        }
                        # guardrails + planner + retrieval + synthesis can exceed 60s on free tiers
                        data = api("POST", "/query", timeout=180, json=payload)

                    # Show Reasoning Steps from Backend
                    steps = data.get("thought_process") or []
                    for step in steps:
                        st.write(f"⚙️ {step}")

                    status.update(label="✅ Answer Synthesized", state="complete", expanded=False)

                except Exception as e:
                    logfire.error(f"❌ UI-Backend Connection Failed: {e}")
                    status.update(label="❌ Connection Failed", state="error")
                    st.error(f"Backend Offline or failed: {e}")
                    st.session_state.messages.pop()  # don't keep a question that got no answer
                    render_sidebar()
                    st.stop()

            # --- SHOW SOURCES ---
            # Outside st.status: Streamlit raises "Expanders may not be nested inside other expanders"
            sources = data.get("sources") or []
            render_sources(sources)

            # Final Answer Streaming
            answer_placeholder = st.empty()
            full_answer = data.get("answer") or "No response."

            curr_text = ""
            for char in full_answer:
                curr_text += char
                answer_placeholder.markdown(curr_text + "▌")
                time.sleep(0.005)

            answer_placeholder.markdown(full_answer)
            st.session_state.messages.append(
                {"role": "assistant", "content": full_answer, "steps": steps, "sources": sources}
            )
            logfire.info("✅ Chat cycle completed successfully.")

render_sidebar()
