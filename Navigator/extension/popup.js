/**
 * popup.js
 * --------
 * Demo scope (no AI):
 *   1. Grabs the active tab's URL + title (via chrome.scripting, no
 *      background-script bridge needed for this simple demo).
 *   2. Opens a WebSocket to the local Python backend (browser_bridge.py).
 *   3. Sends { text, page_url, page_title } on every user message.
 *   4. Renders whatever { reply } comes back — no AI, just an echo of the
 *      page info, proving the full round trip works.
 */

const WS_URL = "ws://localhost:8765/ws";

const messagesEl = document.getElementById("messages");
const inputEl = document.getElementById("input-box");
const sendBtn = document.getElementById("send-btn");
const statusDot = document.getElementById("status-dot");

let socket = null;
let currentTab = { url: "", title: "" };

function addMessage(text, role) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.textContent = text;
  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function setStatus(state) {
  // state: "connected" | "disconnected" | "connecting"
  statusDot.className = state === "connected"
    ? "connected"
    : state === "disconnected"
      ? "disconnected"
      : "";
}

async function getActiveTabInfo() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return { url: "(no active tab)", title: "(no active tab)" };
  return { url: tab.url || "", title: tab.title || "" };
}

function connectSocket() {
  setStatus("connecting");
  socket = new WebSocket(WS_URL);

  socket.onopen = () => {
    setStatus("connected");
    addMessage("Connected to local backend.", "system");
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
    addMessage("Disconnected. Is browser_bridge.py running on port 8765?", "system");
    setSending(false);
  };

  socket.onerror = () => {
    setStatus("disconnected");
  };
}

function setSending(isSending) {
  sendBtn.disabled = isSending;
  sendBtn.textContent = isSending ? "..." : "Send";
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

  // Refresh page info right before sending, in case the user switched tabs
  // while the popup was open.
  currentTab = await getActiveTabInfo();

  socket.send(JSON.stringify({
    text,
    page_url: currentTab.url,
    page_title: currentTab.title,
  }));
}

sendBtn.addEventListener("click", sendMessage);
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendMessage();
});

// ── Init ──────────────────────────────────────────────────────────────
(async () => {
  currentTab = await getActiveTabInfo();
  addMessage(`Looking at: ${currentTab.title || currentTab.url}`, "system");
  connectSocket();
})();
