/**
 * content_script.js
 * ------------------
 * Powers the "Edit with Navigator" right-click flow. This is a separate,
 * stateless feature from the tab-chat popup — no tab_id, no session
 * memory, no relationship to navigator_bridge.py's WebSocket/SessionStore
 * at all. One request, one response, done.
 *
 * Flow:
 *   1. User selects text and right-clicks (menu only shows when there IS
 *      a selection — see background.js's contexts: ["selection"]).
 *   2. background.js relays a "navigator-open-edit-box" message here.
 *      This script re-reads the live selection itself — background.js
 *      has no DOM access to inspect it.
 *   3. This script classifies what's selected into one of three tiers
 *      (see detectEditContext below) and renders a floating box near it,
 *      inside a Shadow DOM so the page's own CSS can't clash with it.
 *   4. On submit, it asks background.js to call the backend's
 *      POST /edit-selection (background.js does the actual fetch — see
 *      that file for why) and either splices the result back in, or —
 *      for text this script can't safely rewrite in place — shows it as
 *      a copyable suggestion instead.
 *
 * Three detection tiers (this is the "how aware can we realistically be"
 * question):
 *
 *   1. FORM FIELDS (<input>, <textarea>) — text lives in .value, a plain
 *      string, with .selectionStart/.selectionEnd. Fully readable and
 *      fully writable. Auto-applies.
 *
 *   2. PLAIN CONTENTEDITABLE — real DOM text nodes under an editable
 *      root (isContentEditable, which covers contenteditable="true",
 *      "", "plaintext-only", and inherited/designMode cases — using the
 *      browser's own resolved flag instead of hand-checking the
 *      attribute string). Fully readable via Range, fully writable via
 *      Range.deleteContents()/insertNode(). Auto-applies.
 *
 *   3. FRAMEWORK-MANAGED RICH TEXT (ProseMirror, Slate, Lexical,
 *      Draft.js, Quill, CKEditor, and similar — likely what claude.ai's
 *      own message box is) — technically contenteditable at the DOM
 *      level, but the framework owns an internal model and re-renders
 *      the DOM out from under any direct edits. Detected via common
 *      fingerprint classes/attributes each library leaves in the DOM.
 *      Reading the selected text still works fine (window.getSelection
 *      doesn't care who manages the DOM). Writing it back safely would
 *      need a framework-specific approach (synthetic input events the
 *      framework's own handler expects) — out of scope for this pass.
 *      So: read-only mode. We still call the backend and show the
 *      result, just as a copyable suggestion instead of an auto-apply.
 *
 *   Anything selected outside all of the above (plain read-only page
 *   text) gets the same copyable-suggestion treatment as tier 3, for
 *   the same reason — there's nothing to write back into.
 */

