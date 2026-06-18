"""
Test/test.py
============
Single consolidated test file (interim, per-module *_test.py files to follow
later). Covers basic code-structure verification across the project.

Run with:
    pytest Test/test.py -v

Sections:
    1. connectors.py <-> telegram_commands.py consistency   (AST-based, no project imports)
    2. setup_command_handlers <-> setup_bot_commands menu   (AST-based, no project imports)
    3. settings.example.json: all values must be empty      (plain json load)
    4. tool_manager.py: structural checks                   (lightweight import)
    5. memory_and_context.py: pure-logic checks             (logic copied here — see note below)
    6. Souls/*.md: every soul referenced in agent.py must exist on disk  (lightweight import)
    7. Trivy — vulnerability & secret scanning (external tool, optional gate)

NOTE on section 5: memory_and_context.py instantiates an OpenAI client and
loads config AT IMPORT TIME (`eval_llm = configuration.get_eval_llm()`,
`_openai_client = AsyncOpenAI()`). Importing it directly here would make
these tests depend on API keys / config files being present, which isn't
what we want for basic structural/logic tests. So the two pure functions
below (_parse_preference_lines, _cosine_similarity) are copied verbatim
from memory_and_context.py — NOT reimplemented, just relocated — so their
logic can be tested in isolation. Once you refactor that file to avoid
import-time side effects, swap these for a direct import.
"""

import ast
import json
import sys
import dataclasses
from pathlib import Path
import subprocess
import shutil

import numpy as np
import pytest

# Paths — adjust here if your folder layout differs
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CONNECTORS_PATH        = PROJECT_ROOT / "connectors.py"
TELEGRAM_COMMANDS_PATH = PROJECT_ROOT / "telegram_commands.py"
SETTINGS_EXAMPLE_PATH  = PROJECT_ROOT / "settings.example.json"
AGENT_PATH = PROJECT_ROOT / "agent.py"
SOULS_DIR  = PROJECT_ROOT / "Souls"

