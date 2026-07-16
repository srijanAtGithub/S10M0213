from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.tools import tool

import configuration
configuration.load_config()

import structlog
from pydantic import BaseModel

log = structlog.get_logger()


# ── ORGANISE TABS DATA MODEL & TOOLS ───────────────────────────────────
class TabInfo(BaseModel):
    id: int
    title: str
    url: str
    groupId: int


class OrganiseTabsRequest(BaseModel):
    tabs: list[TabInfo]


class TabGroupPlan(BaseModel):
    title: str = Field(description="The logical category name for this group of tabs (e.g. 'Social Media', 'Research', 'Shopping', 'Docs'). Keep it short and descriptive.")
    color: str = Field(description="The color of the tab group. Must be one of: 'grey', 'blue', 'red', 'yellow', 'green', 'pink', 'purple', 'cyan', 'orange'.")
    tab_ids: list[int] = Field(description="The list of tab IDs that belong to this group.")


class TabGroupingPlan(BaseModel):
    groups: list[TabGroupPlan] = Field(description="List of tab groups to create.")


@tool(args_schema=TabGroupingPlan)
def organize_tabs_tool(groups: list[TabGroupPlan]) -> list[dict]:
    """
    Organises browser tabs into neat, categorized groups based on their topics, URLs, and titles.
    Use this tool to submit the final tab grouping plan.
    """
    return [g.model_dump() for g in groups]


# ── ORGANISE TABS LOGIC ─────────────────────────────────────────────
async def process_organise_tabs(tabs: list[TabInfo]) -> dict:
    """
    Organise the provided tabs using Sicily AI.
    Exposes a tool-enabled LLM call that groups browser tabs into cohesive categories.
    """
    llm = configuration.navigator_smart_llm()
    
    # Bind the tool to the LLM (and enforce its selection if supported)
    try:
        llm_with_tools = llm.bind_tools([organize_tabs_tool], tool_choice="organize_tabs_tool")
    except Exception:
        llm_with_tools = llm.bind_tools([organize_tabs_tool])
    
    system_msg = SystemMessage(
        content=(
            "You are an automated, programmatic browser assistant. "
            "Your sole task is to organize the user's browser tabs into logical groups. "
            "Examine the titles and URLs of the provided tabs, and cluster similar tabs together. "
            "For each group, pick a highly professional, **concise** title and a corresponding color. "
            "CRITICAL: You MUST call the `organize_tabs_tool` with the final grouping plan. "
            "Do NOT return conversational filler or explanations. Only execute the tool call."
        )
    )
    
    # Format the data cleanly for prompt processing
    tabs_data = [{"id": t.id, "title": t.title, "url": t.url, "groupId": t.groupId} for t in tabs]
    import json
    prompt_text = f"Please organize these active browser tabs:\n\n{json.dumps(tabs_data, indent=2)}"
    
    log.info("Organising tabs with LLM", tab_count=len(tabs))
    
    groups = []
    try:
        response = await llm_with_tools.ainvoke([system_msg, HumanMessage(content=prompt_text)])
        
        # 1. Extract grouping plan from tool calls
        if response.tool_calls:
            for tool_call in response.tool_calls:
                if tool_call["name"] == "organize_tabs_tool":
                    args = tool_call["args"]
                    if isinstance(args, dict):
                        groups = args.get("groups", args)
                    elif isinstance(args, list):
                        groups = args
                        
        # 2. Fallback: Parse response content as JSON if no tool calls were correctly captured
        if not groups and response.content:
            import re
            json_match = re.search(r"\{.*\}|\[.*\]", response.content, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group(0))
                if isinstance(parsed, dict):
                    groups = parsed.get("groups", parsed)
                elif isinstance(parsed, list):
                    groups = parsed
                    
    except Exception as e:
        log.error("Failed to organise tabs using LLM", error=str(e))
        return {"groups": []}
        
    log.info("Tab grouping plan generated", groups_count=len(groups))
    return {"groups": groups}
