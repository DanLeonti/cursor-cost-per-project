import * as https from "https";

export interface UsageEvent {
  timestamp: number;      // ms
  model: string;
  inputTokens: number;
  outputTokens: number;
  chargedCents: number;
  isHeadless: boolean;
}

interface ApiResponse {
  totalUsageEventsCount: number;
  usageEventsDisplay?: ApiEvent[];
  usageEvents?: ApiEvent[];
}

interface ApiEvent {
  timestamp: string;
  model: string;
  isTokenBasedCall?: boolean;
  tokenUsage?: {
    inputTokens?: number;
    outputTokens?: number;
    totalCents?: number;
  };
  chargedCents?: number;
  isHeadless?: boolean;
}

function httpsPost(url: string, body: string, cookie: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const req = https.request(
      {
        hostname: parsed.hostname,
        path: parsed.pathname,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(body),
          "Cookie": cookie,
          // cursor.com rejects state-changing POSTs whose Origin doesn't match
          // the site ("Invalid origin for state-changing request" -> 403).
          "Origin": "https://cursor.com",
          "Referer": "https://cursor.com/dashboard",
          "User-Agent": "CursorCostTracker/0.1",
        },
      },
      (res) => {
        let data = "";
        res.on("data", (chunk: Buffer) => { data += chunk.toString(); });
        res.on("end", () => {
          if (res.statusCode && res.statusCode >= 400) {
            reject(new Error(`API error ${res.statusCode}: ${data.slice(0, 200)}`));
          } else {
            resolve(data);
          }
        });
      }
    );
    req.on("error", reject);
    req.write(body);
    req.end();
  });
}

export async function fetchMonthUsage(
  token: string,
  year: number,
  month: number
): Promise<UsageEvent[]> {
  // The endpoint expects epoch-millisecond strings, not YYYY-MM-DD.
  const startMs = Date.UTC(year, month - 1, 1);
  const endMs   = month === 12 ? Date.UTC(year + 1, 0, 1) : Date.UTC(year, month, 1);

  const cookie = `WorkosCursorSessionToken=${token}`;
  const PAGE_SIZE = 200;
  const allEvents: UsageEvent[] = [];
  let page = 1; // Cursor's API is 1-indexed ("page must be at least 1")
  let totalKnown = Infinity;

  while (allEvents.length < totalKnown) {
    const body = JSON.stringify({
      startDate: String(startMs),
      endDate: String(endMs),
      pageSize: PAGE_SIZE,
      page,
    });

    const raw = await httpsPost(
      "https://cursor.com/api/dashboard/get-filtered-usage-events",
      body,
      cookie
    );

    const parsed = JSON.parse(raw) as ApiResponse;
    totalKnown = parsed.totalUsageEventsCount ?? 0;

    const pageEvents = parsed.usageEventsDisplay ?? parsed.usageEvents ?? [];
    for (const ev of pageEvents) {
      allEvents.push({
        timestamp: parseInt(ev.timestamp, 10),
        model: ev.model ?? "unknown",
        inputTokens: ev.tokenUsage?.inputTokens ?? 0,
        outputTokens: ev.tokenUsage?.outputTokens ?? 0,
        chargedCents: ev.chargedCents ?? ev.tokenUsage?.totalCents ?? 0,
        isHeadless: ev.isHeadless ?? false,
      });
    }

    if (pageEvents.length < PAGE_SIZE) break;
    page++;
  }

  return allEvents;
}

export function buildExactProjectCosts(
  events: UsageEvent[],
  composers: Array<{ composerId: string; project: string; createdAt: number; lastUpdatedAt: number }>
): Map<string, { costUsd: number; inputTokens: number; outputTokens: number; models: Record<string, number> }> {
  const projectMap = new Map<string, { costUsd: number; inputTokens: number; outputTokens: number; models: Record<string, number> }>();

  for (const ev of events) {
    const project = findProject(ev.timestamp, composers);

    if (!projectMap.has(project)) {
      projectMap.set(project, { costUsd: 0, inputTokens: 0, outputTokens: 0, models: {} });
    }

    const stats = projectMap.get(project)!;
    stats.costUsd += ev.chargedCents / 100;
    stats.inputTokens += ev.inputTokens;
    stats.outputTokens += ev.outputTokens;
    stats.models[ev.model] = (stats.models[ev.model] ?? 0) + 1;
  }

  return projectMap;
}

function findProject(
  eventTsMs: number,
  composers: Array<{ composerId: string; project: string; createdAt: number; lastUpdatedAt: number }>
): string {
  // Find the conversation whose active window contains this timestamp.
  // A conversation is "active" from its createdAt to lastUpdatedAt.
  // When multiple conversations overlap, pick the one with the smallest window (most specific).
  const candidates = composers.filter(
    (c) => c.createdAt <= eventTsMs && c.lastUpdatedAt >= eventTsMs - 5_000
  );

  if (candidates.length === 0) return "unattributed";

  const best = candidates.reduce((a, b) => {
    const aWindow = a.lastUpdatedAt - a.createdAt;
    const bWindow = b.lastUpdatedAt - b.createdAt;
    return aWindow <= bWindow ? a : b;
  });

  const parts = best.project.trimEnd().split("/");
  return parts[parts.length - 1] || best.project || "unattributed";
}
