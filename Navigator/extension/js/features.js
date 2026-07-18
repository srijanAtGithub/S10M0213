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

const collectionsPickerOverlay = document.getElementById("collections-picker-overlay");
const collectionsPickerPreview = document.getElementById("collections-picker-preview");
const collectionsPickerInput = document.getElementById("collections-picker-input");
const collectionsPickerList = document.getElementById("collections-picker-list");
const collectionsCreateBtn = document.getElementById("collections-create-btn");
const collectionsCreateBtnLabel = document.getElementById("collections-create-btn-label");

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

// ── Saved Collections Viewer ──────────────────────────────────────────
const collectionsViewOverlay = document.getElementById("collections-view-overlay");
const collectionsViewList = document.getElementById("collections-view-list");
const collectionsViewTitle = document.getElementById("collections-view-title");
const collectionsViewBackBtn = document.getElementById("collections-view-back-btn");
const collectionsViewActions = document.getElementById("collections-view-actions");

// Clicking the background closes the overlay completely
collectionsViewOverlay?.addEventListener("click", (e) => {
  if (!e.target.closest(".organise-card")) {
    collectionsViewOverlay.classList.remove("active");
  }
});

// The "Back" button returns to the main collections list (Scene A)
collectionsViewBackBtn?.addEventListener("click", openSavedCollectionsView);

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
  if (action === "saved-collections") {
    openSavedCollectionsView();
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
  } else if (e.target.closest("#zone-collections")) {
    openCollectionsPicker(droppedText.trim());
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

// ── Add to Collections Picker ───────────────────────────────────────
// Drag-drop entry point is the "collections" branch in the drop
// handler above. This owns the floating picker: it fetches existing
// collections, filters them live as the user types, and posts to
// /collections/add-snippet whether the user picks an existing
// collection or types a brand new name ("Create & Add").

let pendingSnippet = null; // { text, tab_title, url } captured at drop time
let allCollections = [];   // last fetch from GET /collections

async function openCollectionsPicker(text) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

  pendingSnippet = {
    text,
    tab_title: tab?.title || "",
    url: tab?.url || "",
  };

  collectionsPickerPreview.textContent = text;
  collectionsPickerInput.value = "";
  collectionsPickerOverlay.classList.add("active");

  collectionsPickerList.innerHTML = `<div class="collections-picker-empty">Loading collections…</div>`;
  updateCreateButton("");

  try {
    const res = await fetch(`http://${BACKEND_HOST}/collections`);
    if (!res.ok) throw new Error(`status ${res.status}`);
    const data = await res.json();
    allCollections = data.collections || [];
  } catch (err) {
    allCollections = [];
    collectionsPickerList.innerHTML = `<div class="collections-picker-empty">Couldn't reach the backend — is navigator_bridge.py running?</div>`;
    return;
  }

  renderCollectionsList("");
  collectionsPickerInput.focus();
}

function closeCollectionsPicker() {
  collectionsPickerOverlay.classList.remove("active");
  pendingSnippet = null;
}

collectionsPickerOverlay?.addEventListener("click", (e) => {
  if (!e.target.closest(".organise-card")) {
    closeCollectionsPicker();
  }
});

collectionsPickerInput?.addEventListener("input", () => {
  const query = collectionsPickerInput.value.trim();
  renderCollectionsList(query);
  updateCreateButton(query);
});

collectionsPickerInput?.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const query = collectionsPickerInput.value.trim();
    const filtered = filterCollections(query);
    // Exact existing match on Enter -> add to it directly; otherwise
    // fall through to create-and-add, same as clicking the button.
    const exact = filtered.find((c) => c.name.toLowerCase() === query.toLowerCase());
    if (exact) {
      submitSnippetToCollection(exact.name);
    } else if (query) {
      submitSnippetToCollection(query);
    }
  } else if (e.key === "Escape") {
    closeCollectionsPicker();
  }
});

collectionsCreateBtn?.addEventListener("click", () => {
  const query = collectionsPickerInput.value.trim();
  if (query) submitSnippetToCollection(query);
});

function filterCollections(query) {
  if (!query) return allCollections;
  const q = query.toLowerCase();
  return allCollections.filter((c) => c.name.toLowerCase().includes(q));
}

function renderCollectionsList(query) {
  const filtered = filterCollections(query);
  collectionsPickerList.innerHTML = "";

  if (filtered.length === 0) {
    collectionsPickerList.innerHTML = `<div class="collections-picker-empty">${allCollections.length === 0 ? "No collections yet." : "No matching collections."
      }</div>`;
    return;
  }

  filtered.forEach((c) => {
    const item = document.createElement("button");
    item.className = "collection-picker-item";

    const icon = document.createElement("span");
    icon.className = "qa-icon";
    icon.innerHTML = `<svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M6 3.5h5.5L15 7v9a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>
      <path d="M11 3.5V7h3.5" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>
      <path d="M7.5 10.5h5M7.5 13h3.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
    </svg>`;

    const name = document.createElement("span");
    name.className = "collection-picker-name";
    name.textContent = c.name;

    const count = document.createElement("span");
    count.className = "collection-picker-count";
    count.textContent = c.snippet_count;

    item.appendChild(icon);
    item.appendChild(name);
    item.appendChild(count);

    item.addEventListener("click", () => submitSnippetToCollection(c.name));
    collectionsPickerList.appendChild(item);
  });
}

