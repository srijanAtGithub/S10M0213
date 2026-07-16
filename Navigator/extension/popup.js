const BACKEND_HOST = "localhost:8765";

const appWrap = document.getElementById("app-wrap");
const messagesEl = document.getElementById("messages");
const inputEl = document.getElementById("input-box");
const sendBtn = document.getElementById("send-btn");
const clearBtn = document.getElementById("clear-btn");
const statusDot = document.getElementById("status-dot");
const quickActionsWrap = document.getElementById("quick-actions");
const quickActionsBtn = document.getElementById("quick-actions-btn");
const quickActionsMenu = document.getElementById("quick-actions-menu");
const qaItems = document.querySelectorAll(".qa-item");
// New State Variables and Elements
const dragOverlay = document.getElementById("drag-overlay");
const zoneContext = document.getElementById("zone-context");
const zoneCollections = document.getElementById("zone-collections");
const contextShelf = document.getElementById("context-shelf");

let attachedContexts = [];

let socket = null;
let currentTab = { id: null, url: "", title: "" };

function addMessage(text, role) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.textContent = text;
  messagesEl.appendChild(el);

  // Quick fade-in layout bump
  requestAnimationFrame(() => {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  });
}

function clearMessagesUI() {
  messagesEl.innerHTML = "";
}

function setStatus(state) {
  statusDot.className = state === "connected"
    ? "connected"
    : state === "disconnected"
      ? "disconnected"
      : "";
}

function setSending(isSending) {
  sendBtn.disabled = isSending;
  sendBtn.textContent = isSending ? "..." : "Send";
  
  // Sync the futuristic neural scanline effect from content_script.js!
  if (isSending) {
    appWrap.classList.add("busy");
  } else {
    appWrap.classList.remove("busy");
  }
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

  const fresh = await getActiveTabInfo();
  currentTab.url = fresh.url;
  currentTab.title = fresh.title;

  // Update the WebSocket JSON object to include the snippets array
  const payload = {
    text: text,
    page_url: currentTab.url,
    page_title: currentTab.title,
    context_snippets: attachedContexts // Send context array here
  };

  socket.send(JSON.stringify(payload));

  // Clear the shelf state locally right after sending
  attachedContexts = [];
  renderContextShelf();
}

async function handleClear() {
  if (currentTab.id == null) return;

  const ok = await clearHistoryOnBackend(currentTab.id);
  if (ok) {
    clearMessagesUI();
    addMessage("Chat cleared.", "system");
  }
}

sendBtn.addEventListener("click", sendMessage);
clearBtn.addEventListener("click", handleClear);
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendMessage();
});

// ── Quick Actions menu ──────────────────────────────────────────────────
let qaCloseTimer = null;

function openQuickActions() {
  clearTimeout(qaCloseTimer);
  quickActionsWrap.classList.add("open");
}

function closeQuickActions(delay = 0) {
  clearTimeout(qaCloseTimer);
  qaCloseTimer = setTimeout(() => {
    quickActionsWrap.classList.remove("open");
  }, delay);
}

// Hover to open/close with better containment
quickActionsWrap.addEventListener("mouseenter", openQuickActions);

// Keep open while hovering the menu itself or its items
quickActionsMenu.addEventListener("mouseenter", openQuickActions);

quickActionsWrap.addEventListener("mouseleave", () => closeQuickActions(220)); // slightly longer delay

// Click also toggles, for touch/trackpad users who tap instead of hover
quickActionsBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  if (quickActionsWrap.classList.contains("open")) {
    closeQuickActions();
  } else {
    openQuickActions();
  }
});

// Click outside closes it
document.addEventListener("click", (e) => {
  if (!quickActionsWrap.contains(e.target)) {
    closeQuickActions();
  }
});

// Escape closes it
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeQuickActions();
});

// Placeholder handlers — backend wiring comes later.
function handleQuickAction(action) {
  closeQuickActions();

  const labels = {
    "summarise-page": "Summarise Page",
    "organise-tabs": "Organise Tabs",
    "find-more-like-this": "Find More Like This",
    "reading-lists": "Your Reading Lists",
    "saved-collections": "Saved Collections",
  };

  addMessage(`"${labels[action] || action}" is coming soon.`, "system");
}

qaItems.forEach((item) => {
  item.addEventListener("click", () => handleQuickAction(item.dataset.action));
});

// ── Drag & Drop Infrastructure ─────────────────────────────────────────

// Detect a dragged item passing into the panel window
window.addEventListener("dragenter", (e) => {
  e.preventDefault();
  dragOverlay.classList.add("active");
});

// Dragover must be prevented for drop events to execute properly
dragOverlay.addEventListener("dragover", (e) => {
  e.preventDefault();
});

// Hide the drop overlay if the user drags out of the sidepanel
dragOverlay.addEventListener("dragleave", (e) => {
  // Only trigger leave if it leaves the entire overlay bounding container
  if (e.relatedTarget === null || !dragOverlay.contains(e.relatedTarget)) {
    dragOverlay.classList.remove("active");
  }
});

// Drop execution logic
dragOverlay.addEventListener("drop", (e) => {
  e.preventDefault();
  dragOverlay.classList.remove("active");

  const droppedText = e.dataTransfer.getData("text/plain");
  if (!droppedText || droppedText.trim() === "") return;

  // Check if we dropped over the Context Zone specifically
  if (e.target.closest("#zone-context")) {
    attachedContexts.push(droppedText.trim());
    renderContextShelf();
  }
});

// Re-renders the graphical shelf of context chips
function renderContextShelf() {
  contextShelf.innerHTML = "";
  if (attachedContexts.length === 0) {
    contextShelf.classList.add("hidden");
    return;
  }
  
  contextShelf.classList.remove("hidden");
  
  attachedContexts.forEach((text, index) => {
    const chip = document.createElement("div");
    chip.className = "context-chip";
    
    const label = document.createElement("span");
    label.textContent = text;
    
    const closeBtn = document.createElement("div");
    closeBtn.className = "context-close";
    closeBtn.innerHTML = "&times;";
    closeBtn.addEventListener("click", () => {
      attachedContexts.splice(index, 1);
      renderContextShelf();
    });
    
    chip.appendChild(label);
    chip.appendChild(closeBtn);
    contextShelf.appendChild(chip);
  });
}

// ── Init ──────────────────────────────────────────────────────────────
(async () => {
  // Trigger entry glass scaling immediately on window paint
  requestAnimationFrame(() => {
    if (appWrap) appWrap.classList.add("ready");
  });

  currentTab = await getActiveTabInfo();

  if (currentTab.id == null) {
    addMessage("Couldn't identify the active tab.", "system");
    return;
  }

  const history = await loadHistory(currentTab.id);
  for (const m of history) {
    addMessage(m.text, m.role === "user" ? "user" : "ai");
  }

  connectSocket(currentTab.id);
  inputEl.focus();
})();
