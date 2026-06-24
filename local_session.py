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

Use from:
  - uv build
  - uv pip install dist/sicily-0.2.3-py3-none-any.whl
  - .venv\Scripts\activate
  - sicily start
"""

import operator
import uuid
from pathlib import Path
from typing import Annotated, TypedDict

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

import sys
import itertools
import asyncio

import configuration
from local_tools import LOCAL_TOOLS, set_sandbox_root

log = structlog.get_logger()

BANNER = """
╔══════════════════════════════════════════╗
║           Sicily  —  Local Mode          ║
║  Files are sandboxed to this directory.  ║
║      Type  exit  or  quit  to leave.     ║
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

        system_message = (
            """
            You are Sicily, a local assistant with read-only access to the user's files through specialized tools.
            Your role is to investigate the filesystem, inspect relevant files, and answer based on evidence rather than assumptions. 
            When information may exist in the user's files, use tools to verify it before responding. 
            Be thorough, accurate, and transparent about what you found and where you found it.
            """
        )

        sandbox_notice = (
            f"\n\n---\n"
            f"# Filesystem Access\n"
            f"You have READ-ONLY access to the user's local directory via the tools provided.\n"
            f"- All paths must be RELATIVE. Never use absolute paths.\n"
            f"- You CANNOT write, delete, or modify any files.\n"
            f"- Always use your tools to answer questions. Never guess or give up early.\n"
            f"- Chain as many tool calls as needed to give a complete, definitive answer.\n"
        )
        response = await main_llm.ainvoke([
            SystemMessage(content=system_message + sandbox_notice),
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
    print_info(f"Tools available: read_text_file, list_directory, file_tree, search_files, get_file_info, list_allowed_directories")
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
            print("\n\nGoodbye!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "bye"):
            print("\nGoodbye!")
            break

        messages.append(HumanMessage(content=user_input))

        try:
            # Start spinner
            spinner_task = asyncio.create_task(_spinner())

            result = await graph.ainvoke({"messages": messages}, config)
            messages = result["messages"]

            # Stop spinner
            spinner_task.cancel()
            try:
                await spinner_task
            except asyncio.CancelledError:
                pass

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


# ── Terminal I/O helpers ──────────────────────────────────────────────────────
async def _spinner(message: str = "Thinking"):
    """Displays a spinning cursor in the terminal."""
    spinner_chars = itertools.cycle(['-', '\\', '|', '/'])
    # spinner_chars = itertools.cycle(['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'])
    try:
        while True:
            # \r moves the cursor back to the start of the line
            sys.stdout.write(f"\r{message} {next(spinner_chars)}")
            sys.stdout.flush()
            await asyncio.sleep(0.2)
    except asyncio.CancelledError:
        # Clear the line cleanly when the task is cancelled
        sys.stdout.write("\r" + " " * (len(message) + 3) + "\r")
        sys.stdout.flush()
        raise