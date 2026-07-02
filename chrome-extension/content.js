// Runs on cursor.com pages — notifies the background worker that a session
// might now be available (handles SPA navigations that don't fire webNavigation).
let lastPath = location.pathname;

const observer = new MutationObserver(() => {
  if (location.pathname !== lastPath) {
    lastPath = location.pathname;
    chrome.runtime.sendMessage({ type: "sync" }).catch(() => {});
  }
});

observer.observe(document.body, { childList: true, subtree: true });

// Initial trigger on page load — background script decides if the local
// CLI listener is reachable; failures here are silent and expected.
chrome.runtime.sendMessage({ type: "sync" }).catch(() => {});
