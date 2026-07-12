"""
browser_bridge.py
------------------
Standalone FastAPI server exposing a single WebSocket endpoint that the
Chrome extension's popup connects to directly.

Demo scope (no AI):
  - No LLM, no tools, no MCP, no auth, no API key check yet.
  - One WebSocket endpoint: /ws
  - Each incoming message is: { "text": "...", "page_url": "...", "page_title": "..." }
  - Runs the trivial LangGraph graph from browser_graph.py and sends back:
      { "reply": "..." }

Run it:
    uv run uvicorn browser_bridge:app --reload --port 8765
    (or: python -m uvicorn browser_bridge:app --reload --port 8765)

This intentionally does NOT reuse configuration.py / agent.py / Cowork —
there is no LLM to configure yet. Once the real AI is wired in, this file
is where a real ToolManager / configuration.get_main_llm() would be added,
following the same pattern as Cowork/cowork_session.py.
"""

import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from langchain_core.messages import HumanMessage

from Navigator.navigator_graph import build_browser_graph

log = structlog.get_logger()

app = FastAPI(title="Sicily Navigator Bridge (demo, no AI)")

# Graph has no state to persist between turns yet (no memory, no LLM),
# so one compiled graph instance is reused for every request.
graph = build_browser_graph()


@app.get("/health")
async def health():
    """Quick sanity check — hit this in a navigator tab to confirm the server is up."""
    return {"status": "ok", "dimension": "navigator", "ai": False}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    log.info("Extension connected")

    try:
        while True:
            payload = await websocket.receive_json()

            user_text = (payload.get("text") or "").strip()
            page_url = payload.get("page_url") or ""
            page_title = payload.get("page_title") or ""

            log.info("Received message", text=user_text, page_url=page_url)

            if not user_text:
                await websocket.send_json({"reply": "(empty message ignored)"})
                continue

            # Run the no-AI graph for this single turn.
            result = await graph.ainvoke({
                "messages": [HumanMessage(content=user_text)],
                "page_url": page_url,
                "page_title": page_title,
            })

            # Pull the last AI message out as plain text.
            reply_text = "(no response)"
            for msg in reversed(result["messages"]):
                if hasattr(msg, "content") and msg.content:
                    reply_text = msg.content
                    break

            await websocket.send_json({"reply": reply_text})

    except WebSocketDisconnect:
        log.info("Extension disconnected")
    except Exception as e:
        log.exception("Bridge error")
        try:
            await websocket.send_json({"reply": f"Server error: {e}"})
        except Exception:
            pass
