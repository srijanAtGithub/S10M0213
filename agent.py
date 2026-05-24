import operator
import asyncio
import traceback
from datetime import datetime
from typing import TypedDict, Annotated, Literal

from pydantic import BaseModel

from langgraph.graph import StateGraph, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import MemorySaver

import tiktoken
from langchain_core.messages import (SystemMessage, HumanMessage, AIMessage, ToolMessage)

from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI

from memory_and_context import get_system_message
from tool_manager import ToolManager

# State Class
class AgentState(TypedDict):
    # All messages - HumanMessage, AI Message, ToolMessage
    messages: Annotated[list, operator.add]

    # ── human-in-the-loop patterns ───────────────────────────
    approval_log:      Annotated[list, operator.add]  # every interrupt event
    intent_log:        Annotated[list, operator.add]  # yes/no/edit classifications
    
    # ── tool execution patterns ──────────────────────────────
    tool_call_log:     Annotated[list, operator.add]  # all tool runs + outcomes
    tool_error_log:    Annotated[list, operator.add]  # not-found / failed tools


# Pydantic Schemas
class SafetyResult(BaseModel):
    is_safe: bool


class IntentResult(BaseModel):
    intent: Literal["yes", "no", "edit"]
    refined_instruction: str | None = None

tool_manager = ToolManager()

# globals
graph = None

# tokeniser helpers to count tokens
enc = tiktoken.get_encoding("cl100k_base")
TOKEN_THRESHOLD = 15_000

# Number of recent conversational "units" we try to preserve fresh.
# Actual preserved count may be slightly larger because we keep
# tool-call chains together to avoid corrupting message structure.
KEEP_LAST_MESSAGES = 6

