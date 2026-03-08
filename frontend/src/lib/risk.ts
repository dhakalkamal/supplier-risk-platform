export const RISK_CONFIG = {
  high: {
    label: "High",
    badgeClass: "bg-red-950 text-red-400 border border-red-800",
    dotClass: "bg-red-500",
    textClass: "text-red-400",
    scoreRange: "≥ 70",
  },
  medium: {
    label: "Medium",
    badgeClass: "bg-amber-950 text-amber-400 border border-amber-800",
    dotClass: "bg-amber-500",
    textClass: "text-amber-400",
    scoreRange: "40–69",
  },
  low: {
    label: "Low",
    badgeClass: "bg-green-950 text-green-400 border border-green-800",
    dotClass: "bg-green-500",
    textClass: "text-green-400",
    scoreRange: "< 40",
  },
  insufficient_data: {
    label: "Monitoring",
    badgeClass: "bg-slate-800 text-slate-400 border border-slate-700",
    dotClass: "bg-slate-500",
    textClass: "text-slate-400",
    scoreRange: "—",
  },
} as const;

export type RiskLevel = keyof typeof RISK_CONFIG;

export function getRiskLevel(score: number | null): RiskLevel {
  if (score === null) return "insufficient_data";
  if (score >= 70) return "high";
  if (score >= 40) return "medium";
  return "low";
}
