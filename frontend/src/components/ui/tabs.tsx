import { cn } from "@/lib/utils";

interface Tab<T extends string> {
  value: T;
  label: string;
  count?: number;
}

interface TabsProps<T extends string> {
  tabs: Tab<T>[];
  active: T;
  onChange: (value: T) => void;
  className?: string;
}

export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
  className,
}: TabsProps<T>) {
  return (
    <div
      className={cn(
        "flex gap-1 border-b border-[--color-border]",
        className,
      )}
    >
      {tabs.map((tab) => (
        <button
          key={tab.value}
          onClick={() => onChange(tab.value)}
          className={cn(
            "flex items-center gap-1.5 px-3 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors",
            active === tab.value
              ? "border-[--color-brand] text-[--color-text-primary]"
              : "border-transparent text-[--color-text-secondary] hover:text-[--color-text-primary]",
          )}
        >
          {tab.label}
          {tab.count !== undefined && (
            <span
              className={cn(
                "rounded-full px-1.5 py-0.5 text-xs font-semibold",
                active === tab.value
                  ? "bg-[--color-brand] text-white"
                  : "bg-[--color-bg-elevated] text-[--color-text-muted]",
              )}
            >
              {tab.count}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}