# ─────────────────────────────────────────────────────────────
# Initialize agent
# ─────────────────────────────────────────────────────────────
async def initialize_agent():

    global graph

    # main_llm = ChatOpenAI(model="gpt-5.4-mini").bind_tools(tools, parallel_tool_calls=False)
    safety_llm = ChatOpenAI(model="gpt-5.4-nano").with_structured_output(SafetyResult, include_raw=False)
    intent_llm = ChatOpenAI(model="gpt-5.4-nano").with_structured_output(IntentResult, include_raw=False)
    summarizer_llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite", temperature=0)

    # ─────────────────────────────────────────────────────────
    # Nodes
    # ─────────────────────────────────────────────────────────
    async def main_node(state: AgentState) -> AgentState:
        """
        PRIMARY AGENT NODE
        ------------------
        Main entry point for the agent's reasoning loop.

        Responsibilities:
        - Trims/summarizes conversation history if needed.
        - Invokes the main LLM with the current message history.
        - Handles two possible outcomes:

            1. No tool calls → Returns a plain AIMessage (final reply).
            Router will forward it to the evaluator.

            2. Tool call detected → 
            - Attaches the AIMessage (with tool_call payload) to the state.
            - Performs an inline safety classification using safety_llm.
            - Stores the safety result in `response.additional_kwargs["safe"]`.
        
        Returns the updated state for the router to process.
        """

        MAIN_LLM_SOUL = get_system_message("main_llm")

        # ── Two-stage tool retrieval ──────────────────────────────────────
        # Stage 1: router LLM picks which servers are needed
        # Stage 2: within-server embedding filter picks top tools per server
        retrieval_context = " ".join(
            m.content
            for m in state["messages"][-8:]
            if isinstance(m, (HumanMessage, AIMessage)) and isinstance(m.content, str)
        )

        relevant_servers = await tool_manager.get_relevant_servers(state["messages"])
        relevant_tools   = await tool_manager.get_tools_for_servers(
            servers=relevant_servers,
            query=retrieval_context,
            top_k_per_server=12,
        )

        # Fallback: conversational message or router returned nothing
        if not relevant_tools:
            relevant_tools = tool_manager.all_tools

        # Rebind with the new tools
        main_llm = ChatOpenAI(model="gpt-5.4-mini").bind_tools(relevant_tools, parallel_tool_calls=False)
        try:
            trimmed_messages = await maybe_summarize(state["messages"], summarizer_llm)
            response = await main_llm.ainvoke([
                SystemMessage(content=MAIN_LLM_SOUL),
                *trimmed_messages
            ])
        except Exception as e:
            return {
                "messages": [AIMessage(content=f"LLM error: {str(e)}")]
            }

        if not response.tool_calls:
            return {"messages": [response]}

        tool_call = response.tool_calls[0]
        tool_name  = tool_call["name"]

        tool_map  = {e.tool.name: e.tool for e in tool_manager._registry}
        tool_obj  = tool_map.get(tool_name)
        tool_desc = tool_obj.description if tool_obj else "No description available"

        # ── Safety fast-path ─────────────────────────────────────────────
        # Classify by name pattern first. Only call the safety LLM for
        # genuinely ambiguous tool names. This avoids misclassifying
        # read-only tools (search_*, get_*) as unsafe, and keeps latency
        # low regardless of which MCPs are connected.
        
        READ_ONLY_PREFIXES = (
            "get_", "search_", "fetch_", "find_", "list_",
            "track_", "browse_", "view_", "read_", "show_",
        )
        KNOWN_WRITE_PREFIXES = (
            "update_", "create_", "delete_", "remove_", "add_",
            "send_", "post_", "submit_", "place_", "clear_",
            "flush_", "apply_", "set_", "edit_", "schedule_",
        )

        if any(tool_name.startswith(p) for p in READ_ONLY_PREFIXES):
            is_safe = True

        elif any(tool_name.startswith(p) for p in KNOWN_WRITE_PREFIXES):
            is_safe = False

        else:
            SAFETY_LLM_SOUL = get_system_message("safety_llm")
            safety_result = await safety_llm.ainvoke([
                SystemMessage(content=SAFETY_LLM_SOUL),
                HumanMessage(content=(
                    f"Tool name: {tool_name}\n"
                    f"Tool description: {tool_desc}\n"
                    f"Args being passed: {tool_call['args']}\n\n"
                    "Is this safe to auto-execute without asking the user?"
                ))
            ])
            is_safe = safety_result.is_safe

        response.additional_kwargs["safe"] = is_safe
        return {"messages": [response]}


    async def human_approval_node(state: AgentState) -> AgentState:
        """
        HUMAN-IN-THE-LOOP APPROVAL NODE
        -------------------------------
        Interrupts execution when the agent wants to call a tool that failed the 
        automatic safety check. Presents the proposed tool call to the user for approval.

        Flow:
        1. Extracts the pending tool call from the last AIMessage.
        2. Triggers an interrupt to get user input.
        3. Uses intent_llm to classify the user's reply (yes / no / edit).
        4. Handles each case:
            - "yes"  → Returns empty messages (router proceeds to tool execution).
            - "no"   → Adds a ToolMessage indicating rejection.
            - "edit" → Adds a ToolMessage with the user's requested changes.

        Returns the updated state so the router can route accordingly.
        """
        
        last      = state["messages"][-1]
        tool_call = last.tool_calls[0]

        user_reply = interrupt(
            f"⚠️  Agent wants to call `{tool_call['name']}`\n"
            f"📦 Args: {tool_call['args']}\n\n"
            f"Go ahead? Reply with 'yes', 'no', or give new instructions."
        )

        intent_result = await intent_llm.ainvoke([
            SystemMessage(
                content=(
                    "Classify the user's reply to a tool-call approval prompt into yes/no/edit.\n"
                    "IMPORTANT: If the user agrees or confirms — even with extra words like "
                    "'yes please go ahead', 'yeah do it', 'sure remove it', 'yes that's fine' — classify as 'yes'.\n"
                    "Only classify as 'edit' if the user wants to modify the requested action or its parameters before execution.\n"
                    "Only classify as 'no' if the user explicitly cancels or refuses.\n"
                    "If 'edit', populate refined_instruction with what needs to change."
                )
            ),
            HumanMessage(
                content=f"User replied: '{user_reply}'"
            )
        ])

        # ── Populate approval logs ─────────────────────────────
        approval_entry = {
            "tool_name": tool_call["name"],
            "args": tool_call["args"],
            "user_reply": user_reply,
            "timestamp": datetime.utcnow().isoformat()
        }

        intent_entry = {
            "tool_name": tool_call["name"],
            "intent": intent_result.intent,
            "refined_instruction": intent_result.refined_instruction
        }

        if intent_result.intent == "yes":
            return {
                "messages": [],
                "approval_log": [approval_entry],
                "intent_log": [intent_entry]
            }

        content = (
            f"Tool call rejected by user. Reason: {user_reply}"
            if intent_result.intent == "no"
            else f"Tool call not executed. User wants changes: {intent_result.refined_instruction}"
        )
        return {
            "messages": [ToolMessage(tool_call_id=tool_call["id"], content=content)],
            "approval_log": [approval_entry],
            "intent_log": [intent_entry]
        }


    async def tool_executor_node(state: AgentState) -> AgentState:
        """Executes the tool call using whatever is currently in the registry."""
        last = state["messages"][-1]
        tool_call = last.tool_calls[0]

        # Look up the tool by name from the live registry
        tool_map = {e.tool.name: e.tool for e in tool_manager._registry}
        tool = tool_map.get(tool_call["name"])

        if not tool:
            timestamp = datetime.utcnow().isoformat()
            error_entry = {
                "tool_name": tool_call["name"],
                "args": tool_call["args"],
                "error": "Tool not found",
                "timestamp": timestamp
            }
            tool_call_entry = {
                "tool_name": tool_call["name"],
                "args": tool_call["args"],
                "status": "not_found",
                "timestamp": timestamp
            }

            return {
                "messages": [ToolMessage(
                    tool_call_id=tool_call["id"],
                    content=f"Tool '{tool_call['name']}' not found. The connector may not be loaded."
                )],
                "tool_call_log": [tool_call_entry],
                "tool_error_log": [error_entry]
            }

        # ── Base tool execution log ────────────────────────────
        tool_call_entry = {
            "tool_name": tool_call["name"],
            "args": tool_call["args"],
            "status": "pending",
            "timestamp": datetime.utcnow().isoformat()
        }

        try:
            # BEFORE EXECUTION
            print(f"\n🔧 Executing tool: {tool_call['name']}")
            print(f"📦 Args: {tool_call['args']}")

            result = await asyncio.wait_for(tool.ainvoke(tool_call["args"]), timeout=45)

            # AFTER EXECUTION
            print(f"✅ Tool result: {str(result)[:500]}\n")

            tool_call_entry["status"] = "success"
            tool_call_entry["result"] = str(result)[:500]

            return {
                "messages": [
                    ToolMessage(
                        tool_call_id=tool_call["id"],
                        content=str(result)
                    )
                ],
                "tool_call_log": [tool_call_entry]
            } 

        except Exception as e:
            traceback.print_exc()

            tool_call_entry["status"] = "failed"
            tool_call_entry["error"] = str(e)

            error_entry = {
                "tool_name": tool_call["name"],
                "args": tool_call["args"],
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }

            return {
                "messages": [
                    ToolMessage(
                        tool_call_id=tool_call["id"],
                        content=f"Tool execution failed.\nError: {str(e)}"
                    )
                ],
                "tool_call_log": [tool_call_entry],
                "tool_error_log": [error_entry]
            }
        

    # ─────────────────────────────────────────────────────────
    # Routers
    # ─────────────────────────────────────────────────────────
    def route_from_main(state: AgentState):
        last = state["messages"][-1]
        if not last.tool_calls:
            return END
        # safety result was stored as a flag inside the AIMessage by main_node
        if last.additional_kwargs.get("safe"):
            return "tools"
        return "human_approval"

    def route_from_approval(state: AgentState):
        last = state["messages"][-1]
        # if last message is a ToolMessage, it means NO or EDIT was handled → back to main
        if isinstance(last, ToolMessage):
            return "main_node"
        # otherwise intent was YES → execute the tool
        return "tools"

    # ─────────────────────────────────────────────────────────
    # Build graph
    # ─────────────────────────────────────────────────────────
    builder = StateGraph(AgentState)

    builder.add_node("main_node",      main_node)
    builder.add_node("tools", tool_executor_node)
    builder.add_node("human_approval", human_approval_node)

    builder.set_entry_point("main_node")

    builder.add_conditional_edges("main_node", route_from_main)
    builder.add_conditional_edges("human_approval", route_from_approval)

    builder.add_edge("tools", "main_node")

    graph = builder.compile(
        checkpointer=MemorySaver()
    )

    print("✅ LangGraph initialized")


