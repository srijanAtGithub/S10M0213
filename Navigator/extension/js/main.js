import { NotificationService } from "./notifications.js";
import { addMessage, clearMessagesUI, addContextTrail, setSending, sendBtn, appWrap } from "./ui.js";
import { socket, getActiveTabInfo, loadHistory, clearHistoryOnBackend, connectSocket } from "./api.js";
import { attachedContexts, clearAttachedContexts } from "./features.js";
import { getMentionedTabSnippet, hasMentionedTab, clearMentionedTab, isMentionDropdownOpen } from "./mentions.js";

const inputEl = document.getElementById("input-box");
const clearBtn = document.getElementById("clear-btn");
let currentTab = { id: null, url: "", title: "" };

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text) return;

  if (!socket || socket.readyState !== WebSocket.OPEN) {
    addMessage("Not connected to backend yet.", "system");
    return;
  }

  // The mentioned tab's full page content rides alongside any manually
  // attached snippets (drag/drop, summaries) as one more context_snippets
  // entry — the backend already folds every snippet into the turn
  // generically, so no bridge/graph changes are needed for this.
  const mentionSnippet = getMentionedTabSnippet();
  const outgoingSnippets = mentionSnippet
    ? [...attachedContexts, mentionSnippet]
    : attachedContexts;

  addContextTrail(outgoingSnippets);
  addMessage(text, "user");
  inputEl.value = "";
  setSending(true);

  const fresh = await getActiveTabInfo();
  currentTab.url = fresh.url;
  currentTab.title = fresh.title;

  const payload = {
    text: text,
    page_url: currentTab.url,
    page_title: currentTab.title,
    context_snippets: outgoingSnippets
  };

  socket.send(JSON.stringify(payload));
  clearAttachedContexts();
  if (hasMentionedTab()) clearMentionedTab();
}

async function handleClear() {
  if (currentTab.id == null) return;
  const ok = await clearHistoryOnBackend(currentTab.id);
  if (ok) {
    clearMessagesUI();
    NotificationService.show("Conversation cleared.");
  }
}

sendBtn.addEventListener("click", sendMessage);
clearBtn.addEventListener("click", handleClear);
inputEl.addEventListener("keydown", (e) => {
  // Don't send the message if Enter was meant to pick a highlighted
  // tab in the @-mention dropdown instead.
  if (e.key === "Enter" && !isMentionDropdownOpen()) sendMessage();
});

(async () => {
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
