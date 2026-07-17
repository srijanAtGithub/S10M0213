from pydantic import BaseModel
from langchain_core.messages import SystemMessage, HumanMessage
import configuration
import structlog

log = structlog.get_logger()

# ── SUMMARISE PAGE DATA MODELS ──────────────────────────────────────────
class SummarisePageRequest(BaseModel):
    url: str
    title: str
    content: str

class SummarisePageResponse(BaseModel):
    summary: str

# ── SUMMARISE PAGE LOGIC ───────────────────────────────────────────────
async def process_summarise_page(req: SummarisePageRequest) -> SummarisePageResponse:
    """
    Summarise the provided page content using a cost-effective, fast LLM.
    """
    llm = configuration.navigator_general_llm()
    
    system_msg = SystemMessage(
        content=(
            "You are a helpful browser assistant. Your sole task is to summarize the web page content provided by the user. "
            "Extract the core thesis and main points efficiently. "
            "CRITICAL: Output standard, plain text ONLY. Do NOT use markdown, bolding, lists, headers, or conversational filler."
        )
    )
    
    human_msg = HumanMessage(content=f"Title: {req.title}\n\nContent:\n{req.content}")
    
    log.info("Summarising page", url=req.url, content_length=len(req.content))
    
    try:
        response = await llm.ainvoke([system_msg, human_msg])
        summary_text = response.content.strip()
    except Exception as e:
        log.error("Failed to summarise page", error=str(e))
        summary_text = "Failed to generate summary."
        
    return SummarisePageResponse(summary=summary_text)