# Send message
async def send(message: str, thread_id: str, status_callback=None):

    config = {"configurable": {"thread_id": thread_id}}

    print(f"─── Thread: {thread_id} ───")

    try:
        state = graph.get_state(config)

        is_interrupted = (
            bool(state.next)
            and bool(state.tasks)
            and bool(state.tasks[0].interrupts)
        )

        # AUTO RESUME DETECTION
        if is_interrupted:
            print(f"↩️ Resuming: {message}")
            async for event in graph.astream_events(Command(resume=message), config, version="v2"):
                if event["event"] == "on_tool_start" and status_callback:
                    await status_callback(event.get("name", ""))
            log_latest_message(config)
        else:
            print(f"👤 User: {message}")
            async for event in graph.astream_events({"messages": [HumanMessage(content=message)]}, config, version="v2"):
                if event["event"] == "on_tool_start" and status_callback:
                    await status_callback(event.get("name", ""))
            log_latest_message(config)

    except Exception as e:
        print(f"❌ Graph execution failed: {e}")

        # IMPORTANT: Reset corrupted thread state by starting fresh
        return {
            "reply": (
                "Something went wrong while processing the previous tool call. "
                "Please try again."
            ),
            "interrupt": None,
        }

    # Extract latest response
    messages = graph.get_state(config).values.get("messages", [])

    latest_ai_message = None

    for msg in reversed(messages):

        if isinstance(msg, AIMessage) and msg.content:
            latest_ai_message = msg.content
            break

    # Interrupt handling
    state = graph.get_state(config)

    interrupt_message = None

    if (state.next and state.tasks and state.tasks[0].interrupts):
        interrupt_message = state.tasks[0].interrupts[0].value

    return {"reply": latest_ai_message, "interrupt": interrupt_message}


