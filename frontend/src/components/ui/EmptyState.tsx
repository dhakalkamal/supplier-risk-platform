import type { LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";

interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description: string;
  action?: { label: string; onClick: () => void };
  secondaryAction?: { label: string; onClick: () => void };
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  secondaryAction,
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-4 text-center">
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-[--color-bg-elevated]">
        <Icon className="h-7 w-7 text-[--color-text-muted]" />
      </div>
      <h3 className="mb-2 text-base font-semibold text-[--color-text-primary]">
        {title}
      </h3>
      <p className="mb-6 max-w-sm text-sm text-[--color-text-secondary]">
        {description}
      </p>
      {(action ?? secondaryAction) && (
        <div className="flex items-center gap-3">
          {action && (
            <Button onClick={action.onClick}>{action.label}</Button>
          )}
          {secondaryAction && (
            <Button variant="outline" onClick={secondaryAction.onClick}>
              {secondaryAction.label}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
