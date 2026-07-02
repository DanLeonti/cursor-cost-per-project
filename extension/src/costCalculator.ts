import { ProjectStats, DashboardData, getPricing, getModelLabel } from "./models";
import {
  readComposers,
  readCodeHashes,
  normalizeProject,
  monthBounds,
  OUTPUT_TOKENS_PER_HASH,
  INPUT_OUTPUT_RATIO,
} from "./dbReader";
import { fetchMonthUsage, buildExactProjectCosts, UsageEvent } from "./cursorApiClient";

export function getTopModelLabel(models: Record<string, number>): string {
  const keys = Object.keys(models);
  if (keys.length === 0) return "—";
  const top = keys.reduce((a, b) => (models[a] > models[b] ? a : b));
  return getModelLabel(top);
}

function estimateCost(model: string, outputTokens: number): number {
  const pricing = getPricing(model);
  const inputTokens = outputTokens * INPUT_OUTPUT_RATIO;
  return (inputTokens * pricing.inputPerM + outputTokens * pricing.outputPerM) / 1_000_000;
}

// ── Build dashboard from EXACT API data ───────────────────────────────────────

async function buildExactDashboard(
  token: string,
  year: number,
  month: number,
  composers: Awaited<ReturnType<typeof readComposers>>
): Promise<DashboardData> {
  const events: UsageEvent[] = await fetchMonthUsage(token, year, month);
  const composersMapped = composers.map((c) => ({
    composerId: c.composerId,
    project: normalizeProject(c.project),
    createdAt: c.createdAt,
    lastUpdatedAt: c.lastUpdatedAt,
  }));

  const exactCosts = buildExactProjectCosts(events, composersMapped);

  // Merge: get conversation counts from composers, costs from API
  const projectConvCounts = new Map<string, number>();
  for (const c of composers) {
    const name = normalizeProject(c.project);
    projectConvCounts.set(name, (projectConvCounts.get(name) ?? 0) + 1);
  }

  const projects: ProjectStats[] = [];
  for (const [name, apiStats] of exactCosts.entries()) {
    projects.push({
      name,
      path: name,
      conversations: projectConvCounts.get(name) ?? 0,
      hashCount: 0,
      models: apiStats.models,
      estimatedInputTokens: apiStats.inputTokens,
      estimatedOutputTokens: apiStats.outputTokens,
      estimatedCostUsd: apiStats.costUsd,
    });
  }

  // Add projects with conversations but no API events (e.g. included-usage requests)
  for (const [name, convCount] of projectConvCounts.entries()) {
    if (!exactCosts.has(name)) {
      projects.push({
        name,
        path: name,
        conversations: convCount,
        hashCount: 0,
        models: {},
        estimatedInputTokens: 0,
        estimatedOutputTokens: 0,
        estimatedCostUsd: 0,
      });
    }
  }

  projects.sort((a, b) => b.estimatedCostUsd - a.estimatedCostUsd);

  const monthLabel = new Date(year, month - 1, 1).toLocaleString("default", {
    month: "long",
    year: "numeric",
  });

  return {
    monthLabel,
    year,
    month,
    projects,
    totalCostUsd: projects.reduce((s, p) => s + p.estimatedCostUsd, 0),
    totalConversations: composers.length,
    isEstimate: false,
    generatedAt: new Date().toISOString(),
  };
}

// ── Build dashboard from local ESTIMATED data (no API token) ─────────────────

async function buildEstimatedDashboard(
  year: number,
  month: number,
  composers: Awaited<ReturnType<typeof readComposers>>
): Promise<DashboardData> {
  const [monthStart] = monthBounds(year, month);
  const hashesByConv = readCodeHashes(monthStart);

  const projectMap = new Map<string, ProjectStats>();

  for (const c of composers) {
    const projectPath = c.project;
    const name = normalizeProject(projectPath);
    if (!projectMap.has(projectPath)) {
      projectMap.set(projectPath, {
        name,
        path: projectPath,
        conversations: 0,
        hashCount: 0,
        models: {},
        estimatedInputTokens: 0,
        estimatedOutputTokens: 0,
        estimatedCostUsd: 0,
      });
    }

    const stats = projectMap.get(projectPath)!;
    stats.conversations += 1;

    for (const entry of hashesByConv.get(c.composerId) ?? []) {
      const model = entry.model || "default";
      const outputTokens = entry.count * OUTPUT_TOKENS_PER_HASH;
      stats.hashCount += entry.count;
      stats.models[model] = (stats.models[model] ?? 0) + entry.count;
      stats.estimatedOutputTokens += outputTokens;
      stats.estimatedInputTokens += Math.floor(outputTokens * INPUT_OUTPUT_RATIO);
      stats.estimatedCostUsd += estimateCost(model, outputTokens);
    }
  }

  const projects = Array.from(projectMap.values()).sort(
    (a, b) => b.estimatedCostUsd - a.estimatedCostUsd
  );

  const monthLabel = new Date(year, month - 1, 1).toLocaleString("default", {
    month: "long",
    year: "numeric",
  });

  return {
    monthLabel,
    year,
    month,
    projects,
    totalCostUsd: projects.reduce((s, p) => s + p.estimatedCostUsd, 0),
    totalConversations: composers.length,
    isEstimate: true,
    generatedAt: new Date().toISOString(),
  };
}

// ── Public entry point ────────────────────────────────────────────────────────

export async function buildDashboard(
  year: number,
  month: number,
  apiToken?: string
): Promise<DashboardData> {
  const composers = readComposers(year, month);

  if (apiToken) {
    try {
      return await buildExactDashboard(apiToken, year, month, composers);
    } catch (err) {
      console.error("[Cursor Cost Tracker] API fetch failed, falling back to estimates:", err);
    }
  }

  return buildEstimatedDashboard(year, month, composers);
}
