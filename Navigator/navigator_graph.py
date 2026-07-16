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


def _build_turn_text(user_text: str, snippets: list[str]) -> str:
    """
    Folds any attached context snippets directly into THIS turn's human
    message, framed the same way a person would if they pasted text and
    then asked about it in the same breath.

    Why not the system prompt: putting snippets in the system prompt
    frames them as standing background material ("here's some reference
    info, keep it in mind"), not as the thing the user's very next word
    ("this", "that", "summarise this") refers to. Models — like people —
    resolve "this" against what's in front of them in the current turn,
    not against a pile of config-like context sitting above the whole
    conversation. Attaching it to the turn removes the ambiguity that
    was causing "what would you like me to summarize?" replies.
    """
    if not snippets:
        return user_text

    if len(snippets) == 1:
        attached_block = f'--- Attached content ---\n{snippets[0]}\n--- End attached content ---'
    else:
        parts = [f"[{i}] {s}" for i, s in enumerate(snippets, 1)]
        attached_block = "--- Attached content ---\n" + "\n\n".join(parts) + "\n--- End attached content ---"

    # The snippet(s) come first, then the user's own words — mirroring how
    # someone pastes something and then asks about it, so "this"/"it" in
    # the user's text has an unambiguous, immediately-preceding referent.
    return f"{attached_block}\n\n{user_text}"


# ── The Chat Node Logic ─────────────────────────────────────────────────
async def chat_node(state: NavigatorState) -> dict:
    llm = configuration.navigator_general_llm()

    url = state.get("page_url") or "Unknown"
    title = state.get("page_title") or "Unknown"
    snippets = state.get("context_snippets") or []

    # Fold this turn's attached snippets (if any) into the latest human
    # message, rather than parking them in the system prompt. The system
    # prompt stays generic/persona-only; page metadata is still useful
    # ambient context there since it's not something "this" ever refers to.
    messages = list(state["messages"])
    if snippets and messages and isinstance(messages[-1], HumanMessage):
        original_text = messages[-1].content
        messages[-1] = HumanMessage(
            content=_build_turn_text(original_text, snippets)
        )

    system_prompt = SystemMessage(
        content=(
            "You are a smart, premium, and highly capable assistant embedded in a browser side panel.\n\n"
            "When the user's message includes a block marked "
            "'--- Attached content ---', that block is text they just selected "
            "or dropped in specifically to ask you about — treat it as the "
            "direct subject of their message. If they say things like 'this', "
            "'that', 'summarise this', or ask a question with no other subject, "
            "resolve it against that attached content immediately; do not ask "
            "them to clarify what they mean.\n\n"
            "Provide helpful, concise, and insightful answers as plain text "
            "(no markdown formatting)."
        )
    )

    conversation = [system_prompt] + messages
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
