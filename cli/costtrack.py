#!/usr/bin/env python3
"""
Cursor Cost Tracker CLI

Reads Cursor's local SQLite databases to map conversations to git projects,
and (optionally) calls Cursor's usage API for exact billing data.

Usage:
    python3 costtrack.py                     Show current month (estimate or exact)
    python3 costtrack.py --month 2026-05     Show a specific month
    python3 costtrack.py --by-branch         Break each project down by git branch
    python3 costtrack.py --by-tab            Break each project down by conversation ("tab")
    python3 costtrack.py --listen            Auto-connect via the Chrome extension (recommended)
    python3 costtrack.py --setup             Connect manually by pasting your token
    python3 costtrack.py --logout            Disconnect / remove saved token

    python3 costtrack.py --slack-setup       Connect a Slack incoming webhook
    python3 costtrack.py --post-slack        Post today's dashboard to Slack now (skips if already sent today)
    python3 costtrack.py --post-slack --force  Force a resend even if already sent today
    python3 costtrack.py --install-daily HH:MM  Schedule a daily Slack post (default 09:00)
    python3 costtrack.py --uninstall-daily   Remove the daily schedule

No external dependencies — stdlib only.
"""

import sqlite3
import json
import os
import sys
import time
import datetime
import http.server
import socketserver
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, Dict, List

# ── Model pricing ($/million tokens, including Cursor Token Rate $0.25/M) ─────
# Used only for ESTIMATE mode. Exact mode uses real costs from the API.
MODEL_PRICING: dict[str, dict] = {
    "claude-opus-4-8":    {"input": 15.25, "output": 75.25, "label": "Claude Opus 4",     "short": "Opus 4"},
    "claude-opus-4-7":    {"input": 15.25, "output": 75.25, "label": "Claude Opus 4",     "short": "Opus 4"},
    "claude-sonnet-4-6":  {"input":  3.25, "output": 15.25, "label": "Claude Sonnet 4.6", "short": "Sonnet 4.6"},
    "claude-sonnet-4-5":  {"input":  3.25, "output": 15.25, "label": "Claude Sonnet 4.5", "short": "Sonnet 4.5"},
    "gpt-5.5":            {"input": 10.25, "output": 40.25, "label": "GPT-5.5",           "short": "GPT-5.5"},
    "gpt-5.3-codex":      {"input":  5.25, "output": 20.25, "label": "GPT-5.3 Codex",     "short": "Codex"},
    "composer-2.5":       {"input":  0.75, "output":  2.75, "label": "Composer 2.5",      "short": "Composer 2.5"},
    "composer-2":         {"input":  0.75, "output":  2.75, "label": "Composer 2",        "short": "Composer 2"},
    "default":            {"input":  3.25, "output": 15.25, "label": "Unknown",           "short": "Unknown"},
    "unknown":            {"input":  3.25, "output": 15.25, "label": "Unknown",           "short": "Unknown"},
}

INPUT_OUTPUT_RATIO = 4.0            # only used for estimate mode
OUTPUT_TOKENS_PER_HASH = 60         # only used for estimate mode

API_URL = "https://cursor.com/api/dashboard/get-filtered-usage-events"
COOKIE_NAME = "WorkosCursorSessionToken"


@dataclass
class ProjectStats:
    name: str
    conversations: int = 0
    hash_count: int = 0                    # request/hash count depending on mode
    models: dict = field(default_factory=dict)
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    estimated_cost_usd: float = 0.0


# ── Local Cursor DB paths ───────────────────────────────────────────────────

def cursor_db_path() -> Path:
    base = Path.home() / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage"
    return base / "state.vscdb"


def ai_tracking_db_path() -> Path:
    return Path.home() / ".cursor" / "ai-tracking" / "ai-code-tracking.db"


def token_path() -> Path:
    d = Path.home() / ".cursor-costtrack"
    d.mkdir(exist_ok=True)
    return d / "token"


# ── Token storage ────────────────────────────────────────────────────────────

def load_saved_token() -> Optional[str]:
    p = token_path()
    if p.exists():
        val = p.read_text().strip()
        return val or None
    return None


def save_token(token: str) -> None:
    p = token_path()
    p.write_text(token)
    os.chmod(p, 0o600)


def clean_token(raw: str) -> str:
    raw = raw.strip().strip('"').strip("'")
    if raw.startswith(f"{COOKIE_NAME}="):
        raw = raw.split("=", 1)[1]
    return raw


LISTEN_PORT = 7823


