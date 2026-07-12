"""
browser_graph.py
-----------------
The absolute minimum LangGraph graph for the Navigator dimension demo.

No LLM. No tools. No MCP.

One node ("echo_node") that takes whatever the user typed, plus the
current page context (url/title) the extension sent along with it,
and returns a plain text response describing the website.

This exists purely to prove the plumbing end-to-end:

    extension popup (chat UI)
        -> background.js
        -> content_script.js  (reads window.location / document.title)
        -> browser_bridge.py  (WebSocket, FastAPI)
        -> browser_graph.py   (LangGraph, this file)
        -> ...back up the same chain to the popup

Once this works, main_node can be swapped for a real LLM + real
browser-control tools (click/type/read_dom/navigate) without touching
anything else in the chain.
"""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import StateGraph, END
from langchain_core.messages import AIMessage, HumanMessage


# ── State ──────────────────────────────────────────────────────────────
class BrowserState(TypedDict):
    messages: Annotated[list, operator.add]
    # Context sent up from the content script on every turn.
    page_url: str
    page_title: str


# ── The one and only node ─────────────────────────────────────────────
def echo_node(state: BrowserState) -> BrowserState:
    """
    No AI. Just builds a plain text reply out of whatever page info
    the extension handed us, so we can confirm the full round trip
    (popup -> content script -> backend -> popup) actually works.
    """
    last_human = state["messages"][-1]
    user_text = last_human.content if isinstance(last_human, HumanMessage) else ""

    url = state.get("page_url") or "(no url received)"
    title = state.get("page_title") or "(no title received)"

    reply_text = (
        f"You said: \"{user_text}\"\n\n"
        f"Current website:\n"
        f"  Title: {title}\n"
        f"  URL:   {url}"
    )

    return {"messages": [AIMessage(content=reply_text)]}


# ── Build the graph ────────────────────────────────────────────────────
def build_browser_graph():
    """
    Simplest possible graph shape:

        echo_node -> END

    No router, no tool node, no conditional edges — there is nothing
    to branch on yet since there are no tools and no LLM.
    """
    graph = StateGraph(BrowserState)
    graph.add_node("echo", echo_node)
    graph.set_entry_point("echo")
    graph.add_edge("echo", END)
    return graph.compile()
