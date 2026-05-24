import numpy as np
from dataclasses import dataclass, field
from pydantic import BaseModel
from langchain_core.tools import BaseTool
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage


# Pydantic schema for router output
class ServerSelection(BaseModel):
    server_names: list[str]


# ToolEntry
@dataclass
class ToolEntry:
    tool: BaseTool
    server: str
    embedding: np.ndarray = field(default=None, repr=False)


# ToolManager
class ToolManager:

    # One tool per server shown to main LLM when no query context available.
    # When query IS available, within-server embedding filter picks better.
    DEFAULT_TOOLS_PER_SERVER = 10

    def __init__(self):
        self._registry: list[ToolEntry] = []
        self._server_descriptions: dict[str, str] = {}
        self._embedder = OpenAIEmbeddings(model="text-embedding-3-small")
        self._router   = ChatOpenAI(model="gpt-5.4-nano", temperature=0).with_structured_output(ServerSelection, include_raw=False)
        self._describer  = ChatOpenAI(model="gpt-5.4-nano", temperature=0)

    # ── Registration ─────────────────────────────────────────
    async def register(self, tools: list[BaseTool], server: str, server_description: str | None = None):
        if any(e.server == server for e in self._registry):
            print(f"⚠️  [{server}] already loaded — skipping")
            return

        # Auto-generate description from tool descriptions if not provided
        if not server_description:
            server_description = await self._generate_server_description(server, tools)

        self._server_descriptions[server] = server_description

        # Embed all tool descriptions for within-server filtering later
        tool_descriptions = [f"{t.name}: {t.description}" for t in tools]
        embeddings = await self._embedder.aembed_documents(tool_descriptions)

        for tool, emb in zip(tools, embeddings):
            self._registry.append(ToolEntry(
                tool=tool,
                server=server,
                embedding=np.array(emb),
            ))

        print(f"✅ Registered {len(tools)} tools from [{server}]")
        for tool in tools:
            print(f"   🔧 {tool.name}")
            # print(f"{tool.description}")


    def unregister(self, server: str):
        before = len(self._registry)
        self._registry      = [e for e in self._registry if e.server != server]
        self._server_descriptions.pop(server, None)
        print(f"🗑️  Unregistered [{server}] — removed {before - len(self._registry)} tools")


    @property
    def loaded_servers(self) -> list[str]:
        return list(self._server_descriptions.keys())

    @property
    def all_tools(self) -> list[BaseTool]:
        return [e.tool for e in self._registry]

    
    async def _generate_server_description(self, server: str, tools: list[BaseTool]) -> str:
        """
        Auto-generates a one-line server description from tool descriptions.
        Called at registration time — runs once, zero ongoing maintenance.
        """
        tool_summary = "\n".join(
            f"- {t.name}: {t.description}"
            for t in tools
        )
        try:
            result = await self._describer.ainvoke([
                SystemMessage(content=(
                    "You are writing a description of a service for an AI router.\n"
                    "The router reads this description to decide whether this service "
                    "is needed for a given user request.\n\n"
                    "Write a clear, comprehensive description covering:\n"
                    "- What this service is for (the main purpose)\n"
                    "- What kinds of user requests it handles\n"
                    "- What actions it can perform\n"
                    "- What it explicitly does NOT handle (if relevant)\n\n"
                    "Be thorough enough that the router can confidently include or "
                    "exclude this service. Use plain English. No bullet points — "
                    "write in flowing prose. 3-5 sentences is ideal."
                )),
                HumanMessage(content=(
                    f"Service name: {server}\n\n"
                    f"Tools available:\n{tool_summary}\n\n"
                    "Describe this service."
                ))
            ])
            # Router returns ServerSelection, but we need raw text here
            # Use a separate simple LLM call for this
            return result.content if hasattr(result, 'content') else str(result)
        except Exception:
            # Fallback: join tool names
            return f"Service with tools: {', '.join(t.name for t in tools)}"


    # ── Stage 1: Server routing ───────────────────────────────
    async def get_relevant_servers(self, messages: list) -> list[str]:
        """
        Single cheap LLM call that reads the conversation and returns
        which servers are needed right now.

        Returns [] for pure conversation (greetings, small talk).
        Returns one or more server names for operational requests.
        Falls back to all servers on error — never crashes the agent.
        """
        if not self._server_descriptions:
            return []

        server_list = "\n".join(
            f"- {server}: {desc}"
            for server, desc in self._server_descriptions.items()
        )

        recent = "\n".join(
            f"{type(m).__name__}: {m.content}"
            for m in messages[-8:]
            if isinstance(m, (HumanMessage, AIMessage))
            and isinstance(m.content, str)
        )

        try:
            result = await self._router.ainvoke([
                SystemMessage(content=(
                    "You are a service router for an AI assistant.\n"
                    "Given a conversation, return the names of services "
                    "needed to respond to the user's CURRENT request.\n\n"
                    "Rules:\n"
                    "- Only include services where an action or data lookup "
                    "is genuinely needed RIGHT NOW\n"
                    "- Ignore services mentioned only as past context or "
                    "in passing\n"
                    "- Return an empty list for pure conversation: greetings, "
                    "thank-yous, general questions that need no external data\n"
                    "- Return only names exactly as they appear in the list\n"
                    "- When unsure between one or two services, include both"
                )),
                HumanMessage(content=(
                    f"Available services:\n{server_list}\n\n"
                    f"Conversation (most recent last):\n{recent}\n\n"
                    "Which services are needed to respond right now?"
                ))
            ])

            valid = set(self._server_descriptions.keys())
            selected = [s for s in result.server_names if s in valid]

            print(f"🌐 Relevant servers: {selected or '(none — conversational)'}")
            return selected

        except Exception as e:
            # Non-fatal — fall back to all servers
            print(f"⚠️  Server router failed ({e}) — falling back to all servers")
            return list(self._server_descriptions.keys())


    # ── Stage 2: Within-server tool filtering ────────────────
    async def get_tools_for_servers(self, servers: list[str], query: str | None = None, top_k_per_server: int = DEFAULT_TOOLS_PER_SERVER) -> list[BaseTool]:
        """
        Returns tools from the selected servers.

        If query is provided, uses embedding similarity within each server
        to return the top_k_per_server most relevant tools.

        If no query or top_k_per_server >= server tool count,
        returns all tools from those servers — no filtering.
        """
        if not servers:
            return []

        result: list[BaseTool] = []

        for server in servers:
            server_entries = [e for e in self._registry if e.server == server]

            if not server_entries:
                continue

            # If we have a query AND filtering would actually reduce the list
            if query and len(server_entries) > top_k_per_server:
                query_emb = np.array(await self._embedder.aembed_query(query))

                scores = []
                for entry in server_entries:
                    cosine = float(
                        np.dot(query_emb, entry.embedding)
                        / (np.linalg.norm(query_emb) * np.linalg.norm(entry.embedding) + 1e-9)
                    )
                    scores.append((cosine, entry))

                scores.sort(reverse=True, key=lambda x: x[0])
                result.extend(entry.tool for _, entry in scores[:top_k_per_server])

            else:
                # Server has fewer tools than top_k, or no query — take all
                result.extend(e.tool for e in server_entries)

        print(f"🔍 Selected tools ({len(result)}): {[t.name for t in result]}")
        return result