class _TokenRequestHandler(http.server.BaseHTTPRequestHandler):
    """Receives the session token pushed by the Chrome extension."""

    received_token: Optional[str] = None

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # CORS preflight
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self) -> None:
        if self.path != "/token":
            self._send_json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")

        try:
            data = json.loads(raw)
            token = clean_token(data.get("token", ""))
        except (json.JSONDecodeError, AttributeError):
            self._send_json(400, {"error": "bad request"})
            return

        if len(token) < 50:
            self._send_json(400, {"error": "token too short"})
            return

        _TokenRequestHandler.received_token = token
        self._send_json(200, {"ok": True})

    def log_message(self, format: str, *args) -> None:  # noqa: A002 — silence default logging
        pass


def listen_for_token(timeout_seconds: int = 120) -> Optional[str]:
    """Start a local HTTP server and wait for the Chrome extension to push a token."""
    _TokenRequestHandler.received_token = None

    try:
        server = socketserver.TCPServer(("127.0.0.1", LISTEN_PORT), _TokenRequestHandler)
    except OSError as e:
        print(f"  ⚠  Couldn't start listener on port {LISTEN_PORT}: {e}")
        print("     Something else may already be using this port.")
        return None

    server.timeout = 1.0

    print()
    print(f"  Listening on http://localhost:{LISTEN_PORT} for the Chrome extension…")
    print("  Open https://cursor.com in Chrome (with the extension installed)")
    print("  and make sure you're logged in.")
    print()
    print("  Waiting… (Ctrl+C to cancel)")

    start = time.time()
    try:
        while time.time() - start < timeout_seconds:
            server.handle_request()
            if _TokenRequestHandler.received_token:
                break
    except KeyboardInterrupt:
        print("\n  Cancelled.")
    finally:
        server.server_close()

    return _TokenRequestHandler.received_token


def listen_flow() -> None:
    token = listen_for_token()
    if token:
        save_token(token)
        print()
        print("  ✅ Connected! Run costtrack.py again to see exact billing.")
        print()
    else:
        print()
        print("  ⚠  Timed out waiting for the Chrome extension.")
        print("     Make sure it's installed (chrome://extensions → Load unpacked →")
        print("     chrome-extension/) and that you visited https://cursor.com.")
        print("     Falling back to manual setup: run `costtrack.py --setup`.")
        print()


def setup_flow() -> None:
    print()
    print("  Cursor Cost Tracker — Manual Setup")
    print("  " + "─" * 46)
    print()
    print("  Tip: `costtrack.py --listen` + the Chrome extension does this")
    print("  automatically and avoids copy-paste mistakes. Use manual setup")
    print("  only if you'd rather not install the extension.")
    print()
    print("  To fetch exact billing data, we need your Cursor")
    print("  session token. It's saved only on this machine")
    print(f"  ({token_path()}, permissions 600).")
    print()
    print("  The token is stored as an httpOnly cookie, so it can't be read")
    print("  via a JavaScript snippet — you'll copy it directly from DevTools.")
    print()
    print("  1. Open https://cursor.com in your browser (stay logged in)")
    print("  2. Open DevTools (Cmd+Option+I)")
    print("  3. Go to the Application tab (Chrome/Brave/Edge)")
    print("     or Storage tab (Firefox/Safari)")
    print("  4. In the left sidebar: Cookies → https://cursor.com")
    print("  5. Find the row named  WorkosCursorSessionToken")
    print("  6. Click once on its Value cell to edit it, then Cmd+A")
    print("     to select the ENTIRE value, then Cmd+C to copy")
    print()
    print("  ⚠  Don't double-click — the token contains '.', '-', or '%3A'")
    print("     characters that make double-click only select part of it,")
    print("     which silently truncates the token.")
    print()
    print("  Copy it exactly as shown — don't decode or trim it.")
    print()

    token = clean_token(input("  Paste it here: "))

    if len(token) < 50:
        print()
        print(f"  ⚠  That token looks too short ({len(token)} chars) — real session")
        print("     tokens are usually 100+ characters. You likely double-clicked")
        print("     instead of using Cmd+A, which truncates the copy. Try again.")
        sys.exit(1)

    save_token(token)
    print()
    print(f"  ✅ Saved. Run costtrack.py again to see exact billing.")
    print()


# ── Slack integration ───────────────────────────────────────────────────────

def slack_webhook_path() -> Path:
    d = Path.home() / ".cursor-costtrack"
    d.mkdir(exist_ok=True)
    return d / "slack_webhook"


def load_slack_webhook() -> Optional[str]:
    p = slack_webhook_path()
    if p.exists():
        val = p.read_text().strip()
        return val or None
    return None


def save_slack_webhook(url: str) -> None:
    p = slack_webhook_path()
    p.write_text(url)
    os.chmod(p, 0o600)


