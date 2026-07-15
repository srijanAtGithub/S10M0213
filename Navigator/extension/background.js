/**
 * background.js
 * -------------
 * Demo scope (no AI): one job — clean up a tab's conversation on the
 * backend the moment that tab actually closes.
 *
 * The popup talks directly to the local Python backend over WebSocket/REST
 * and reads the active tab itself via chrome.tabs — for this baby-steps
 * demo there's no need for a background-script relay for chat traffic.
 *
 * But tab-close is the one thing only the background script can reliably
 * observe (the popup isn't necessarily open when a tab closes — the user
 * could've closed the popup ages ago and then closed the tab). So that's
 * the one thing this file does: tell the backend "tab X is gone, forget
 * its conversation."
 *
 * This file also owns the "Edit with Navigator" right-click flow — a
 * second job, unrelated to the tab-chat session cleanup above:
 *   - registers the context menu item
 *   - on click, tells content_script.js (in that tab) to open the
 *     floating edit box near the current selection
 *   - relays content_script.js's edit request to the backend's
 *     POST /edit-selection and hands the result back
 *
 * That relay exists because content scripts run in the page's own
 * context and their fetches are subject to the page's CSP, which can
 * block arbitrary cross-origin requests on some sites. Extension pages
 * (background.js included) aren't subject to page CSP, so routing the
 * fetch through here is the reliable path — same reasoning as why the
 * popup already talks to the backend directly rather than through a
 * content script.
 *
 * This one-shot edit flow is intentionally NOT wired into SessionStore
 * or the tab-chat WebSocket in navigator_bridge.py — it has no
 * conversation to remember, so it doesn't touch tab_id at all.
 */

const BACKEND_HOST = "localhost:8765";
const EDIT_MENU_ID = "sicily-navigator-edit-selection";

function setupContextMenu() {
  // removeAll then create avoids "duplicate id" errors when the service
  // worker re-registers this during development reloads.
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: EDIT_MENU_ID,
      title: "Use Sicily",
      // Only show when text is actually selected. content_script.js does
      // the real classification when clicked (form field vs. plain
      // contenteditable vs. framework-managed rich text vs. plain page
      // text) — background.js has no DOM access to check any of that
      // itself, so this is just the coarse "is there a selection at all" gate.
      contexts: ["selection"],
    });
  });
}

// Enable the side panel to open on extension icon click
chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch((error) => console.error(error));

chrome.runtime.onInstalled.addListener(() => {
  console.log("[Sicily Navigator] extension installed.");
  setupContextMenu();
});

// ── Tab-Specific Side Panel Management ────────────────────────────────
// REMOVE or comment out the old chrome.sidePanel.setPanelBehavior block entirely.

chrome.action.onClicked.addListener((tab) => {
  if (!tab || !tab.id) return;

  // 1. Configure the side panel path strictly for this tab.
  // We do NOT use 'await' here so we don't break the synchronous execution turn.
  chrome.sidePanel.setOptions({
    tabId: tab.id,
    path: "popup.html",
    enabled: true
  });

  // 2. Immediately invoke open() in the exact same code block.
  // This preserves the active user gesture token!
  chrome.sidePanel.open({ tabId: tab.id }).catch((error) => {
    console.error("Failed to open tab-specific side panel:", error);
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === EDIT_MENU_ID && tab && tab.id != null) {
    chrome.tabs.sendMessage(tab.id, { type: "navigator-open-edit-box" });
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type !== "navigator-edit-selection-request") return false;

  fetch(`http://${BACKEND_HOST}/edit-selection`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      selected_text: message.selected_text,
      instruction: message.instruction,
      action_type: message.action_type || "edit"
    }),
  })
    .then((res) => {
      if (!res.ok) throw new Error(`status ${res.status}`);
      return res.json();
    })
    .then((data) => {
      sendResponse({ ok: true, edited_text: data.edited_text });
    })
    .catch((err) => {
      console.log("[Sicily Navigator] edit-selection request failed", err);
      sendResponse({ ok: false, error: "Backend not reachable — is navigator_bridge.py running?" });
    });

  return true; // Important: keeps the channel open for async response
});

chrome.tabs.onRemoved.addListener((tabId) => {
  // Best-effort: if the backend isn't running, there's nothing to clean up
  // anyway (in-memory sessions die with the server), so just log and move on.
  fetch(`http://${BACKEND_HOST}/session/${tabId}`, { method: "DELETE" }).catch((err) => {
    console.log("[Sicily Navigator] couldn't clear session for closed tab", tabId, err);
  });
});