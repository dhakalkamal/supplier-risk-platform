import { cn } from "@/lib/utils";

interface SkeletonProps {
  className?: string;
}

function Skeleton({ className }: SkeletonProps) {
  return (
    <div
      className={cn("animate-pulse rounded bg-[--color-bg-elevated]", className)}
    />
  );
}

interface SkeletonRowProps {
  columns?: number;
}

export function SkeletonRow({ columns = 7 }: SkeletonRowProps) {
  const widths = ["w-40", "w-16", "w-20", "w-12", "w-16", "w-8", "w-24"];

  return (
    <tr className="border-b border-[--color-border]">
      {Array.from({ length: columns }).map((_, i) => (
        <td key={i} className="px-4 py-3">
          <Skeleton className={cn("h-4", widths[i % widths.length])} />
        </td>
      ))}
    </tr>
  );
}

export { Skeleton };
