/**
 * popup.js
 * --------
 * Demo scope (no AI):
 *   1. Identifies the active tab (id, url, title).
 *   2. Restores that tab's chat history from the backend
 *      (GET /session/{tabId}) — so switching tabs and coming back shows
 *      the right conversation, not a blank window.
 *   3. Opens a WebSocket to the local Python backend, scoped to this tab
 *      (ws://localhost:8765/ws/{tabId}).
 *   4. Sends { text, page_url, page_title } on every user message.
 *   5. Renders whatever { reply } comes back — no AI yet, just proving the
 *      round trip + per-tab memory work.
 *
 * Session lifetime: this tab's history lives on the backend for as long as
 * the tab is open. It survives the popup being closed and reopened. It's
 * deleted only by the "Clear" button (explicit, immediate — no confirm
 * dialog yet, this is still v0.1.0) or when the tab itself closes
 * (background.js handles that via chrome.tabs.onRemoved).
 */

const BACKEND_HOST = "localhost:8765";

const messagesEl = document.getElementById("messages");
const inputEl = document.getElementById("input-box");
const sendBtn = document.getElementById("send-btn");
const clearBtn = document.getElementById("clear-btn");
const statusDot = document.getElementById("status-dot");

let socket = null;
let currentTab = { id: null, url: "", title: "" };

function addMessage(text, role) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.textContent = text;
  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function clearMessagesUI() {
  messagesEl.innerHTML = "";
}

function setStatus(state) {
  // state: "connected" | "disconnected" | "connecting"
  statusDot.className = state === "connected"
    ? "connected"
    : state === "disconnected"
      ? "disconnected"
      : "";
}

function setSending(isSending) {
  sendBtn.disabled = isSending;
  sendBtn.textContent = isSending ? "..." : "Send";
}

async function getActiveTabInfo() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return { id: null, url: "(no active tab)", title: "(no active tab)" };
  return { id: tab.id, url: tab.url || "", title: tab.title || "" };
}

async function loadHistory(tabId) {
  try {
    const res = await fetch(`http://${BACKEND_HOST}/session/${tabId}`);
    if (!res.ok) throw new Error(`status ${res.status}`);
    const data = await res.json();
    return data.messages || [];
  } catch (err) {
    addMessage("Couldn't load this tab's history (backend not running?).", "system");
    return [];
  }
}

async function clearHistoryOnBackend(tabId) {
  try {
    await fetch(`http://${BACKEND_HOST}/session/${tabId}`, { method: "DELETE" });
    return true;
  } catch (err) {
    addMessage("Couldn't clear history — is navigator_bridge.py running?", "system");
    return false;
  }
}

function connectSocket(tabId) {
  setStatus("connecting");
  socket = new WebSocket(`ws://${BACKEND_HOST}/ws/${tabId}`);

  socket.onopen = () => {
    setStatus("connected");
  };

  socket.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      addMessage(data.reply ?? "(empty response)", "ai");
    } catch (err) {
      addMessage("Couldn't parse server response.", "system");
    }
    setSending(false);
  };

  socket.onclose = () => {
    setStatus("disconnected");
    addMessage("Disconnected. Is navigator_bridge.py running on port 8765?", "system");
    setSending(false);
  };

  socket.onerror = () => {
    setStatus("disconnected");
  };
}

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text) return;

  if (!socket || socket.readyState !== WebSocket.OPEN) {
    addMessage("Not connected to backend yet.", "system");
    return;
  }

  addMessage(text, "user");
  inputEl.value = "";
  setSending(true);

  // Refresh page info right before sending, in case the tab navigated
  // while the popup was open.
  const fresh = await getActiveTabInfo();
  currentTab.url = fresh.url;
  currentTab.title = fresh.title;

  socket.send(JSON.stringify({
    text,
    page_url: currentTab.url,
    page_title: currentTab.title,
  }));
}

async function handleClear() {
  if (currentTab.id == null) return;

  const ok = await clearHistoryOnBackend(currentTab.id);
  if (ok) {
    clearMessagesUI();
    // addMessage(`Looking at: ${currentTab.title || currentTab.url}`, "system");
    addMessage("Chat cleared.", "system");
  }
}

sendBtn.addEventListener("click", sendMessage);
clearBtn.addEventListener("click", handleClear);
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendMessage();
});

// ── Init ──────────────────────────────────────────────────────────────
(async () => {
  currentTab = await getActiveTabInfo();

  if (currentTab.id == null) {
    addMessage("Couldn't identify the active tab.", "system");
    return;
  }

  // addMessage(`Looking at: ${currentTab.title || currentTab.url}`, "system");

  const history = await loadHistory(currentTab.id);
  for (const m of history) {
    addMessage(m.text, m.role === "user" ? "user" : "ai");
  }

  connectSocket(currentTab.id);
})();
