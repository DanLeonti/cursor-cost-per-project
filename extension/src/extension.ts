import * as vscode from "vscode";
import { CostTrackWebviewProvider } from "./webviewProvider";
import { buildDashboard } from "./costCalculator";
import { TokenServer } from "./tokenServer";

const SECRET_KEY = "cursor-session-token";
let refreshTimer: ReturnType<typeof setTimeout> | undefined;

function currentMonthYear(): { year: number; month: number } {
  const now = new Date();
  return { year: now.getFullYear(), month: now.getMonth() + 1 };
}

function clampMonth(year: number, month: number): { year: number; month: number } {
  if (month < 1)  { month = 12; year -= 1; }
  if (month > 12) { month =  1; year += 1; }
  return { year, month };
}

export function activate(context: vscode.ExtensionContext): void {
  const provider = new CostTrackWebviewProvider(context.extensionUri);

  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(CostTrackWebviewProvider.viewType, provider, {
      webviewOptions: { retainContextWhenHidden: true },
    })
  );

  let { year, month } = currentMonthYear();

  async function getToken(): Promise<string | undefined> {
    return context.secrets.get(SECRET_KEY);
  }

  async function refresh(): Promise<void> {
    provider.setLoading();
    try {
      const token = await getToken();
      const data = await buildDashboard(year, month, token);
      provider.update(data);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      provider.setError(msg);
      vscode.window.showWarningMessage(`Cursor Cost Tracker: ${msg}`);
    }
  }

  function scheduleRefresh(): void {
    clearTimeout(refreshTimer);
    const intervalMs =
      (vscode.workspace.getConfiguration("costtrack").get<number>("refreshIntervalSeconds") ?? 60) * 1000;
    refreshTimer = setTimeout(() => {
      void refresh();
      scheduleRefresh();
    }, intervalMs);
  }

  // ── Token server: receives token from Chrome extension ─────────────────────
  const tokenServer = new TokenServer(async (token) => {
    await context.secrets.store(SECRET_KEY, token);
    provider.setConnected(true);
    void refresh();
    vscode.window.showInformationMessage(
      "Cursor Cost Tracker: Connected! Switching to exact billing data."
    );
  });
  tokenServer.start(context.secrets);
  context.subscriptions.push({ dispose: () => tokenServer.stop() });

  // ── Commands ───────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand("costtrack.refresh", () => {
      void refresh();
      scheduleRefresh();
    }),

    vscode.commands.registerCommand("costtrack.prevMonth", () => {
      ({ year, month } = clampMonth(year, month - 1));
      void refresh();
    }),

    vscode.commands.registerCommand("costtrack.nextMonth", () => {
      ({ year, month } = clampMonth(year, month + 1));
      void refresh();
    }),

    vscode.commands.registerCommand("costtrack.disconnect", async () => {
      await context.secrets.delete(SECRET_KEY);
      provider.setConnected(false);
      void refresh();
      vscode.window.showInformationMessage("Cursor Cost Tracker: Disconnected. Showing estimates.");
    })
  );

  // ── Initial load ───────────────────────────────────────────────────────────
  getToken().then((token) => {
    provider.setConnected(!!token);
    void refresh();
    scheduleRefresh();
  });
}

export function deactivate(): void {
  clearTimeout(refreshTimer);
}
