import json
from Tests.conftest import SETTINGS_EXAMPLE_PATH


# settings.example.json — must stay a blank template
def test_settings_example_values_are_all_empty():
    with open(SETTINGS_EXAMPLE_PATH, encoding="utf-8") as f:
        settings = json.load(f)

    non_empty = {k: v for k, v in settings.items() if v != ""}
    assert not non_empty, f"settings.example.json should be a blank template, but found values: {non_empty}"


def test_settings_example_has_expected_keys():
    """Sanity check that the template wasn't accidentally stripped of a field."""
    with open(SETTINGS_EXAMPLE_PATH, encoding="utf-8") as f:
        settings = json.load(f)

    expected_keys = {"OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "TAVILY_API_KEY"}
    assert expected_keys.issubset(settings.keys()), f"Missing keys: {expected_keys - settings.keys()}"
