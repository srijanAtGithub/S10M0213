import os
import uuid
import time
import asyncio
import swiggy_auth

from dotenv import load_dotenv
from dataclasses import dataclass, field
from datetime import datetime
import traceback
import json
from pathlib import Path

load_dotenv()

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from telegram import Update, BotCommand
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

import agent as agent_module
from agent import initialize_agent, send, tool_manager
from configuration import TOOL_LABELS
from memory_and_context import run_evaluator
from RECURRING_TASKS.recurring_tasks import start_recurring_tasks, set_dispatch
from connectors import CONNECTORS
from session_store import init_db, load_all_sessions, load_session, save_session, delete_session

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID_FILE = Path(".chat_id")

# Telegram globals
active_chat_id: int | None = None
telegram_app: Application | None = None

# OAuth future
_oauth_code_future = None

# Session Management
IDLE_MINUTES = 5
GRACEFUL_DRAIN_TIMEOUT = 10  # seconds to wait for in-flight tasks before force-cancel

@dataclass
class UserSession:
    session_id: str
    user_name: str
    chat_id: int | None = field(default=None)

    started_at: float           = field(default_factory=time.time)
    last_interaction_at: float  = field(default_factory=time.time)
    expiry_task: asyncio.Task | None = field(default=None, repr=False)

    # ── Concurrency control ───────────────────────────────────
    is_processing: bool                 = field(default=False)
    cancel_requested: bool              = field(default=False)
    active_task: asyncio.Task | None    = field(default=None, repr=False)


# key = telegram user_id (str)
_sessions: dict[str, UserSession] = {}


def format_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


async def expire_session_after_timeout(user_id: str, session_id: str, user_name: str, override_seconds: float | None = None):

    try:
        sleep_duration = override_seconds if override_seconds is not None else IDLE_MINUTES * 60
        await asyncio.sleep(sleep_duration)
    except asyncio.CancelledError:
        print(f"🔄 Session timer reset for user {user_id} ({user_name})")
        return

    # ── Guard: session might have already been replaced ───────────────────
    session = _sessions.get(user_id)

    if session is None or session.session_id != session_id:
        return

    # ── Print expiry header ───────────────────────────────────────────────
    print(
        f"\n⏰ SESSION EXPIRED"
        f"\n👤 User      : {user_name} ({user_id})"
        f"\n🧵 Session ID : {session_id}"
        f"\n🕒 Started   : {format_time(session.started_at)}"
        f"\n🕒 Last msg  : {format_time(session.last_interaction_at)}\n"
    )

    try:
        # ── Fetch full message history from LangGraph ─────────────────────────
        config = {"configurable": {"thread_id": session_id}} # "thread_id" inside configurable is LangGraph's internal contract

        # Accessing the live graph via the module, not the stale imported None
        current_graph = agent_module.graph
        if current_graph is None:
            print("⚠️  Graph not yet initialized — skipping evaluator.")
            return

        state = await current_graph.aget_state(config)
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
        await run_evaluator(session.session_id, messages)

    except Exception as e:
        print(f"❌ Error during session expiry for {user_id}: {e}", flush=True)
        traceback.print_exc()  

    finally:
        _sessions.pop(user_id, None)
        await delete_session(user_id)
        print("🗑️  Session removed from store.\n")


# Session store logic
# get_or_create_session  →  always returns a valid (session_id, is_new) pair
# The expiry task is created/reset here by the async caller (on_telegram_message)
async def get_or_create_session(user_id: str, user_name: str) -> tuple[str, bool]:
    """
    Returns (session_id, is_new_session).

    On first call after a restart, loads the persisted session from SQLite
    so LangGraph can resume from the same thread_id (= session_id).

    Does NOT create expiry tasks — that's the async caller's job,
    because create_task must be called from an async context.

    With the active-expiry design, idle-timeout rotation is handled
    automatically by expire_session_after_timeout, so we only need
    two cases here:
      1. No session exists yet → create one.
      2. Session exists and is still active → return it.
    """

    existing = _sessions.get(user_id)
 
    if existing is not None:
        # Already in memory — just refresh the timestamp.
        existing.last_interaction_at = time.time()
        return existing.session_id, False
 
    # Not in memory — check the DB.
    persisted = await load_session(user_id)
 
    if persisted is not None:
        elapsed = time.time() - persisted.last_interaction_at
        remaining = (IDLE_MINUTES * 60) - elapsed

        # Expired during downtime — delete and treat as new session
        if remaining <= 0:
            await delete_session(user_id)
            print(
                f"\n🗑️ SESSION EXPIRED DURING DOWNTIME"
                f"\n👤 User      : {user_name} ({user_id})"
                f"\n🧵 Session ID: {persisted.session_id[:8]}...\n"
            )
        else:
            # Still valid — restore it
            session = UserSession(
                session_id=persisted.session_id,
                user_name=persisted.user_name,
                started_at=persisted.started_at,
                last_interaction_at=time.time(),
            )
            _sessions[user_id] = session
            print(
                f"\n♻️ RESTORED SESSION"
                f"\n👤 User      : {user_name} ({user_id})"
                f"\n🧵 Session ID: {persisted.session_id[:8]}...\n"
            )
            return session.session_id, False

    # Genuinely new user — create a fresh session (existing code below, unchanged)
    session = UserSession(
        session_id=str(uuid.uuid4()),
        user_name=user_name,
    )
    _sessions[user_id] = session
    return session.session_id, True


