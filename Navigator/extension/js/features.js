import { NotificationService } from "./notifications.js";
import { appWrap } from "./ui.js";
import { BACKEND_HOST } from "./api.js";

const quickActionsWrap = document.getElementById("quick-actions");
const quickActionsBtn = document.getElementById("quick-actions-btn");
const quickActionsMenu = document.getElementById("quick-actions-menu");
const qaItems = document.querySelectorAll(".qa-item");

const dragOverlay = document.getElementById("drag-overlay");
const zoneContext = document.getElementById("zone-context");
const zoneCollections = document.getElementById("zone-collections");
const contextShelf = document.getElementById("context-shelf");

const organiseTabsOverlay = document.getElementById("organise-tabs-overlay");
const btnIncludeGrouped = document.getElementById("btn-include-grouped");
const btnOnlyUngrouped = document.getElementById("btn-only-ungrouped");

const summaryOverlay = document.getElementById("summary-overlay");
const summaryContent = document.getElementById("summary-content");
const btnOkSummary = document.getElementById("btn-ok-summary");
const btnAddChatSummary = document.getElementById("btn-add-chat-summary");

// Wire up the OK button to just close the overlay
btnOkSummary?.addEventListener("click", () => {
  summaryOverlay.classList.remove("active");
});

// Wire up the "Add to Chat" button
btnAddChatSummary?.addEventListener("click", () => {
  const summaryText = summaryContent.textContent;

  if (summaryText && summaryText.trim() !== "") {
    // 1. Push it into the existing context array
    attachedContexts.push(summaryText.trim());

    // 2. Re-render the visual UI shelf at the bottom dock
    renderContextShelf();

    // 3. Notify the user it was successful
    NotificationService.show("Summary added to context.");
  }

  // Close the window
  summaryOverlay.classList.remove("active");
});

export let attachedContexts = [];
let expandedContextIndices = new Set();
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

quickActionsWrap.addEventListener("mouseenter", openQuickActions);
quickActionsMenu.addEventListener("mouseenter", openQuickActions);
quickActionsWrap.addEventListener("mouseleave", () => closeQuickActions(220));

quickActionsBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  if (quickActionsWrap.classList.contains("open")) {
    closeQuickActions();
  } else {
    openQuickActions();
  }
});

document.addEventListener("click", (e) => {
  if (!quickActionsWrap.contains(e.target)) {
    closeQuickActions();
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeQuickActions();
});

function handleQuickAction(action) {
  closeQuickActions();
  if (action === "organise-tabs") {
    showOrganiseTabsOverlay();
    return;
  }
  if (action === "summarise-page") {
    startSummarisePage();
    return;
  }
  const labels = {
    "summarise-page": "Summarise Page",
    "find-more-like-this": "Find More Like This",
    "reading-lists": "Your Reading Lists",
    "saved-collections": "Saved Collections",
  };
  const actionName = labels[action] || action;
  NotificationService.show(`"${actionName}" is coming soon.`);
}

qaItems.forEach((item) => {
  item.addEventListener("click", () => handleQuickAction(item.dataset.action));
});

function setDragHoverState(targetZone) {
  zoneContext.classList.toggle("drag-hover", targetZone === "context");
  zoneCollections.classList.toggle("drag-hover", targetZone === "collections");
}

function clearDragHoverState() {
  zoneContext.classList.remove("drag-hover");
  zoneCollections.classList.remove("drag-hover");
}

window.addEventListener("dragenter", (e) => {
  e.preventDefault();
  dragOverlay.classList.add("active");
});

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

dragOverlay.addEventListener("dragleave", (e) => {
  if (e.relatedTarget === null || !dragOverlay.contains(e.relatedTarget)) {
    dragOverlay.classList.remove("active");
    clearDragHoverState();
  }
});

dragOverlay.addEventListener("drop", (e) => {
  e.preventDefault();
  dragOverlay.classList.remove("active");
  const droppedText = e.dataTransfer.getData("text/plain");
  if (!droppedText || droppedText.trim() === "") {
    clearDragHoverState();
    return;
  }
  if (e.target.closest("#zone-context")) {
    attachedContexts.push(droppedText.trim());
    renderContextShelf();
  }
  clearDragHoverState();
});

export function makeContextLabel(text) {
  const cleaned = text.replace(/\s+/g, " ").trim();
  const maxLen = 42;
  return cleaned.length > maxLen ? cleaned.slice(0, maxLen).trimEnd() + "…" : cleaned;
}

export function renderContextShelf() {
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
      e.stopPropagation();
      attachedContexts.splice(index, 1);
      expandedContextIndices.delete(index);
      renderContextShelf();
    });

    head.appendChild(icon);
    head.appendChild(label);
    head.appendChild(caret);
    head.appendChild(closeBtn);

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

