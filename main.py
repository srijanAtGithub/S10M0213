import os
import uuid
import time
import asyncio
import swiggy_auth

from dotenv import load_dotenv
from dataclasses import dataclass, field
from datetime import datetime

load_dotenv()

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from telegram import Update, BotCommand
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

from agent import initialize_agent, send, graph, tool_manager
from memory_and_context import run_evaluator
from connectors import CONNECTORS

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Telegram globals
active_chat_id: int | None = None
telegram_app: Application | None = None

# OAuth future
_oauth_code_future = None

# Session Management
IDLE_MINUTES = 5

@dataclass
class UserSession:
    thread_id: str
    user_name: str
    started_at: float           = field(default_factory=time.time)
    last_interaction_at: float  = field(default_factory=time.time)
    expiry_task: asyncio.Task | None = field(default=None, repr=False)


# key = telegram user_id (str)
_sessions: dict[str, UserSession] = {}


def format_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


async def expire_session_after_timeout(user_id: str, thread_id: str, user_name: str):

    try:

        await asyncio.sleep(IDLE_MINUTES * 60)

        # ── Guard: session might have already been replaced ───────────────────
        session = _sessions.get(user_id)

        if session is None or session.thread_id != thread_id:
            return

        # ── Print expiry header ───────────────────────────────────────────────
        print(
            f"\n⏰ SESSION EXPIRED"
            f"\n👤 User      : {user_name} ({user_id})"
            f"\n🧵 Thread ID : {thread_id}"
            f"\n🕒 Started   : {format_time(session.started_at)}"
            f"\n🕒 Last msg  : {format_time(session.last_interaction_at)}\n"
        )

        # ── Fetch full message history from LangGraph ─────────────────────────
        config = {"configurable": {"thread_id": thread_id}}
        state  = graph.get_state(config)
        messages = state.values.get("messages", [])

        if messages:

            print("📜 SESSION MESSAGE HISTORY:\n")

            for msg in messages:
                msg_type = type(msg).__name__
                content  = getattr(msg, "content", "") or ""
                if content:
                    # truncate very long tool results so the log stays readable
                    display = content if len(content) <= 300 else content[:300] + "…"
                    print(f"  [{msg_type}] {display}\n")

        else:
            print("📜 No messages in this session.\n")

        # ── Run evaluator on session messages ─────────────────────────────────
        await run_evaluator(thread_id, messages)

        # ── Clean up ──────────────────────────────────────────────────────────
        del _sessions[user_id]
        print("🗑️  Session removed from store.\n")

    except asyncio.CancelledError:
        # Normal — user sent a new message, timer was reset
        print(f"🔄 Session timer reset for user {user_id} ({user_name})")


# Session store logic
# get_or_create_session  →  always returns a valid (thread_id, is_new) pair
# The expiry task is created/reset here by the async caller (on_telegram_message)
def get_or_create_session(user_id: str, user_name: str) -> tuple[str, bool]:
    """
    Returns (thread_id, is_new_session).

    Does NOT create expiry tasks — that's the async caller's job,
    because create_task must be called from an async context.

    With the active-expiry design, idle-timeout rotation is handled
    automatically by expire_session_after_timeout, so we only need
    two cases here:
      1. No session exists yet → create one.
      2. Session exists and is still active → return it.
    """

    existing = _sessions.get(user_id)

    if existing is None:
        session = UserSession(
            thread_id=str(uuid.uuid4()),
            user_name=user_name,
        )
        _sessions[user_id] = session
        return session.thread_id, True

    # Session exists — expiry task is already running (or was reset).
    # Update last_interaction_at so the expiry guard can use it for logging.
    existing.last_interaction_at = time.time()
    return existing.thread_id, False


# ──────────────────────────────────────────────────────────────────────────────
# Telegram handler
# ──────────────────────────────────────────────────────────────────────────────
async def on_telegram_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_chat_id

    active_chat_id = update.effective_chat.id

    user      = update.effective_user
    user_id   = str(user.id)
    user_name = user.first_name

    text      = update.message.text or ""

    # ── Session: get or create ────────────────────────────────────────────────
    session_id, is_new_session = get_or_create_session(user_id, user_name)
    session = _sessions[user_id]

    if is_new_session:
        # First message — start the expiry countdown
        session.expiry_task = asyncio.create_task(
            expire_session_after_timeout(user_id, session_id, user_name)
        )
        print(
            f"\n🆕 NEW SESSION"
            f"\n👤 User      : {user_name} ({user_id})"
            f"\n🧵 Session ID: {session_id}"
            f"\n🕒 Started   : {format_time(session.started_at)}\n"
        )

    else:
        # Returning message — cancel old timer, restart it fresh
        if session.expiry_task and not session.expiry_task.done():
            session.expiry_task.cancel()

        session.expiry_task = asyncio.create_task(
            expire_session_after_timeout(user_id, session_id, user_name)
        )

    print(
        f"\n📨 [Telegram Message]"
        f"\n👤 User      : {user_name} ({user_id})"
        f"\n🧵 Session ID: {session_id}"
        f"\n🕒 Since     : {format_time(session.started_at)}"
        f"\n💬 Message   : {text}\n",
        flush=True
    )

    # ── Send to LangGraph ─────────────────────────────────────────────────────
    try:
        result = await send(text, session_id)

        if result["interrupt"]:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=result["interrupt"]
            )
        elif result["reply"]:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=result["reply"]
            )

    except Exception as e:
        print(f"❌ Error in send(): {e}", flush=True)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Something went wrong. Please try again."
        )