def slack_setup_flow() -> None:
    print()
    print("  Cursor Cost Tracker — Slack Setup")
    print("  " + "─" * 46)
    print()
    print("  1. Go to https://api.slack.com/apps → Create New App → From scratch")
    print("  2. Name it (e.g. 'Cost Tracker') and pick your workspace")
    print("  3. In the left sidebar: Incoming Webhooks → toggle On")
    print("  4. Click 'Add New Webhook to Workspace', choose a channel")
    print("     (e.g. #cost-tracker, or a DM to yourself)")
    print("  5. Copy the Webhook URL — starts with")
    print("     https://hooks.slack.com/services/...")
    print()

    url = input("  Paste the webhook URL here: ").strip()

    if not url.startswith("https://hooks.slack.com/services/"):
        print()
        print("  ⚠  That doesn't look like a Slack webhook URL. Aborting.")
        sys.exit(1)

    save_slack_webhook(url)
    print()
    print("  ✅ Saved. Run `costtrack.py --post-slack` to send a test message.")
    print("     Run `costtrack.py --install-daily 09:00` to schedule a daily post.")
    print()


def daily_marker_path() -> Path:
    return Path.home() / ".cursor-costtrack" / "last_slack_post_date"


def already_posted_today() -> bool:
    p = daily_marker_path()
    return p.exists() and p.read_text().strip() == datetime.date.today().isoformat()


def mark_posted_today() -> None:
    daily_marker_path().write_text(datetime.date.today().isoformat())


