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
const disconnectedScreen = document.getElementById("disconnected-screen");

let attachedContexts = [];

let socket = null;
let currentTab = { id: null, url: "", title: "" };

function addMessage(text, role) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;

  // Plain text for both AI and user/system messages — no markdown
  // parsing or HTML rendering involved.
  el.textContent = text;

  messagesEl.appendChild(el);

  // Quick fade-in layout bump
  requestAnimationFrame(() => {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  });
}

// Renders the context snippets that accompanied a sent message, as small
// read-only chips sitting just above the user's bubble in the thread.
function addContextTrail(snippets) {
  if (!snippets || snippets.length === 0) return;

  const trail = document.createElement("div");
  trail.className = "msg-context-trail";

  snippets.forEach((text) => {
    const chip = document.createElement("div");
    chip.className = "context-trail-chip";
    chip.title = text;

    const icon = document.createElement("span");
    icon.className = "context-trail-icon";
    icon.innerHTML = `<svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M4 3.5h9l3 3v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-12a1 1 0 0 1 1-1z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>
      <path d="M13 3.5v3h3" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>
      <path d="M6.5 10.5h7M6.5 13h7M6.5 8h3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
    </svg>`;

    const label = document.createElement("span");
    label.className = "context-trail-label";
    label.textContent = makeContextLabel(text);

    chip.appendChild(icon);
    chip.appendChild(label);
    trail.appendChild(chip);
  });

  messagesEl.appendChild(trail);

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

  if (state === "connected") {
    showOnline();
  } else if (state === "disconnected") {
    showOffline();
  }
}

function showOffline() {
  appWrap.classList.add("offline");
  disconnectedScreen.classList.add("visible");
}

function showOnline() {
  appWrap.classList.remove("offline");
  disconnectedScreen.classList.remove("visible");
  disconnectedScreen.classList.remove("retrying");
}

function setSending(isSending) {
  sendBtn.disabled = isSending;

  const icon    = document.getElementById("send-icon");
  const spinner = document.getElementById("send-spinner");
  if (icon)    icon.style.display    = isSending ? "none"         : "";
  if (spinner) spinner.style.display = isSending ? "inline-block" : "none";

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

  addContextTrail(attachedContexts);
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

    NotificationService.show(
      "Conversation cleared."
    );
  }
}

sendBtn.addEventListener("click", sendMessage);
clearBtn.addEventListener("click", handleClear);
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendMessage();
});

// ── Send Button Proximity Reveal ─────────────────────────────────────────
// The send button starts hidden. When the user moves their mouse near the
// input box (or the bottom-right corner of the panel), we spring-animate it
// out as if it's emerging from the text field itself.

const inputRow = document.getElementById("input-row");
const PROXIMITY_THRESHOLD = 90; // px — how close the cursor must get
let sendBtnRevealTimer = null;

function revealSendBtn() {
  clearTimeout(sendBtnRevealTimer);
  sendBtn.classList.add("revealed");
}

function hideSendBtn(delay = 400) {
  clearTimeout(sendBtnRevealTimer);
  sendBtnRevealTimer = setTimeout(() => {
    // Don't hide if the cursor is still over the button itself
    if (!sendBtn.matches(":hover")) {
      sendBtn.classList.remove("revealed");
    }
  }, delay);
}

// Keep revealed while hovering over the button itself
sendBtn.addEventListener("mouseenter", revealSendBtn);
sendBtn.addEventListener("mouseleave", () => hideSendBtn(300));

