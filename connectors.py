from langchain_mcp_adapters.client import MultiServerMCPClient
from swiggy_auth import get_valid_token


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


# Registry of all available connectors — add new ones here
CONNECTORS = {
    "swiggy":      load_swiggy_tools,
}
