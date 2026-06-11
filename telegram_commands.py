import json
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from agent import tool_manager
from connectors import CONNECTORS

# CALLBACK PREFIXES  (inline button payloads)
_CONNECT_PREFIX    = "connect:"
_DISCONNECT_PREFIX = "disconnect:"

# CORE COMMANDS
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import main

    main.active_chat_id = update.effective_chat.id

    if not main.CHAT_ID_FILE.exists():
        main.CHAT_ID_FILE.write_text(json.dumps({"chat_id": main.active_chat_id}))
        print(f"💾 Registered chat_id: {main.active_chat_id} for user {update.effective_user.first_name}")
        await update.message.reply_text(
            "👋 Hi! I'm S10M0213. You're all set up.."
        )
    else:
        await update.message.reply_text("👋 Hey! Already registered. Ready to go.")


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel whatever is currently processing. No reply — just stop silently."""
    import main

    user_id = str(update.effective_user.id)
    session = main._sessions.get(user_id)

    if session is None or not session.is_processing:
        return

    print(
        f"\n🛑 /stop received"
        f"\n👤 User : {session.user_name} ({user_id})"
        f"\n🧵 Session: {session.session_id}\n"
    )

    session.cancel_requested = True

    if session.active_task and not session.active_task.done():
        session.active_task.cancel()


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


# CONNECTOR COMMANDS  (dynamic, driven by CONNECTORS registry)
async def connectors_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all available connectors that are NOT yet loaded, as inline buttons."""
    loaded = set(tool_manager.loaded_servers)
    available = [name for name in CONNECTORS if name not in loaded]

    if not available:
        await update.message.reply_text("✅ All available connectors are already loaded.")
        return

    keyboard = _build_keyboard(available, prefix=_CONNECT_PREFIX)
    await update.message.reply_text(
        "📦 Available connectors — tap one to connect:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def loaded_connectors_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all currently loaded MCP servers."""
    loaded = tool_manager.loaded_servers
    if not loaded:
        await update.message.reply_text("🔌 No connectors loaded currently.")
        return
    text = "\n".join(f"  • {s}" for s in loaded)
    await update.message.reply_text(f"🔌 Loaded connectors:\n{text}")


async def disconnect_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show loaded connectors as inline buttons — tap one to disconnect."""
    loaded = tool_manager.loaded_servers
    if not loaded:
        await update.message.reply_text("🔌 No connectors are currently loaded.")
        return

    keyboard = _build_keyboard(loaded, prefix=_DISCONNECT_PREFIX)
    await update.message.reply_text(
        "🔌 Loaded connectors — tap one to disconnect:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# INLINE BUTTON CALLBACKS
async def on_connect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fired when user taps a connector button from /connectors."""
    query = update.callback_query
    await query.answer()

    connector_name = query.data[len(_CONNECT_PREFIX):]

    if connector_name not in CONNECTORS:
        await query.edit_message_text(f"❌ Unknown connector: {connector_name}")
        return

    if connector_name in tool_manager.loaded_servers:
        await query.edit_message_text(f"⚠️ {connector_name} is already connected.")
        return

    await query.edit_message_text(f"⏳ Connecting {connector_name}...")

    try:
        await CONNECTORS[connector_name](tool_manager)
        await query.edit_message_text(f"✅ {connector_name} connected successfully!")
    except Exception as e:
        await query.edit_message_text(f"❌ Failed to connect {connector_name}:\n{e}")


async def on_disconnect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fired when user taps a server button from /disconnect."""
    query = update.callback_query
    await query.answer()

    server_name = query.data[len(_DISCONNECT_PREFIX):]

    tool_manager.unregister(server_name)
    await query.edit_message_text(f"🗑️ {server_name} disconnected.")


# HELPERS
def _build_keyboard(
    names: list[str],
    prefix: str,
    columns: int = 2,
) -> list[list[InlineKeyboardButton]]:
    """Lay out buttons in a grid of `columns` per row."""
    buttons = [
        InlineKeyboardButton(name, callback_data=f"{prefix}{name}")
        for name in names
    ]
    return [buttons[i : i + columns] for i in range(0, len(buttons), columns)]


# REGISTRATION HELPERS
def setup_command_handlers(telegram_app):
    """Attaches all command + callback handlers to the application."""
    # Commands
    telegram_app.add_handler(CommandHandler("start",             start_command))
    telegram_app.add_handler(CommandHandler("stop",              stop_command))
    telegram_app.add_handler(CommandHandler("status",            status_command))
    telegram_app.add_handler(CommandHandler("connectors",        connectors_command))
    telegram_app.add_handler(CommandHandler("loaded_connectors", loaded_connectors_command))
    telegram_app.add_handler(CommandHandler("disconnect",        disconnect_command))

    # Inline button callbacks
    telegram_app.add_handler(CallbackQueryHandler(on_connect_callback,    pattern=f"^{_CONNECT_PREFIX}"))
    telegram_app.add_handler(CallbackQueryHandler(on_disconnect_callback, pattern=f"^{_DISCONNECT_PREFIX}"))


async def setup_bot_commands(telegram_app):
    """Sets the visible UI menu commands in Telegram (shown on '/')."""
    await telegram_app.bot.set_my_commands([
        BotCommand("start",             "Start the bot"),
        BotCommand("stop",              "Stop the current process"),
        BotCommand("status",            "Show your session info"),
        BotCommand("connectors",        "Connect a new service"),
        BotCommand("loaded_connectors", "Show currently loaded connectors"),
        BotCommand("disconnect",        "Disconnect a loaded connector"),
    ])