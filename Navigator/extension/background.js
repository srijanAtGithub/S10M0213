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
 * This file exists now (and is declared in manifest.json) so that later,
 * when the plugin needs to:
 *   - track "start" across multiple tabs on the same origin
 *   - inject content scripts into newly opened tabs on that origin
 *   - keep a session alive while the popup is closed
 * ...the wiring is already in place and popup.js / navigator_bridge.py
 * don't need to change shape.
 */

const BACKEND_HOST = "localhost:8765";

chrome.runtime.onInstalled.addListener(() => {
  console.log("[Sicily Navigator demo] extension installed.");
});

chrome.tabs.onRemoved.addListener((tabId) => {
  // Best-effort: if the backend isn't running, there's nothing to clean up
  // anyway (in-memory sessions die with the server), so just log and move on.
  fetch(`http://${BACKEND_HOST}/session/${tabId}`, { method: "DELETE" }).catch((err) => {
    console.log("[Sicily Navigator demo] couldn't clear session for closed tab", tabId, err);
  });
});