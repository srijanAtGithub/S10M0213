"""
navigator_bridge.py
------------------
FastAPI server serving as the browser extension bridge.

Features:
  - Chat (WebSocket): Tab-scoped conversation state managed in-memory via LangGraph[cite: 3]. 
    Sessions are created lazily and cleared on tab closure or explicit user request[cite: 3].
  - Tools (REST): Stateless endpoints for /edit-selection and /organise-tabs[cite: 3].

Design:
  - This is a modular demo. The SessionStore and internal logic are structured 
    for easy migration to persistent storage and integration with LLM 
    configuration/ToolManagers in the future[cite: 3].

Run:
  uv run uvicorn Navigator.navigator_bridge:app --reload --port 8765[cite: 3]
"""

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

import configuration
configuration.load_config()

import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from Navigator.navigator_graph import build_navigator_graph

from Navigator.Task_Files.Organise_Tools import OrganiseTabsRequest, process_organise_tabs
from Navigator.Task_Files.Edit_Selection import EditSelectionRequest, EditSelectionResponse, process_edit_selection

log = structlog.get_logger()

app = FastAPI(title="Sicily Navigator Bridge")

# Dev-only: the popup and background service worker run from a
# chrome-extension:// origin. Extension contexts with host_permissions
# can normally bypass this anyway, but keeping it open here too makes
# the REST endpoints easy to hit directly (curl, a browser tab, etc.)
# while iterating.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/organise-tabs")
async def organise_tabs(req: OrganiseTabsRequest):
    return await process_organise_tabs(req.tabs)

@app.post("/edit-selection", response_model=EditSelectionResponse)
async def edit_selection(req: EditSelectionRequest):
    return await process_edit_selection(req)

# One compiled graph instance, reused for every turn on every tab.
# It has no memory of its own — memory lives in SessionStore below and
# is handed to the graph fresh on each invocation as part of the state.
graph = build_navigator_graph()


class SessionStore:
    """
    In-memory, tab-scoped conversation history.

    tab_id -> list[BaseMessage]

    One entry per tab that has sent at least one message. Deliberately
    dumb (no TTL, no persistence to disk) — this is exactly the amount
    of state the demo needs, and it's isolated here so it can be swapped
    for something real (Redis, a LangGraph checkpointer, whatever) later
    without touching the graph or the WebSocket handler logic.
    """

    def __init__(self):
        self._sessions: dict[str, list[BaseMessage]] = {}

    def get(self, tab_id: str) -> list[BaseMessage]:
        return self._sessions.get(tab_id, [])

    def set(self, tab_id: str, messages: list[BaseMessage]) -> None:
        self._sessions[tab_id] = messages

    def clear(self, tab_id: str) -> bool:
        """Returns True if a session existed and was removed."""
        return self._sessions.pop(tab_id, None) is not None

    def __len__(self) -> int:
        return len(self._sessions)


sessions = SessionStore()


def serialize_messages(messages: list[BaseMessage]) -> list[dict]:
    """Turn LangChain messages into the plain {role, text} shape the popup renders."""
    out = []
    for m in messages:
        if isinstance(m, HumanMessage):
            role = "user"
        elif isinstance(m, AIMessage):
            role = "ai"
        else:
            continue  # nothing else shows up in this demo's history
        out.append({"role": role, "text": m.content})
    return out


@app.get("/health")
async def health():
    """Quick sanity check — hit this in a navigator tab to confirm the server is up."""
    return {
        "status": "ok",
        "dimension": "navigator",
        "ai": False,
        "active_sessions": len(sessions),
    }


@app.get("/session/{tab_id}")
async def get_session(tab_id: str):
    """
    Called by the popup on open, so switching back to a tab restores
    that tab's chat instead of showing a blank window.
    """
    return {"messages": serialize_messages(sessions.get(tab_id))}


@app.delete("/session/{tab_id}")
async def delete_session(tab_id: str):
    """
    Called by the popup's "Clear chat" button (explicit user action) and
    by background.js when the tab closes (chrome.tabs.onRemoved).
    """
    existed = sessions.clear(tab_id)
    return {"status": "cleared", "existed": existed}


@app.websocket("/ws/{tab_id}")
async def websocket_endpoint(websocket: WebSocket, tab_id: str):
    await websocket.accept()
    log.info("Extension connected", tab_id=tab_id)

    try:
        while True:
            payload = await websocket.receive_json()

            user_text = (payload.get("text") or "").strip()
            page_url = payload.get("page_url") or ""
            page_title = payload.get("page_title") or ""
            # 1. Extract the drag-and-dropped snippets array from frontend
            context_snippets = payload.get("context_snippets") or []

            log.info("Received message", tab_id=tab_id, text=user_text, page_url=page_url, snippets_count=len(context_snippets))

            if not user_text:
                await websocket.send_json({"reply": "(empty message ignored)"})
                continue

            # Feed in this tab's history so far, not just the new message —
            # this is what makes the graph's state class actually mean
            # something instead of being a fresh single-turn call every time.
            history = sessions.get(tab_id)
            result = await graph.ainvoke({
                "messages": history + [HumanMessage(content=user_text)],
                "page_url": page_url,
                "page_title": page_title,
                "context_snippets": context_snippets, # 2. Forward the snippets into LangGraph state!
            })

            # Track token usage from the returned message state
            try:
                from usage_tracker import record_usage
                for msg in result["messages"]:
                    if hasattr(msg, "usage_metadata") and msg.usage_metadata:
                        usage_meta = msg.usage_metadata
                        model_name = msg.response_metadata.get("model_name", "unknown")
                        msg_id = getattr(msg, "id", None)
                        
                        try:
                            record_usage(
                                dimension="navigator",
                                session_id=tab_id,
                                model_name=model_name,
                                input_tokens=usage_meta.get("input_tokens", 0),
                                output_tokens=usage_meta.get("output_tokens", 0),
                                cached_input_tokens=usage_meta.get("input_token_details", {}).get("cache_read_tokens", 0),
                                message_id=msg_id
                            )
                        except Exception as rec_err:
                            log.warning("record_usage failed for navigator", error=str(rec_err))
            except Exception as token_err:
                log.warning("Failed to collect navigator token metrics", error=str(token_err))

            # Persist the updated history (old messages + new human + new ai)
            # for this tab, so it's there next time this tab's popup opens.
            sessions.set(tab_id, result["messages"])

            # Pull the newest AI message out as plain text.
            reply_text = "(no response)"
            for msg in reversed(result["messages"]):
                if isinstance(msg, AIMessage) and msg.content:
                    reply_text = msg.content
                    break

            await websocket.send_json({"reply": reply_text})

    except WebSocketDisconnect:
        # The popup closing disconnects this socket, but the tab itself is
        # very likely still open — so we deliberately do NOT clear the
        # session here. Only DELETE /session/{tab_id} clears it (explicit
        # "Clear chat", or background.js reacting to the tab actually
        # closing).
        log.info("Extension disconnected", tab_id=tab_id)
    except Exception as e:
        log.exception("Bridge error", tab_id=tab_id)
        try:
            await websocket.send_json({"reply": f"Server error: {e}"})
        except Exception:
            pass
