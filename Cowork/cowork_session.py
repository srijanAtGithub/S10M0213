"""
cowork_session.py
----------------
A self-contained terminal chat session for `sicily start`.

Completely independent of:
  - Telegram
  - FastAPI / uvicorn
  - session_store (SQLite)
  - recurring tasks

Uses:
  - LangGraph (same as the main agent, but a fresh minimal graph)
  - cowork_tools.py  (sandboxed file tools)
  - configuration.py (reuses your existing LLM setup)
  - memory_and_context.get_system_message  (reuses your Soul files)

Use from:
  - uv build
  - uv pip install dist/sicily-0.2.3-py3-none-any.whl
  - .venv\Scripts\activate // source .venv/bin/activate
  - sicily start
"""

from pathlib import Path

def _load_settings():
    """Load configuration for local session."""
    from configuration import load_config
    load_config()

_load_settings()

import asyncio
import operator
import uuid
from pathlib import Path
from typing import Annotated, TypedDict

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

import configuration

# Initialize the rich console for styling
console = Console()
from Cowork.cowork_tools import LOCAL_TOOLS, set_sandbox_root, get_friendly_tool_message
from agent import maybe_summarize

log = structlog.get_logger()

BANNER = """
╔═════════ Sicily Cowork v2.4.4 ════════════╦══════════════ Capabilities ════════════════╗
║                                           ║                                            ║
║                                           ║  - Read & Parse Text, PDF, Word, Excel.    ║
║  Files are sandboxed to this directory.   ║  - Inspect File Trees & Metadata           ║
║      Type  exit/quit  to leave.           ║  - Create Text Files & Directories         ║
║                                           ║  - Strictly Safe: No Overwrites            ║
║                                           ║                                            ║
╚═══════════════════════════════════════════╩════════════════════════════════════════════╝
"""
LOCAL_TOKEN_THRESHOLD = 5_000

summarizer_llm = configuration.get_summarizer_llm()

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

        system_message = """
            You are Sicily, a local filesystem assistant with access to the user's files through specialized tools.
            Your role is to investigate the filesystem, inspect relevant files, and answer based on evidence rather than assumptions. 
            When information may exist in the user's files, use tools to verify it before responding. 
            Be thorough, accurate, and transparent about what you found and where you found it.
            """

        sandbox_notice = """
            ---
            # Filesystem Access

            You have sandboxed access to the user's local directory. All paths must be RELATIVE — \
            never use absolute paths.

            ## Reading files
            - Before reading any file in full, use `head=50` first to understand its structure \
            and purpose. Only read the complete file when the question genuinely requires it \
            (e.g. "summarise everything", "find all occurrences of X").
            - For questions about what a file does, its structure, or its purpose — the first \
            50 lines are almost always sufficient.
            - Chain reads as needed. Never guess when you can verify.

            ## Writing files
            - You may CREATE new files (`create_text_file`) and new directories (`make_directory`).
            - You CANNOT overwrite, edit, or delete anything that already exists.
            - Always confirm with the user before creating files unless explicitly asked to do so.

            ## General rules
            - Never fabricate file contents. If you haven't read it, say so.
            - If a tool call fails, report the error exactly — do not paper over it.
            """

        trimmed_messages = await maybe_summarize(
            state["messages"], 
            summarizer_llm, 
            token_threshold=LOCAL_TOKEN_THRESHOLD, 
            show_log=False
        )
        response = await main_llm.ainvoke([
            SystemMessage(content=system_message + sandbox_notice),
            *trimmed_messages,
        ])
        return {"messages": [response]}

    def router(state: LocalState):
        last = state["messages"][-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            return "tools"
        return END

    tool_node = ToolNode(LOCAL_TOOLS, handle_tool_errors=True)

    graph = StateGraph(LocalState)
    graph.add_node("main", main_node)
    graph.add_node("tools", tool_node)

    graph.set_entry_point("main")
    graph.add_conditional_edges("main", router, {"tools": "tools", END: END})
    graph.add_edge("tools", "main")

    return graph.compile()


# Main session loop
async def run_local_session():
    """Entry point called by `sicily start` in cli.py."""

    # 1. Lock the sandbox to wherever the command was run from
    cwd = Path.cwd().resolve()
    set_sandbox_root(cwd)

    console.print(f"[bold dark_orange]{BANNER}[/bold dark_orange]")
    print_info(f"Sandbox root: {cwd}")
    console.print()

    # initialise RAG index
    from Cowork.cowork_rag import SicilyRAG, set_rag

    rag = SicilyRAG(cwd)
    with console.status("[grey50]Indexing files...[/grey50]", spinner="dots", spinner_style="dim"):
        loop    = asyncio.get_event_loop()
        summary = await loop.run_in_executor(None, rag.index_session)
    set_rag(rag)

    print_info(
        f"Index ready — "
        f"{summary['indexed']} file(s) indexed, "
        f"{summary['skipped']} unchanged, "
        f"{summary['deleted']} removed, "
        f"{summary['failed']} failed"
    )
    console.print()
    # end RAG init

    # 2. Build the graph (no persistence needed for local sessions)
    graph = build_local_graph()
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    messages: list = []

    # 3. Chat loop
    while True:
        try:
            user_input = console.input("[white]>>>:[/white] ").strip()
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
            # 1. Start the rich status spinner
            with console.status("[grey50]Thinking...[/grey50]", spinner="dots", spinner_style="dim") as status:
                
                root_run_id = None
                
                # 2. Stream events to catch on_tool_start
                async for event in graph.astream_events({"messages": messages}, config, version="v2"):
                    
                    # Track the root graph run to capture the final output later
                    if root_run_id is None:
                        root_run_id = event.get("run_id")

                    # 3. Intercept tool execution 
                    if event["event"] == "on_tool_start":
                        tool_name = event.get("name")
                        tool_args = event.get("data", {}).get("input", {})
                        
                        # Format the payload for your helper function
                        tool_call = {"name": tool_name, "args": tool_args}
                        msg = get_friendly_tool_message(tool_call)
                        
                        # Update the terminal spinner with the new message
                        status.update(f"[grey50]{msg}...[/grey50]")

                    elif event["event"] == "on_tool_error":
                        tool_name = event.get("name", "tool")
                        error = event.get("data", {}).get("error", "unknown error")
                        status.update(f"[yellow]{tool_name} hit an error: {error}[/yellow]")
                    
                    # 4. Capture the final state when the main graph finishes
                    elif event["event"] == "on_chain_end" and event.get("run_id") == root_run_id:
                        output = event.get("data", {}).get("output")
                        if output and "messages" in output:
                            messages = output["messages"]

            # 5. Find the last AI text response
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
def print_ai(text: str):
    # Renders the text as Markdown inside a styled box
    md = Markdown(text)
    panel = Panel(md, title="[grey50]Sicily[/grey50]", border_style="grey50", padding=(1, 2), title_align="left")
    console.print()
    console.print(panel)
    console.print()

def print_info(text: str):
    console.print(f"[dim italic]    {text}[/dim italic]")
