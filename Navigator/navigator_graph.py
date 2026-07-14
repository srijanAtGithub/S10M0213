"""
navigator_graph.py
-----------------
The absolute minimum LangGraph graph for the Navigator dimension demo.

No LLM. No tools. No MCP.

One node ("echo_node") that takes the tab's full conversation so far
(state["messages"] — handed in by navigator_bridge.py's SessionStore on
every turn, not just the newest message), plus the current page context
(url/title) the extension sent along with it, and returns a plain text
response describing the website.

This exists purely to prove the plumbing end-to-end, memory included:

    extension popup (chat UI, tab-scoped)
        -> background.js            (tab lifecycle only)
        -> navigator_bridge.py       (WebSocket + REST, per-tab SessionStore)
        -> navigator_graph.py        (LangGraph, this file)
        -> ...back up the same chain to the popup

Because the bridge now passes in the accumulated history for that tab
instead of a single HumanMessage, NavigatorState.messages is genuinely
doing something: echo_node can see how many turns have happened in this
tab's session. Once this works, echo_node can be swapped for a real LLM +
real browser-control tools (click/type/read_dom/navigate) without touching
anything else in the chain — the state shape and the per-tab history
plumbing stay exactly as they are.
"""

import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, StateGraph


# ── State ──────────────────────────────────────────────────────────────
class NavigatorState(TypedDict):
    messages: Annotated[list, operator.add]
    # Context sent up from the popup on every turn.
    page_url: str
    page_title: str


# ── The one and only node ─────────────────────────────────────────────
def echo_node(state: NavigatorState) -> NavigatorState:
    """
    No AI. Builds a plain text reply out of whatever page info the
    extension handed us, plus a turn count derived from the *real*
    accumulated history for this tab — so we can confirm both the full
    round trip (popup -> backend -> popup) and per-tab memory actually
    work, before any LLM is involved.
    """
    messages = state["messages"]
    last_human = messages[-1]
    user_text = last_human.content if isinstance(last_human, HumanMessage) else ""

    turn_number = sum(1 for m in messages if isinstance(m, HumanMessage))

    url = state.get("page_url") or "(no url received)"
    title = state.get("page_title") or "(no title received)"

    reply_text = (
        f"[Turn {turn_number} in this tab's session]\n"
        f"You said: \"{user_text}\"\n\n"
        f"Current website:\n"
        f"  Title: {title}\n"
        f"  URL:   {url}"
    )

    return {"messages": [AIMessage(content=reply_text)]}


# ── Build the graph ────────────────────────────────────────────────────
def build_navigator_graph():
    """
    Simplest possible graph shape:

        echo_node -> END

    No router, no tool node, no conditional edges — there is nothing
    to branch on yet since there are no tools and no LLM. Memory across
    turns comes from the caller (navigator_bridge.py) passing in the full
    history each time, not from anything inside the graph itself.
    """
    graph = StateGraph(NavigatorState)
    graph.add_node("echo", echo_node)
    graph.set_entry_point("echo")
    graph.add_edge("echo", END)
    return graph.compile()
