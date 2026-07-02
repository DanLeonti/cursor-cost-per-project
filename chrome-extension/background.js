const CURSOR_DOMAIN = "cursor.com";
const TOKEN_COOKIE  = "WorkosCursorSessionToken";
const LOCAL_SERVER  = "http://localhost:7823/token";
const ALARM_NAME    = "token-refresh";
const REFRESH_MINS  = 30;

// ── Read the session token from browser cookies ───────────────────────────────
// chrome.cookies can read httpOnly cookies even though page JS cannot —
// this is why the extension exists instead of a console copy-paste snippet.

async function getCursorToken() {
  const cookie = await chrome.cookies.get({
    url: "https://cursor.com",
    name: TOKEN_COOKIE,
  });
  return cookie?.value ?? null;
}

// ── Push the token to the local CLI listener (costtrack.py --listen) ─────────
// The CLI listener only runs on-demand (not a background daemon), so most
// pushes will fail with "listener offline" — that's expected, not an error.

async function pushToken(token, { manual = false } = {}) {
  try {
    const resp = await fetch(LOCAL_SERVER, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });

    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    await chrome.storage.local.set({
      lastSync: Date.now(),
      status: "connected",
    });

    updateBadge("✓", "#22c55e");
    return true;
  } catch {
    await chrome.storage.local.set({ status: "listener-offline" });
    // Neutral badge — the listener being offline is the normal idle state,
    // not an error condition worth alarming the user about.
    updateBadge(manual ? "!" : "", manual ? "#f59e0b" : "#94a3b8");
    return false;
  }
}

// ── Main sync routine ─────────────────────────────────────────────────────────

async function syncToken({ manual = false } = {}) {
  const token = await getCursorToken();

  if (!token) {
    await chrome.storage.local.set({ status: "not-logged-in" });
    updateBadge("?", "#94a3b8");
    return { status: "not-logged-in" };
  }

  await pushToken(token, { manual });
  const state = await chrome.storage.local.get(["status", "lastSync"]);
  return state;
}

// ── Badge helper ──────────────────────────────────────────────────────────────

function updateBadge(text, color) {
  chrome.action.setBadgeText({ text });
  if (text) chrome.action.setBadgeBackgroundColor({ color });
}

// ── Alarm for periodic background refresh (silent, best-effort) ──────────────

chrome.alarms.create(ALARM_NAME, { periodInMinutes: REFRESH_MINS });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_NAME) syncToken({ manual: false });
});

chrome.runtime.onInstalled.addListener(() => {
  syncToken({ manual: false });
});

chrome.webNavigation?.onCompleted?.addListener(
  (details) => {
    if (details.frameId === 0) syncToken({ manual: false });
  },
  { url: [{ hostSuffix: CURSOR_DOMAIN }] }
);

// ── Handle messages from popup and content script ────────────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "sync") {
    syncToken({ manual: true }).then(sendResponse);
    return true; // keep channel open for async response
  }

  if (msg.type === "get-status") {
    chrome.storage.local.get(["status", "lastSync"], sendResponse);
    return true;
  }
});
