import { NotificationService } from "./notifications.js";

/**
 * @-mention tab picker.
 *
 * Typing "@" in the input box opens a dynamic dropdown of every open tab
 * (across all windows). Picking one:
 *   1. Removes the "@" (and whatever partial filter text followed it)
 *      from the input.
 *   2. Extracts that tab's full page text (same DOM-cleaning approach as
 *      Summarise Page, but WITHOUT the summarisation call and WITHOUT the
 *      5000-char cap — this is the raw page content, sent straight to chat
 *      as-is on the next message).
 *   3. Shows a single "Using: <tab title>" bar just above the input row.
 *
 * Re-mentioning a tab REPLACES the current mention (there is only ever one
 * mentioned tab at a time) rather than stacking, per spec.
 */

const inputEl = document.getElementById("input-box");
const bottomDock = document.getElementById("bottom-dock");
const inputRow = document.getElementById("input-row");

// ── State ────────────────────────────────────────────────────────────
// The currently mentioned tab's extracted content + metadata, or null.
let mentionedTab = null; // { tabId, title, url, favIconUrl, content }

// Mention-typing state: are we mid "@filter" in the input right now?
let mentionActive = false;
let mentionStartIndex = -1; // index of the "@" character in inputEl.value

let dropdownEl = null;
let allTabsCache = []; // refreshed each time the dropdown opens
let highlightedIndex = 0;

// ── Public API ───────────────────────────────────────────────────────
export function getMentionedTabContent() {
    return mentionedTab ? mentionedTab.content : null;
}

export function getMentionedTabSnippet() {
    // Framed the same way attachedContexts snippets read on the wire, so
    // the backend (which just folds context_snippets into the turn) needs
    // no changes at all.
    if (!mentionedTab) return null;
    return `[Full page content — Tab: "${mentionedTab.title}"]\n\n${mentionedTab.content}`;
}

export function hasMentionedTab() {
    return !!mentionedTab;
}

export function clearMentionedTab({ animate = true } = {}) {
    if (!mentionedTab) return;
    mentionedTab = null;
    hideUsingBar(animate);
}

export function isMentionDropdownOpen() {
    return mentionActive && !!dropdownEl && dropdownEl.classList.contains("mention-dropdown--visible");
}

// ── "Using: tab" bar ─────────────────────────────────────────────────
let usingBarEl = null;

function ensureUsingBarEl() {
    if (usingBarEl) return usingBarEl;

    usingBarEl = document.createElement("div");
    usingBarEl.id = "using-tab-bar";
    usingBarEl.className = "using-tab-bar";

    const iconWrap = document.createElement("div");
    iconWrap.className = "using-tab-icon";

    const label = document.createElement("div");
    label.className = "using-tab-label";

    const prefix = document.createElement("span");
    prefix.className = "using-tab-prefix";
    prefix.textContent = "Using: ";

    const name = document.createElement("span");
    name.className = "using-tab-name";

    label.appendChild(prefix);
    label.appendChild(name);

    const closeBtn = document.createElement("div");
    closeBtn.className = "using-tab-close";
    closeBtn.innerHTML = "&times;";
    closeBtn.title = "Stop using this tab";
    closeBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        clearMentionedTab();
    });

    usingBarEl.appendChild(iconWrap);
    usingBarEl.appendChild(label);
    usingBarEl.appendChild(closeBtn);

    // Insert directly above #input-row, inside the same floating dock, so
    // it shares the glass cluster and pushes the input row down naturally.
    bottomDock.insertBefore(usingBarEl, inputRow);

    return usingBarEl;
}

function showUsingBar(tab, { animate = true } = {}) {
    const el = ensureUsingBarEl();

    const iconWrap = el.querySelector(".using-tab-icon");
    const nameEl = el.querySelector(".using-tab-name");

    nameEl.textContent = tab.title || tab.url || "Untitled tab";
    el.title = tab.url || "";
    setTabIcon(iconWrap, tab.favIconUrl);

    // Restart the "born from the input box" entrance animation every time
    // a mention is (re)made — including replacing an existing mention —
    // so the bar always visibly reasserts itself.
    el.classList.remove("using-tab-bar--born");
    el.classList.add("using-tab-bar--visible");
    if (animate) {
        // Force reflow so the removed class actually resets before we re-add it.
        void el.offsetWidth;
        el.classList.add("using-tab-bar--born");
    }
}

