from Tests.conftest import (
    parse,
    get_dict_literal,
    dict_str_keys,
    all_function_names,
    CONNECTORS_PATH,
    TELEGRAM_COMMANDS_PATH,
)


# connectors.py <-> telegram_commands.py consistency
def test_each_connector_has_connect_and_disconnect_commands():
    """
    For every key in CONNECTORS (connectors.py), telegram_commands.py must
    define a connect_<key>_command and a disconnect_<key>_command function.
    """
    connectors_tree = parse(CONNECTORS_PATH)
    commands_tree   = parse(TELEGRAM_COMMANDS_PATH)

    connectors_dict = get_dict_literal(connectors_tree, "CONNECTORS")
    assert connectors_dict is not None, "Could not find a `CONNECTORS = {...}` dict in connectors.py"

    connector_keys = dict_str_keys(connectors_dict)
    assert connector_keys, "CONNECTORS dict has no string keys — check it's defined as expected"

    defined_functions = all_function_names(commands_tree)

    missing = []
    for key in connector_keys:
        for fn_name in (f"connect_{key}_command", f"disconnect_{key}_command"):
            if fn_name not in defined_functions:
                missing.append(fn_name)

    assert not missing, f"telegram_commands.py is missing: {missing}"