function showOrganiseTabsOverlay() {
  organiseTabsOverlay.classList.add("active");
}

function hideOrganiseTabsOverlay() {
  organiseTabsOverlay.classList.remove("active");
}

organiseTabsOverlay.addEventListener("click", (e) => {
  if (!e.target.closest(".organise-card")) hideOrganiseTabsOverlay();
});

async function startOrganiseTabs(includeGrouped) {
  hideOrganiseTabsOverlay();
  NotificationService.show("Organising your tabs...");
  appWrap.classList.add("busy");

  try {
    const tabs = await chrome.tabs.query({ currentWindow: true });
    let filteredTabs = tabs;
    if (!includeGrouped) {
      filteredTabs = tabs.filter(tab => tab.groupId === -1 || tab.groupId === (chrome.tabGroups ? chrome.tabGroups.TAB_GROUP_ID_NONE : -1));
    }

    if (filteredTabs.length === 0) {
      appWrap.classList.remove("busy");
      NotificationService.show("No tabs found that need organising.");
      return;
    }

    const tabData = filteredTabs.map(tab => ({
      id: tab.id,
      title: tab.title || "",
      url: tab.url || "",
      groupId: tab.groupId
    }));

    const response = await fetch(`http://${BACKEND_HOST}/organise-tabs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tabs: tabData })
    });

    if (!response.ok) throw new Error(`HTTP Error Status: ${response.status}`);

    const data = await response.json();

    if (data.groups && data.groups.length > 0) {
      for (const groupPlan of data.groups) {
        const { title, color, tab_ids } = groupPlan;
        if (tab_ids && tab_ids.length > 0) {
          try {
            const groupId = await chrome.tabs.group({ tabIds: tab_ids });
            await chrome.tabGroups.update(groupId, {
              title: title,
              color: color
            });
          } catch (groupError) {
            console.warn("Failed to complete an individual tab group structure", groupError);
          }
        }
      }
      appWrap.classList.remove("busy");
      NotificationService.show(`Successfully organised tabs into ${data.groups.length} groups!`);
    } else {
      appWrap.classList.remove("busy");
      NotificationService.show("FastAPI LLM suggested no groupings.");
    }
  } catch (err) {
    appWrap.classList.remove("busy");
    console.error("Error organizing tabs:", err);
    NotificationService.show("Could not contact the Sicily Bridge. Is uvicorn active?");
  }
}

btnIncludeGrouped.addEventListener("click", () => startOrganiseTabs(true));
btnOnlyUngrouped.addEventListener("click", () => startOrganiseTabs(false));

export function clearAttachedContexts() {
  attachedContexts.length = 0;
  renderContextShelf();
}

async function startSummarisePage() {
  // 1. Show notification and trigger neural glow
  NotificationService.show("Summarising page...");
  appWrap.classList.add("busy");

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) throw new Error("No active tab found.");

    // 2. Pre-LLM Extraction Layer (Domestic Chores)
    const injectionResult = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        // Strip out useless structural tags before grabbing text
        const clone = document.cloneNode(true);
        const tagsToRemove = ['nav', 'footer', 'aside', 'script', 'style', 'noscript', 'header'];
        tagsToRemove.forEach(tag => {
          const elements = clone.querySelectorAll(tag);
          elements.forEach(el => el.parentNode?.removeChild(el));
        });

        // Truncate to ~5000 characters to cap tokens (Smart Cap)
        return clone.body ? clone.body.innerText.substring(0, 5000) : "";
      }
    });

    const cleanText = injectionResult[0]?.result || "";

    if (!cleanText) {
      throw new Error("Could not extract text from this page.");
    }

    // 3. API Call to the dedicated nano-model route
    const response = await fetch(`http://${BACKEND_HOST}/summarise-page`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: tab.url,
        title: tab.title,
        content: cleanText
      })
    });

    if (!response.ok) throw new Error(`HTTP Error Status: ${response.status}`);

    const data = await response.json();

    // 4. Show the plain text result in the glassmorphism overlay
    summaryContent.textContent = data.summary;
    summaryOverlay.classList.add("active");

  } catch (err) {
    console.error("Summarise error:", err);
    NotificationService.show("Failed to summarise page. Is the backend running?");
  } finally {
    // Stop the neural glow
    appWrap.classList.remove("busy");
  }
}