# ──────────────────────────────────────────────────────────────────────────────
# TELEGRAM COMMANDS EXECUTORS
# ──────────────────────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Hello! I'm Maple. How can I help?")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    session = _sessions.get(user_id)
    if session:
        await update.message.reply_text(
            f"🧵 Session ID: {session.thread_id[:8]}...\n"
            f"🕒 Started: {format_time(session.started_at)}\n"
            f"💬 Last msg: {format_time(session.last_interaction_at)}"
        )
    else:
        await update.message.reply_text("No active session.")


async def connect_swiggy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loaded = tool_manager.loaded_servers
    if "swiggy-food" in loaded or "swiggy-instamart" in loaded:
        await update.message.reply_text("⚠️ Swiggy is already connected.")
        return
    await update.message.reply_text("⏳ Connecting Swiggy...")
    await CONNECTORS["swiggy"](tool_manager)
    await update.message.reply_text("✅ Swiggy connected! Food and Instamart tools are ready.")


async def disconnect_swiggy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tool_manager.unregister("swiggy-food")
    tool_manager.unregister("swiggy-instamart")
    await update.message.reply_text("🗑️ Swiggy disconnected. All Swiggy tools unloaded.")


async def connectors_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    available = "\n".join(f"• {name}" for name in CONNECTORS.keys())

    await update.message.reply_text(
        f"📦 Available connectors:\n\n"
        f"{available}\n\n"
        f"Use /connect_swiggy to connect."
    )


async def loaded_connectors_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loaded = tool_manager.loaded_servers
    if not loaded:
        await update.message.reply_text("🔌 No connectors loaded currently.")
        return
    text = "\n".join(f"  • {s}" for s in loaded)
    await update.message.reply_text(f"🔌 Loaded connectors:\n{text}")

# ──────────────────────────────────────────────────────────────────────────────
# FastAPI lifespan
# ──────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_app

    telegram_app = Application.builder().token(TOKEN).build()

    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("status", status_command))
    telegram_app.add_handler(CommandHandler("connectors",       connectors_command))
    telegram_app.add_handler(CommandHandler("loaded_connectors", loaded_connectors_command))
    telegram_app.add_handler(CommandHandler("connect_swiggy",    connect_swiggy_command))
    telegram_app.add_handler(CommandHandler("disconnect_swiggy", disconnect_swiggy_command))

    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_telegram_message))

    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.bot.set_my_commands([
        BotCommand("start",             "Start the bot"),
        BotCommand("status",            "Show your session info"),
        BotCommand("connectors",        "Show available connectors"),
        BotCommand("loaded_connectors", "Show currently loaded connectors"),
        BotCommand("connect_swiggy",    "Connect Swiggy (food + instamart)"),
        BotCommand("disconnect_swiggy", "Disconnect Swiggy tools"),
    ])
    await telegram_app.updater.start_polling()

    print("🤖 Telegram bot is running...")

    # Do NOT await this here, or startup deadlocks.
    asyncio.create_task(initialize_agent())

    yield

    # ── Cleanup: cancel all pending expiry tasks on shutdown ──────────────────
    for uid, session in _sessions.items():
        if session.expiry_task and not session.expiry_task.done():
            session.expiry_task.cancel()

    await telegram_app.updater.stop()
    await telegram_app.stop()
    await telegram_app.shutdown()


# ──────────────────────────────────────────────────────────────────────────────
# FastAPI app
# ──────────────────────────────────────────────────────────────────────────────
app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    return {
        "status": "running",
        "chat_id": active_chat_id,
        "active_sessions": len(_sessions)
    }


@app.get("/callback")
async def oauth_callback(code: str, state: str | None = None):
    print("✅ OAuth callback received", flush=True)

    if swiggy_auth._oauth_code_future and not swiggy_auth._oauth_code_future.done():
        swiggy_auth._oauth_code_future.set_result(code)

    return {
        "status": "success",
        "message": "OAuth completed. You can close this tab."
    }


@app.post("/send")
async def send_to_telegram(text: str):
    """
    Send message to the last active Telegram chat.
    """

    if active_chat_id is None:
        return {"error": "No active chat yet — send a message from Telegram first"}

    await telegram_app.bot.send_message(chat_id=active_chat_id, text=text)

    print(f"✉️ [Sent to Telegram]: {text}")

    return {"status": "sent", "text": text}


# Entry
def main():
    print("S10M0213 started successfully.")
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
