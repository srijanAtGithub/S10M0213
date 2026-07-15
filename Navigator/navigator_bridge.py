"""
navigator_bridge.py
------------------
Standalone FastAPI server exposing:
  - REST endpoints for tab-scoped session management (view / clear history)
  - One WebSocket endpoint per tab for chat turns

Demo scope (no AI):
  - No LLM, no tools, no MCP, no auth, no API key check yet.
  - Conversation state is held in-memory, one list of LangChain messages
    per browser tab (keyed by tab_id, sent by the extension).
  - Runs the trivial LangGraph graph from navigator_graph.py on every turn,
    feeding it the tab's full message history so far (not just the latest
    message), and stores the graph's updated history back for next time.

Session lifecycle (this is the piece that used to be missing):
  - A tab's history is created lazily on its first message.
  - It is returned to the popup on open via GET /session/{tab_id}, so
    switching tabs and coming back restores that tab's conversation.
  - It is wiped explicitly via DELETE /session/{tab_id} ("Clear chat" button).
  - It is wiped automatically when the tab itself closes — background.js
    listens for chrome.tabs.onRemoved and calls DELETE /session/{tab_id}.
  - Nothing here survives a server restart; that's fine for this demo —
    a real persistence layer can replace SessionStore later without
    touching the graph or the extension's protocol.

This file also exposes a second, unrelated pipeline: POST /edit-selection,
for the "Edit with Navigator" right-click-a-selection flow. That one is
deliberately NOT part of the tab-session system above — it's stateless,
single-shot (selected text + instruction in, edited text out), has no
tab_id, and touches SessionStore not at all. Don't be tempted to route it
through the WebSocket/graph machinery; it has no conversation to remember.

Run it:
    uv run uvicorn Navigator.navigator_bridge:app --reload --port 8765
    (or: python -m uvicorn Navigator.navigator_bridge:app --reload --port 8765)

This intentionally does NOT reuse configuration.py / agent.py / Cowork —
there is no LLM to configure yet. Once the real AI is wired in, this file
is where a real ToolManager / configuration.get_main_llm() would be added,
following the same pattern as Cowork/cowork_session.py. SessionStore below
is deliberately shaped so that swap is easy: it's already just "tab_id ->
list of LangChain messages", which is what a checkpointer would want too.
"""

from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, HumanMessage

import configuration
configuration.load_config()

import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel

from Navigator.navigator_graph import build_navigator_graph

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


class EditSelectionRequest(BaseModel):
    selected_text: str
    instruction: str
    surrounding_context: str = ""
    action_type: str = "edit"  # New flag: "edit" or "ask"


class EditSelectionResponse(BaseModel):
    edited_text: str


# For Pydantic purposes
class EditResult(BaseModel):
    edited_text: str = Field(
        description="The final output to return to the user, either a rewritten text or a direct answer to their question."
    )


import uuid

async def call_edit_model(selected_text: str, instruction: str, action_type: str = "edit", surrounding_context: str = "") -> str:
    llm = configuration.get_reading_tool_llm(EditResult)
    
    # Branch the persona based on the button clicked
    if action_type == "ask":
        system_msg = SystemMessage(
            content=(
                "You are a precise, direct information assistant. "
                "The user has selected some text and asked a question about it. "
                "Answer their question completely and directly based on the selected text and context. "
                "CRITICAL CONSTRAINT: Output ONLY the direct answer to the user's question. "
                "Do NOT include any conversational filler, pleasantries, meta-commentary, "
                "or follow-up prompts (e.g., never end with 'Let me know if you need more details', "
                "'Hope this helps!', or 'Shall I do anything else?'). "
                "Provide a clean, self-contained final response with absolutely no open-ended transitions."
            )
        )
    else:
        system_msg = SystemMessage(
            content=(
                "You are an automated, programmatic text-replacement engine. "
                "Rewrite the user's selected text exactly according to their instruction. "
                "CRITICAL CONSTRAINT: Output EXCLUSIVELY the final revised text. "
                "Do NOT include any introductions, explanations, pleasantries, meta-commentary, "
                "or follow-up questions (e.g., never say 'Here is the rewrite' or ask 'Want me to shorten it?'). "
                "Your entire output will be injected directly into the user's document, so any extra words, "
                "conversational notes, or markdown formatting wrappers will completely corrupt their file."
            )
        )
    
    prompt_text = f"Instruction/Question: {instruction}\n\nSelected Text:\n{selected_text}"
    if surrounding_context:
        prompt_text += f"\n\nSurrounding Context:\n{surrounding_context}"
        
    from usage_tracker import record_usage
    
    edited_text = ""
    session_id = f"edit_{uuid.uuid4().hex[:8]}"
    
    try:
        # Use astream_events to catch the AIMessage tokens before Pydantic parsing
        async for event in llm.astream_events([system_msg, HumanMessage(content=prompt_text)], version="v2"):
            
            # 1. Catch the raw LLM usage stats
            if event["event"] == "on_chat_model_end":
                output = event.get("data", {}).get("output")
                if output and hasattr(output, "usage_metadata") and output.usage_metadata:
                    usage = output.usage_metadata
                    model_name = output.response_metadata.get("model_name", "unknown")
                    
                    record_usage(
                        dimension="navigator",
                        session_id=session_id,
                        model_name=model_name,
                        input_tokens=usage.get("input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                        cached_input_tokens=usage.get("input_token_details", {}).get("cache_read_tokens", 0)
                    )
            
            # 2. Catch the final structured output
            elif event["event"] == "on_chain_end":
                data_out = event.get("data", {}).get("output")
                if isinstance(data_out, EditResult):
                    edited_text = data_out.edited_text
                    
    except Exception as e:
        log.warning("Failed during edit event stream tracking", error=str(e))
    
    # Fallback in case the event stream didn't resolve the text correctly
    if not edited_text:
        response = await llm.ainvoke([system_msg, HumanMessage(content=prompt_text)])
        edited_text = response.edited_text
        
    return edited_text


@app.post("/edit-selection", response_model=EditSelectionResponse)
async def edit_selection(req: EditSelectionRequest):
    # Pass the action_type down
    edited = await call_edit_model(
        req.selected_text, 
        req.instruction, 
        req.action_type, 
        req.surrounding_context
    )
    return EditSelectionResponse(edited_text=edited)


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

            log.info("Received message", tab_id=tab_id, text=user_text, page_url=page_url)

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
            })

            # Track token usage from the returned message state
            try:
                from usage_tracker import record_usage
                for msg in result["messages"]:
                    if hasattr(msg, "usage_metadata") and msg.usage_metadata:
                        usage_meta = msg.usage_metadata
                        model_name = msg.response_metadata.get("model_name", "unknown")
                        msg_id = getattr(msg, "id", None)
                        
                        record_usage(
                            dimension="navigator",
                            session_id=tab_id,
                            model_name=model_name,
                            input_tokens=usage_meta.get("input_tokens", 0),
                            output_tokens=usage_meta.get("output_tokens", 0),
                            cached_input_tokens=usage_meta.get("input_token_details", {}).get("cache_read_tokens", 0),
                            message_id=msg_id
                        )
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
