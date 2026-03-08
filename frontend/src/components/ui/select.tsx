import * as React from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export type SelectProps = React.SelectHTMLAttributes<HTMLSelectElement>;

const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, children, ...props }, ref) => (
    <div className="relative">
      <select
        className={cn(
          "flex h-9 w-full appearance-none rounded-md border border-[--color-border] bg-[--color-bg-input]",
          "px-3 pr-8 py-1 text-sm text-[--color-text-primary]",
          "focus:outline-none focus:border-[--color-border-focus]",
          "disabled:cursor-not-allowed disabled:opacity-50",
          "transition-colors cursor-pointer",
          className,
        )}
        ref={ref}
        {...props}
      >
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-[--color-text-muted]" />
    </div>
  ),
);
Select.displayName = "Select";

export { Select };