// Proximity detection on the whole document — fires whenever the cursor
// is within PROXIMITY_THRESHOLD pixels of the input row's bounding rect.
document.addEventListener("mousemove", (e) => {
  const rect = inputRow.getBoundingClientRect();

  // Compute shortest distance from cursor to the input-row rectangle
  const dx = Math.max(rect.left - e.clientX, 0, e.clientX - rect.right);
  const dy = Math.max(rect.top - e.clientY, 0, e.clientY - rect.bottom);
  const dist = Math.sqrt(dx * dx + dy * dy);

  if (dist <= PROXIMITY_THRESHOLD) {
    revealSendBtn();
  } else {
    hideSendBtn(300);
  }
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

  const actionName = labels[action] || action;

  // Call the new notification service instead of dirtying the chat history!
  NotificationService.show(`"${actionName}" is coming soon.`);
}

qaItems.forEach((item) => {
  item.addEventListener("click", () => handleQuickAction(item.dataset.action));
});

// ── Drag & Drop Infrastructure ─────────────────────────────────────────

function setDragHoverState(targetZone) {
  zoneContext.classList.toggle("drag-hover", targetZone === "context");
  zoneCollections.classList.toggle("drag-hover", targetZone === "collections");
}

function clearDragHoverState() {
  zoneContext.classList.remove("drag-hover");
  zoneCollections.classList.remove("drag-hover");
}

// Detect a dragged item passing into the panel window
window.addEventListener("dragenter", (e) => {
  e.preventDefault();
  dragOverlay.classList.add("active");
});

// Dragover must be prevented for drop events to execute properly
dragOverlay.addEventListener("dragover", (e) => {
  e.preventDefault();

  const overContext = !!e.target.closest("#zone-context");
  const overCollections = !!e.target.closest("#zone-collections");

  if (overContext) {
    setDragHoverState("context");
  } else if (overCollections) {
    setDragHoverState("collections");
  } else {
    clearDragHoverState();
  }
});

// Hide the drop overlay if the user drags out of the sidepanel
dragOverlay.addEventListener("dragleave", (e) => {
  // Only trigger leave if it leaves the entire overlay bounding container
  if (e.relatedTarget === null || !dragOverlay.contains(e.relatedTarget)) {
    dragOverlay.classList.remove("active");
    clearDragHoverState();
  }
});

// Drop execution logic
dragOverlay.addEventListener("drop", (e) => {
  e.preventDefault();
  dragOverlay.classList.remove("active");

  const droppedText = e.dataTransfer.getData("text/plain");
  if (!droppedText || droppedText.trim() === "") {
    clearDragHoverState();
    return;
  }

  // Check if we dropped over the Context Zone specifically
  if (e.target.closest("#zone-context")) {
    attachedContexts.push(droppedText.trim());
    renderContextShelf();
  }

  clearDragHoverState();
});

// Tracks which chip(s) are currently expanded across re-renders,
// so toggling one doesn't collapse everything and lose the user's place.
let expandedContextIndices = new Set();

// Builds a short one-line label from a context snippet (Gemini-style summary line)
function makeContextLabel(text) {
  const cleaned = text.replace(/\s+/g, " ").trim();
  const maxLen = 42;
  return cleaned.length > maxLen ? cleaned.slice(0, maxLen).trimEnd() + "…" : cleaned;
}

// Re-renders the graphical shelf of context chips
function renderContextShelf() {
  contextShelf.innerHTML = "";
  if (attachedContexts.length === 0) {
    contextShelf.classList.add("hidden");
    expandedContextIndices.clear();
    return;
  }

  contextShelf.classList.remove("hidden");

  attachedContexts.forEach((text, index) => {
    const isExpanded = expandedContextIndices.has(index);

    const chip = document.createElement("div");
    chip.className = "context-chip" + (isExpanded ? " expanded" : "");

    // ── Header: icon + short label + caret + close ──
    const head = document.createElement("div");
    head.className = "context-chip-head";

    const icon = document.createElement("div");
    icon.className = "context-chip-icon";
    icon.innerHTML = `<svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M4 3.5h9l3 3v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-12a1 1 0 0 1 1-1z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>
      <path d="M13 3.5v3h3" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>
      <path d="M6.5 10.5h7M6.5 13h7M6.5 8h3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
    </svg>`;

    const label = document.createElement("span");
    label.className = "context-chip-label";
    label.textContent = makeContextLabel(text);

    const caret = document.createElement("div");
    caret.className = "context-chip-caret";
    caret.innerHTML = `<svg viewBox="0 0 12 8" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M1 1.5L6 6.5L11 1.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;

    const closeBtn = document.createElement("div");
    closeBtn.className = "context-close";
    closeBtn.innerHTML = "&times;";
    closeBtn.title = "Remove";
    closeBtn.addEventListener("click", (e) => {
      e.stopPropagation(); // don't trigger expand/collapse
      attachedContexts.splice(index, 1);
      expandedContextIndices.delete(index);
      renderContextShelf();
    });

    head.appendChild(icon);
    head.appendChild(label);
    head.appendChild(caret);
    head.appendChild(closeBtn);

    // ── Body: full text, revealed on expand ──
    const body = document.createElement("div");
    body.className = "context-chip-body";

    const bodyInner = document.createElement("div");
    bodyInner.className = "context-chip-body-inner";

    const fullText = document.createElement("div");
    fullText.className = "context-chip-text";
    fullText.textContent = text;

    bodyInner.appendChild(fullText);
    body.appendChild(bodyInner);

    chip.appendChild(head);
    chip.appendChild(body);

    // Click anywhere on the card (except close) toggles expand/collapse
    chip.addEventListener("click", () => {
      if (expandedContextIndices.has(index)) {
        expandedContextIndices.delete(index);
      } else {
        expandedContextIndices.add(index);
      }
      renderContextShelf();
    });

    contextShelf.appendChild(chip);
  });
}

// ── Reusable Glass Notification Service ─────────────────────────────────
const NotificationService = {
  container: null,

  // Lazy-initializes the container within the premium wrapper
  initContainer() {
    if (this.container) return;

    // We append inside the "app-wrap" container so it honors any rounding/constraints
    const appWrap = document.getElementById("app-wrap") || document.body;
    this.container = document.createElement("div");
    this.container.id = "notification-container";
    appWrap.appendChild(this.container);
  },

  /**
   * Displays a beautiful glass toast notification.
   * @param {string} message - The text content to display.
   * @param {number} duration - Time in milliseconds before it starts hiding (default: 3000ms).
   */
  show(message, duration = 3000) {
    this.initContainer();

    const toast = document.createElement("div");
    toast.className = "glass-notification";
    toast.textContent = message;

    this.container.appendChild(toast);

    // Force reflow to trigger CSS transitions
    toast.offsetHeight;

    // Slide and fade in
    toast.classList.add("show");

    // Phase 1: Fade & Slide out
    const hideTimeout = setTimeout(() => {
      toast.classList.remove("show");

      // Phase 2: Complete DOM removal after transition finishes (300ms matches CSS transition)
      toast.addEventListener("transitionend", () => {
        toast.remove();
      });
    }, duration);

    // Optional click-to-dismiss early
    toast.style.cursor = "pointer";
    toast.addEventListener("click", () => {
      clearTimeout(hideTimeout);
      toast.classList.remove("show");
      toast.addEventListener("transitionend", () => {
        toast.remove();
      });
    });
  }
};

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
    if (m.role === "user" && Array.isArray(m.context_snippets) && m.context_snippets.length) {
      addContextTrail(m.context_snippets);
    }
    addMessage(m.text, m.role === "user" ? "user" : "ai");
  }

  connectSocket(currentTab.id);
  inputEl.focus();
})();
