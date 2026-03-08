import { cn } from "@/lib/utils";
import { getRiskLevel, RISK_CONFIG, type RiskLevel } from "@/lib/risk";

interface RiskBadgeProps {
  level?: RiskLevel;
  score?: number | null;
  className?: string;
}

export function RiskBadge({ level, score, className }: RiskBadgeProps) {
  const resolvedLevel: RiskLevel =
    level ?? getRiskLevel(score !== undefined ? score : null);
  const config = RISK_CONFIG[resolvedLevel];

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium",
        config.badgeClass,
        className,
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", config.dotClass)} />
      {config.label}
    </span>
  );
}
