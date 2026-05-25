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