def post_to_slack(webhook_url: str, text: str) -> None:
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Slack rejected the message ({e.code}): {e.read()[:200]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error posting to Slack: {e.reason}")


# ── Daily scheduling (macOS launchd) ────────────────────────────────────────

def daily_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.costtrack.dailyreport.plist"


def install_daily_flow(time_str: str) -> None:
    try:
        hour, minute = map(int, time_str.split(":"))
        assert 0 <= hour <= 23 and 0 <= minute <= 59
    except (ValueError, AssertionError):
        print("  Usage: --install-daily HH:MM   e.g. --install-daily 09:00")
        sys.exit(1)

    if not load_slack_webhook():
        print("  ⚠  No Slack webhook configured yet. Run `costtrack.py --slack-setup` first.")
        sys.exit(1)

    script_path = Path(__file__).resolve()
    python_path = sys.executable
    log_dir = Path.home() / ".cursor-costtrack"
    log_dir.mkdir(exist_ok=True)

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.costtrack.dailyreport</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{script_path}</string>
        <string>--post-slack</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{hour}</integer>
        <key>Minute</key>
        <integer>{minute}</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{log_dir}/daily.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/daily.err.log</string>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""

    plist_path = daily_plist_path()
    plist_path.write_text(plist)

    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    result = subprocess.run(["launchctl", "load", "-w", str(plist_path)], capture_output=True, text=True)

    print()
    if result.returncode == 0:
        print(f"  ✅ Scheduled: Slack report will be sent daily at {time_str}.")
        print(f"     Logs: {log_dir}/daily.log")
        print()
        print("     Catch-up behavior:")
        print("     • Mac asleep at the scheduled time → sends as soon as it wakes.")
        print("     • Mac fully off/shut down → sends as soon as you next log in.")
        print("     • Won't double-send: at most one report per calendar day.")
        print()
        print("     Remove with `costtrack.py --uninstall-daily`.")
    else:
        print(f"  ⚠  launchctl error: {result.stderr.strip()}")
    print()


def uninstall_daily_flow() -> None:
    plist_path = daily_plist_path()
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    if plist_path.exists():
        plist_path.unlink()
        print("  Daily Slack schedule removed.")
    else:
        print("  No daily schedule found.")


# ── Local SQLite reads ──────────────────────────────────────────────────────

def get_month_composers(db_path: Path, year: int, month: int) -> list[dict]:
    """Read composerHeaders and return conversations active in the given month."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT value FROM ItemTable WHERE key = 'composer.composerHeaders'"
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return []

    data = json.loads(row[0])
    composers = data.get("allComposers", [])

    month_start = int(datetime.datetime(year, month, 1).timestamp() * 1000)
    if month == 12:
        month_end = int(datetime.datetime(year + 1, 1, 1).timestamp() * 1000)
    else:
        month_end = int(datetime.datetime(year, month + 1, 1).timestamp() * 1000)

    result = []
    for c in composers:
        updated = c.get("lastUpdatedAt", 0)
        if month_start <= updated < month_end:
            repos = c.get("trackedGitRepos", [])
            if repos:
                project = repos[0].get("repoPath", "")
                branches = repos[0].get("branches", [])
                # A conversation can touch multiple branches (if you switch mid-chat) —
                # attribute it to whichever branch was interacted with most recently.
                branch = (
                    max(branches, key=lambda b: b.get("lastInteractionAt", 0)).get("branchName", "no-branch")
                    if branches else "no-branch"
                )
            else:
                ws = c.get("workspaceIdentifier", {})
                project = ws.get("uri", {}).get("fsPath", "no-workspace")
                branch = "no-branch"

            result.append({
                "composerId": c.get("composerId", ""),
                "project": project.rstrip(),
                "branch": branch,
                "is_worktree": bool(c.get("isWorktree", False)),
                "name": c.get("name", ""),
                "createdAt": c.get("createdAt", 0),
                "lastUpdatedAt": updated,
                "contextUsagePercent": c.get("contextUsagePercent", 0),
            })
    return result


def get_code_hashes_by_conversation(db_path: Path, since_ts_ms: int) -> dict[str, list[dict]]:
    """Return {conversationId: [{model, count}, ...]} for the period. Estimate mode only."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            """
            SELECT conversationId, model, COUNT(*) as cnt
            FROM ai_code_hashes
            WHERE timestamp > ?
            GROUP BY conversationId, model
            """,
            (since_ts_ms // 1000,),
        ).fetchall()
    finally:
        conn.close()

    result: dict[str, list[dict]] = defaultdict(list)
    for conversation_id, model, cnt in rows:
        if conversation_id:
            result[conversation_id].append({"model": model or "default", "count": cnt})
    return result


def normalize_project_name(path: str) -> str:
    if not path or path == "no-workspace":
        return "no-workspace"
    return Path(path).name or path


# ── Cursor usage API (exact mode) ───────────────────────────────────────────

def fetch_usage_events(token: str, year: int, month: int) -> list[dict]:
    """Fetch every usage event for the given month via Cursor's dashboard API."""
    # The endpoint expects epoch-millisecond strings, not YYYY-MM-DD.
    start_ms = int(datetime.datetime(year, month, 1).timestamp() * 1000)
    if month == 12:
        end_ms = int(datetime.datetime(year + 1, 1, 1).timestamp() * 1000)
    else:
        end_ms = int(datetime.datetime(year, month + 1, 1).timestamp() * 1000)

    cookie = f"{COOKIE_NAME}={token}"
    page_size = 200
    page = 1  # Cursor's API is 1-indexed and rejects page 0 ("page must be at least 1")
    total_known: Optional[int] = None
    events: list[dict] = []

    while total_known is None or len(events) < total_known:
        body = json.dumps({
            "startDate": str(start_ms),
            "endDate": str(end_ms),
            "pageSize": page_size,
            "page": page,
        }).encode("utf-8")

        req = urllib.request.Request(
            API_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Cookie": cookie,
                # cursor.com rejects state-changing POSTs whose Origin doesn't
                # match the site ("Invalid origin for state-changing request" -> 403).
                "Origin": "https://cursor.com",
                "Referer": "https://cursor.com/dashboard",
                "User-Agent": "CursorCostTracker/0.2",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise PermissionError(
                    "Cursor session token is invalid or expired. "
                    "Reconnect with --listen (or --setup)."
                )
            if e.code == 403:
                raise PermissionError(
                    "Cursor API rejected the request (403). The token is likely "
                    "expired — reconnect with --listen (or --setup)."
                )
            raise RuntimeError(f"Cursor API error {e.code}: {e.read().decode('utf-8')[:200]}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Network error reaching Cursor API: {e.reason}")

        data = json.loads(raw)
        total_known = data.get("totalUsageEventsCount", 0)
        # The response field was renamed usageEvents -> usageEventsDisplay;
        # keep a fallback so older/newer responses both work.
        page_events = data.get("usageEventsDisplay") or data.get("usageEvents") or []

        for ev in page_events:
            token_usage = ev.get("tokenUsage") or {}
            events.append({
                "timestamp": int(ev.get("timestamp", 0)),
                "model": ev.get("model") or "unknown",
                "input_tokens": token_usage.get("inputTokens", 0),
                "output_tokens": token_usage.get("outputTokens", 0),
                "charged_cents": ev.get("chargedCents", token_usage.get("totalCents", 0)) or 0,
                "is_headless": ev.get("isHeadless", False),
            })

        if len(page_events) < page_size:
            break
        page += 1

    return events


def find_composer_for_timestamp(ts_ms: int, composers: list[dict]) -> Optional[dict]:
    """Find the conversation most likely to have generated a billing event at ts_ms,
    by matching its timestamp against each conversation's active window
    [createdAt, lastUpdatedAt]. Returns None if no conversation matches."""
    candidates = [
        c for c in composers
        if c["createdAt"] <= ts_ms <= c["lastUpdatedAt"] + 5_000
    ]
    if not candidates:
        return None

    return min(candidates, key=lambda c: c["lastUpdatedAt"] - c["createdAt"])


def build_exact_project_stats(events: list[dict], composers: list[dict]) -> dict[str, ProjectStats]:
    conv_counts: dict[str, int] = defaultdict(int)
    for c in composers:
        conv_counts[normalize_project_name(c["project"])] += 1

    projects: dict[str, ProjectStats] = {}

    for ev in events:
        composer = find_composer_for_timestamp(ev["timestamp"], composers)
        name = normalize_project_name(composer["project"]) if composer else "unattributed"

        if name not in projects:
            projects[name] = ProjectStats(name=name, conversations=conv_counts.get(name, 0))

        stats = projects[name]
        stats.hash_count += 1
        stats.models[ev["model"]] = stats.models.get(ev["model"], 0) + 1
        stats.estimated_input_tokens += ev["input_tokens"]
        stats.estimated_output_tokens += ev["output_tokens"]
        stats.estimated_cost_usd += ev["charged_cents"] / 100

    for name, count in conv_counts.items():
        if name not in projects:
            projects[name] = ProjectStats(name=name, conversations=count)

    return projects


def build_exact_branch_stats(events: list[dict], composers: list[dict]) -> dict[str, dict[str, ProjectStats]]:
    """Same attribution as build_exact_project_stats, but nested one level
    deeper: {project_name: {branch_name: ProjectStats}}."""
    conv_counts: dict[tuple[str, str], int] = defaultdict(int)
    for c in composers:
        key = (normalize_project_name(c["project"]), c.get("branch", "no-branch"))
        conv_counts[key] += 1

    branches: dict[str, dict[str, ProjectStats]] = defaultdict(dict)

    def get_stats(project_name: str, branch: str) -> ProjectStats:
        bucket = branches[project_name]
        if branch not in bucket:
            bucket[branch] = ProjectStats(name=branch, conversations=conv_counts.get((project_name, branch), 0))
        return bucket[branch]

    for ev in events:
        composer = find_composer_for_timestamp(ev["timestamp"], composers)
        if composer is None:
            project_name, branch = "unattributed", "no-branch"
        else:
            project_name = normalize_project_name(composer["project"])
            branch = composer.get("branch", "no-branch")

        stats = get_stats(project_name, branch)
        stats.hash_count += 1
        stats.models[ev["model"]] = stats.models.get(ev["model"], 0) + 1
        stats.estimated_input_tokens += ev["input_tokens"]
        stats.estimated_output_tokens += ev["output_tokens"]
        stats.estimated_cost_usd += ev["charged_cents"] / 100

    for project_name, branch in conv_counts:
        get_stats(project_name, branch)

    return branches


def tab_label(name: str, composer_id: str) -> str:
    """Display label for a conversation ("tab"). Cursor conversations often
    have no auto-generated title yet, so fall back to a short, stable id."""
    name = (name or "").strip()
    if name:
        return name
    short_id = (composer_id or "")[:8] or "unknown"
    return f"(untitled {short_id})"


def build_exact_tab_stats(events: list[dict], composers: list[dict]) -> dict[str, dict[str, ProjectStats]]:
    """Same attribution as build_exact_project_stats, but nested one level
    deeper: {project_name: {conversation_label: ProjectStats}}."""
    conv_counts: dict[tuple[str, str], int] = defaultdict(int)
    for c in composers:
        key = (normalize_project_name(c["project"]), tab_label(c.get("name", ""), c.get("composerId", "")))
        conv_counts[key] += 1

    tabs: dict[str, dict[str, ProjectStats]] = defaultdict(dict)

    def get_stats(project_name: str, tab: str) -> ProjectStats:
        bucket = tabs[project_name]
        if tab not in bucket:
            bucket[tab] = ProjectStats(name=tab, conversations=conv_counts.get((project_name, tab), 0))
        return bucket[tab]

    for ev in events:
        composer = find_composer_for_timestamp(ev["timestamp"], composers)
        if composer is None:
            project_name, tab = "unattributed", "(untitled unknown)"
        else:
            project_name = normalize_project_name(composer["project"])
            tab = tab_label(composer.get("name", ""), composer.get("composerId", ""))

        stats = get_stats(project_name, tab)
        stats.hash_count += 1
        stats.models[ev["model"]] = stats.models.get(ev["model"], 0) + 1
        stats.estimated_input_tokens += ev["input_tokens"]
        stats.estimated_output_tokens += ev["output_tokens"]
        stats.estimated_cost_usd += ev["charged_cents"] / 100

    for project_name, tab in conv_counts:
        get_stats(project_name, tab)

    return tabs


# ── Estimate mode (no token) ────────────────────────────────────────────────

def estimate_cost(model: str, output_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model, MODEL_PRICING["default"])
    input_tokens = int(output_tokens * INPUT_OUTPUT_RATIO)
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


def build_estimated_project_stats(
    composers: list[dict],
    hashes_by_conv: dict[str, list[dict]],
) -> dict[str, ProjectStats]:
    projects: dict[str, ProjectStats] = {}

    for c in composers:
        project_path = c["project"]
        project_name = normalize_project_name(project_path)

        if project_path not in projects:
            projects[project_path] = ProjectStats(name=project_name)

        stats = projects[project_path]
        stats.conversations += 1

        for entry in hashes_by_conv.get(c["composerId"], []):
            model = entry["model"] or "default"
            count = entry["count"]
            stats.hash_count += count
            stats.models[model] = stats.models.get(model, 0) + count

            output_tokens = count * OUTPUT_TOKENS_PER_HASH
            stats.estimated_output_tokens += output_tokens
            stats.estimated_input_tokens += int(output_tokens * INPUT_OUTPUT_RATIO)
            stats.estimated_cost_usd += estimate_cost(model, output_tokens)

    return projects


def build_estimated_branch_stats(
    composers: list[dict],
    hashes_by_conv: dict[str, list[dict]],
) -> dict[str, dict[str, ProjectStats]]:
    """Same as build_estimated_project_stats, but nested one level deeper:
    {project_name: {branch_name: ProjectStats}}."""
    branches: dict[str, dict[str, ProjectStats]] = defaultdict(dict)

    for c in composers:
        project_name = normalize_project_name(c["project"])
        branch = c.get("branch", "no-branch")
        bucket = branches[project_name]
        if branch not in bucket:
            bucket[branch] = ProjectStats(name=branch)

        stats = bucket[branch]
        stats.conversations += 1

        for entry in hashes_by_conv.get(c["composerId"], []):
            model = entry["model"] or "default"
            count = entry["count"]
            stats.hash_count += count
            stats.models[model] = stats.models.get(model, 0) + count

            output_tokens = count * OUTPUT_TOKENS_PER_HASH
            stats.estimated_output_tokens += output_tokens
            stats.estimated_input_tokens += int(output_tokens * INPUT_OUTPUT_RATIO)
            stats.estimated_cost_usd += estimate_cost(model, output_tokens)

    return branches


def build_estimated_tab_stats(
    composers: list[dict],
    hashes_by_conv: dict[str, list[dict]],
) -> dict[str, dict[str, ProjectStats]]:
    """Same as build_estimated_project_stats, but nested one level deeper:
    {project_name: {conversation_label: ProjectStats}}."""
    tabs: dict[str, dict[str, ProjectStats]] = defaultdict(dict)

    for c in composers:
        project_name = normalize_project_name(c["project"])
        tab = tab_label(c.get("name", ""), c.get("composerId", ""))
        bucket = tabs[project_name]
        if tab not in bucket:
            bucket[tab] = ProjectStats(name=tab)

        stats = bucket[tab]
        stats.conversations += 1

        for entry in hashes_by_conv.get(c["composerId"], []):
            model = entry["model"] or "default"
            count = entry["count"]
            stats.hash_count += count
            stats.models[model] = stats.models.get(model, 0) + count

            output_tokens = count * OUTPUT_TOKENS_PER_HASH
            stats.estimated_output_tokens += output_tokens
            stats.estimated_input_tokens += int(output_tokens * INPUT_OUTPUT_RATIO)
            stats.estimated_cost_usd += estimate_cost(model, output_tokens)

    return tabs


# ── Rendering ────────────────────────────────────────────────────────────────

def bar(fraction: float, width: int = 8) -> str:
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled)


def model_short(model: str) -> str:
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]["short"]

    # The API returns suffixed variants (e.g. "claude-opus-4-8-thinking-high",
    # "claude-sonnet-5-thinking-high", "claude-4.6-sonnet-medium-thinking",
    # "composer-2.5-fast") that aren't exact MODEL_PRICING keys. Normalize them.
    m = model.lower()
    if "opus" in m:
        return "Opus 4"
    if "sonnet-5" in m or "sonnet5" in m or "-5-sonnet" in m:
        return "Sonnet 5"
    if "sonnet" in m:
        return "Sonnet 4.6" if ("4.6" in m or "4-6" in m) else "Sonnet"
    if "composer" in m:
        return "Composer 2.5" if "2.5" in m else "Composer"
    if "codex" in m:
        return "Codex"
    if "gpt-5.5" in m:
        return "GPT-5.5"
    if "gpt" in m:
        return "GPT"
    return MODEL_PRICING.get(model, MODEL_PRICING["default"]).get("short", model)


