from Tests.conftest import (
    parse,
    get_function_node,
    extract_call_first_arg_strings,
    TELEGRAM_COMMANDS_PATH,
)

# setup_command_handlers <-> setup_bot_commands consistency
def test_registered_commands_match_bot_menu():
    """
    Every command registered via CommandHandler(...) in setup_command_handlers
    should also appear in the bot's command menu (setup_bot_commands), and
    vice versa — otherwise you get either an invisible command or a menu
    entry that does nothing when tapped.
    """
    tree = parse(TELEGRAM_COMMANDS_PATH)

    handlers_node = get_function_node(tree, "setup_command_handlers")
    menu_node     = get_function_node(tree, "setup_bot_commands")

    assert handlers_node is not None, "setup_command_handlers() not found in telegram_commands.py"
    assert menu_node is not None, "setup_bot_commands() not found in telegram_commands.py"

    handler_commands = set(extract_call_first_arg_strings(handlers_node, "CommandHandler"))
    menu_commands    = set(extract_call_first_arg_strings(menu_node, "BotCommand"))

    only_in_handlers = handler_commands - menu_commands
    only_in_menu     = menu_commands - handler_commands

    assert not only_in_handlers, f"Registered as a handler but missing from the /menu list: {only_in_handlers}"
    assert not only_in_menu, f"In the /menu list but has no CommandHandler registered: {only_in_menu}"
