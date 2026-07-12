/**
 * background.js
 * -------------
 * Demo scope (no AI): does nothing yet.
 *
 * The popup talks directly to the local Python backend over WebSocket
 * and reads the active tab itself via chrome.tabs — for this baby-steps
 * demo there's no need for a background-script relay.
 *
 * This file exists now (and is declared in manifest.json) so that later,
 * when the plugin needs to:
 *   - track "start" across multiple tabs on the same origin
 *   - inject content scripts into newly opened tabs on that origin
 *   - keep a session alive while the popup is closed
 * ...the wiring is already in place and content_script.js /
 * browser_bridge.py don't need to change shape.
 */

chrome.runtime.onInstalled.addListener(() => {
  console.log("[Sicily Navigator demo] extension installed.");
});
