# configuration.py
# Change PROVIDER to switch models system-wide.
# Options: "openai" | "google"

PROVIDER = "openai"

from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI


def get_main_llm(tools=None):
    if PROVIDER == "openai":
        llm = ChatOpenAI(model="gpt-5.4-mini")
    elif PROVIDER == "google":
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite")

    if tools:
        return llm.bind_tools(tools, parallel_tool_calls=False)
    return llm


def get_safety_llm(schema):
    if PROVIDER == "openai":
        return ChatOpenAI(model="gpt-5.4-nano").with_structured_output(schema, include_raw=False)
    elif PROVIDER == "google":
        return ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite").with_structured_output(schema, include_raw=False)


def get_intent_llm(schema):
    if PROVIDER == "openai":
        return ChatOpenAI(model="gpt-5.4-nano").with_structured_output(schema, include_raw=False)
    elif PROVIDER == "google":
        return ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite").with_structured_output(schema, include_raw=False)


def get_eval_llm():
    if PROVIDER == "openai":
        return ChatOpenAI(model="gpt-4o-mini", temperature=0)
    elif PROVIDER == "google":
        return ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite", temperature=0)


def get_summarizer_llm():
    if PROVIDER == "openai":
        return ChatOpenAI(model="gpt-4o-mini", temperature=0)
    elif PROVIDER == "google":
        return ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite", temperature=0) 


TOOL_LABELS = {
    # ── Instamart: Discover ──────────────────────────────────
    "search_products":    "🔍 Searching for products...",
    "your_go_to_items":   "⭐ Fetching your go-to items...",
    "get_addresses":      "📍 Fetching your saved addresses...",
    "create_address":     "📍 Saving new address...",
    "delete_address":     "🗑️ Deleting address...",

    # ── Instamart: Cart ──────────────────────────────────────
    "get_cart":           "🛒 Fetching your cart...",
    "update_cart":        "🛒 Updating your cart...",
    "clear_cart":         "🗑️ Clearing your cart...",

    # ── Instamart: Order ─────────────────────────────────────
    "checkout":           "📦 Placing your order...",

    # ── Instamart: Track ─────────────────────────────────────
    "get_orders":         "📋 Fetching your order history...",
    "get_order_details":  "🔎 Getting order details...",
    "track_order":        "🚴 Tracking your order...",

    # ── Instamart: Support ───────────────────────────────────
    "report_error":       "📝 Generating error report...",

    # ── Food: Discover ───────────────────────────────────────
    "search_restaurants": "🔍 Searching restaurants...",
    "search_menu":        "🍽️ Searching the menu...",
    "get_restaurant_menu":"🍽️ Fetching restaurant menu...",

    # ── Food: Cart ───────────────────────────────────────────
    "get_food_cart":      "🛒 Fetching your food cart...",
    "update_food_cart":   "🛒 Updating your food cart...",
    "flush_food_cart":    "🗑️ Clearing your food cart...",
    "fetch_food_coupons": "🎟️ Finding available coupons...",
    "apply_food_coupon":  "🎟️ Applying coupon...",

    # ── Food: Order ──────────────────────────────────────────
    "place_food_order":   "📦 Placing your food order...",

    # ── Food: Track ──────────────────────────────────────────
    "get_food_orders":    "📋 Fetching your food orders...",
    "get_food_order_details": "🔎 Getting order details...",
    "track_food_order":   "🚴 Tracking your food order...",

    # ── Gmail ────────────────────────────────────────────────
    "create_draft":       "✉️ Creating email draft...",
    "list_drafts":        "📄 Fetching email drafts...",
    "get_thread":         "📧 Loading email conversation...",
    "search_threads":     "🔍 Searching emails...",
    "label_thread":       "🏷️ Updating conversation labels...",
    "unlabel_thread":     "🏷️ Removing conversation labels...",
    "list_labels":        "📋 Fetching email labels...",
    "label_message":      "🏷️ Updating email labels...",
    "unlabel_message":    "🏷️ Removing email labels...",
    "create_label":       "➕ Creating email label...",
    "update_label":       "✏️ Updating email label...",
    "delete_label":       "🗑️ Deleting email label...",
}