def model_summary(models: dict, width: int) -> str:
    """Render a compact multi-model breakdown, e.g. 'Opus 4 62%, Sonnet 4.6 38%'."""
    if not models:
        return "—"

    # Different model IDs (e.g. claude-opus-4-8 vs claude-opus-4-7) can share
    # the same short label — merge their counts before computing percentages.
    grouped: dict[str, int] = defaultdict(int)
    for model, count in models.items():
        grouped[model_short(model)] += count

    total = sum(grouped.values())
    ranked = sorted(grouped.items(), key=lambda kv: kv[1], reverse=True)
    parts = [f"{label} {round(c / total * 100)}%" for label, c in ranked]

    full = ", ".join(parts)
    if len(full) <= width:
        return full

    # Doesn't fit — keep as many whole parts as fit, then "+N more"
    fitted: list[str] = []
    for i, p in enumerate(parts):
        remaining = len(parts) - (i + 1)
        suffix = f" +{remaining}" if remaining else ""
        candidate = ", ".join(fitted + [p])
        if len(candidate) + len(suffix) <= width:
            fitted.append(p)
        else:
            break

    remaining = len(parts) - len(fitted)
    result = ", ".join(fitted)
    if remaining:
        result += f" +{remaining}"
    return result[:width]


TREE_MID  = "      ├──  "   # non-last sub-row under a project
TREE_LAST = "      └──  "   # last sub-row under a project