function hideUsingBar(animate = true) {
    if (!usingBarEl) return;
    if (!animate) {
        usingBarEl.classList.remove("using-tab-bar--visible", "using-tab-bar--born");
        return;
    }
    usingBarEl.classList.add("using-tab-bar--leaving");
    usingBarEl.classList.remove("using-tab-bar--visible");
    setTimeout(() => {
        usingBarEl?.classList.remove("using-tab-bar--leaving", "using-tab-bar--born");
    }, 320);
}

function fallbackTabIconSvg() {
    return `<svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect x="2.5" y="4" width="15" height="12" rx="2" stroke="currentColor" stroke-width="1.4"/>
    <path d="M2.5 7.5h15" stroke="currentColor" stroke-width="1.4"/>
  </svg>`;
}

// Renders a favicon <img> that swaps itself out for the fallback SVG on
// load failure, instead of leaving a broken image / console error. Some
// favicons (cross-origin, NotSameOrigin-blocked, 404s) simply won't load
// in the extension popup context — that's expected and cosmetic, not a
// bug, so we just degrade quietly to the generic tab icon.
function setTabIcon(iconEl, favIconUrl) {
    if (!favIconUrl) {
        iconEl.innerHTML = fallbackTabIconSvg();
        return;
    }
    iconEl.innerHTML = "";
    const img = document.createElement("img");
    img.alt = "";
    img.referrerPolicy = "no-referrer";
    img.onerror = () => {
        iconEl.innerHTML = fallbackTabIconSvg();
    };
    img.src = favIconUrl;
    iconEl.appendChild(img);
}

// Tabs whose URL scheme Chrome will never allow scripting into,
// regardless of what host_permissions the extension holds. Filtering
// these out up front means the user never picks a dead option in the
// first place, instead of hitting a runtime permission error after
// the fact.
const UNSCRIPTABLE_URL_PREFIXES = [
    "chrome://", "chrome-extension://", "edge://", "about:",
    "chrome.google.com/webstore", "chromewebstore.google.com",
    "https://chrome.google.com/webstore",
];

function isScriptableTab(tab) {
    const url = tab.url || "";
    if (!url) return false;
    return !UNSCRIPTABLE_URL_PREFIXES.some(prefix => url.startsWith(prefix) || url.includes(prefix));
}
async function extractFullPageText(tabId) {
    const injectionResult = await chrome.scripting.executeScript({
        target: { tabId },
        func: () => {
            const clone = document.cloneNode(true);
            const tagsToRemove = ['nav', 'footer', 'aside', 'script', 'style', 'noscript', 'header'];
            tagsToRemove.forEach(tag => {
                const elements = clone.querySelectorAll(tag);
                elements.forEach(el => el.parentNode?.removeChild(el));
            });
            // No truncation here — this is the raw content the model should
            // reason over directly, not a summarisation input.
            return clone.body ? clone.body.innerText : "";
        }
    });
    return injectionResult[0]?.result || "";
}

function isMissingHostPermissionError(err) {
    const message = String(err?.message || err || "");
    return message.includes("Cannot access contents") || message.includes("Extension manifest must request permission");
}

/**
 * Chrome only injects host_permissions grants into a tab's renderer at
 * the moment that tab navigates. A tab that was already open BEFORE this
 * extension was installed, reloaded (common during dev via "Reload" in
 * chrome://extensions), or granted <all_urls> will still throw "Cannot
 * access contents..." from executeScript even though the manifest is
 * completely correct — until that tab itself reloads/navigates once.
 *
 * So on that specific error (not other failures), we reload the target
 * tab once and retry the injection — this silently self-heals the
 * overwhelmingly common case without the user needing to know why.
 */
async function extractFullPageTextWithRetry(tab) {
    try {
        return await extractFullPageText(tab.id);
    } catch (err) {
        if (!isMissingHostPermissionError(err)) throw err;

        NotificationService.show(`Refreshing "${tab.title || tab.url}" to enable access…`);
        await chrome.tabs.reload(tab.id);
        await waitForTabComplete(tab.id);

        // One retry only — if it still fails after a reload, surface the
        // real error rather than looping.
        return await extractFullPageText(tab.id);
    }
}