function updateCreateButton(query) {
  if (!query) {
    collectionsCreateBtn.classList.add("hidden");
    return;
  }
  const exists = allCollections.some((c) => c.name.toLowerCase() === query.toLowerCase());
  if (exists) {
    collectionsCreateBtn.classList.add("hidden");
  } else {
    collectionsCreateBtnLabel.textContent = `Create "${query}" & Add`;
    collectionsCreateBtn.classList.remove("hidden");
  }
}

async function submitSnippetToCollection(collectionName) {
  if (!pendingSnippet) return;
  const snippet = pendingSnippet;
  closeCollectionsPicker();

  try {
    const res = await fetch(`http://${BACKEND_HOST}/collections/add-snippet`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        collection_name: collectionName,
        text: snippet.text,
        tab_title: snippet.tab_title,
        url: snippet.url,
      }),
    });
    if (!res.ok) throw new Error(`status ${res.status}`);
    const data = await res.json();
    NotificationService.show(
      data.created_new_collection
        ? `Created "${data.collection_name}" and added snippet.`
        : `Added to "${data.collection_name}".`
    );
  } catch (err) {
    console.error("Add to collection failed:", err);
    NotificationService.show("Couldn't save to collection — is the backend running?");
  }
}

async function openSavedCollectionsView() {
  collectionsViewOverlay.classList.add("active");
  collectionsViewTitle.textContent = "Saved Collections";

  // SCENE A: Hide the Back button completely
  collectionsViewActions.style.display = "none";

  collectionsViewList.innerHTML = `<div class="collections-picker-empty">Loading collections...</div>`;

  try {
    const res = await fetch(`http://${BACKEND_HOST}/collections`);
    if (!res.ok) throw new Error("Failed to load");
    const data = await res.json();
    renderCollectionsView(data.collections || []);
  } catch (err) {
    collectionsViewList.innerHTML = `<div class="collections-picker-empty">Backend unreachable.</div>`;
  }
}

function renderCollectionsView(collections) {
  collectionsViewList.innerHTML = "";
  if (collections.length === 0) {
    collectionsViewList.innerHTML = `<div class="collections-picker-empty">No saved collections yet.</div>`;
    return;
  }

  collections.forEach(c => {
    const item = document.createElement("button");
    item.className = "collection-view-item";
    item.innerHTML = `
      <div style="padding-right: 32px; text-align: left;">
        <div style="font-weight: 600;">${c.name}</div>
        <div style="font-size: 11px; color: #8e8e93; margin-top: 3px;">${c.snippet_count} saved snippet${c.snippet_count !== 1 ? 's' : ''}</div>
      </div>
      <div class="item-delete-btn" title="Delete Collection">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
          <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
        </svg>
      </div>
    `;

    item.addEventListener("click", (e) => {
      e.stopPropagation();
      openCollectionSnippets(c.id, c.name);
    });

    const deleteBtn = item.querySelector('.item-delete-btn');
    deleteBtn.addEventListener('click', async (e) => {
      e.stopPropagation(); // Stop the card click from firing
      if (!confirm(`Delete collection "${c.name}"?`)) return;

      try {
        const res = await fetch(`http://${BACKEND_HOST}/collections/${c.id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error("Failed to delete");

        item.remove();
        NotificationService.show(`Deleted "${c.name}"`);
      } catch (err) {
        NotificationService.show("Error deleting collection.");
      }
    });

    collectionsViewList.appendChild(item);
  });
}

async function openCollectionSnippets(collectionId, collectionName) {
  collectionsViewTitle.textContent = collectionName;

  // SCENE B: Show the Back button
  collectionsViewActions.style.display = "flex";

  collectionsViewList.innerHTML = `<div class="collections-picker-empty">Loading snippets...</div>`;

  try {
    const res = await fetch(`http://${BACKEND_HOST}/collections/${collectionId}`);
    if (!res.ok) throw new Error("Failed to load");
    const data = await res.json();
    renderSnippetsView(data.snippets || []);
  } catch (err) {
    collectionsViewList.innerHTML = `<div class="collections-picker-empty">Error loading snippets.</div>`;
  }
}

function renderSnippetsView(snippets) {
  collectionsViewList.innerHTML = "";
  if (snippets.length === 0) {
    collectionsViewList.innerHTML = `<div class="collections-picker-empty">This collection is empty.</div>`;
    return;
  }

  snippets.forEach(s => {
    const item = document.createElement("div");
    item.className = "snippet-view-item";
    const dateStr = new Date(s.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });

    item.innerHTML = `
      <div style="padding-right: 32px;">
        <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 4px;">
          <div style="font-weight: 600; font-size: 11.5px; color: #e5e5ea; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 80%;">${s.tab_title || 'Unknown Source'}</div>
          <div style="font-size: 10px; color: #636366;">${dateStr}</div>
        </div>
        <div class="snippet-text">${s.text}</div>
      </div>
      <div class="item-delete-btn" title="Delete Snippet">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
          <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
        </svg>
      </div>
    `;

    const deleteBtn = item.querySelector('.item-delete-btn');
    deleteBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm("Delete this snippet?")) return;

      try {
        const res = await fetch(`http://${BACKEND_HOST}/collections/snippets/${s.id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error("Failed to delete");

        item.remove();
        NotificationService.show("Snippet deleted.");
      } catch (err) {
        NotificationService.show("Error deleting snippet.");
      }
    });

    collectionsViewList.appendChild(item);
  });
}