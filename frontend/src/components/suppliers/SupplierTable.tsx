import { useState, useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Search, X, SlidersHorizontal, AlertCircle, RefreshCw } from "lucide-react";
import { Building, ShieldCheck } from "lucide-react";
import { usePortfolioSuppliers } from "@/hooks/usePortfolio";
import { useDebounce } from "@/hooks/useDebounce";
import { SupplierRow } from "./SupplierRow";
import { SkeletonRow } from "@/components/ui/SkeletonRow";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { SupplierSummary } from "@/types/api";
import type { RiskLevel } from "@/lib/risk";

const RISK_LEVELS: RiskLevel[] = ["high", "medium", "low"];
const RISK_LABELS: Record<string, string> = { high: "High", medium: "Medium", low: "Low" };

function FilterChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-[--color-bg-elevated] border border-[--color-border] px-2.5 py-0.5 text-xs text-[--color-text-secondary]">
      {label}
      <button onClick={onRemove} className="ml-1 hover:text-[--color-text-primary]">
        <X className="h-3 w-3" />
      </button>
    </span>
  );
}

export function SupplierTable() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [showFilters, setShowFilters] = useState(false);

  // URL-persisted filter state
  const riskFilter = (searchParams.get("risk")?.split(",").filter(Boolean) ?? []) as RiskLevel[];
  const countryFilter = searchParams.get("country")?.split(",").filter(Boolean) ?? [];

  // Local search input state (debounced → URL)
  const [searchInput, setSearchInput] = useState(searchParams.get("q") ?? "");
  const debouncedSearch = useDebounce(searchInput, 300);

  useEffect(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (debouncedSearch) next.set("q", debouncedSearch);
        else next.delete("q");
        return next;
      },
      { replace: true },
    );
  }, [debouncedSearch, setSearchParams]);

  const { data, isLoading, isError, refetch } = usePortfolioSuppliers({
    per_page: 200,
    sort_by: "risk_score",
    sort_order: "desc",
  });

  const allSuppliers = data?.data ?? [];

  // Unique countries from data for filter options
  const uniqueCountries = [...new Set(allSuppliers.map((s) => s.country))].sort();

  // Client-side filtering
  const filtered = allSuppliers.filter((s) => {
    const q = debouncedSearch.toLowerCase();
    if (q) {
      const name = (s.custom_name ?? s.canonical_name).toLowerCase();
      const canonical = s.canonical_name.toLowerCase();
      if (!name.includes(q) && !canonical.includes(q)) return false;
    }
    if (riskFilter.length > 0 && !riskFilter.includes(s.risk_level as RiskLevel)) return false;
    if (countryFilter.length > 0 && !countryFilter.includes(s.country)) return false;
    return true;
  });

  function toggleRisk(level: RiskLevel) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      const current = (prev.get("risk")?.split(",").filter(Boolean) ?? []) as RiskLevel[];
      const updated = current.includes(level)
        ? current.filter((r) => r !== level)
        : [...current, level];
      if (updated.length > 0) next.set("risk", updated.join(","));
      else next.delete("risk");
      return next;
    }, { replace: true });
  }

  function toggleCountry(country: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      const current = prev.get("country")?.split(",").filter(Boolean) ?? [];
      const updated = current.includes(country)
        ? current.filter((c) => c !== country)
        : [...current, country];
      if (updated.length > 0) next.set("country", updated.join(","));
      else next.delete("country");
      return next;
    }, { replace: true });
  }

  function clearAllFilters() {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("risk");
      next.delete("country");
      next.delete("q");
      return next;
    }, { replace: true });
    setSearchInput("");
  }

  const activeFilterCount = riskFilter.length + countryFilter.length;

  const isEmptyPortfolio = !isLoading && allSuppliers.length === 0;
  const isEmptySearch = !isLoading && allSuppliers.length > 0 && filtered.length === 0;

  return (
    <div>
      {/* Search + filter bar */}
      <div className="flex items-center gap-3 px-6 py-4">
        <div className="relative flex-1 max-w-sm">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[--color-text-muted]" />
          <Input
            placeholder="Search suppliers…"
            className="pl-9"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
        </div>
        <button
          onClick={() => setShowFilters((v) => !v)}
          className={cn(
            "inline-flex items-center gap-2 rounded-md border px-3 h-9 text-sm transition-colors",
            showFilters || activeFilterCount > 0
              ? "border-[--color-brand] bg-[--color-brand]/10 text-[--color-brand]"
              : "border-[--color-border] text-[--color-text-secondary] hover:text-[--color-text-primary] hover:border-[--color-text-muted]",
          )}
        >
          <SlidersHorizontal className="h-4 w-4" />
          Filters
          {activeFilterCount > 0 && (
            <span className="ml-0.5 rounded-full bg-[--color-brand] text-white text-xs px-1.5 py-0.5">
              {activeFilterCount}
            </span>
          )}
        </button>
      </div>

      {/* Filter panel */}
      {showFilters && (
        <div className="px-6 pb-4 border-b border-[--color-border] space-y-4">
          <div className="flex flex-wrap gap-6">
            {/* Risk level checkboxes */}
            <div>
              <p className="mb-2 text-xs font-medium text-[--color-text-muted] uppercase tracking-wider">
                Risk Level
              </p>
              <div className="flex gap-3">
                {RISK_LEVELS.map((level) => (
                  <label key={level} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={riskFilter.includes(level)}
                      onChange={() => toggleRisk(level)}
                      className="rounded border-[--color-border] bg-[--color-bg-input] accent-[--color-brand]"
                    />
                    <span className="text-sm text-[--color-text-secondary]">
                      {RISK_LABELS[level]}
                    </span>
                  </label>
                ))}
              </div>
            </div>
            {/* Country filter */}
            {uniqueCountries.length > 0 && (
              <div>
                <p className="mb-2 text-xs font-medium text-[--color-text-muted] uppercase tracking-wider">
                  Country
                </p>
                <div className="flex flex-wrap gap-2 max-h-20 overflow-y-auto">
                  {uniqueCountries.map((country) => (
                    <button
                      key={country}
                      onClick={() => toggleCountry(country)}
                      className={cn(
                        "rounded-full border px-2.5 py-0.5 text-xs transition-colors",
                        countryFilter.includes(country)
                          ? "border-[--color-brand] bg-[--color-brand]/10 text-[--color-brand]"
                          : "border-[--color-border] text-[--color-text-secondary] hover:border-[--color-text-muted]",
                      )}
                    >
                      {country}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Active filter chips */}
      {(riskFilter.length > 0 || countryFilter.length > 0) && (
        <div className="flex flex-wrap items-center gap-2 px-6 py-2 bg-[--color-bg-surface]/50">
          <span className="text-xs text-[--color-text-muted]">Active:</span>
          {riskFilter.map((r) => (
            <FilterChip key={r} label={`Risk: ${RISK_LABELS[r]}`} onRemove={() => toggleRisk(r)} />
          ))}
          {countryFilter.map((c) => (
            <FilterChip key={c} label={`Country: ${c}`} onRemove={() => toggleCountry(c)} />
          ))}
          <button
            onClick={clearAllFilters}
            className="text-xs text-[--color-text-muted] hover:text-[--color-text-secondary] underline"
          >
            Clear all
          </button>
        </div>
      )}

      {/* Error state */}
      {isError && (
        <div className="flex flex-col items-center justify-center py-16 gap-3 px-6 text-center">
          <AlertCircle className="h-10 w-10 text-red-400" />
          <p className="font-semibold text-[--color-text-primary]">Failed to load suppliers</p>
          <p className="text-sm text-[--color-text-secondary]">
            There was a problem connecting to the server.
          </p>
          <button
            onClick={() => void refetch()}
            className="inline-flex items-center gap-2 rounded-md border border-[--color-border] px-4 py-2 text-sm text-[--color-text-secondary] hover:text-[--color-text-primary] hover:bg-[--color-bg-elevated] transition-colors"
          >
            <RefreshCw className="h-4 w-4" /> Try again
          </button>
        </div>
      )}

      {/* Table */}
      {!isError && (
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-[--color-border]">
              {["Name", "Country", "Industry", "Risk", "Score", "7d Trend", "Alerts", "Last Updated"].map(
                (col, i) => (
                  <th
                    key={col}
                    className={cn(
                      "px-4 py-3 text-left text-xs font-medium text-[--color-text-muted] uppercase tracking-wider",
                      i === 2 && "hidden md:table-cell",
                      i === 5 && "hidden sm:table-cell",
                      i === 7 && "hidden lg:table-cell",
                    )}
                  >
                    {col}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {isLoading &&
              Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} columns={8} />)}

            {!isLoading &&
              filtered.map((supplier: SupplierSummary) => (
                <SupplierRow
                  key={supplier.portfolio_supplier_id}
                  supplier={supplier}
                  onClick={() => navigate(`/suppliers/${supplier.supplier_id}`)}
                />
              ))}
          </tbody>
        </table>

        {isEmptyPortfolio && (
          <EmptyState
            icon={Building}
            title="Your portfolio is empty"
            description="Add your first supplier to start monitoring risk signals."
            action={{ label: "+ Add Supplier", onClick: () => navigate("/suppliers/add") }}
            secondaryAction={{ label: "Upload CSV", onClick: () => navigate("/suppliers/add") }}
          />
        )}

        {isEmptySearch && (
          <EmptyState
            icon={Search}
            title={`No suppliers match "${debouncedSearch || "your filters"}"`}
            description="Try a different name or clear your filters."
            action={{ label: "Clear filters", onClick: clearAllFilters }}
          />
        )}

        {!isLoading && filtered.length === 0 && !isEmptyPortfolio && !isEmptySearch && (
          <EmptyState
            icon={ShieldCheck}
            title="All clear"
            description="No suppliers match your current filters."
            action={{ label: "Clear filters", onClick: clearAllFilters }}
          />
        )}
      </div>
      )}
    </div>
  );
}
