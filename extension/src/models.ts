export interface ModelPricing {
  inputPerM: number;
  outputPerM: number;
  label: string;
}

export const MODEL_PRICING: Record<string, ModelPricing> = {
  "claude-opus-4-8":   { inputPerM: 15.25, outputPerM: 75.25, label: "Claude Opus 4" },
  "claude-opus-4-7":   { inputPerM: 15.25, outputPerM: 75.25, label: "Claude Opus 4" },
  "claude-sonnet-4-6": { inputPerM:  3.25, outputPerM: 15.25, label: "Claude Sonnet 4.6" },
  "claude-sonnet-4-5": { inputPerM:  3.25, outputPerM: 15.25, label: "Claude Sonnet 4.5" },
  "gpt-5.5":           { inputPerM: 10.25, outputPerM: 40.25, label: "GPT-5.5" },
  "gpt-5.3-codex":     { inputPerM:  5.25, outputPerM: 20.25, label: "GPT-5.3 Codex" },
  "composer-2.5":      { inputPerM:  0.75, outputPerM:  2.75, label: "Composer 2.5" },
  "composer-2":        { inputPerM:  0.75, outputPerM:  2.75, label: "Composer 2" },
  "default":           { inputPerM:  3.25, outputPerM: 15.25, label: "Unknown" },
};

export function getPricing(model: string): ModelPricing {
  return MODEL_PRICING[model] ?? MODEL_PRICING["default"];
}

export function getModelLabel(model: string): string {
  return getPricing(model).label;
}

export interface ComposerHeader {
  composerId: string;
  project: string;
  name: string;
  createdAt: number;
  lastUpdatedAt: number;
  contextUsagePercent: number;
}

export interface CodeHashEntry {
  model: string;
  count: number;
}

export interface ProjectStats {
  name: string;
  path: string;
  conversations: number;
  hashCount: number;
  models: Record<string, number>;
  estimatedInputTokens: number;
  estimatedOutputTokens: number;
  estimatedCostUsd: number;
}

export interface DashboardData {
  monthLabel: string;
  year: number;
  month: number;
  projects: ProjectStats[];
  totalCostUsd: number;
  totalConversations: number;
  isEstimate: boolean;
  generatedAt: string;
}
