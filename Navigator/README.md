# Sicily — Navigator dimension

Baby-steps demo. No LLM, no tools, no MCP. Proves the full loop:

```
extension popup (chat UI)
   -> reads active tab's URL/title (chrome.tabs)
   -> WebSocket -> navigator_bridge.py (FastAPI)
   -> navigator_graph.py (LangGraph, one node, no AI)
   -> reply sent back down the same WebSocket
   -> rendered in the popup
```

Type anything in the popup chat. The response is just the current tab's
title and URL, echoed back through a real (trivial) LangGraph graph.

---

## 1. Run the backend

```bash
cd Navigator
pip install -r requirements.txt
uvicorn navigator_bridge:app --reload --port 8765
```

Confirm it's up: open `http://localhost:8765/health` in a normal browser
tab — you should see `{"status":"ok","dimension":"navigator","ai":false}`.

Leave this running. The extension connects to `ws://localhost:8765/ws`.

## 2. Load the extension in Chrome

1. Go to `chrome://extensions`
2. Enable **Developer mode** (top right toggle)
3. Click **Load unpacked**
4. Select the `Navigator/extension` folder
5. The "Sicily Navigator" icon appears in your toolbar

## 3. Test it

1. Open any website (e.g. `https://www.overleaf.com/...`, or literally
   any tab)
2. Click the Sicily Navigator extension icon
3. Popup opens, shows "Looking at: <page title>", and a green dot once
   connected to the backend
4. Type anything, hit Send
5. You get back the page's title + URL, proving:
   - the popup can read the live active tab
   - the WebSocket round trip to Python works
   - a real (if trivial) LangGraph graph executed and returned a message

If the dot is red / you see "Disconnected", the backend isn't running or
isn't reachable on port 8765 — check step 1.

---

## File map

```
Navigator/
├── navigator_graph.py     # LangGraph: one node, no LLM, no tools
├── navigator_bridge.py    # FastAPI + WebSocket server (the transport)
├── requirements.txt
├── README.md             # this file
└── extension/
    ├── manifest.json     # MV3 manifest
    ├── popup.html         # chat UI
    ├── popup.js           # chat logic + WebSocket client + tab reading
    ├── background.js     # MV3 service worker (currently a no-op, scaffolding for later)
    └── icon.png
```

## What's deliberately NOT here yet

- No LLM (`configuration.py` isn't touched)
- No tools (no `navigator_tools.py` with click/type/navigate)
- No API key / auth check
- No multi-tab / "any overleaf.com/\* page" permission scoping
  (extension currently reads whatever tab is active when you hit Send —
  that's the `activeTab` + `chrome.tabs` permission, the simplest case)
- No persistence — every popup open is a fresh, stateless graph run

These are the next steps, each additive on top of this scaffold:

1. Swap `echo_node` for a real `main_node` calling `configuration.get_main_llm()`
2. Add `navigator_tools.py` (click/read_dom/navigate) — content script becomes
   the execution target instead of just a read-only info source
3. Add origin-wide permission scoping in `background.js` so "start" on one
   page gives access to all tabs on that origin
4. Add the three-gate safety pipeline (same pattern as `agent.py`) before
   any write-style tool (click/type/submit) executes
