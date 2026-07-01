import json
from pathlib import Path
# Import the single source of truth from your configuration script
from configuration import REQUIRED_KEYS


def test_settings_template_matches_required_keys():
    """
    Validates that the keys defined in settings.example.json exactly match 
    the REQUIRED_KEYS listed in configuration.py.
    """
    # Locating settings.example.json (one level up from the Tests/ directory)
    test_dir = Path(__file__).resolve().parent
    example_json_path = test_dir.parent / "settings.example.json"

    # 1. Ensure the file actually exists where we expect it
    assert example_json_path.exists(), f"Could not find settings.example.json at {example_json_path}"

    # 2. Parse the keys from the template file
    with open(example_json_path, "r") as f:
        try:
            example_data = json.load(f)
        except json.JSONDecodeError:
            assert False, f"settings.example.json is not valid JSON!"

    template_keys = set(example_data.keys())
    code_keys = set(REQUIRED_KEYS)

    # 3. Find any discrepancies
    missing_in_code = template_keys - code_keys
    missing_in_template = code_keys - template_keys

    # 4. Assertions with clear, helpful error messages
    assert not missing_in_code, (
        f"Discrepancy found! The following keys are in settings.example.json "
        f"but are missing from REQUIRED_KEYS in configuration.py: {missing_in_code}"
    )

    assert not missing_in_template, (
        f"Discrepancy found! The following keys are in REQUIRED_KEYS (configuration.py) "
        f"but are missing from the settings.example.json template: {missing_in_template}"
    )