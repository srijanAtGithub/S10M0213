import { NotificationService } from "./notifications.js";
import { addMessage, clearMessagesUI, addContextTrail, setSending, sendBtn, appWrap } from "./ui.js";
import { socket, getActiveTabInfo, loadHistory, clearHistoryOnBackend, connectSocket, BACKEND_HOST } from "./api.js";
import { attachedContexts, clearAttachedContexts } from "./features.js";
import {
  getMentionedTabSnippet, hasMentionedTab, clearMentionedTab, isMentionDropdownOpen,
  hasMentionedCollection, getMentionedCollectionId, clearMentionedCollection
} from "./mentions.js";

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

  const mentionSnippet = getMentionedTabSnippet();

  // Copy attachedContexts into a new array so we can safely push to it
  const outgoingSnippets = mentionSnippet
    ? [...attachedContexts, mentionSnippet]
    : [...attachedContexts];

  // Fetch the collection text on the frontend so the UI can render it immediately!
  if (hasMentionedCollection()) {
    try {
      const res = await fetch(`http://${BACKEND_HOST}/collections/${getMentionedCollectionId()}`);
      if (res.ok) {
        const data = await res.json();
        if (data.snippets && data.snippets.length > 0) {
          const coll_text = data.snippets.map(s => `- ${s.text}`).join("\n\n");
          outgoingSnippets.push(`Collection: ${data.name}\n\n${coll_text}`);
        } else {
          outgoingSnippets.push(`Collection: ${data.name} (Empty)`);
        }
      }
    } catch (err) {
      console.error("Failed to load collection text:", err);
    }
  }

  // Render the UI chips (this will now render the collection visually!)
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
  if (hasMentionedCollection()) clearMentionedCollection();
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
