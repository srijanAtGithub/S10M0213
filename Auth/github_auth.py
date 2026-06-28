import os

async def get_github_config() -> dict:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN is not set. Please add it to your settings.json and run `sicily config`."
        )
    return {"GITHUB_TOKEN": token}