def count_tokens(messages) -> int:
    total = 0
    for msg in messages:
        # Count content
        content = getattr(msg, "content", "")
        if isinstance(content, str):
            total += len(enc.encode(content))
        elif isinstance(content, list):
            # Some tool results come back as list of content blocks
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    total += len(enc.encode(block["text"]))

        # Count tool call payloads (missed entirely before)
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            for tc in tool_calls:
                tc_text = f"{tc.get('name', '')} {str(tc.get('args', ''))}"
                total += len(enc.encode(tc_text))

    return total


def message_to_text(m) -> str:

    msg_type = type(m).__name__
    content = getattr(m, "content", "")

    # Tool calls
    if hasattr(m, "tool_calls") and m.tool_calls:

        tool_parts = []

        for tc in m.tool_calls:
            tool_parts.append(
                f"[Tool Call: {tc['name']} | Args: {tc['args']}]"
            )

        return f"{msg_type}: {' '.join(tool_parts)}"

    return f"{msg_type}: {content}"


def get_safe_fresh_messages(messages, keep_last=KEEP_LAST_MESSAGES):

    """
    Returns a structurally-safe "fresh" tail of messages.

    Why this exists:
    ----------------
    Tool-calling conversations have strict structure rules:

        AIMessage(tool_call)
            ↓
        ToolMessage(result)

    These pairs MUST stay together.

    If summarization cuts between them, OpenAI/LangGraph
    can throw errors like:

        "assistant message with tool_calls must be followed
         by tool messages"

    So instead of blindly slicing the last N messages,
    we walk backwards carefully and preserve complete
    tool interaction chains.
    """

    if len(messages) <= keep_last:
        return messages

    fresh = []
    i = len(messages) - 1

    # Walk backwards through history
    while i >= 0 and len(fresh) < keep_last:

        msg = messages[i]

        # Always include current message
        fresh.append(msg)

        # ---------------------------------------------------------
        # CASE 1: ToolMessage found.
        # Preserve preceding AIMessage(tool_call)
        # ---------------------------------------------------------
        if type(msg).__name__ == "ToolMessage":

            if i - 1 >= 0:

                prev_msg = messages[i - 1]

                has_tool_calls = (hasattr(prev_msg, "tool_calls") and bool(prev_msg.tool_calls))
                if has_tool_calls:
                    fresh.append(prev_msg)
                    i -= 1

        # ---------------------------------------------------------
        # CASE 2: AIMessage(tool_call) found.
        # Preserve following ToolMessage.
        # (Rare edge case protection.)
        # ---------------------------------------------------------
        elif (hasattr(msg, "tool_calls") and bool(msg.tool_calls)):
            if i + 1 < len(messages):

                next_msg = messages[i + 1]

                if type(next_msg).__name__ == "ToolMessage":

                    # Avoid duplicate append
                    if next_msg not in fresh:
                        fresh.append(next_msg)

        i -= 1

    # We walked backwards, so reverse to restore chronology
    fresh.reverse()

    return fresh


