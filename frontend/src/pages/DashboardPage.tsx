import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth0 } from "@auth0/auth0-react";
import {
  TrendingUp,
  TrendingDown,
  Minus,
  AlertTriangle,
  Bell,
  Users,
  ChevronRight,
} from "lucide-react";
import { usePortfolioSummary } from "@/hooks/usePortfolio";
import { useAlerts } from "@/hooks/useAlerts";
import { SupplierTable } from "@/components/suppliers/SupplierTable";
import { Skeleton } from "@/components/ui/SkeletonRow";
import { cn, getGreeting, formatTimeAgo } from "@/lib/utils";
import { getRiskLevel, RISK_CONFIG } from "@/lib/risk";
import type { Alert } from "@/types/api";

// ── Stat Card ──────────────────────────────────────────────────────────────────

interface StatCardProps {
  label: string;
  value: string | number;
  icon: React.ReactNode;
  accent?: string;
  onClick?: () => void;
  animationDelay: number;
}

function StatCard({ label, value, icon, accent, onClick, animationDelay }: StatCardProps) {
  return (
    <div
      className={cn(
        "rounded-xl border border-[--color-border] bg-[--color-bg-surface] p-5",
        "flex items-center gap-4",
        onClick && "cursor-pointer hover:border-[--color-brand] transition-colors",
      )}
      style={{ animation: "fadeInUp 0.4s ease forwards", animationDelay: `${animationDelay}ms`, opacity: 0 }}
      onClick={onClick}
    >
      <div className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-lg", accent ?? "bg-[--color-bg-elevated]")}>
        {icon}
      </div>
      <div>
        <p className="text-2xl font-bold text-[--color-text-primary]">{value}</p>
        <p className="text-sm text-[--color-text-secondary]">{label}</p>
      </div>
    </div>
  );
}

function StatCardSkeleton({ animationDelay }: { animationDelay: number }) {
  return (
    <div
      className="rounded-xl border border-[--color-border] bg-[--color-bg-surface] p-5 flex items-center gap-4"
      style={{ animation: "fadeInUp 0.4s ease forwards", animationDelay: `${animationDelay}ms`, opacity: 0 }}
    >
      <Skeleton className="h-10 w-10 rounded-lg shrink-0" />
      <div className="space-y-2">
        <Skeleton className="h-6 w-12" />
        <Skeleton className="h-4 w-24" />
      </div>
    </div>
  );
}

// ── Alert Strip Card ───────────────────────────────────────────────────────────

function AlertCard({ alert }: { alert: Alert }) {
  const navigate = useNavigate();
  const scoreProxy =
    alert.severity === "critical" || alert.severity === "high" ? 80
    : alert.severity === "medium" ? 55
    : 25;
  const level = getRiskLevel(scoreProxy);
  const config = RISK_CONFIG[level];

  return (
    <button
      onClick={() => navigate("/alerts")}
      className={cn(
        "flex-shrink-0 w-64 rounded-lg border bg-[--color-bg-surface] p-3 text-left",
        "hover:bg-[--color-bg-elevated] transition-colors",
        level === "high" ? "border-red-900"
          : level === "medium" ? "border-amber-900"
          : "border-[--color-border]",
      )}
    >
      <div className="flex items-start gap-2">
        <span className={cn("mt-1 h-2 w-2 rounded-full shrink-0", config.dotClass)} />
        <div className="min-w-0">
          <p className="text-xs font-semibold text-[--color-text-primary] truncate">{alert.supplier_name}</p>
          <p className="text-xs text-[--color-text-secondary] line-clamp-2 mt-0.5">{alert.title}</p>
          <p className="text-xs text-[--color-text-muted] mt-1">{formatTimeAgo(alert.fired_at)}</p>
        </div>
      </div>
    </button>
  );
}

