import * as vscode from "vscode";
import { DashboardData, ProjectStats, getModelLabel } from "./models";

export class CostTrackWebviewProvider implements vscode.WebviewViewProvider {
  public static readonly viewType = "costtrack.dashboard";

  private _view?: vscode.WebviewView;
  private _data?: DashboardData;
  private _loading = true;
  private _error?: string;
  private _connected = false;

  constructor(private readonly _extensionUri: vscode.Uri) {}

  public resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ): void {
    this._view = webviewView;

    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this._extensionUri],
    };

    this._render();
  }

  public update(data: DashboardData): void {
    this._data = data;
    this._loading = false;
    this._error = undefined;
    this._render();
  }

  public setError(message: string): void {
    this._error = message;
    this._loading = false;
    this._render();
  }

  public setLoading(): void {
    this._loading = true;
    this._render();
  }

  public setConnected(connected: boolean): void {
    this._connected = connected;
    this._render();
  }

  private _render(): void {
    if (!this._view) return;
    this._view.webview.html = this._buildHtml();
  }

  private _buildHtml(): string {
    if (this._loading) {
      return this._shell(`<div class="loading">Loading usage data…</div>`);
    }

    if (this._error) {
      return this._shell(`<div class="error">⚠ ${this._escapeHtml(this._error)}</div>`);
    }

    const data = this._data!;
    const projectRows = data.projects
      .filter((p) => p.conversations > 0)
      .map((p) => this._projectRow(p, data.totalCostUsd))
      .join("");

    const topModel = this._topModelAcrossAll(data.projects);
    const modelBadge = topModel ? `<span class="badge">${this._escapeHtml(topModel)}</span>` : "";

    const body = `
      <div class="header">
        <div class="month-label">${this._escapeHtml(data.monthLabel)}</div>
        <div class="total-cost">~$${data.totalCostUsd.toFixed(2)}</div>
        <div class="meta">${data.totalConversations} conversations ${modelBadge}</div>
      </div>

      <div class="project-list">
        ${projectRows}
      </div>

      <div class="footer">
        ${data.isEstimate
          ? `<span class="estimate-badge">⚠ Estimated</span> Based on code output volume only — costs may be 5–10× lower than actual billing.
             <br><a href="#" class="connect-link" onclick="return false" title="Install the Cursor Cost Tracker Chrome extension to connect">Connect Chrome extension for exact billing →</a>`
          : `<span class="exact-badge">✓ Exact billing</span> Live data from your Cursor account.
             <a href="#" class="disconnect-link" onclick="return false" title="Disconnect">Disconnect</a>`}
        <br><span class="updated-at">Updated ${new Date(data.generatedAt).toLocaleTimeString()}</span>
      </div>
    `;

    return this._shell(body);
  }

  private _projectRow(p: ProjectStats, totalCost: number): string {
    const share = totalCost > 0 ? p.estimatedCostUsd / totalCost : 0;
    const barWidth = Math.round(share * 100);
    const topModel = this._topModelForProject(p);
    const modelLabel = topModel ? getModelLabel(topModel) : "—";
    const tokens = p.estimatedOutputTokens > 0
      ? `${this._formatTokens(p.estimatedOutputTokens)} out tokens`
      : "";

    return `
      <div class="project-row">
        <div class="project-header">
          <span class="project-name" title="${this._escapeHtml(p.path)}">${this._escapeHtml(p.name)}</span>
          <span class="project-cost">~$${p.estimatedCostUsd.toFixed(2)}</span>
        </div>
        <div class="bar-track">
          <div class="bar-fill" style="width: ${barWidth}%"></div>
        </div>
        <div class="project-meta">
          <span>${p.conversations} conv</span>
          <span>${this._escapeHtml(modelLabel)}</span>
          ${tokens ? `<span class="tokens">${tokens}</span>` : ""}
        </div>
      </div>
    `;
  }

  private _topModelForProject(p: ProjectStats): string | null {
    const keys = Object.keys(p.models);
    if (keys.length === 0) return null;
    return keys.reduce((a, b) => (p.models[a] > p.models[b] ? a : b));
  }

  private _topModelAcrossAll(projects: ProjectStats[]): string | null {
    const combined: Record<string, number> = {};
    for (const p of projects) {
      for (const [model, count] of Object.entries(p.models)) {
        combined[model] = (combined[model] ?? 0) + count;
      }
    }
    const keys = Object.keys(combined);
    if (keys.length === 0) return null;
    const top = keys.reduce((a, b) => (combined[a] > combined[b] ? a : b));
    return getModelLabel(top);
  }

  private _formatTokens(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(0)}k`;
    return String(n);
  }

  private _escapeHtml(str: string): string {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  private _shell(body: string): string {
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
  <title>Cursor Cost Tracker</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
      color: var(--vscode-foreground);
      background: var(--vscode-sideBar-background);
      padding: 0;
    }

    .loading, .error {
      padding: 24px 16px;
      color: var(--vscode-descriptionForeground);
      text-align: center;
    }

    .error { color: var(--vscode-errorForeground); }

    /* ── Header ── */
    .header {
      padding: 16px 16px 12px;
      border-bottom: 1px solid var(--vscode-sideBarSectionHeader-border, rgba(128,128,128,0.2));
    }

    .month-label {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--vscode-descriptionForeground);
      margin-bottom: 4px;
    }

    .total-cost {
      font-size: 28px;
      font-weight: 700;
      color: var(--vscode-foreground);
      line-height: 1.1;
      margin-bottom: 4px;
    }

    .meta {
      font-size: 11px;
      color: var(--vscode-descriptionForeground);
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .badge {
      background: var(--vscode-badge-background);
      color: var(--vscode-badge-foreground);
      border-radius: 3px;
      padding: 1px 5px;
      font-size: 10px;
    }

    /* ── Project list ── */
    .project-list {
      padding: 8px 0;
    }

    .project-row {
      padding: 8px 16px;
      cursor: default;
    }

    .project-row:hover {
      background: var(--vscode-list-hoverBackground);
    }

    .project-header {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      margin-bottom: 4px;
    }

    .project-name {
      font-size: 13px;
      font-weight: 500;
      color: var(--vscode-foreground);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 160px;
    }

    .project-cost {
      font-size: 13px;
      font-weight: 600;
      color: var(--vscode-foreground);
      white-space: nowrap;
      margin-left: 8px;
    }

    .bar-track {
      height: 4px;
      background: var(--vscode-progressBar-background, rgba(128,128,128,0.2));
      border-radius: 2px;
      overflow: hidden;
      margin-bottom: 4px;
    }

    .bar-fill {
      height: 100%;
      background: var(--vscode-button-background, #007acc);
      border-radius: 2px;
      transition: width 0.4s ease;
      min-width: ${0}%;
    }

    .project-meta {
      display: flex;
      gap: 10px;
      font-size: 11px;
      color: var(--vscode-descriptionForeground);
    }

    .tokens {
      margin-left: auto;
      font-size: 10px;
    }

    /* ── Footer ── */
    .footer {
      padding: 12px 16px;
      font-size: 10px;
      color: var(--vscode-descriptionForeground);
      border-top: 1px solid var(--vscode-sideBarSectionHeader-border, rgba(128,128,128,0.2));
      line-height: 1.6;
    }

    .estimate-badge {
      display: inline-block;
      background: var(--vscode-inputValidation-warningBackground, rgba(255,190,0,0.15));
      color: var(--vscode-inputValidation-warningForeground, #cca700);
      border-radius: 3px;
      padding: 1px 5px;
      margin-bottom: 4px;
      font-size: 10px;
    }

    .exact-badge {
      display: inline-block;
      background: rgba(34,197,94,0.15);
      color: #4ade80;
      border-radius: 3px;
      padding: 1px 5px;
      margin-bottom: 4px;
      font-size: 10px;
    }

    .connect-link, .disconnect-link {
      color: var(--vscode-textLink-foreground);
      text-decoration: none;
      cursor: pointer;
    }

    .connect-link:hover, .disconnect-link:hover {
      text-decoration: underline;
    }

    .updated-at {
      color: var(--vscode-descriptionForeground);
    }
  </style>
</head>
<body>
  ${body}
</body>
</html>`;
  }
}