(() => {
  const OPEN_BOX_MESSAGE = "navigator-open-edit-box";
  const EDIT_REQUEST_MESSAGE = "navigator-edit-selection-request";

  if (!document.getElementById("navigator-highlight-style")) {
    const style = document.createElement("style");
    style.id = "navigator-highlight-style";
    // Using a nice transparent version of your #3a6df0 blue
    style.textContent = `::highlight(navigator-selection) { background-color: #3a6df0 !important; color: #ffffff !important; }`;
    document.head.appendChild(style);
  }

  let activeBox = null; // only one edit box open at a time

  // ── Tier 3 fingerprints ─────────────────────────────────────────────
  // Heuristic, not exhaustive — common conventions each library leaves
  // in the DOM. False negatives just fall through to tier-2 handling
  // (which will fail loudly if the framework rejects the direct edit,
  // rather than silently corrupting state, since text is only mutated
  // in the DOM subtree under the detected root).

  const FRAMEWORK_FINGERPRINTS = [
    { name: "ProseMirror", test: (root) => root.classList?.contains("ProseMirror") || !!root.closest?.(".ProseMirror") },
    { name: "Slate", test: (root) => root.hasAttribute?.("data-slate-editor") || !!root.querySelector?.("[data-slate-string]") },
    { name: "Lexical", test: (root) => root.hasAttribute?.("data-lexical-editor") || !!root.querySelector?.("[data-lexical-text]") },
    { name: "Draft.js", test: (root) => root.classList?.contains("DraftEditor-root") || !!root.closest?.(".DraftEditor-root") || !!root.querySelector?.('[data-contents="true"]') },
    { name: "Quill", test: (root) => root.classList?.contains("ql-editor") || !!root.closest?.(".ql-editor") },
    { name: "CKEditor", test: (root) => root.classList?.contains("ck-editor__editable") || !!root.closest?.(".ck-editor__editable") },
  ];

  function detectFramework(editableRoot) {
    for (const fp of FRAMEWORK_FINGERPRINTS) {
      try {
        if (fp.test(editableRoot)) return fp.name;
      } catch (e) {
        // A fingerprint test throwing shouldn't break detection of the
        // others — just skip it.
      }
    }
    return null;
  }

  // ── Figure out what's selected and how (or whether) to write back ──

  function isTextLikeInput(el) {
    const textTypes = ["text", "search", "url", "tel", "email", "password", ""];
    return textTypes.includes((el.getAttribute("type") || "").toLowerCase());
  }

  function findEditableAncestor(el) {
    // .isContentEditable is the browser's own resolved answer — it
    // already accounts for contenteditable="true"/""/"plaintext-only",
    // inheritance from a parent, and is false where explicitly
    // overridden — more robust than matching the attribute string
    // ourselves.
    let node = el;
    while (node && node !== document.body && node !== document.documentElement) {
      if (node.nodeType === Node.ELEMENT_NODE && node.isContentEditable) return node;
      node = node.parentElement;
    }
    return null;
  }

  function detectEditContext() {
    // Tier 1: an <input>/<textarea> with an active selection. Note that
    // window.getSelection() does NOT see into form controls in Chrome —
    // their selection only shows up via selectionStart/selectionEnd on
    // the focused element — so this has to be checked separately from
    // everything below.
    const active = document.activeElement;
    if (
      active &&
      (active.tagName === "TEXTAREA" || (active.tagName === "INPUT" && isTextLikeInput(active)))
    ) {
      if (typeof active.selectionStart === "number" && active.selectionStart !== active.selectionEnd) {
        return {
          tier: "form",
          element: active,
          start: active.selectionStart,
          end: active.selectionEnd,
          text: active.value.slice(active.selectionStart, active.selectionEnd),
          // Approximate: positions under the whole field rather than the
          // exact selected glyphs. Precise sub-field caret geometry needs
          // a hidden-mirror-div measurement technique — skipped here.
          rect: active.getBoundingClientRect(),
        };
      }
    }

    // Tiers 2/3/readonly all start from a real window selection.
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
      return null;
    }

    const range = selection.getRangeAt(0);
    const text = selection.toString();
    const rect = range.getBoundingClientRect();
    const container = range.commonAncestorContainer;
    const el = container.nodeType === Node.TEXT_NODE ? container.parentElement : container;

    const editableRoot = (el && findEditableAncestor(el))
      || (document.designMode === "on" ? document.body : null);

    if (!editableRoot) {
      // Selected, but nothing editable underneath it — ordinary page text.
      return { tier: "readonly", text, rect, range: range.cloneRange() };
    }

    const framework = detectFramework(editableRoot);
    if (framework) {
      return { tier: "rich-text", frameworkName: framework, text, rect, range: range.cloneRange() };
    }

    return {
      tier: "contenteditable",
      range: range.cloneRange(),
      root: editableRoot,
      text,
      rect,
    };
  }

  // Helper: Briefly flash a subtle highlight on an element to signal a change
  function pulseElement(el) {
    if (!el) return;
    const originalTransition = el.style.transition;
    const originalBg = el.style.backgroundColor;

    el.style.transition = "background-color 0.15s ease-in-out";
    el.style.backgroundColor = "rgba(58, 109, 240, 0.15)"; // Soft blue pulse

    setTimeout(() => {
      el.style.transition = "background-color 0.8s ease-out";
      el.style.backgroundColor = originalBg;

      setTimeout(() => {
        el.style.transition = originalTransition;
        if (!originalBg) el.style.removeProperty("background-color");
      }, 800);
    }, 200);
  }

  // Smoothly apply edits with an adaptive typewriter animation
  function applyEdit(context, newText) {
    if (!newText) return;

    // ── TIER 1: Standard Form Fields (<input>, <textarea>) ─────────────────────
    if (context.tier === "form" && context.element) {
      const el = context.element;
      const start = el.selectionStart || 0;
      const end = el.selectionEnd || 0;
      const oldVal = el.value;

      const prefix = oldVal.substring(0, start);
      const suffix = oldVal.substring(end);

      let charIndex = 0;
      const speed = Math.max(8, Math.floor(350 / newText.length));

      const timer = setInterval(() => {
        charIndex += Math.max(1, Math.floor(newText.length / 25));
        if (charIndex >= newText.length) {
          charIndex = newText.length;
          clearInterval(timer);
          pulseElement(el);
        }

        const currentSlice = newText.substring(0, charIndex);
        el.value = prefix + currentSlice + suffix;

        const newCursorPos = prefix.length + currentSlice.length;
        el.setSelectionRange(newCursorPos, newCursorPos);

        el.dispatchEvent(new Event("input", { bubbles: true }));
      }, speed);

      el.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }

    // ── TIER 2 & 3: ContentEditable & Rich-Text Frameworks (Notion, Slack, etc.) ──
    if ((context.tier === "contenteditable" || context.tier === "rich-text") && context.range) {
      const selection = window.getSelection();
      if (!selection) return;

      // 1. Force focus back to the target element if it slipped away
      if (context.root && typeof context.root.focus === "function") {
        context.root.focus();
      }

      // 2. Re-select the exact text block the user highlighted
      selection.removeAllRanges();
      selection.addRange(context.range);

      let charIndex = 0;
      const speed = Math.max(8, Math.floor(350 / newText.length));
      const chunkSize = Math.max(1, Math.floor(newText.length / 25));

      // 3. Clear out the highlighted text using the browser's native command
      // This alerts frameworks (like ProseMirror/Lexical) that an active deletion happened
      document.execCommand("delete", false);

      const timer = setInterval(() => {
        if (charIndex >= newText.length) {
          clearInterval(timer);
          // Highlight target: use root container or fallback to where the cursor is resting
          pulseElement(context.root || selection.anchorNode?.parentElement);
          return;
        }

        const nextChunk = newText.substring(charIndex, charIndex + chunkSize);
        charIndex += chunkSize;

        // 4. Stream chunks directly at the cursor position
        // The editor's virtual DOM engine intercepts this and updates safely!
        document.execCommand("insertText", false, nextChunk);

        // Throw an explicit input event at the container to cover reactive bindings
        if (context.root) {
          context.root.dispatchEvent(new Event("input", { bubbles: true }));
        }
      }, speed);

      return;
    }
  }

  // ── Floating box (Shadow DOM keeps page CSS from leaking in/out) ──────

  function closeActiveBox() {
    if (!activeBox) return;

    if (activeBox.onScroll) {
      window.removeEventListener("scroll", activeBox.onScroll, true);
    }

    activeBox.host.remove();
    document.removeEventListener("keydown", activeBox.onKeydown, true);
    document.removeEventListener("mousedown", activeBox.onOutsideClick, true);

    if (CSS.highlights) {
      CSS.highlights.delete("navigator-selection");
    }

    activeBox = null;
  }

  function tierNotice(context) {
    if (context.tier === "readonly") {
      return "Static text detected — edits will show as a copyable result.";
    }
    // We removed the rich-text warning entirely because our new 
    // execCommand trick natively handles it now!
    return null;
  }

  function openEditBox(context) {
    closeActiveBox();

    // Apply the highlight if a DOM range exists
    if (CSS.highlights && context.range) {
      const highlight = new Highlight(context.range);
      CSS.highlights.set("navigator-selection", highlight);
    }

    const host = document.createElement("div");
    host.style.position = "fixed";
    host.style.zIndex = "2147483647"; // Stay above everything
    document.body.appendChild(host);

    const shadow = host.attachShadow({ mode: "open" });
    const notice = tierNotice(context);

    shadow.innerHTML = `
      <style>
        * { box-sizing: border-box; }
        .wrap {
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          width: 340px;
        }
        .notice {
          font-size: 11px;
          color: #cfa93c;
          background: #2a2410;
          border: 1px solid #4a3f1a;
          border-radius: 8px;
          padding: 6px 8px;
          margin-bottom: 6px;
          display: ${notice ? "block" : "none"};
          line-height: 1.4;
        }
        .box {
          background: #1e1e1e;
          border: 1px solid #3a3a3a;
          border-radius: 10px;
          box-shadow: 0 6px 24px rgba(0,0,0,0.35);
          padding: 8px;
          display: flex;
          gap: 6px;
          align-items: center;
        }
        input {
          flex: 1;
          padding: 7px 9px;
          border-radius: 7px;
          border: 1px solid #3a3a3a;
          background: #2a2a2a;
          color: #eaeaea;
          font-size: 13px;
          outline: none;
        }
        button {
          padding: 7px 12px;
          border-radius: 7px;
          border: none;
          background: #3a6df0;
          color: white;
          font-size: 13px;
          cursor: pointer;
          white-space: nowrap;
        }
        button:disabled { background: #444; cursor: not-allowed; }
        button.secondary {
          background: #333;
        }
        .status {
          font-size: 11px;
          color: #e53935;
          padding: 6px 4px 0;
          display: none;
        }
        .result {
          display: none;
          background: #1e1e1e;
          border: 1px solid #3a3a3a;
          border-radius: 10px;
          box-shadow: 0 6px 24px rgba(0,0,0,0.35);
          padding: 10px;
          margin-top: 6px;
        }
        .result-text {
          font-size: 13px;
          color: #eaeaea;
          white-space: pre-wrap;
          word-wrap: break-word;
          margin-bottom: 8px;
          max-height: 140px;
          overflow-y: auto;
        }
        .result-actions {
          display: flex;
          justify-content: flex-end;
          gap: 6px;
          margin-top: 6px;
        }
      </style>
      <div class="wrap">
        <div class="notice">${notice || ""}</div>
        <div class="box">
          <input type="text" placeholder="Tell Navigator how to edit this..." />
          <button class="submit-btn">Go</button>
        </div>
        <div class="status"></div>
        <div class="result">
          <div class="result-text"></div>
          <div class="result-actions">
            <button class="secondary copy-btn">Copy</button>
            <button class="secondary close-btn">Close</button>
          </div>
        </div>
      </div>
    `;

    const input = shadow.querySelector("input");
    const submitBtn = shadow.querySelector(".submit-btn");
    const status = shadow.querySelector(".status");
    const box = shadow.querySelector(".box");
    const resultEl = shadow.querySelector(".result");
    const resultText = shadow.querySelector(".result-text");
    const copyBtn = shadow.querySelector(".copy-btn");
    const closeBtn = shadow.querySelector(".close-btn");
    const wrapEl = shadow.querySelector(".wrap");

    // --- NEW: Helper to get live, updated bounds of the target ---
    function getFreshRect() {
      if (context.tier === "form" && context.element) {
        return context.element.getBoundingClientRect();
      } else if (context.range) {
        return context.range.getBoundingClientRect();
      }
      return context.rect;
    }

    // --- NEW: Smart collision positioning logic ---
    function reposition() {
      const rect = getFreshRect();
      if (!rect) return;

      // 1. Prevent Horizontal Clip (Right Edge)
      const boxWidth = 340;
      let left = rect.left;
      if (left + boxWidth > window.innerWidth) {
        left = window.innerWidth - boxWidth - 16;
      }
      host.style.left = `${Math.max(8, left)}px`;

      // 2. Prevent Vertical Clip (Bottom Edge Check)
      // Read the exact dynamic height of our wrapper box inside the shadow DOM
      const boxHeight = wrapEl ? wrapEl.offsetHeight : 110;
      const spaceBelow = window.innerHeight - rect.bottom;

      // If space below is tighter than our box height, flip it over the top
      if (spaceBelow < boxHeight + 12 && rect.top > boxHeight + 12) {
        host.style.top = `${rect.top - boxHeight - 8}px`;
      } else {
        host.style.top = `${rect.bottom + 6}px`;
      }
    }

    // Run the initial smart position calculation
    reposition();

    function showError(msg) {
      status.textContent = msg;
      status.style.display = "block";
      reposition(); // Re-calculate layout since error text expands the container height
    }

    function setBusy(isBusy) {
      submitBtn.disabled = isBusy;
      input.disabled = isBusy;
      submitBtn.textContent = isBusy ? "..." : "Go";
    }

    function showResult(text) {
      box.style.display = "none";
      resultText.textContent = text;
      resultEl.style.display = "block";
      reposition(); // Re-calculate layout since the result menu has a unique height
    }

    function submit() {
      const instruction = input.value.trim();
      if (!instruction) return;

      setBusy(true);
      status.style.display = "none";

      chrome.runtime.sendMessage(
        {
          type: EDIT_REQUEST_MESSAGE,
          selected_text: context.text,
          instruction,
        },
        (response) => {
          setBusy(false);

          if (chrome.runtime.lastError) {
            showError("Couldn't reach the extension backend.");
            return;
          }
          if (!response || !response.ok) {
            showError((response && response.error) || "Edit failed.");
            return;
          }

          // --- Added context.tier === "rich-text" ---
          if (context.tier === "form" || context.tier === "contenteditable" || context.tier === "rich-text") {
            applyEdit(context, response.edited_text);
            closeActiveBox();
          } else {
            showResult(response.edited_text);
          }
        }
      );
    }

    submitBtn.addEventListener("click", submit);

    // 1. Stop propagation on keydown
    input.addEventListener("keydown", (e) => {
      e.stopPropagation(); // Stops GitHub from seeing the event
      if (e.key === "Enter") submit();
      if (e.key === "Escape") closeActiveBox();
    });

    // 2. Also stop keyup and keypress, just in case a site's hotkeys 
    // are bound to those instead of keydown.
    input.addEventListener("keyup", (e) => e.stopPropagation());
    input.addEventListener("keypress", (e) => e.stopPropagation());

    copyBtn.addEventListener("click", () => {
      navigator.clipboard?.writeText(resultText.textContent || "").catch(() => { });
      copyBtn.textContent = "Copied";
      setTimeout(() => { copyBtn.textContent = "Copy"; }, 1200);
    });
    closeBtn.addEventListener("click", closeActiveBox);

    const onKeydown = (e) => {
      if (e.key === "Escape") closeActiveBox();
    };
    const onOutsideClick = (e) => {
      if (!host.contains(e.target)) closeActiveBox();
    };

    // --- Live Scroll tracking handler ---
    const handleScroll = () => {
      reposition();
    };

    // true setting uses the capture phase, tracking scrolls on internal DOM containers too
    window.addEventListener("scroll", handleScroll, true);

    document.addEventListener("keydown", onKeydown, true);
    setTimeout(() => document.addEventListener("mousedown", onOutsideClick, true), 0);

    activeBox = { host, onKeydown, onOutsideClick, onScroll: handleScroll };
    input.focus();
  }

  // ── Entry point: background.js relays the context-menu click here ─────

  chrome.runtime.onMessage.addListener((message) => {
    if (message.type !== OPEN_BOX_MESSAGE) return;

    const context = detectEditContext();
    if (!context) {
      // Menu is gated on contexts: ["selection"], so this shouldn't
      // normally happen — but selection can vanish between the
      // right-click and this message arriving. Log for visibility while
      // testing rather than failing completely silently.
      console.log("[Sicily Navigator] no selection found when edit box was requested.");
      return;
    }
    openEditBox(context);
  });
})();