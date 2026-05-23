from langchain_core.messages import (
    BaseMessage,
    SystemMessage,
    HumanMessage,
)
from langchain_google_genai import ChatGoogleGenerativeAI
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
SOUL_DIR = BASE_DIR / "SOULS"
CONTEXT_DIR = BASE_DIR / "CONTEXT"

PREFERENCES_FILE = CONTEXT_DIR / "preferences.md"

eval_llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite", temperature=0)


async def run_evaluator(thread_id: str, messages: list[BaseMessage]) -> None:
    """
    Called once a session ends.

    Steps:
    1. Extract session preferences
    2. Load existing preferences.md
    3. Merge intelligently
    4. Save updated preferences.md
    """

    if not messages:
        print("📊 Evaluator skipped — no messages in session.\n")
        return

    # Serialise messages to plain text so the LLM can read them. Keeping only the Human and AI message and avoiding the tool chatter
    allowed_types = {"HumanMessage", "AIMessage"}

    conversation_text = "\n".join(
        f"[{type(msg).__name__}] {msg.content}"
        for msg in messages
        if type(msg).__name__ in allowed_types
        and getattr(msg, "content", "")
    )

    if not conversation_text.strip():
        print("📊 Evaluator skipped — all messages were empty.\n")
        return

    # STEP 1: Extract session prefs
    EVAL_LLM_SOUL = get_system_message("eval_llm")

    try:
        extraction_result = await eval_llm.ainvoke([
            SystemMessage(content=EVAL_LLM_SOUL),
            HumanMessage(
                content=(
                    f"Full session conversation:\n\n{conversation_text}\n\n"
                    "Extract the user's preferences and behaviour patterns. Return the result in clean Markdown format."
                )
            ),
        ])
    except Exception as e:
        print(f"❌ Preference extraction failed: {e}")
        return

    session_preferences = extraction_result.content.strip()

    print("\n📊 Session Preferences Extracted:\n")
    print()

    # STEP 2: Load existing prefs
    existing_preferences = load_preferences_file()

    # If this is the first memory write or if preferences is empty, skip merge step entirely
    if not existing_preferences.strip():
        save_to_preferences_file(session_preferences)

        print("✅ preferences.md created.\n")
        return

    # STEP 3: Merge intelligently
    try:
        merged_preferences = await merge_preferences(
            existing_preferences=existing_preferences,
            new_preferences=session_preferences,
        )
    except Exception as e:
        print(f"❌ Preference merge failed: {e}")
        return

    # STEP 4: Save
    save_to_preferences_file(merged_preferences)

    print("✅ preferences.md updated.\n")

async def merge_preferences(existing_preferences: str, new_preferences: str) -> str:
    """
    Uses the LLM to intelligently merge old + new preferences.

    Goals:
    - avoid duplicates
    - preserve stable long-term preferences
    - update changed preferences if needed
    - keep concise markdown structure
    """

    merge_prompt = f"""
    You are maintaining a long-term user preference memory.

    You are given:

    1. Existing stored preferences
    2. Newly extracted session preferences

    Your task:
    - merge them intelligently
    - remove duplicates
    - preserve useful long-term preferences
    - avoid repetitive wording
    - keep the memory concise
    - keep everything in clean Markdown
    - do not invent information
    - if new information contradicts old information, prefer newer information
    - do not store temporary objectives or one-time requests
    - avoid storing information that is unlikely to matter in future sessions
    - prefer stable behavioural patterns over transient context

    Return ONLY the final merged Markdown.

    # Existing Preferences

    {existing_preferences}

    # New Session Preferences

    {new_preferences}
    """

    result = await eval_llm.ainvoke([
        SystemMessage(
            content="You are a memory consolidation system."
        ),
        HumanMessage(content=merge_prompt),
    ])

    return result.content.strip()


def load_preferences_file() -> str:
    """
    Loads existing preferences.md content.
    """

    if not PREFERENCES_FILE.exists():
        return ""

    return PREFERENCES_FILE.read_text(encoding="utf-8").strip()


def save_to_preferences_file(content: str) -> None:
    """
    Saves merged preferences into preferences.md
    """

    CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

    PREFERENCES_FILE.write_text(content.strip() + "\n", encoding="utf-8")


def get_system_message(name: str) -> str:
    """
    Loads and returns the contents of:
    SOULS/<name>.md
    """

    file_path = SOUL_DIR / f"{name}.md"

    if not file_path.exists():
        return "You are a helpful AI assistant."

    return file_path.read_text(encoding="utf-8")
