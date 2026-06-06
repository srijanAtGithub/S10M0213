import os

async def get_telegram_config() -> dict:
    env = os.environ.copy()
    
    env.update({
        "TELEGRAM_API_ID":         os.environ["TELEGRAM_API_ID"],
        "TELEGRAM_API_HASH":       os.environ["TELEGRAM_API_HASH"],
        "TELEGRAM_SESSION_STRING": os.environ["TELEGRAM_SESSION_STRING"],
    })

    return env