def build_table_lines(
    stats: dict[str, ProjectStats],
    is_exact: bool,
    sub_stats: Optional[dict[str, dict[str, ProjectStats]]] = None,
) -> list[str]:
    """Build the project cost table as plain-text lines (no leading indent),
    shared by both the terminal printer and the Slack formatter. If
    sub_stats is given, each project row is followed by indented tree-style
    sub-rows breaking that project's cost down further (by branch or by
    conversation/"tab", depending on which builder produced sub_stats)."""
    sorted_projects = sorted(stats.values(), key=lambda s: s.estimated_cost_usd, reverse=True)
    total_cost = sum(s.estimated_cost_usd for s in sorted_projects)
    total_convs = sum(s.conversations for s in sorted_projects)

    # Sub-rows eat into the name column via their tree prefix, and conversation
    # titles run longer than branch names — borrow some width from MODELS.
    cost_w, convs_w = 9, 5
    name_w, models_w = (34, 24) if sub_stats else (24, 34)
    header = f"{'PROJECT':<{name_w}}  {'COST':>{cost_w}}  {'CONVS':>{convs_w}}  {'MODELS':<{models_w}}  SHARE"
    divider = "─" * len(header)

    def render_row(name: str, s: ProjectStats, share_base: float) -> str:
        share = s.estimated_cost_usd / share_base if share_base > 0 else 0
        name_str = name[:name_w]
        models_str = model_summary(s.models, models_w)
        cost_str = f"${s.estimated_cost_usd:.2f}" if is_exact else f"~${s.estimated_cost_usd:.2f}"
        return (
            f"{name_str:<{name_w}}  {cost_str:>{cost_w}}  {s.conversations:>{convs_w}}  "
            f"{models_str:<{models_w}}  {bar(share)} {share*100:.0f}%"
        )

    lines = [divider, header, divider]

    for s in sorted_projects:
        if s.estimated_cost_usd == 0 and s.conversations == 0:
            continue
        lines.append(render_row(s.name, s, total_cost))

        if sub_stats:
            children = sub_stats.get(s.name, {})
            sorted_children = [
                b for b in sorted(children.values(), key=lambda b: b.estimated_cost_usd, reverse=True)
                if not (b.estimated_cost_usd == 0 and b.conversations == 0)
            ]
            # Sub-row share is relative to its own project's cost — "how much
            # of this project's spend" the branch/tab accounts for.
            for i, b in enumerate(sorted_children):
                prefix = TREE_LAST if i == len(sorted_children) - 1 else TREE_MID
                lines.append(render_row(f"{prefix}{b.name}", b, s.estimated_cost_usd))

    lines.append(divider)
    total_str = f"${total_cost:.2f}" if is_exact else f"~${total_cost:.2f}"
    lines.append(f"{'TOTAL':<{name_w}}  {total_str:>{cost_w}}  {total_convs:>{convs_w}}")

    return lines


