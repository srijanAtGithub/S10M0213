# S10M0213 — State-Locked Autonomous Agent Framework & Resilient Tool Orchestrator

A production-grade, cost-efficient, and crash-resilient AI runtime powered by **LangGraph** and **Multi-Transport MCP**. Operating through an asynchronous **Telegram** interface, it can coordinate external tools, execute recurring tasks, maintain long-term memory, and persist workflow state across restarts.

Designed as a persistent personal agent, S10M0213 emphasizes efficient reasoning, safe automation, and operational reliability.

The system is built around five core principles:

- **Cost-efficient reasoning** — use specialized models, selective context retrieval, and multi-stage tool filtering to maximize accuracy while minimizing token consumption.
- **Scalable tool orchestration** — dynamically retrieve only the tools relevant to the current request, keeping reasoning focused even as the available toolset grows.
- **Persistent memory** — learn user preferences over time and inject only contextually relevant information into conversations.
- **Safe automation** — enforce human approval before executing potentially destructive actions.
- **Operational resilience** — preserve sessions, checkpoints, and scheduled workflows across downtime and infrastructure failures.

## Architecture Overview

<img src="Images/main_system_workflow.svg" alt="Main System Workflow" width="75%">

A message arrives via Telegram, passes through the FastAPI backend and session manager, then enters the LangGraph state graph. Inside the graph, the system prepares context (injecting relevant user preferences), fetches only the tools needed for this specific request, reasons with the main LLM, optionally pauses for human approval on destructive actions, executes tools, and sends back a response.

---

## Core Capabilities

### Handles Unlimited Tools Without Losing Focus

Most agents fall apart as you add more tools — the context window fills up, costs spike, and the model starts hallucinating wrong tool calls. This system solves that with a **two-stage retrieval pipeline** that filters tools *before* they ever reach the main LLM.

<img src="Images/two_stage_tool_retrieval.svg" alt="Two-Stage Tool Retrieval" width="75%">

**Stage 1 — Intent routing:** A cheap nano model reads the last few messages and decides which *services* are relevant right now (e.g. only Swiggy, not Gmail). Everything else is ignored entirely.

**Stage 2 — Semantic filtering:** Within each selected service, tool descriptions are compared to the query using cosine similarity. Only the most relevant tools per service are passed forward.

The result: the main LLM always sees a short, focused list of tools regardless of how many are registered. You can add so many more tools tomorrow from multiple servers and the model won't know or care about the ones that aren't relevant.

---

### Remembers You — And Gets Better Over Time

The agent builds a personal profile of you automatically. Every session, after you've been idle for some time, an evaluator LLM analyses the conversation and extracts stable behavioural patterns — things like ordering preferences, communication style, time habits, or how you like information presented.

<img src="Images/memory_and_personalization.svg" alt="Memory and Personalization" width="75%">

These are stored as a clean flat list in `preferences.md`. When preferences contradict each other (you said you prefer concise responses last month but now you clearly want detail), the merge step resolves the conflict and keeps only the newer version.

On every new session, only the preferences *relevant to your current request* are retrieved via semantic search and woven into the system prompt. You're not stuffing the context with everything — just what matters right now.

The agent's core personality and tone live separately in `Souls/{name}.md`, which can be swapped out entirely without touching any application logic.

---

### Never Does Anything Destructive Without Asking

Every tool call goes through a three-gate safety pipeline before execution.

<img src="Images/hitl_safety_pipeline.svg" alt="HITL Safety Pipeline" width="75%">

**Gate 1 — Prefix fast-path:** Tools starting with `get_`, `search_`, `read_` are immediately marked safe. No LLM call needed.

**Gate 2 — Heuristic detection:** Tools starting with `update_`, `delete_`, `send_` are flagged as unsafe automatically.

**Gate 3 — LLM safety net:** Anything ambiguous gets evaluated by a dedicated safety LLM that reads the tool description and the arguments being passed.

If a tool is flagged unsafe, the LangGraph graph *pauses* and asks you: approve, abort, or edit the arguments. Nothing happens until you decide. If a tool hallucinated by the model doesn't exist, the executor catches it cleanly and returns a `ToolMessage` saying "Tool not found" — no graph crashes, no cascading errors.

---

### Always On — Scheduled Tasks Run 24/7

The agent doesn't just respond to messages. It proactively executes tasks on a schedule, dispatching them into the main agent the same way a user message would be handled.

<img src="Images/recurring_task_engine.svg" alt="Recurring Task Engine" width="75%">

Tasks are defined in plain YAML — no code changes needed to add, modify, or disable them:

```yaml
- id: morning_news
  enabled: true
  task: "Summarize the top AI news headlines"
  schedule:
    mode: daily
    at: "08:00"
    days: [mon, tue, wed, thu, fri]

- id: email_check
  enabled: true
  task: "Check unread emails and flag anything urgent"
  schedule:
    mode: interval
    every: 30m
    days: [mon, tue, wed]
```

Each enabled task gets its own independent async loop. Schedules support daily execution at a specific time, fixed intervals (minutes or hours), and optional weekday filters.

---

### Sessions Survive Server Downtime

Sessions are persisted to SQLite and survive crashes, restarts, and planned downtime. On every boot, the system scans all stored sessions and reconciles them: sessions that expired while the server was down are cleaned up, and valid sessions have their idle timers reconstructed from where they left off.

The LangGraph checkpoint store lives in the same database, so the full conversation context is restored exactly where it was — no lost history, no cold-start on reconnect.

On unexpected shutdown, a 10-second graceful drain gives in-flight tasks time to settle their database commits before the process is force-killed.

---

### Keeps Context Sharp as Conversations Grow Long

Long conversations accumulate fast, especially with tool calls. Once the message history exceeds tokens threshold, the context trimmer kicks in.

<img src="Images/context_trimming.svg" alt="Context Trimming" width="75%">

It walks backward through the message history to find a safe cut point — specifically, it never splits an `AIMessage` (tool call) from its corresponding `ToolMessage` (result), because the OpenAI API rejects sequences where a tool call has no matching result. Everything before the cut is compressed into a single summary message. The active portion of the conversation stays intact.