function waitForTabComplete(tabId, timeoutMs = 8000) {
    return new Promise((resolve) => {
        let settled = false;
        const finish = () => {
            if (settled) return;
            settled = true;
            chrome.tabs.onUpdated.removeListener(listener);
            clearTimeout(timer);
            resolve();
        };
        const listener = (updatedTabId, changeInfo) => {
            if (updatedTabId === tabId && changeInfo.status === "complete") finish();
        };
        chrome.tabs.onUpdated.addListener(listener);
        const timer = setTimeout(finish, timeoutMs);
    });
}

async function selectTabForMention(tab) {
    // IMPORTANT: remove the "@filter" text from the input BEFORE closing
    // the dropdown — closeDropdown() resets mentionStartIndex, and once
    // that's gone removeMentionTextFromInput() has nothing to go on and
    // silently no-ops, leaving the stray "@" sitting in the box.
    removeMentionTextFromInput();
    closeDropdown();

    NotificationService.show(`Reading "${tab.title || tab.url}"...`);

    try {
        const content = await extractFullPageTextWithRetry(tab);
        if (!content) {
            NotificationService.show("Couldn't extract text from that tab.");
            return;
        }
        mentionedTab = {
            tabId: tab.id,
            title: tab.title || tab.url || "Untitled tab",
            url: tab.url || "",
            favIconUrl: tab.favIconUrl || "",
            content,
        };
        showUsingBar(mentionedTab, { animate: true });
    } catch (err) {
        console.error("Mention extraction error:", err);
        if (isMissingHostPermissionError(err)) {
            // We already tried a reload-and-retry inside extractFullPageTextWithRetry
            // and it still failed — genuinely out of automatic options here.
            NotificationService.show("Still can't access that tab after refreshing it. Try switching to the tab manually once, then mention it again.");
        } else {
            NotificationService.show("Couldn't read that tab.");
        }
    } finally {
        inputEl.focus();
    }
}

// ── Dropdown ─────────────────────────────────────────────────────────
function ensureDropdownEl() {
    if (dropdownEl) return dropdownEl;
    dropdownEl = document.createElement("div");
    dropdownEl.id = "mention-dropdown";
    dropdownEl.className = "mention-dropdown";
    bottomDock.appendChild(dropdownEl);
    return dropdownEl;
}

function renderDropdown(filter) {
    const el = ensureDropdownEl();
    const q = filter.trim().toLowerCase();

    const filtered = q
        ? allTabsCache.filter(t =>
            (t.title || "").toLowerCase().includes(q) ||
            (t.url || "").toLowerCase().includes(q))
        : allTabsCache;

    el.innerHTML = "";

    if (filtered.length === 0) {
        const empty = document.createElement("div");
        empty.className = "mention-dropdown-empty";
        empty.textContent = "No matching tabs";
        el.appendChild(empty);
        el.classList.add("mention-dropdown--visible");
        return;
    }

    highlightedIndex = Math.min(highlightedIndex, filtered.length - 1);

    filtered.forEach((tab, idx) => {
        const item = document.createElement("div");
        item.className = "mention-dropdown-item" + (idx === highlightedIndex ? " highlighted" : "");

        const icon = document.createElement("div");
        icon.className = "mention-dropdown-icon";
        setTabIcon(icon, tab.favIconUrl);

        const textWrap = document.createElement("div");
        textWrap.className = "mention-dropdown-text";

        const titleEl = document.createElement("div");
        titleEl.className = "mention-dropdown-title";
        titleEl.textContent = tab.title || "(untitled)";
        // Full URL still lives in the title attribute for anyone who wants
        // to hover-disambiguate near-identical tab titles, without taking
        // up a permanent visible line in the list.
        titleEl.title = tab.url || "";

        textWrap.appendChild(titleEl);

        item.appendChild(icon);
        item.appendChild(textWrap);

        item.addEventListener("mousedown", (e) => {
            // mousedown (not click) so it fires before the input's blur.
            e.preventDefault();
            selectTabForMention(tab);
        });

        el.appendChild(item);
    });

    el._filteredTabs = filtered;
    el.classList.add("mention-dropdown--visible");

    const highlightedEl = el.querySelector(".mention-dropdown-item.highlighted");
    if (highlightedEl) {
        highlightedEl.scrollIntoView({ block: "nearest" });
    }
}

