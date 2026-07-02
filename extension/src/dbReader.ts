import * as path from "path";
import * as os from "os";
import * as fs from "fs";
import { execFileSync } from "child_process";
import { ComposerHeader, CodeHashEntry } from "./models";

export const OUTPUT_TOKENS_PER_HASH = 60;
export const INPUT_OUTPUT_RATIO = 4.0;

const SQLITE3 = "/usr/bin/sqlite3";

function globalDbPath(): string {
  return path.join(
    os.homedir(),
    "Library",
    "Application Support",
    "Cursor",
    "User",
    "globalStorage",
    "state.vscdb"
  );
}

function trackingDbPath(): string {
  return path.join(os.homedir(), ".cursor", "ai-tracking", "ai-code-tracking.db");
}

export function normalizeProject(fsPath: string): string {
  if (!fsPath || fsPath === "no-workspace") return "no-workspace";
  return path.basename(fsPath.trimEnd()) || fsPath;
}

export function monthBounds(year: number, month: number): [number, number] {
  const start = new Date(year, month - 1, 1).getTime();
  const end = month === 12
    ? new Date(year + 1, 0, 1).getTime()
    : new Date(year, month, 1).getTime();
  return [start, end];
}

function querySqlite<T = Record<string, unknown>>(dbPath: string, sql: string): T[] {
  const output = execFileSync(SQLITE3, ["-json", dbPath, sql], {
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  }).trim();
  if (!output) return [];
  return JSON.parse(output) as T[];
}

export function readComposers(year: number, month: number): ComposerHeader[] {
  const dbPath = globalDbPath();
  if (!fs.existsSync(dbPath)) {
    throw new Error(`Cursor database not found at:\n${dbPath}`);
  }

  const rows = querySqlite<{ value: string }>(
    dbPath,
    "SELECT value FROM ItemTable WHERE key = 'composer.composerHeaders'"
  );
  if (!rows.length) return [];

  const data = JSON.parse(rows[0].value) as { allComposers?: unknown[] };
  const allComposers = (data.allComposers ?? []) as Record<string, unknown>[];

  const [monthStart, monthEnd] = monthBounds(year, month);

  return allComposers
    .filter((c) => {
      const updated = (c.lastUpdatedAt as number) ?? 0;
      return updated >= monthStart && updated < monthEnd;
    })
    .map((c) => {
      const repos = (c.trackedGitRepos as Array<{ repoPath?: string }>) ?? [];
      let project: string;
      if (repos.length > 0) {
        project = repos[0].repoPath ?? "";
      } else {
        const ws = c.workspaceIdentifier as Record<string, unknown> | undefined;
        const uri = ws?.uri as Record<string, unknown> | undefined;
        project = (uri?.fsPath as string) ?? "no-workspace";
      }
      return {
        composerId: c.composerId as string,
        project: project.trimEnd(),
        name: (c.name as string) ?? "",
        createdAt: (c.createdAt as number) ?? 0,
        lastUpdatedAt: (c.lastUpdatedAt as number) ?? 0,
        contextUsagePercent: (c.contextUsagePercent as number) ?? 0,
      };
    });
}

export function readCodeHashes(sinceMs: number): Map<string, CodeHashEntry[]> {
  const dbPath = trackingDbPath();
  if (!fs.existsSync(dbPath)) return new Map();

  const rows = querySqlite<{ conversationId: string; model: string; count: number }>(
    dbPath,
    `SELECT conversationId, model, COUNT(*) as count
     FROM ai_code_hashes
     WHERE timestamp > ${Math.floor(sinceMs / 1000)}
     GROUP BY conversationId, model`
  );

  const map = new Map<string, CodeHashEntry[]>();
  for (const row of rows) {
    if (!row.conversationId) continue;
    const entries = map.get(row.conversationId) ?? [];
    entries.push({ model: row.model || "default", count: Number(row.count) });
    map.set(row.conversationId, entries);
  }
  return map;
}
