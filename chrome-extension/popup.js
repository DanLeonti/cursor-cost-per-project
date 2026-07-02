const STATUS_CONFIG = {
  connected: {
    label: "Connected",
    detail: () => "Exact billing data is flowing to costtrack.py",
    cardClass: "connected",
    instructions: null,
  },
  "listener-offline": {
    label: "Waiting for the CLI",
    detail: () => "Run the listener command below, then click Sync Now",
    cardClass: "listener-offline",
    instructions: () => `
      <div class="step"><div class="step-num">1</div>
        <div>Open a terminal and run:<br><code>python3 costtrack.py --listen</code></div></div>
      <div class="step"><div class="step-num">2</div>
        <div>Come back here and click <strong>Sync Now</strong></div></div>
    `,
  },
  "not-logged-in": {
    label: "Not logged in to cursor.com",
    detail: () => "Visit cursor.com and sign in first",
    cardClass: "not-logged-in",
    instructions: () => `
      <div class="step"><div class="step-num">1</div>
        <div>Open <strong>cursor.com</strong> and sign in</div></div>
      <div class="step"><div class="step-num">2</div>
        <div>Come back here and click <strong>Sync Now</strong></div></div>
    `,
  },
  loading: {
    label: "Checking…",
    detail: () => "",
    cardClass: "loading",
    instructions: null,
  },
};

function render(state) {
  const config = STATUS_CONFIG[state.status] ?? STATUS_CONFIG.loading;

  const card = document.getElementById("status-card");
  card.className = "status-card " + config.cardClass;
  document.getElementById("status-label").textContent = config.label;
  document.getElementById("status-detail").textContent = config.detail(state);

  const box = document.getElementById("instructions");
  if (config.instructions) {
    box.style.display = "block";
    document.getElementById("instructions-body").innerHTML = config.instructions();
  } else {
    box.style.display = "none";
  }

  if (state.lastSync) {
    const ago = Math.round((Date.now() - state.lastSync) / 1000);
    document.getElementById("last-sync").textContent =
      `Last connected ${ago < 60 ? ago + "s" : Math.round(ago / 60) + "m"} ago`;
  } else {
    document.getElementById("last-sync").textContent = "";
  }
}

function setLoading(on) {
  const btn = document.getElementById("sync-btn");
  btn.disabled = on;
  btn.textContent = on ? "Syncing…" : "Sync Now";
}

// ── Initial state ─────────────────────────────────────────────────────────────

chrome.runtime.sendMessage({ type: "get-status" }, (state) => {
  render(state ?? { status: "loading" });
});

// ── Sync button ───────────────────────────────────────────────────────────────

document.getElementById("sync-btn").addEventListener("click", () => {
  setLoading(true);
  chrome.runtime.sendMessage({ type: "sync" }, (state) => {
    render(state ?? { status: "loading" });
    setLoading(false);
  });
});