function closeDropdown() {
    mentionActive = false;
    mentionStartIndex = -1;
    if (dropdownEl) {
        dropdownEl.classList.remove("mention-dropdown--visible");
    }
}

async function openDropdown() {
    try {
        const tabs = await chrome.tabs.query({});
        allTabsCache = tabs.filter(isScriptableTab);
    } catch (err) {
        console.error("Failed to query tabs:", err);
        allTabsCache = [];
    }
    highlightedIndex = 0;
    renderDropdown("");
}

function removeMentionTextFromInput() {
    if (mentionStartIndex === -1) return;
    const value = inputEl.value;
    const caret = inputEl.selectionStart ?? value.length;
    // Remove from the "@" up to the current caret position (covers
    // whatever filter text the user typed after the "@").
    const before = value.slice(0, mentionStartIndex);
    const after = value.slice(caret);
    inputEl.value = before + after;
    const newCaret = before.length;
    inputEl.setSelectionRange(newCaret, newCaret);
    mentionActive = false;
    mentionStartIndex = -1;
}

// ── Input wiring ─────────────────────────────────────────────────────
inputEl.addEventListener("input", () => {
    const value = inputEl.value;
    const caret = inputEl.selectionStart ?? value.length;

    if (!mentionActive) {
        // Detect a freshly-typed "@" immediately before the caret, and only
        // when it starts a token (start of input or preceded by whitespace)
        // so email-like text or "user@host" doesn't trigger it.
        const charBeforeCaret = value[caret - 1];
        if (charBeforeCaret === "@") {
            const precedingChar = value[caret - 2];
            const startsToken = caret === 1 || precedingChar === undefined || /\s/.test(precedingChar);
            if (startsToken) {
                mentionActive = true;
                mentionStartIndex = caret - 1;
                openDropdown();
                return;
            }
        }
        return;
    }

    // Mention is active: keep filtering, or close if the "@" got deleted
    // or the user typed a space/newline (ends the token).
    if (caret <= mentionStartIndex || value[mentionStartIndex] !== "@") {
        closeDropdown();
        return;
    }
    const filterText = value.slice(mentionStartIndex + 1, caret);
    if (/\s/.test(filterText)) {
        closeDropdown();
        return;
    }
    renderDropdown(filterText);
});

inputEl.addEventListener("keydown", (e) => {
    if (!mentionActive || !dropdownEl || !dropdownEl.classList.contains("mention-dropdown--visible")) {
        return;
    }
    const filtered = dropdownEl._filteredTabs || [];
    if (e.key === "ArrowDown") {
        e.preventDefault();
        if (filtered.length) {
            highlightedIndex = (highlightedIndex + 1) % filtered.length;
            renderDropdown(getCurrentFilterText());
        }
    } else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (filtered.length) {
            highlightedIndex = (highlightedIndex - 1 + filtered.length) % filtered.length;
            renderDropdown(getCurrentFilterText());
        }
    } else if (e.key === "Enter") {
        if (filtered.length) {
            e.preventDefault();
            e.stopImmediatePropagation();
            selectTabForMention(filtered[highlightedIndex]);
        }
    } else if (e.key === "Escape") {
        e.preventDefault();
        closeDropdown();
    }
});

function getCurrentFilterText() {
    const value = inputEl.value;
    const caret = inputEl.selectionStart ?? value.length;
    if (mentionStartIndex === -1) return "";
    return value.slice(mentionStartIndex + 1, caret);
}

inputEl.addEventListener("blur", () => {
    // Slight delay so a mousedown-selected dropdown item still registers.
    setTimeout(() => closeDropdown(), 120);
});

document.addEventListener("click", (e) => {
    if (dropdownEl && !dropdownEl.contains(e.target) && e.target !== inputEl) {
        closeDropdown();
    }
});