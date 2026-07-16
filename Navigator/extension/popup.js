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

  if (role === "ai") {
    // AI replies are asked to format with markdown — render it.
    // User/system text stays as plain textContent (safer, and it's
    // literal input, not something meant to be styled).
    el.innerHTML = renderMarkdown(text);
  } else {
    el.textContent = text;
  }

  messagesEl.appendChild(el);

  // Quick fade-in layout bump
  requestAnimationFrame(() => {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  });
}

// ── Minimal Markdown Renderer ────────────────────────────────────────
// Small, dependency-free subset covering what chat replies actually use:
// headings, bold/italic, inline code, fenced code blocks, links,
// bullet/numbered lists, and paragraph breaks. HTML is escaped FIRST,
// so raw markup in the model's output can never inject into the DOM —
// only the tags this function deliberately adds ever render.
function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderInlineMarkdown(text) {
  let out = escapeHtml(text);

  // Inline code: `code`
  out = out.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Bold: **text** or __text__
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/__([^_]+)__/g, '<strong>$1</strong>');

  // Italic: *text* or _text_ (after bold, so ** isn't eaten by *)
  out = out.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  out = out.replace(/(^|[^\w])_([^_]+)_(?!\w)/g, '$1<em>$2</em>');

  // Links: [label](url) — only allow http(s) targets
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

  return out;
}

function renderMarkdown(raw) {
  if (!raw) return "";

  const lines = raw.replace(/\r\n/g, "\n").split("\n");
  const htmlParts = [];

  let inCodeBlock = false;
  let codeLines = [];
  let listBuffer = [];
  let listType = null; // "ul" | "ol"

  function flushList() {
    if (!listBuffer.length) return;
    const tag = listType === "ol" ? "ol" : "ul";
    htmlParts.push(`<${tag}>` + listBuffer.map(li => `<li>${renderInlineMarkdown(li)}</li>`).join("") + `</${tag}>`);
    listBuffer = [];
    listType = null;
  }

  for (const line of lines) {
    // Fenced code blocks: ```
    if (/^```/.test(line.trim())) {
      if (!inCodeBlock) {
        flushList();
        inCodeBlock = true;
        codeLines = [];
      } else {
        htmlParts.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        inCodeBlock = false;
      }
      continue;
    }
    if (inCodeBlock) {
      codeLines.push(line);
      continue;
    }

    const trimmed = line.trim();

    if (trimmed === "") {
      flushList();
      continue;
    }

    // Headings: #, ##, ###
    const headingMatch = /^(#{1,3})\s+(.*)$/.exec(trimmed);
    if (headingMatch) {
      flushList();
      const level = headingMatch[1].length;
      htmlParts.push(`<h${level}>${renderInlineMarkdown(headingMatch[2])}</h${level}>`);
      continue;
    }

    // Bullet list items: -, *, •
    const bulletMatch = /^[-*•]\s+(.*)$/.exec(trimmed);
    if (bulletMatch) {
      if (listType && listType !== "ul") flushList();
      listType = "ul";
      listBuffer.push(bulletMatch[1]);
      continue;
    }

    // Numbered list items: 1. , 2. , etc.
    const orderedMatch = /^\d+\.\s+(.*)$/.exec(trimmed);
    if (orderedMatch) {
      if (listType && listType !== "ol") flushList();
      listType = "ol";
      listBuffer.push(orderedMatch[1]);
      continue;
    }

    // Plain paragraph line
    flushList();
    htmlParts.push(`<p>${renderInlineMarkdown(trimmed)}</p>`);
  }

  // Close out any dangling code block or list at end of input
  if (inCodeBlock && codeLines.length) {
    htmlParts.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
  }
  flushList();

  return htmlParts.join("");
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