# ──────────────────────────────────────────────────────────────────────────────
# Telegram handler
# ──────────────────────────────────────────────────────────────────────────────
async def on_telegram_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    active_chat_id = update.effective_chat.id

    user      = update.effective_user
    user_id   = str(user.id)
    user_name = user.first_name
    text      = update.message.text or ""

    # ── Session: get or create ────────────────────────────────────────────────
    session_id, is_new_session = await get_or_create_session(user_id, user_name)
    session = _sessions[user_id]
    session.chat_id = update.effective_chat.id

    # Persist after every interaction (updates last_interaction_at)
    await save_session(user_id, session)

    # ── REJECT-WHILE-BUSY ─────────────────────────────────────────────────────
    # If a response is already being generated for this user, don't start
    # another LangGraph run. Tell them to wait or use /stop.
    if session.is_processing:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                "⏳ I'm still working on your previous message.\n\n"
                "Please wait for it to finish — or send /stop if you'd like to cancel it."
            )
        )
        return

    # ── Reset cancel flag from any previous /stop ─────────────────────────────
    # Must happen before we set is_processing, so a stale cancel_requested
    # from a prior session doesn't immediately abort this new request.
    session.cancel_requested = False

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

    # ── Mark busy ─────────────────────────────────────────────────────────────
    session.is_processing = True

    # ── Sending the user meaning and helper messages before final response ────
    thinking_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="⏳ Thinking..."
    )

    async def status_callback(tool_name: str):
        label = TOOL_LABELS.get(tool_name, f"🔧 Running {tool_name}...")
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=thinking_msg.message_id,
                text=label
            )
        except Exception:
            pass

    # ── Wrap send() in a Task so /stop can cancel it ──────────────────────────
    async def _run_send():
        return await send(
            text,
            session_id,
            status_callback=status_callback,
            cancel_check=lambda: session.cancel_requested,
        )
 
    task = asyncio.create_task(_run_send())
    session.active_task = task

    # ── Send to LangGraph ─────────────────────────────────────────────────────
    try:
        result = await task

        # Task completed normally — only reply if not cancelled.
        # (If cancel was requested mid-run, send() returns None result;
        #  we just silently drop it per spec.)
        if not session.cancel_requested:
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
    except asyncio.CancelledError:
        # /stop fired — task was cancelled externally. Say nothing. Do nothing.
        print(f"🛑 Task cancelled for user {user_name} ({user_id})")

    except Exception as e:
        print(f"❌ Error in send(): {e}", flush=True)
        if not session.cancel_requested:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Something went wrong. Please try again."
            )

    finally:
        # clearing processing state, regardless of how we got here.
        session.is_processing = False
        session.active_task = None

        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=thinking_msg.message_id
            )
        except Exception:
            pass

