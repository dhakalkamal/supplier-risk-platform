import * as React from "react";
import { cn } from "@/lib/utils";

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => (
    <input
      type={type}
      className={cn(
        "flex h-9 w-full rounded-md border border-[--color-border] bg-[--color-bg-input]",
        "px-3 py-1 text-sm text-[--color-text-primary] placeholder:text-[--color-text-muted]",
        "focus:outline-none focus:border-[--color-border-focus]",
        "disabled:cursor-not-allowed disabled:opacity-50",
        "transition-colors",
        className,
      )}
      ref={ref}
      {...props}
    />
  ),
);
Input.displayName = "Input";

export { Input };
