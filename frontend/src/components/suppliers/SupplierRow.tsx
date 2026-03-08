import { RiskBadge } from "@/components/ui/RiskBadge";
import { ScoreTrend } from "@/components/ui/ScoreTrend";
import { formatScore, getCountryFlag, formatTimeAgo } from "@/lib/utils";
import type { SupplierSummary } from "@/types/api";

interface SupplierRowProps {
  supplier: SupplierSummary;
  onClick: () => void;
}

export function SupplierRow({ supplier, onClick }: SupplierRowProps) {
  const displayName = supplier.custom_name ?? supplier.canonical_name;
  const flag = getCountryFlag(supplier.country);

  return (
    <tr
      className="border-b border-[--color-border] hover:bg-[--color-bg-elevated] cursor-pointer transition-colors"
      onClick={onClick}
    >
      {/* Name */}
      <td className="px-4 py-3">
        <span className="text-sm font-medium text-[--color-text-primary] line-clamp-1">
          {displayName}
        </span>
        {supplier.custom_name && (
          <span className="block text-xs text-[--color-text-muted] line-clamp-1">
            {supplier.canonical_name}
          </span>
        )}
      </td>

      {/* Country */}
      <td className="px-4 py-3">
        <span className="inline-flex items-center gap-1.5 text-sm text-[--color-text-secondary]">
          <span role="img" aria-label={supplier.country}>
            {flag}
          </span>
          {supplier.country}
        </span>
      </td>

      {/* Industry — hidden on mobile */}
      <td className="hidden md:table-cell px-4 py-3">
        <span className="text-sm text-[--color-text-secondary] line-clamp-1">
          {supplier.industry_name}
        </span>
      </td>

      {/* Risk */}
      <td className="px-4 py-3">
        <RiskBadge level={supplier.risk_level} />
      </td>

      {/* Score */}
      <td className="px-4 py-3">
        <span className="text-sm font-semibold text-[--color-text-primary]">
          {formatScore(supplier.risk_score)}
        </span>
      </td>

      {/* 7d Trend */}
      <td className="hidden sm:table-cell px-4 py-3">
        <ScoreTrend delta={supplier.score_7d_delta} />
      </td>

      {/* Unread Alerts */}
      <td className="px-4 py-3">
        {supplier.unread_alerts_count > 0 ? (
          <span className="inline-flex items-center justify-center min-w-5 h-5 rounded-full bg-red-950 border border-red-800 text-red-400 text-xs font-semibold px-1.5">
            {supplier.unread_alerts_count}
          </span>
        ) : (
          <span className="text-sm text-[--color-text-muted]">—</span>
        )}
      </td>

      {/* Last Updated — hidden on small screens */}
      <td className="hidden lg:table-cell px-4 py-3">
        <span className="text-xs text-[--color-text-muted]">
          {formatTimeAgo(supplier.last_score_updated_at)}
        </span>
      </td>
    </tr>
  );
}
