import json
from telegram import Update, BotCommand
from telegram.ext import CommandHandler, ContextTypes

from agent import tool_manager
from connectors import CONNECTORS

# ──────────────────────────────────────────────────────────────────────────────
# TELEGRAM COMMANDS EXECUTORS
# ──────────────────────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import main

    main.active_chat_id = update.effective_chat.id

    if not main.CHAT_ID_FILE.exists():
        main.CHAT_ID_FILE.write_text(json.dumps({"chat_id": main.active_chat_id}))
        print(f"💾 Registered chat_id: {main.active_chat_id} for user {update.effective_user.first_name}")
        await update.message.reply_text(
            "👋 Hi! I'm Sicily. You're all set up.."
        )
    else:
        await update.message.reply_text("👋 Hey! Already registered. Ready to go.")


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Cancel whatever is currently processing for this user.
    No reply to the user — just stop everything silently.
    """
    import main

    user_id = str(update.effective_user.id)
    session = main._sessions.get(user_id)
 
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
    import main 
    
    user_id = str(update.effective_user.id)
    session = main._sessions.get(user_id)
    if session:
        await update.message.reply_text(
            f"🧵 Session ID: {session.session_id[:8]}...\n"
            f"🕒 Started: {main.format_time(session.started_at)}\n"
            f"💬 Last msg: {main.format_time(session.last_interaction_at)}\n"
            f"⚙️  Processing: {'Yes' if session.is_processing else 'No'}"
        )
    else:
        await update.message.reply_text("No active session.")


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


async def connect_telegram_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loaded = tool_manager.loaded_servers
    if "telegram" in loaded:
        await update.message.reply_text("⚠️ Telegram is already connected.")
        return
    
    await update.message.reply_text("⏳ Connecting Telegram MCP...")
    try:
        await CONNECTORS["telegram"](tool_manager)
        await update.message.reply_text("✅ Telegram connected successfully! You can now ask me to read or send messages.")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to connect Telegram:\n{str(e)}")


async def disconnect_telegram_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tool_manager.unregister("telegram")
    await update.message.reply_text("🗑️ Telegram disconnected.")


async def connect_tavily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loaded = tool_manager.loaded_servers
    if "tavily" in loaded:
        await update.message.reply_text("⚠️ Tavily is already connected.")
        return
    
    await update.message.reply_text("⏳ Connecting Tavily MCP...")
    try:
        await CONNECTORS["tavily"](tool_manager)
        await update.message.reply_text("✅ Tavily connected successfully!\nYou can now use search and web tools.")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to connect Tavily:\n{str(e)}")


async def disconnect_tavily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tool_manager.unregister("tavily")
    await update.message.reply_text("🗑️ Tavily disconnected.")


# ──────────────────────────────────────────────────────────────────────────────
# REGISTRATION HELPERS
# ──────────────────────────────────────────────────────────────────────────────
def setup_command_handlers(telegram_app):
    """Attaches all command handlers to the application."""
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("stop", stop_command))
    telegram_app.add_handler(CommandHandler("status", status_command))
    telegram_app.add_handler(CommandHandler("connectors", connectors_command))
    telegram_app.add_handler(CommandHandler("loaded_connectors", loaded_connectors_command))
    telegram_app.add_handler(CommandHandler("connect_swiggy", connect_swiggy_command))
    telegram_app.add_handler(CommandHandler("disconnect_swiggy", disconnect_swiggy_command))
    telegram_app.add_handler(CommandHandler("connect_gmail", connect_gmail_command))
    telegram_app.add_handler(CommandHandler("disconnect_gmail", disconnect_gmail_command))
    telegram_app.add_handler(CommandHandler("connect_telegram", connect_telegram_command))
    telegram_app.add_handler(CommandHandler("disconnect_telegram", disconnect_telegram_command))
    telegram_app.add_handler(CommandHandler("connect_tavily", connect_tavily_command))
    telegram_app.add_handler(CommandHandler("disconnect_tavily", disconnect_tavily_command))


async def setup_bot_commands(telegram_app):
    """Sets the UI menu commands in Telegram."""
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
        BotCommand("connect_telegram",  "Connect Telegram"),
        BotCommand("disconnect_telegram", "Disconnect Telegram"),
        BotCommand("connect_tavily",    "Connect Tavily search"),
        BotCommand("disconnect_tavily", "Disconnect Tavily"),
    ])