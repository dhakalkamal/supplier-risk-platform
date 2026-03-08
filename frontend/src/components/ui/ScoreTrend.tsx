import { TrendingUp, TrendingDown, Minus } from "lucide-react";
import { cn } from "@/lib/utils";
import { getScoreTrend } from "@/lib/utils";

interface ScoreTrendProps {
  delta: number;
  className?: string;
  showIcon?: boolean;
}

export function ScoreTrend({ delta, className, showIcon = true }: ScoreTrendProps) {
  const trend = getScoreTrend(delta);

  if (trend === "up") {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1 text-sm font-medium text-red-400",
          className,
        )}
      >
        {showIcon && <TrendingUp className="h-3.5 w-3.5" />}
        +{delta}
      </span>
    );
  }

  if (trend === "down") {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1 text-sm font-medium text-green-400",
          className,
        )}
      >
        {showIcon && <TrendingDown className="h-3.5 w-3.5" />}
        {delta}
      </span>
    );
  }

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-sm font-medium text-[--color-text-muted]",
        className,
      )}
    >
      {showIcon && <Minus className="h-3.5 w-3.5" />}
      {delta === 0 ? "0" : delta > 0 ? `+${delta}` : delta}
    </span>
  );
}
