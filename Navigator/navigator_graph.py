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

# ── State Declaration Update ───────────────────────────────────────────
class NavigatorState(TypedDict):
    messages: Annotated[list, operator.add]
    page_url: str
    page_title: str
    context_snippets: list[str] # Add the snippets parameter to the state array


# ── The Chat Node Logic ─────────────────────────────────────────────────
async def chat_node(state: NavigatorState) -> dict:
    llm = configuration.get_writing_tool_llm()

    url = state.get("page_url") or "Unknown"
    title = state.get("page_title") or "Unknown"
    snippets = state.get("context_snippets") or []

    # Map selected snippets into a clean, itemized list format
    snippets_block = ""
    if snippets:
        snippets_block = "\n\nCRITICAL REFERENCE CONTEXT:\nThe user has highlighted and attached the following relevant snippets from the webpage to ground your reply:\n"
        for i, snippet in enumerate(snippets, 1):
            snippets_block += f'[{i}] "{snippet}"\n'

    system_prompt = SystemMessage(
        content=(
            "You are a smart, premium, and highly capable assistant.\n"
            f"{snippets_block}\n\n"
            "Provide helpful, concise, and insightful answers. Format your responses cleanly using markdown."
        )
    )

    conversation = [system_prompt] + state["messages"]
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
