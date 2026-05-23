import numpy as np
from dataclasses import dataclass, field
from langchain_core.tools import BaseTool
from langchain_openai import OpenAIEmbeddings
from langchain_core.messages import HumanMessage, AIMessage

@dataclass
class ToolEntry:
    tool: BaseTool
    server: str                  # "swiggy-food", "swiggy-instamart", etc.
    tags: list[str]              # coarse categories: ["cart", "food", "order"]
    embedding: np.ndarray = field(default=None, repr=False)

class ToolManager:
    def __init__(self):
        self._registry: list[ToolEntry] = []
        self._embedder = OpenAIEmbeddings(model="text-embedding-3-small")


    async def register(self, tools: list[BaseTool], server: str, tags: list[str]):
        
        # Prevent double-loading the same server
        if any(e.server == server for e in self._registry):
            print(f"⚠️  [{server}] already loaded — skipping")
            return

        descriptions = [f"{t.name}: {t.description}" for t in tools]
        embeddings = await self._embedder.aembed_documents(descriptions)

        for tool, emb in zip(tools, embeddings):
            self._registry.append(ToolEntry(
                tool=tool,
                server=server,
                tags=tags,
                embedding=np.array(emb),
            ))
        print(f"✅ Registered {len(tools)} tools from [{server}]")
        # for tool in tools:
        #     print(f"   🔧 {tool.name}")


    def unregister(self, server: str):
        before = len(self._registry)
        self._registry = [e for e in self._registry if e.server != server]
        print(f"🗑️  Unregistered [{server}] — removed {before - len(self._registry)} tools")

    @property
    def loaded_servers(self) -> list[str]:
        return list({e.server for e in self._registry})


    def infer_hint_tags(self, messages: list) -> list[str]:
        """
        Dynamically infer which server domains are relevant to the current
        conversation by matching recent message text against registered tags
        and server name tokens.

        Fully generic — works for any MCP added in the future.
        No hardcoded service names anywhere.
        """
        recent_text = " ".join(
            m.content.lower()
            for m in messages[-6:]
            if isinstance(m, (HumanMessage, AIMessage)) and isinstance(m.content, str)
        )

        matched_tags: set[str] = set()

        for entry in self._registry:

            # Match against registered tags (e.g. "grocery", "calendar", "email")
            for tag in entry.tags:
                if tag.lower() in recent_text:
                    matched_tags.update(entry.tags)
                    break

            # Also match against server name tokens
            # e.g. "swiggy-instamart" → ["swiggy", "instamart"]
            # e.g. "google-calendar"  → ["google", "calendar"]
            server_tokens = entry.server.lower().replace("-", " ").replace("_", " ").split()
            for token in server_tokens:
                if len(token) > 3 and token in recent_text:  # skip short tokens like "by", "io"
                    matched_tags.update(entry.tags)
                    break

        return list(matched_tags)


    async def get_relevant_tools(self, query: str, top_k: int = 12, always_include: list[str] = None, 
        hint_tags: list[str] = None, tag_boost: float = 0.15) -> list[BaseTool]:
        
        """
        Retrieve tools most relevant to this query.
        hint_tags boosts tools whose registered tags overlap,
        helping pick the right domain when multiple MCPs are loaded.
        """

        query_emb = np.array(await self._embedder.aembed_query(query))

        scores = []
        for entry in self._registry:
            cosine = float(
                np.dot(query_emb, entry.embedding)
                / (np.linalg.norm(query_emb) * np.linalg.norm(entry.embedding) + 1e-9)
            )

            boost = 0.0
            if hint_tags:
                overlap = len(set(hint_tags) & set(entry.tags))
                boost = tag_boost * overlap

            scores.append((cosine + boost, entry))

        scores.sort(reverse=True, key=lambda x: x[0])
        selected = {e.tool.name: e.tool for _, e in scores[:top_k]}

        if always_include:
            for entry in self._registry:
                if entry.tool.name in always_include:
                    selected[entry.tool.name] = entry.tool

        print(f"🔍 Selected tools: {list(selected.keys())}")
        return list(selected.values())

    @property
    def all_tools(self) -> list[BaseTool]:
        return [e.tool for e in self._registry]
