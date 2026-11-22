import hashlib
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


# --- IDENTITY: guest link or Google account ---
ACCOUNT_PREFIX = "g_"   # must match app/persistence.py


def _valid_guest_id(value) -> bool:
    # Guest IDs come from the URL, so refuse the account prefix: otherwise someone could put a
    # signed-in user's owner ID into ?u= and read their chats as a "guest".
    return _valid_id(value) and not value.startswith(ACCOUNT_PREFIX)


def account_owner_id(subject: str) -> str:
    """Stable, non-reversible owner ID for a Google account (the OIDC 'sub' claim)."""
    return ACCOUNT_PREFIX + hashlib.sha256(f"google:{subject}".encode()).hexdigest()[:40]


def resolve_owner(logged_in: bool, subject, url_owner, session_guest) -> str:
    """Whose chats to show: the account when signed in, else this tab's guest ID / the URL's / a new one."""
    if logged_in and subject:
        return account_owner_id(subject)
    if session_guest and _valid_guest_id(session_guest):
        return session_guest
    if _valid_guest_id(url_owner):
        return url_owner
    return _new_id()


def _user_claim(key: str):
    """Read a claim (sub, email, name) from st.user, whichever access style this Streamlit version supports."""
    try:
        return st.user.get(key)
    except Exception:
        return getattr(st.user, key, None)


def _auth_enabled() -> bool:
    """Google sign-in is offered only when .streamlit/secrets.toml has an [auth] section."""
    try:
        return bool(st.secrets.get("auth", {}).get("client_id"))
    except Exception:
        return False


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
# Guests: both IDs live in the page URL (?u=<owner>&chat=<chat>), so refreshing or opening a
# bookmarked link restores the chat list. Signed-in users: chats belong to their Google account
# (works on any device), and only ?chat= is kept in the URL.
AUTH_ENABLED = _auth_enabled()
LOGGED_IN = AUTH_ENABLED and bool(getattr(st.user, "is_logged_in", False))
SUBJECT = (_user_claim("sub") or _user_claim("email")) if LOGGED_IN else None

url_owner = st.query_params.get("u")
owner = resolve_owner(LOGGED_IN, SUBJECT, url_owner, st.session_state.get("guest_owner"))
if not LOGGED_IN:
    st.session_state.guest_owner = owner
elif _valid_guest_id(url_owner):
    # A signed-in user opened a guest link: offer to move its chats into the account
    st.session_state.import_from = url_owner

if st.session_state.get("owner_id") != owner:
    # First run, or the identity changed (signed in / out): start from that owner's chats
    st.session_state.owner_id = owner
    st.session_state.pop("session_id", None)

if "session_id" not in st.session_state:
    url_chat = st.query_params.get("chat")
    if _valid_id(url_chat):
        open_chat(url_chat)
    else:
        new_chat()

if LOGGED_IN:
    if "u" in st.query_params:
        del st.query_params["u"]
else:
    st.query_params["u"] = st.session_state.owner_id
st.query_params["chat"] = st.session_state.session_id


def render_account() -> None:
    """Sign-in / sign-out controls and the 'move guest chats' offer (only if sign-in is configured)."""
    if not AUTH_ENABLED:
        return
    if not LOGGED_IN:
        st.button("🔐 Sign in with Google", width="stretch", on_click=st.login)
        st.caption("Optional — sign in to keep your chats on any device. Or just start chatting as a guest.")
        return

    st.caption(f"Signed in as **{_user_claim('name') or _user_claim('email') or 'Google user'}**")
    st.button("Sign out", width="stretch", on_click=st.logout)

    guest = st.session_state.get("import_from")
    if guest:
        try:
            guest_chats = api("GET", "/chats", params={"owner_id": guest}).get("chats", [])
        except Exception:
            guest_chats = []
        if not guest_chats:
            st.session_state.pop("import_from", None)
        else:
            st.info(f"This guest link has {len(guest_chats)} chat(s).")
            if st.button("📥 Move them to my account", width="stretch"):
                try:
                    moved = api("POST", "/chats/claim",
                                json={"from_owner_id": guest, "to_owner_id": st.session_state.owner_id})
                    st.session_state.pop("import_from", None)
                    st.toast(f"Moved {moved.get('moved', 0)} chat(s) to your account.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Couldn't move the chats ({e}).")


def render_sidebar() -> None:
    # Rendered at the END of the script so a chat created by the message just sent already
    # appears in the list.
    with st.sidebar:
        st.title("🧠 Agent OS")
        render_account()

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

        if not LOGGED_IN:
            st.caption("🔖 **Bookmark this page** to come back to your chats later — "
                       "as a guest, your chat list is tied to this link.")
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
