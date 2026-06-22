from Tests.conftest import (
    parse,
    extract_call_first_arg_strings,
    AGENT_PATH,
    SOULS_DIR,
)


# Souls/*.md — every soul referenced in agent.py must exist on disk
def test_referenced_soul_files_exist():
    """
    agent.py calls get_system_message("eval_llm" / "main_llm" / "safety_llm"),
    which loads Souls/<name>.md. get_system_message() silently falls back to
    a generic prompt if the file is missing instead of raising an error —
    so this test catches that before it quietly degrades behaviour.
 
    Scans agent.py for every get_system_message(...) call rather than
    hardcoding the 3 current names, so it stays correct if more are added.
    """
    agent_tree = parse(AGENT_PATH)
    referenced_souls = set(extract_call_first_arg_strings(agent_tree, "get_system_message"))
 
    assert referenced_souls, "No get_system_message(...) calls found in agent.py — check the function name hasn't changed"
 
    missing = [name for name in referenced_souls if not (SOULS_DIR / f"{name}.md").exists()]
    assert not missing, f"Missing Soul files in {SOULS_DIR}: {[f'{m}.md' for m in missing]}"
