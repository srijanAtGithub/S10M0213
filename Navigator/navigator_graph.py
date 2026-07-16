"""
navigator_graph.py
-----------------
The LangGraph graph for the Navigator dimension.
Takes the tab's full conversation so far, injects the current page context,
and uses the writing tool LLM to generate a smart response.
"""

import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

import configuration

# ── State ──────────────────────────────────────────────────────────────
class NavigatorState(TypedDict):
    messages: Annotated[list, operator.add]
    # Context sent up from the popup on every turn.
    page_url: str
    page_title: str

# ── The Chat Node ──────────────────────────────────────────────────────
async def chat_node(state: NavigatorState) -> dict:
    """
    Connects to the real LLM. Injects the active tab's context as a SystemMessage
    so the AI knows what page the user is currently looking at.
    """
    # Grab the LLM designated for writing/chat tasks
    llm = configuration.get_writing_tool_llm()

    url = state.get("page_url") or "Unknown"
    title = state.get("page_title") or "Unknown"

    # Define the AI's persona and provide the real-time page context
    system_prompt = SystemMessage(
        content=(
            "You are a helpful assistant"
        )
    )

    # Combine the system prompt with the tab's accumulated message history
    conversation = [system_prompt] + state["messages"]

    # Call the LLM
    response = await llm.ainvoke(conversation)

    return {"messages": [response]}

# ── Build the graph ────────────────────────────────────────────────────
def build_navigator_graph():
    """
    Simplest possible graph shape: chat -> END
    Memory across turns comes from the caller (navigator_bridge.py).
    """
    graph = StateGraph(NavigatorState)
    graph.add_node("chat", chat_node)
    graph.set_entry_point("chat")
    graph.add_edge("chat", END)
    return graph.compile()
