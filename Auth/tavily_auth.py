import os

async def get_tavily_config() -> dict:
    env = os.environ.copy()
    
    env.update({
        "TAVILY_API_KEY": os.environ["TAVILY_API_KEY"],
    })

    return env