# ──────────────────────────────────────────────────────────────────────────────
# TELEGRAM COMMANDS EXECUTORS
# ──────────────────────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_chat_id

    active_chat_id = update.effective_chat.id

    if not CHAT_ID_FILE.exists():
        CHAT_ID_FILE.write_text(json.dumps({"chat_id": active_chat_id}))
        print(f"💾 Registered chat_id: {active_chat_id} for user {update.effective_user.first_name}")
        await update.message.reply_text(
            "👋 Hi! I'm S10M0213. You're all set up.."
        )
    else:
        await update.message.reply_text("👋 Hey! Already registered. Ready to go.")


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Cancel whatever is currently processing for this user.
    No reply to the user — just stop everything silently.
    """
    user_id = str(update.effective_user.id)
    session = _sessions.get(user_id)
 
    if session is None or not session.is_processing:
        # Nothing running — silently do nothing.
        return
 
    print(
        f"\n🛑 /stop received"
        f"\n👤 User : {session.user_name} ({user_id})"
        f"\n🧵 Session: {session.session_id}\n"
    )
 
    # Signal the cancel_check lambda inside send()
    session.cancel_requested = True
 
    # Cancel the asyncio Task wrapping send()
    if session.active_task and not session.active_task.done():
        session.active_task.cancel()
 
    # No reply, no acknowledgement — per spec.


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    session = _sessions.get(user_id)
    if session:
        await update.message.reply_text(
            f"🧵 Session ID: {session.session_id[:8]}...\n"
            f"🕒 Started: {format_time(session.started_at)}\n"
            f"💬 Last msg: {format_time(session.last_interaction_at)}\n"
            f"⚙️  Processing: {'Yes' if session.is_processing else 'No'}"
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


async def connect_gmail_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loaded = tool_manager.loaded_servers
    if "gmail" in loaded:
        await update.message.reply_text("⚠️ Gmail is already connected.")
        return
    
    await update.message.reply_text("⏳ Connecting Gmail...\nBrowser will open for login.")
    try:
        await CONNECTORS["gmail"](tool_manager)
        await update.message.reply_text("✅ Gmail connected successfully!\nYou can now use Gmail tools.")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to connect Gmail:\n{str(e)}")


async def disconnect_gmail_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tool_manager.unregister("gmail")
    await update.message.reply_text("🗑️ Gmail disconnected.")


async def connectors_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    available = "\n".join(f"• {name}" for name in CONNECTORS.keys())

    await update.message.reply_text(
        f"📦 Available connectors:\n\n"
        f"{available}\n\n"
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
def load_persisted_chat_id() -> int | None:
    try:
        return json.loads(CHAT_ID_FILE.read_text())["chat_id"]
    except Exception:
        return None
    

async def dispatch_recurring_task(task_id: str, task_text: str):
    session_id = str(uuid.uuid4())

    print(
        f"\n⏰ RECURRING TASK DISPATCH"
        f"\n🆔 Task      : {task_id}"
        f"\n🧵 Session ID : {session_id}\n",
        flush=True
    )

    try:
        result = await send(task_text, session_id)
    except Exception as e:
        print(f"❌ Recurring task agent error [{task_id}]: {e}", flush=True)
        return

    reply = result.get("reply") or result.get("interrupt")
    if not reply:
        print(f"⚠️  No reply from agent for task {task_id}")
        return

    if active_chat_id is None:
        print(f"⚠️  No active Telegram chat for task {task_id} — reply dropped.")
        return

    try:
        await telegram_app.bot.send_message(chat_id=active_chat_id, text=reply)
        print(f"✉️  Recurring task [{task_id}] reply sent.")
    except Exception as e:
        print(f"❌ Failed to send recurring task reply [{task_id}]: {e}", flush=True)
    
    
@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_app
    global active_chat_id

    # ── Init persistent storage ───────────────────────────────────────────────
    await init_db()

    persisted = await load_all_sessions()
    now = time.time()

    for uid, p in persisted.items():
        elapsed = now - p.last_interaction_at
        remaining = (IDLE_MINUTES * 60) - elapsed

        # Session should have expired while server was down — clean it up
        if remaining <= 0:
            await delete_session(uid)
            print(
                f"\n🗑️ EXPIRED DURING DOWNTIME (skipped restore)"
                f"\n👤 User      : {p.user_name} ({uid})"
                f"\n🧵 Session ID: {p.session_id[:8]}...\n"
            )
            continue

        # Session is still valid — restore it with the remaining time
        session = UserSession(
            session_id=p.session_id,
            user_name=p.user_name,
            started_at=p.started_at,
            last_interaction_at=p.last_interaction_at,
        )
        _sessions[uid] = session

        # Start expiry timer with REMAINING time, not full IDLE_MINUTES
        session.expiry_task = asyncio.create_task(
            expire_session_after_timeout(uid, p.session_id, p.user_name, override_seconds=remaining)
        )

        print(
            f"\n♻️ RESTORED SESSION"
            f"\n👤 User      : {p.user_name} ({uid})"
            f"\n🧵 Session ID: {p.session_id[:8]}..."
            f"\n⏳ Expires in: {remaining:.0f}s\n"
        )

    if persisted:
        print(f"♻️  Restored {len(_sessions)} valid session(s) from DB")

    saved = load_persisted_chat_id()
    if saved:
        active_chat_id = saved
        print(f"Loaded persisted chat_id: {active_chat_id}")
    else:
        print("No chat_id yet — user must send /start first.")

    telegram_app = Application.builder().token(TOKEN).concurrent_updates(True).build()

    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("stop", stop_command))
    telegram_app.add_handler(CommandHandler("status", status_command))
    telegram_app.add_handler(CommandHandler("connectors", connectors_command))
    telegram_app.add_handler(CommandHandler("loaded_connectors", loaded_connectors_command))
    telegram_app.add_handler(CommandHandler("connect_swiggy", connect_swiggy_command))
    telegram_app.add_handler(CommandHandler("disconnect_swiggy", disconnect_swiggy_command))
    telegram_app.add_handler(CommandHandler("connect_gmail", connect_gmail_command))
    telegram_app.add_handler(CommandHandler("disconnect_gmail", disconnect_gmail_command))

    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_telegram_message))

    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.bot.set_my_commands([
        BotCommand("start",             "Start the bot"),
        BotCommand("stop",              "Stop the current process"),
        BotCommand("status",            "Show your session info"),
        BotCommand("connectors",        "Show available connectors"),
        BotCommand("loaded_connectors", "Show currently loaded connectors"),
        BotCommand("connect_swiggy",    "Connect Swiggy (food + instamart)"),
        BotCommand("disconnect_swiggy", "Disconnect Swiggy tools"),
        BotCommand("connect_gmail",     "Connect Gmail"),
        BotCommand("disconnect_gmail",  "Disconnect Gmail"),
    ])
    await telegram_app.updater.start_polling()

    print("🤖 Telegram bot is running...")

    set_dispatch(dispatch_recurring_task)

    # Do NOT await this here, or startup deadlocks.
    asyncio.create_task(initialize_agent())
    asyncio.create_task(start_recurring_tasks())

    yield

    # ── Graceful drain on shutdown ────────────────────────────────────────────
    # 1. Notify every user who is mid-process.
    # 2. Give in-flight tasks up to GRACEFUL_DRAIN_TIMEOUT seconds to finish.
    # 3. Force-cancel anything still running after the timeout.
 
    processing_sessions = [
        (uid, s) for uid, s in _sessions.items() if s.is_processing
    ]
 
    if processing_sessions:
        print(f"\n ⚠️ Shutdown: {len(processing_sessions)} active session(s) in progress. Notifying users...")
 
        notify_tasks = []
        for uid, session in processing_sessions:
            # Best-effort notification — if Telegram is also down this will just fail silently.
            async def _notify(s=session):
                try:
                    # We need the chat_id for this user. We track active_chat_id globally
                    # (last active), but for a multi-user bot we need per-user chat ids.
                    # For now we use active_chat_id as a best effort; see note below.
                    if active_chat_id:
                        await telegram_app.bot.send_message(
                            chat_id=s.chat_id,
                            text=(
                                "⚠️ It looks like our connection was interrupted.\n\n"
                                "I wasn't able to finish processing your request. "
                                "Please try again in a moment — I'll be right back."
                            )
                        )
                except Exception as e:
                    print(f"⚠️  Could not notify user {s.user_name}: {e}")
 
            notify_tasks.append(asyncio.create_task(_notify()))
 
        # Wait for all notifications to go out before cancelling tasks
        await asyncio.gather(*notify_tasks, return_exceptions=True)
 
        # Collect active tasks to drain
        active_tasks = [
            s.active_task
            for _, s in processing_sessions
            if s.active_task and not s.active_task.done()
        ]
 
        if active_tasks:
            print(f"⏳ Waiting up to {GRACEFUL_DRAIN_TIMEOUT}s for {len(active_tasks)} task(s)...")
            _, pending = await asyncio.wait(active_tasks, timeout=GRACEFUL_DRAIN_TIMEOUT)
 
            if pending:
                print(f"🔨 Force-cancelling {len(pending)} task(s) that did not finish in time.")
                for t in pending:
                    t.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

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