def print_dashboard(
    stats: dict[str, ProjectStats],
    month_label: str,
    is_exact: bool,
    sub_stats: Optional[dict[str, dict[str, ProjectStats]]] = None,
) -> None:
    print()
    print(f"  Cursor Cost Tracker — {month_label}")
    for line in build_table_lines(stats, is_exact, sub_stats):
        print(f"  {line}")
    print()

    if is_exact:
        print("  ✅ Exact billing data from your Cursor account.")
    else:
        print("  ⚠  Estimated from code output volume — real cost may differ significantly.")
        print("     Run `python3 costtrack.py --setup` once to see exact billing.")
    print()


def format_dashboard_for_slack(
    stats: dict[str, ProjectStats],
    month_label: str,
    is_exact: bool,
    sub_stats: Optional[dict[str, dict[str, ProjectStats]]] = None,
) -> str:
    table = "\n".join(build_table_lines(stats, is_exact, sub_stats))
    footer = (
        "✅ _Exact billing data from your Cursor account._"
        if is_exact
        else "⚠️ _Estimated from code output volume — run `--listen` or `--setup` for exact billing._"
    )
    return f"*Cursor Cost Tracker — {month_label}*\n```{table}```\n{footer}"


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]

    if "--listen" in args:
        listen_flow()
        return

    if "--setup" in args:
        setup_flow()
        return

    if "--logout" in args:
        p = token_path()
        if p.exists():
            p.unlink()
            print("  Token removed. Future runs will show estimates.")
        else:
            print("  No saved token found.")
        return

    if "--slack-setup" in args:
        slack_setup_flow()
        return

    if "--install-daily" in args:
        idx = args.index("--install-daily")
        time_str = args[idx + 1] if idx + 1 < len(args) and ":" in args[idx + 1] else "09:00"
        install_daily_flow(time_str)
        return

    if "--uninstall-daily" in args:
        uninstall_daily_flow()
        return

    if "--post-slack" in args and "--force" not in args and already_posted_today():
        print("  Already posted today's report — skipping (use --force to resend).")
        return

    now = datetime.datetime.now()
    year, month = now.year, now.month
    if "--month" in args:
        idx = args.index("--month")
        try:
            year, month = map(int, args[idx + 1].split("-"))
        except (ValueError, IndexError):
            print(
                "Usage: costtrack.py [--month YYYY-MM] [--listen] [--setup] [--logout] "
                "[--token TOKEN] [--slack-setup] [--post-slack] [--install-daily HH:MM] "
                "[--uninstall-daily]"
            )
            sys.exit(1)

    token: Optional[str] = None
    if "--token" in args:
        idx = args.index("--token")
        token = clean_token(args[idx + 1])
    else:
        token = load_saved_token()

    month_label = datetime.date(year, month, 1).strftime("%B %Y")
    month_start_ms = int(datetime.datetime(year, month, 1).timestamp() * 1000)

    global_db = cursor_db_path()
    tracking_db = ai_tracking_db_path()

    if not global_db.exists():
        print(f"Error: Cursor database not found at {global_db}")
        print("Make sure Cursor is installed and has been used at least once.")
        sys.exit(1)

    composers = get_month_composers(global_db, year, month)
    if not composers:
        print(f"No conversations found for {month_label}.")
        sys.exit(0)

    by_branch = "--by-branch" in args
    by_tab = "--by-tab" in args
    if by_branch and by_tab:
        print("  ⚠  --by-branch and --by-tab can't be combined yet — showing tabs.")
        by_branch = False

    stats: Optional[dict] = None
    sub_stats: Optional[dict] = None
    is_exact = False
    events: Optional[list] = None

    if token:
        try:
            print("  Fetching exact billing data from Cursor…")
            events = fetch_usage_events(token, year, month)
            stats = build_exact_project_stats(events, composers)
            is_exact = True
        except PermissionError as e:
            print(f"  ⚠  {e}")
            print("  Falling back to estimates.\n")
        except Exception as e:
            print(f"  ⚠  Could not fetch exact billing ({e}). Falling back to estimates.\n")

    if stats is not None and is_exact:
        if by_tab:
            sub_stats = build_exact_tab_stats(events, composers)
        elif by_branch:
            sub_stats = build_exact_branch_stats(events, composers)
    else:
        if not tracking_db.exists():
            print(f"Warning: AI tracking database not found at {tracking_db}")
            print("Cost estimates will be based on conversation count only.")

        hashes_by_conv: dict = {}
        if tracking_db.exists():
            hashes_by_conv = get_code_hashes_by_conversation(tracking_db, month_start_ms)

        stats = build_estimated_project_stats(composers, hashes_by_conv)
        if by_tab:
            sub_stats = build_estimated_tab_stats(composers, hashes_by_conv)
        elif by_branch:
            sub_stats = build_estimated_branch_stats(composers, hashes_by_conv)

    if "--post-slack" in args:
        webhook = load_slack_webhook()
        if not webhook:
            print("  ⚠  No Slack webhook configured. Run `costtrack.py --slack-setup` first.")
            sys.exit(1)
        message = format_dashboard_for_slack(stats, month_label, is_exact, sub_stats)
        try:
            post_to_slack(webhook, message)
            mark_posted_today()
            print(f"  ✅ Posted {month_label} report to Slack.")
        except RuntimeError as e:
            print(f"  ⚠  {e}")
            sys.exit(1)
    else:
        print_dashboard(stats, month_label, is_exact, sub_stats)


if __name__ == "__main__":
    main()
