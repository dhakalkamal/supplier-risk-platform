import { useState } from "react";
import { AlertCircle, Info } from "lucide-react";

interface DataFreshnessBarProps {
  scoredAt: string | undefined;
}

export function DataFreshnessBar({ scoredAt }: DataFreshnessBarProps) {
  // Captured at mount — stable for the component lifetime, no continuous re-renders needed
  const [now] = useState(() => Date.now());

  if (!scoredAt) return null;

  const hoursAgo = (now - new Date(scoredAt).getTime()) / (1000 * 60 * 60);

  if (hoursAgo < 6) return null;

  if (hoursAgo < 12) {
    return (
      <div className="mx-6 mt-4 flex items-center gap-2 rounded-lg border border-[--color-border] bg-[--color-bg-elevated] px-4 py-3 text-sm text-[--color-text-secondary]">
        <Info className="h-4 w-4 shrink-0" />
        Score updated {Math.round(hoursAgo)}h ago
      </div>
    );
  }

  if (hoursAgo < 24) {
    return (
      <div className="mx-6 mt-4 flex items-center gap-2 rounded-lg border border-amber-800 bg-amber-950/50 px-4 py-3 text-sm text-amber-300">
        <AlertCircle className="h-4 w-4 shrink-0" />
        Score updated {Math.round(hoursAgo)}h ago — may not reflect latest data
      </div>
    );
  }

  return (
    <div className="mx-6 mt-4 flex items-center gap-2 rounded-lg border border-red-800 bg-red-950/50 px-4 py-3 text-sm text-red-300">
      <AlertCircle className="h-4 w-4 shrink-0" />
      Score is over 24 hours old — data may be significantly outdated
    </div>
  );
}
