from langchain_mcp_adapters.client import MultiServerMCPClient
from Auth.swiggy_auth import get_valid_token
from Auth.gmail_auth import get_gmail_token
from Auth.telegram_auth import get_telegram_config
from Auth.tavily_auth import get_tavily_config

from configuration import TELEGRAM_BLACKLIST

async def load_swiggy_tools(tool_manager):
    token = await get_valid_token()

    # return await client.get_tools()
    food_client = MultiServerMCPClient({
        "swiggy-food": {
            "transport": "streamable_http",
            "url": "https://mcp.swiggy.com/food",
            "headers": {"Authorization": f"Bearer {token}"},
        }
    })
    instamart_client = MultiServerMCPClient({
        "swiggy-instamart": {
            "transport": "streamable_http",
            "url": "https://mcp.swiggy.com/im",
            "headers": {"Authorization": f"Bearer {token}"},
        }
    })

    food_tools = await food_client.get_tools()
    im_tools   = await instamart_client.get_tools()

    await tool_manager.register(food_tools, "swiggy-food")
    await tool_manager.register(im_tools, "swiggy-instamart")


async def load_gmail_tools(tool_manager):
    token = await get_gmail_token()

    gmail_client = MultiServerMCPClient({
        "gmail": {
            "transport": "streamable_http",
            "url": "https://gmailmcp.googleapis.com/mcp/v1",
            "headers": {"Authorization": f"Bearer {token}"},
        }
    })

    tools = await gmail_client.get_tools()
    await tool_manager.register(tools, "gmail")


async def load_telegram_tools(tool_manager):
    env = await get_telegram_config()

    telegram_client = MultiServerMCPClient({
        "telegram": {
            "transport": "stdio",
            "command": "uv",
            "args": [
                "--directory", "/Users/srijan/MCP Servers/Telegram MCP Server",
                "run",
                "main.py",
            ],
            "env": env,
        }
    })

    raw_tools = await telegram_client.get_tools()
    # excluding blacklisted tools
    filtered_tools = [tool for tool in raw_tools if tool.name not in TELEGRAM_BLACKLIST]
    await tool_manager.register(filtered_tools, "telegram")


async def load_tavily_tools(tool_manager):
    env = await get_tavily_config()
    
    tavily_client = MultiServerMCPClient({
        "tavily": {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "tavily-mcp@0.1.4"],
            "env": env,
        }
    })
    tools = await tavily_client.get_tools()
    await tool_manager.register(tools, "tavily")


# Registry of all available connectors — add new ones here
CONNECTORS = {
    "swiggy":      load_swiggy_tools,
    "gmail":       load_gmail_tools,
    "telegram":    load_telegram_tools,
    "tavily":      load_tavily_tools,
}