# AST helpers — read source as text, no project imports required
def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _get_function_node(tree: ast.Module, func_name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return node
    return None


def _all_function_names(tree: ast.Module) -> set[str]:
    return {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _get_dict_literal(tree: ast.Module, var_name: str):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == var_name and isinstance(node.value, ast.Dict):
                    return node.value
    return None


def _dict_str_keys(dict_node: ast.Dict) -> list[str]:
    return [k.value for k in dict_node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]


def _extract_call_first_arg_strings(scope_node, call_name: str) -> list[str]:
    """Within a given AST node, find all calls to `call_name` and return the
    first positional argument when it's a string literal."""
    results = []
    for node in ast.walk(scope_node):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
            if name == call_name and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                results.append(node.args[0].value)
    return results


# 1. connectors.py <-> telegram_commands.py consistency
def test_each_connector_has_connect_and_disconnect_commands():
    """
    For every key in CONNECTORS (connectors.py), telegram_commands.py must
    define a connect_<key>_command and a disconnect_<key>_command function.
    """
    connectors_tree = _parse(CONNECTORS_PATH)
    commands_tree   = _parse(TELEGRAM_COMMANDS_PATH)

    connectors_dict = _get_dict_literal(connectors_tree, "CONNECTORS")
    assert connectors_dict is not None, "Could not find a `CONNECTORS = {...}` dict in connectors.py"

    connector_keys = _dict_str_keys(connectors_dict)
    assert connector_keys, "CONNECTORS dict has no string keys — check it's defined as expected"

    defined_functions = _all_function_names(commands_tree)

    missing = []
    for key in connector_keys:
        for fn_name in (f"connect_{key}_command", f"disconnect_{key}_command"):
            if fn_name not in defined_functions:
                missing.append(fn_name)

    assert not missing, f"telegram_commands.py is missing: {missing}"


# 2. setup_command_handlers <-> setup_bot_commands consistency
def test_registered_commands_match_bot_menu():
    """
    Every command registered via CommandHandler(...) in setup_command_handlers
    should also appear in the bot's command menu (setup_bot_commands), and
    vice versa — otherwise you get either an invisible command or a menu
    entry that does nothing when tapped.
    """
    tree = _parse(TELEGRAM_COMMANDS_PATH)

    handlers_node = _get_function_node(tree, "setup_command_handlers")
    menu_node     = _get_function_node(tree, "setup_bot_commands")

    assert handlers_node is not None, "setup_command_handlers() not found in telegram_commands.py"
    assert menu_node is not None, "setup_bot_commands() not found in telegram_commands.py"

    handler_commands = set(_extract_call_first_arg_strings(handlers_node, "CommandHandler"))
    menu_commands    = set(_extract_call_first_arg_strings(menu_node, "BotCommand"))

    only_in_handlers = handler_commands - menu_commands
    only_in_menu     = menu_commands - handler_commands

    assert not only_in_handlers, f"Registered as a handler but missing from the /menu list: {only_in_handlers}"
    assert not only_in_menu, f"In the /menu list but has no CommandHandler registered: {only_in_menu}"


# 3. settings.example.json — must stay a blank template
def test_settings_example_values_are_all_empty():
    with open(SETTINGS_EXAMPLE_PATH, encoding="utf-8") as f:
        settings = json.load(f)

    non_empty = {k: v for k, v in settings.items() if v != ""}
    assert not non_empty, f"settings.example.json should be a blank template, but found values: {non_empty}"


def test_settings_example_has_expected_keys():
    """Sanity check that the template wasn't accidentally stripped of a field."""
    with open(SETTINGS_EXAMPLE_PATH, encoding="utf-8") as f:
        settings = json.load(f)

    expected_keys = {"OPENAI_API_KEY", "GEMINI_API_KEY", "AI_PROVIDER", "TELEGRAM_BOT_TOKEN"}
    assert expected_keys.issubset(settings.keys()), f"Missing keys: {expected_keys - settings.keys()}"


# 4. tool_manager.py — structural checks (lightweight import, no API calls
#    are made — ToolManager() is never instantiated, only the class itself
#    is inspected, since instantiating it creates live OpenAI/Chat clients)
try:
    from tool_manager import ToolManager, ToolEntry
    _TOOL_MANAGER_IMPORT_ERROR = None
except Exception as e:  # pragma: no cover
    ToolManager, ToolEntry = None, None
    _TOOL_MANAGER_IMPORT_ERROR = e


def test_tool_manager_imports_cleanly():
    assert _TOOL_MANAGER_IMPORT_ERROR is None, f"tool_manager.py failed to import: {_TOOL_MANAGER_IMPORT_ERROR}"


@pytest.mark.skipif(ToolManager is None, reason="tool_manager import failed — see test_tool_manager_imports_cleanly")
def test_tool_manager_has_expected_interface():
    for attr in ("register", "unregister", "get_relevant_servers", "get_tools_for_servers", "loaded_servers", "all_tools"):
        assert hasattr(ToolManager, attr), f"ToolManager is missing expected attribute/method: {attr}"


@pytest.mark.skipif(ToolManager is None, reason="tool_manager import failed — see test_tool_manager_imports_cleanly")
def test_default_tools_per_server_is_a_positive_int():
    assert isinstance(ToolManager.DEFAULT_TOOLS_PER_SERVER, int)
    assert ToolManager.DEFAULT_TOOLS_PER_SERVER > 0


@pytest.mark.skipif(ToolEntry is None, reason="tool_manager import failed — see test_tool_manager_imports_cleanly")
def test_tool_entry_dataclass_fields():
    field_names = {f.name for f in dataclasses.fields(ToolEntry)}
    assert field_names == {"tool", "server", "embedding"}


# 5. memory_and_context.py — pure-logic checks
#    (functions copied verbatim — see module docstring note at top of file)
def _parse_preference_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()

        if not line:
            continue
        if line.startswith("#"):
            continue
        if set(line) <= set("-_* "):
            continue

        for marker in ("-", "*", "•", "–"):
            if line.startswith(marker):
                line = line[len(marker):].strip()
                break

        if line:
            lines.append(line)

    return lines


# --- copied verbatim from memory_and_context.py ---
def _cosine_similarity(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    q     = query_vec / (np.linalg.norm(query_vec) + 1e-9)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
    return (matrix / norms) @ q


def test_parse_preference_lines_strips_markers_and_skips_noise():
    text = """
# Header — should be skipped
Plain preference line
- Hyphen bullet
* Star bullet
• Round bullet
- En-dash bullet
---

Another plain line
""".strip()

    result = _parse_preference_lines(text)

    assert result == [
        "Plain preference line",
        "Hyphen bullet",
        "Star bullet",
        "Round bullet",
        "En-dash bullet",
        "Another plain line",
    ]


def test_parse_preference_lines_empty_input_returns_empty_list():
    assert _parse_preference_lines("") == []
    assert _parse_preference_lines("   \n\n   ") == []


def test_cosine_similarity_known_values():
    query  = np.array([1.0, 0.0])
    matrix = np.array([
        [1.0, 0.0],   # identical direction -> ~1.0
        [0.0, 1.0],   # orthogonal          -> ~0.0
        [1.0, 1.0],   # 45 degrees          -> ~0.7071
    ])

    scores = _cosine_similarity(query, matrix)

    assert np.isclose(scores[0], 1.0, atol=1e-6)
    assert np.isclose(scores[1], 0.0, atol=1e-6)
    assert np.isclose(scores[2], 1 / np.sqrt(2), atol=1e-6)


# 6. Souls/*.md — every soul referenced in agent.py must exist on disk
def test_referenced_soul_files_exist():
    """
    agent.py calls get_system_message("eval_llm" / "main_llm" / "safety_llm"),
    which loads Souls/<name>.md. get_system_message() silently falls back to
    a generic prompt if the file is missing instead of raising an error —
    so this test catches that before it quietly degrades behaviour.
 
    Scans agent.py for every get_system_message(...) call rather than
    hardcoding the 3 current names, so it stays correct if more are added.
    """
    agent_tree = _parse(AGENT_PATH)
    referenced_souls = set(_extract_call_first_arg_strings(agent_tree, "get_system_message"))
 
    assert referenced_souls, "No get_system_message(...) calls found in agent.py — check the function name hasn't changed"
 
    missing = [name for name in referenced_souls if not (SOULS_DIR / f"{name}.md").exists()]
    assert not missing, f"Missing Soul files in {SOULS_DIR}: {[f'{m}.md' for m in missing]}"


# 7. Trivy — vulnerability & secret scanning (external tool, optional gate)
#
# Trivy is a standalone binary, not a pip package — install separately
# (e.g. `brew install trivy`, or see https://aquasecurity.github.io/trivy).
# Both tests auto-skip with a clear reason if it isn't found, so the suite
# still runs fine on machines without it installed.
TRIVY_AVAILABLE = shutil.which("trivy") is not None
 
 
@pytest.mark.skipif(not TRIVY_AVAILABLE, reason="trivy is not installed — see https://aquasecurity.github.io/trivy")
def test_no_high_or_critical_dependency_vulnerabilities():
    """
    Scans dependency files (requirements.txt / pyproject.toml etc.) in the
    project for known CVEs. Fails if any HIGH or CRITICAL severity
    vulnerability is found in a dependency.
    """
    result = subprocess.run(
        ["trivy", "fs", "--scanners", "vuln", "--severity", "HIGH,CRITICAL",
         "--exit-code", "1", "--quiet", str(PROJECT_ROOT)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Trivy found HIGH/CRITICAL vulnerabilities:\n{result.stdout}"
 
 
@pytest.mark.skipif(not TRIVY_AVAILABLE, reason="trivy is not installed — see https://aquasecurity.github.io/trivy")
def test_no_secrets_committed():
    """
    Scans the project for accidentally hardcoded secrets (API keys, tokens).
    Relevant here since this project handles OPENAI_API_KEY,
    TELEGRAM_BOT_TOKEN, and OAuth tokens across .env / settings.json / Auth/.
    """
    result = subprocess.run(
        ["trivy", "fs", "--scanners", "secret", "--exit-code", "1", "--quiet", str(PROJECT_ROOT)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Trivy found exposed secrets:\n{result.stdout}"
 