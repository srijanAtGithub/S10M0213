"""
local_session.py
----------------
A self-contained terminal chat session for `sicily start`.

Completely independent of:
  - Telegram
  - FastAPI / uvicorn
  - session_store (SQLite)
  - recurring tasks

Uses:
  - LangGraph (same as the main agent, but a fresh minimal graph)
  - local_tools.py  (sandboxed file tools)
  - configuration.py (reuses your existing LLM setup)
  - memory_and_context.get_system_message  (reuses your Soul files)
"""

import operator
import uuid
from pathlib import Path
from typing import Annotated, TypedDict

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

import configuration
from local_tools import LOCAL_TOOLS, set_sandbox_root
from memory_and_context import get_system_message

log = structlog.get_logger()

BANNER = """
╔══════════════════════════════════════════╗
║           Sicily  —  Local Mode          ║
║  Files are sandboxed to this directory.  ║
║  Type  exit  or  quit  to leave.         ║
╚══════════════════════════════════════════╝
"""


# ── Agent state ───────────────────────────────────────────────────────────────

class LocalState(TypedDict):
    messages: Annotated[list, operator.add]


# ── Build a minimal LangGraph for local use ───────────────────────────────────

def build_local_graph():
    """
    A simple ReAct-style graph:

        main_node  →  (tool calls?)  →  tool_node  →  main_node
                   ↘  (no tool calls) → END
    """
    main_llm = configuration.get_main_llm(tools=LOCAL_TOOLS)

    async def main_node(state: LocalState) -> LocalState:
        soul = get_system_message("main_llm")
        sandbox_notice = (
            f"\n\n---\n"
            f"# Local File Access\n"
            f"You have access to the user's local directory via sandboxed tools.\n"
            f"You can list immediate files/folders, show a full file tree, and get file info.\n"
            f"You CANNOT read file contents, write, delete, or modify any files.\n"
            f"All paths you pass to tools must be relative paths.\n"
        )
        response = await main_llm.ainvoke([
            SystemMessage(content=soul + sandbox_notice),
            *state["messages"],
        ])
        return {"messages": [response]}

    def router(state: LocalState):
        last = state["messages"][-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            return "tools"
        return END

    tool_node = ToolNode(LOCAL_TOOLS)

    graph = StateGraph(LocalState)
    graph.add_node("main", main_node)
    graph.add_node("tools", tool_node)

    graph.set_entry_point("main")
    graph.add_conditional_edges("main", router, {"tools": "tools", END: END})
    graph.add_edge("tools", "main")

    return graph.compile()


# ── Terminal I/O helpers ──────────────────────────────────────────────────────

def print_ai(text: str):
    print(f"\nSicily:  {text}\n")


def print_info(text: str):
    print(f"    {text}")


# ── Main session loop ─────────────────────────────────────────────────────────

async def run_local_session():
    """Entry point called by `sicily start` in cli.py."""

    # 1. Lock the sandbox to wherever the command was run from
    cwd = Path.cwd().resolve()
    set_sandbox_root(cwd)

    print(BANNER)
    print_info(f"Sandbox root: {cwd}")
    print_info(f"Tools available: list_files, file_tree, file_info")
    print()

    # 2. Build the graph (no persistence needed for local sessions)
    graph = build_local_graph()
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    messages: list = []

    # 3. Chat loop
    while True:
        try:
            user_input = input(">: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nGoodbye! 👋")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "bye"):
            print("\nGoodbye! 👋")
            break

        messages.append(HumanMessage(content=user_input))

        try:
            result = await graph.ainvoke({"messages": messages}, config)
            messages = result["messages"]

            # Find the last AI text response
            reply = None
            for msg in reversed(messages):
                if isinstance(msg, AIMessage) and msg.content:
                    reply = msg.content
                    break

            if reply:
                print_ai(reply)
            else:
                print_ai("(No response)")

        except Exception as e:
            log.exception("Local session error")
            print_ai(f"Something went wrong: {e}")