async def maybe_summarize(messages, summarizer_llm):

    token_count = count_tokens(messages)

    print(f"🧠 Context tokens: {token_count}")

    if token_count < TOKEN_THRESHOLD:
        return messages

    print("📝 Summarizing old conversation history...")

    # ---------------------------------------------------------
    # Preserve a structurally-safe recent window.
    #
    # IMPORTANT:
    # We do NOT blindly slice the last N messages,
    # because that can split:
    #
    #   AI tool call
    #   Tool result
    #
    # which corrupts conversation structure.
    # ---------------------------------------------------------
    fresh = get_safe_fresh_messages(messages, KEEP_LAST_MESSAGES)
    to_summarize = messages[:-len(fresh)]

    history_text = "\n".join(
        message_to_text(m)
        for m in to_summarize
    )

    summary = await summarizer_llm.ainvoke([
        SystemMessage(content=(
            """
            Summarize the conversation briefly while preserving:
            - important context
            - user preferences
            - tool results
            - pending tasks
            - decisions and constraints

            Avoid unnecessary details.
            """
        )),
        HumanMessage(content=history_text)
    ])

    summary_message = SystemMessage(
        content=(
            "[Conversation Summary]\n"
            f"{summary.content}"
        )
    )

    return [summary_message] + fresh


def log_latest_message(config):
    messages = graph.get_state(config).values.get("messages", [])

    if not messages:
        return

    last = messages[-1]

    msg_type = type(last).__name__

    print(f"\n🤖 [{msg_type}]")

    # AI tool calls
    if hasattr(last, "tool_calls") and last.tool_calls:
        for tc in last.tool_calls:
            print(f"🔧 Tool Call: {tc['name']}")
            print(f"📦 Args: {tc['args']}")

    # Normal content
    if getattr(last, "content", None):
        print(f"💬 {last.content}\n")
