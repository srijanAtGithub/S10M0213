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
  const payloadSnippets = mentionSnippet
    ? [...attachedContexts, mentionSnippet]
    : [...attachedContexts];

  // Fetch the collection text (AI needs this full content)
  if (hasMentionedCollection()) {
    try {
      const res = await fetch(`http://${BACKEND_HOST}/collections/${getMentionedCollectionId()}`);
      if (res.ok) {
        const data = await res.json();
        if (data.snippets && data.snippets.length > 0) {
          const coll_text = data.snippets.map(s => `- ${s.text}`).join("\n\n");
          payloadSnippets.push(`Collection: ${data.name}\n\n${coll_text}`);
        } else {
          payloadSnippets.push(`Collection: ${data.name}\n\n(Empty)`);
        }
      }
    } catch (err) {
      console.error("Failed to load collection text:", err);
    }
  }

  // --- NEW: Create a clean display array for the UI ---
  const displaySnippets = payloadSnippets.map(snippet => {
    // If it starts with our prefixes, take only the header line (before the \n\n)
    if (snippet.startsWith('Tab: ') || snippet.startsWith('Collection: ')) {
      return snippet.split('\n\n')[0];
    }
    // For manual drag/drop, just show the first 30 chars
    return snippet.length > 30 ? snippet.substring(0, 30) + "..." : snippet;
  });

  // Render the UI with only the short labels
  addContextTrail(displaySnippets);

  addMessage(text, "user");
  inputEl.value = "";
  setSending(true);

  // Send the FULL content to the backend
  const fresh = await getActiveTabInfo();
  const payload = {
    text: text,
    page_url: fresh.url,
    page_title: fresh.title,
    context_snippets: payloadSnippets
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