function TrendIcon({ trend }: { trend: "improving" | "worsening" | "stable" }) {
  if (trend === "improving") return <TrendingDown className="h-5 w-5 text-green-400" />;
  if (trend === "worsening") return <TrendingUp className="h-5 w-5 text-red-400" />;
  return <Minus className="h-5 w-5 text-[--color-text-muted]" />;
}

// ── Dashboard Page ─────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const navigate = useNavigate();
  const { user } = useAuth0();
  const firstName = user?.name?.split(" ")[0] ?? "there";

  const { data: summary, isLoading: summaryLoading } = usePortfolioSummary();
  const { data: alertsData } = useAlerts({ status: "new", per_page: 3 });
  const unreadAlerts = alertsData?.data ?? [];

  const trend = summary?.score_trend_7d ?? "stable";
  const trendLabel = trend === "improving" ? "Improving" : trend === "worsening" ? "Worsening" : "Stable";
  const trendAccent =
    trend === "improving" ? "bg-green-950"
    : trend === "worsening" ? "bg-red-950"
    : "bg-[--color-bg-elevated]";

  useEffect(() => {
    document.title = "Dashboard — Supplier Risk Platform";
  }, []);

  return (
    <div className="pb-12">
      {/* Page header */}
      <div className="flex items-center justify-between px-6 py-6 border-b border-[--color-border]">
        <div>
          <h1
            className="text-2xl text-[--color-text-primary]"
            style={{ fontFamily: "'DM Serif Display', serif" }}
          >
            {getGreeting(firstName)}
          </h1>
          <p className="mt-0.5 text-sm text-[--color-text-secondary]">
            {new Date().toLocaleDateString("en-US", {
              weekday: "long",
              month: "long",
              day: "numeric",
            })}
          </p>
        </div>
        <button
          onClick={() => navigate("/suppliers/add")}
          className="inline-flex items-center gap-2 rounded-md bg-[--color-brand] px-4 py-2 text-sm font-medium text-white hover:bg-[--color-brand-hover] transition-colors"
        >
          + Add Supplier
        </button>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 px-6 py-6">
        {summaryLoading ? (
          [0, 1, 2, 3].map((i) => <StatCardSkeleton key={i} animationDelay={i * 50} />)
        ) : (
          <>
            <StatCard
              label="Total Suppliers"
              value={summary?.total_suppliers ?? 0}
              icon={<Users className="h-5 w-5 text-[--color-text-secondary]" />}
              animationDelay={0}
              onClick={() => navigate("/suppliers")}
            />
            <StatCard
              label="High Risk"
              value={summary?.high_risk_count ?? 0}
              icon={<AlertTriangle className="h-5 w-5 text-red-400" />}
              accent="bg-red-950"
              animationDelay={50}
            />
            <StatCard
              label="New Alerts"
              value={summary?.unread_alerts_count ?? 0}
              icon={<Bell className="h-5 w-5 text-[--color-brand]" />}
              accent="bg-indigo-950"
              animationDelay={100}
              onClick={() => navigate("/alerts")}
            />
            <StatCard
              label="Portfolio Trend"
              value={trendLabel}
              icon={<TrendIcon trend={trend} />}
              accent={trendAccent}
              animationDelay={150}
            />
          </>
        )}
      </div>

      {/* Alerts strip — hidden when zero unread */}
      {unreadAlerts.length > 0 && (
        <div className="px-6 pb-6">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-[--color-text-secondary] uppercase tracking-wider">
              Unread Alerts
            </h2>
            <button
              onClick={() => navigate("/alerts")}
              className="flex items-center gap-1 text-sm text-[--color-brand] hover:text-[--color-brand-hover]"
            >
              View all alerts <ChevronRight className="h-4 w-4" />
            </button>
          </div>
          <div className="flex gap-3 overflow-x-auto pb-2 scrollbar-none">
            {unreadAlerts.map((alert) => (
              <AlertCard key={alert.alert_id} alert={alert} />
            ))}
          </div>
        </div>
      )}

      {/* Supplier table */}
      <div className="border-t border-[--color-border]">
        <SupplierTable />
      </div>
    </div>
  );
}
