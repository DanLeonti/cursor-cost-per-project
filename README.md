# Cursor Cost Per Project

Per-project AI spend tracking for [Cursor](https://cursor.com). See exactly which repos are costing you money each month — with exact billing data and optional daily Slack reports.

**Requirements:** macOS, Python 3.9+ (stdlib only, no dependencies to install), and Cursor installed and used at least once. *(Reads Cursor's local database paths, which are currently macOS-specific.)*

## Installation

```bash
git clone https://github.com/DanLeonti/cursor-cost-per-project.git
cd cursor-cost-per-project
python3 cli/costtrack.py
```

That's it — no `pip install`, no build step, no VS Code extension required.

## Quick start

```bash
python3 cli/costtrack.py
```

First run shows **estimates** (based on local code-output volume). For **exact billing** matching your Cursor invoice, connect your account once:

```bash
python3 cli/costtrack.py --listen
```

Then, with the [Chrome extension](#chrome-extension-optional) installed, open [cursor.com](https://cursor.com) in Chrome (logged in) — the token transfers automatically and gets saved locally. From then on, `costtrack.py` uses exact numbers.

Prefer not to install a browser extension? Use manual setup instead:

```bash
python3 cli/costtrack.py --setup
```

This walks you through copying your session token directly from browser DevTools.

## Usage

```bash
python3 cli/costtrack.py                     # current month
python3 cli/costtrack.py --month 2026-06     # specific month
python3 cli/costtrack.py --listen            # connect via Chrome extension (recommended)
python3 cli/costtrack.py --setup             # connect by pasting token manually
python3 cli/costtrack.py --logout            # disconnect / remove saved token

python3 cli/costtrack.py --slack-setup       # connect a Slack incoming webhook
python3 cli/costtrack.py --post-slack        # post today's dashboard to Slack now
python3 cli/costtrack.py --install-daily 09:00  # schedule a daily Slack post
python3 cli/costtrack.py --uninstall-daily   # remove the daily schedule
```

Sample output:

```
  Cursor Cost Tracker — June 2026
  ─────────────────────────────────────────────────────────────────────────────────────
  PROJECT                        COST  CONVS  MODELS                              SHARE
  ─────────────────────────────────────────────────────────────────────────────────────
  playground                  $132.76      8  Opus 4 94%, GPT-5.5 4% +3           ████░░░░ 54%
  General                      $57.56     24  Opus 4 99%, GPT-5.5 1%              ██░░░░░░ 23%
  Grafana                      $57.27     23  Opus 4 99%, Sonnet 4.6 0% +2        ██░░░░░░ 23%
  ─────────────────────────────────────────────────────────────────────────────────────
  TOTAL                       $247.59     55

  ✅ Exact billing data from your Cursor account.
```

## Chrome extension (optional)

Automates getting your session token — no manual copy-paste, no truncation mistakes.

1. Open Chrome → `chrome://extensions`
2. Toggle **Developer mode** on
3. **Load unpacked** → select the `chrome-extension/` folder
4. Run `python3 cli/costtrack.py --listen` in a terminal
5. Visit [cursor.com](https://cursor.com) (logged in) — token transfers automatically

The extension uses `chrome.cookies.get()`, which can read the `httpOnly` session cookie that page JavaScript cannot — this is why it's more reliable than copying from DevTools by hand.

**Note:** the CLI listener only runs on-demand (`--listen`), not as a background daemon. If your session expires, just run `--listen` again — no need to reinstall anything.

## Slack: daily cost report

Get the same table posted to a Slack channel or DM every morning.

**1. Connect a Slack webhook** (one-time):

```bash
python3 cli/costtrack.py --slack-setup
```

This walks you through creating a free [Incoming Webhook](https://api.slack.com/apps) — no Slack app review, no bot scopes, just a URL, saved locally at `~/.cursor-costtrack/slack_webhook` (permissions 600).

**2. Test it:**

```bash
python3 cli/costtrack.py --post-slack
```

**3. Schedule it to run automatically every day:**

```bash
python3 cli/costtrack.py --install-daily 09:00
```

This installs a [launchd](https://en.wikipedia.org/wiki/Launchd) user agent (`~/Library/LaunchAgents/com.costtrack.dailyreport.plist`) that runs `costtrack.py --post-slack` at 9:00 AM every day — no cron, no server. Logs go to `~/.cursor-costtrack/daily.log`.

**What happens if your Mac isn't awake at 9:00 AM?**

| Mac state at 9:00 AM | Behavior |
|---|---|
| Asleep | Sends as soon as it wakes up (native `launchd` behavior) |
| Fully off / shut down | Sends as soon as you next log in (`RunAtLoad`) |
| Already sent once today | Skipped — never double-sends the same day |

Force a resend on the same day (e.g. for testing) with:

```bash
python3 cli/costtrack.py --post-slack --force
```

Remove the schedule with:

```bash
python3 cli/costtrack.py --uninstall-daily
```

**Note:** if your Cursor session token expires, the daily post automatically falls back to estimates rather than failing silently — the Slack message tells you which mode it used (✅ exact vs. ⚠️ estimated). Re-run `--listen` or `--setup` to restore exact billing.

## How it works

| Data source | What's read | Used for |
|---|---|---|
| `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` | `composer.composerHeaders` | Maps conversations → git repos, by timestamp |
| `~/.cursor/ai-tracking/ai-code-tracking.db` | `ai_code_hashes` | Model + volume proxy, used only for estimate mode |
| `cursor.com` usage API (with your session token) | Per-request token counts and exact cost | Exact billing mode |

Since Cursor's billing API has no per-project field, exact-mode costs are attributed to a project by matching each billing event's timestamp against the active window of your local conversations (`createdAt` → `lastUpdatedAt`).

## Why a cookie instead of an API key?

Cursor's usage-history endpoint (`/api/dashboard/get-filtered-usage-events`) is the internal web dashboard's private API — it isn't part of the public, key-authenticated API surface. The official `CURSOR_API_KEY` (used by the Cursor SDK) is scoped to running agents programmatically and has no permission to read usage/billing history.

If you're a **Team/Business plan admin**, Cursor does expose an official Admin API for spending data — that's the proper path if it applies to you. For individual/Pro accounts, the session-cookie approach here is the only option.

## Model pricing (June 2026)

All prices include Cursor Token Rate ($0.25/M tokens). Only used in estimate mode — exact mode uses real costs from the API.

| Model | Input ($/M) | Output ($/M) |
|---|---|---|
| Claude Opus 4 | $15.25 | $75.25 |
| Claude Sonnet 4.6 | $3.25 | $15.25 |
| GPT-5.5 | $10.25 | $40.25 |
| Composer 2.5 | $0.75 | $2.75 |

## Project structure

| Path | Status |
|---|---|
| `cli/costtrack.py` | **Active** — the whole tool. Start here. |
| `chrome-extension/` | **Active, optional** — automates session token syncing (see above). |
| `extension/` | *Legacy* — an earlier VS Code extension prototype, superseded by the CLI. Kept for reference only, not required for anything above. |

## Roadmap

- [ ] CSV export for client billing
- [ ] Budget alerts per project
- [ ] Team dashboard (shared backend)

## License

MIT
