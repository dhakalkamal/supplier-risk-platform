import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  X,
  ExternalLink,
  Copy,
  Check,
  ChevronRight,
} from "lucide-react";
import { useAlerts, usePatchAlert, VALID_NEXT_STATUSES } from "@/hooks/useAlerts";
import { useWebSocket } from "@/hooks/useWebSocket";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/SkeletonRow";
import { cn, formatDate, formatTimeAgo } from "@/lib/utils";
import { getRiskLevel, RISK_CONFIG } from "@/lib/risk";
import type { Alert, AlertStatus } from "@/types/api";

// ── Status label map ───────────────────────────────────────────────────────────

const STATUS_LABELS: Record<AlertStatus, string> = {
  new: "New",
  investigating: "Investigating",
  resolved: "Resolved",
  dismissed: "Dismissed",
};

const STATUS_BADGE: Record<AlertStatus, string> = {
  new: "bg-red-950 text-red-400 border border-red-800",
  investigating: "bg-amber-950 text-amber-400 border border-amber-800",
  resolved: "bg-green-950 text-green-400 border border-green-800",
  dismissed: "bg-slate-800 text-slate-400 border border-slate-700",
};

const BORDER_ACCENT: Record<AlertStatus, string> = {
  new: "border-l-2 border-l-red-500",
  investigating: "border-l-2 border-l-amber-500",
  resolved: "border-l-transparent",
  dismissed: "border-l-transparent",
};

type TabValue = AlertStatus | "all";

const TABS: { value: TabValue; label: string }[] = [
  { value: "new", label: "New" },
  { value: "investigating", label: "Investigating" },
  { value: "resolved", label: "Resolved" },
  { value: "all", label: "All" },
];

// ── Alert Row ──────────────────────────────────────────────────────────────────

interface AlertRowProps {
  alert: Alert;
  isSelected: boolean;
  isHighlighted: boolean;
  onClick: () => void;
}

function AlertRow({ alert, isSelected, isHighlighted, onClick }: AlertRowProps) {
  const level = getRiskLevel(
    alert.severity === "critical" || alert.severity === "high" ? 80
    : alert.severity === "medium" ? 55
    : 25,
  );
  const dot = RISK_CONFIG[level].dotClass;

  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full text-left px-4 py-3 border-b border-[--color-border] transition-colors",
        "hover:bg-[--color-bg-elevated]",
        isSelected && "bg-[--color-bg-elevated]",
        BORDER_ACCENT[alert.status],
        isHighlighted && "animate-[highlightFlash_0.5s_ease_forwards]",
      )}
    >
      <div className="flex items-start gap-3">
        <span className={cn("mt-1.5 h-2 w-2 rounded-full shrink-0", dot)} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2 mb-0.5">
            <span className="text-sm font-medium text-[--color-text-primary] truncate">
              {alert.supplier_name}
            </span>
            <span
              className={cn(
                "shrink-0 rounded-full px-2 py-0.5 text-xs font-medium",
                STATUS_BADGE[alert.status],
              )}
            >
              {STATUS_LABELS[alert.status]}
            </span>
          </div>
          <p className="text-sm text-[--color-text-secondary] line-clamp-2">{alert.title}</p>
          <p className="mt-1 text-xs text-[--color-text-muted]">{formatTimeAgo(alert.fired_at)}</p>
        </div>
      </div>
    </button>
  );
}

// ── Alert Detail Panel ─────────────────────────────────────────────────────────

interface AlertDetailProps {
  alert: Alert;
  onClose: () => void;
}

function AlertDetail({ alert, onClose }: AlertDetailProps) {
  const navigate = useNavigate();
  const patchAlert = usePatchAlert();
  const [note, setNote] = useState(alert.note ?? "");
  const [copied, setCopied] = useState(false);

  const level = getRiskLevel(
    alert.severity === "critical" || alert.severity === "high" ? 80
    : alert.severity === "medium" ? 55
    : 25,
  );
  const config = RISK_CONFIG[level];

  const validNextStatuses = VALID_NEXT_STATUSES[alert.status];

  function saveNote() {
    if (note !== (alert.note ?? "")) {
      patchAlert.mutate({ alertId: alert.alert_id, note });
    }
  }

  function handleStatusChange(newStatus: string) {
    if (newStatus && newStatus !== alert.status) {
      patchAlert.mutate({ alertId: alert.alert_id, status: newStatus as AlertStatus });
    }
  }

  function handleNoteKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && e.ctrlKey) {
      e.preventDefault();
      saveNote();
    }
  }

  function copyLink() {
    void navigator.clipboard.writeText(window.location.href);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  const meta = alert.metadata as Record<string, unknown>;

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      {/* Header */}
      <div className="flex items-start justify-between gap-3 p-5 border-b border-[--color-border]">
        <div className="flex items-start gap-3">
          <AlertTriangle className={cn("mt-0.5 h-5 w-5 shrink-0", config.textClass)} />
          <div>
            <h2 className="text-base font-semibold text-[--color-text-primary]">{alert.title}</h2>
            <p className="text-sm text-[--color-text-secondary] mt-0.5">{alert.supplier_name}</p>
            <p className="text-xs text-[--color-text-muted] mt-0.5">
              Fired: {formatDate(alert.fired_at)} at {new Date(alert.fired_at).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })} UTC
            </p>
          </div>
        </div>
        <button
          onClick={onClose}
          className="shrink-0 p-1 rounded text-[--color-text-muted] hover:text-[--color-text-primary] hover:bg-[--color-bg-elevated]"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 p-5 space-y-5">
        {/* Message */}
        <div className="rounded-lg bg-[--color-bg-elevated] p-4 text-sm text-[--color-text-secondary]">
          {alert.message}
        </div>

        {/* Metadata */}
        {meta.score_before !== undefined && (
          <div className="grid grid-cols-3 gap-3 text-center">
            <div className="rounded-lg bg-[--color-bg-surface] border border-[--color-border] p-3">
              <p className="text-lg font-bold text-[--color-text-primary]">{String(meta.score_before)}</p>
              <p className="text-xs text-[--color-text-muted]">Score before</p>
            </div>
            <div className="flex items-center justify-center">
              <ChevronRight className="h-5 w-5 text-[--color-text-muted]" />
            </div>
            <div className="rounded-lg bg-[--color-bg-surface] border border-[--color-border] p-3">
              <p className={cn("text-lg font-bold", config.textClass)}>{String(meta.score_after)}</p>
              <p className="text-xs text-[--color-text-muted]">Score after</p>
            </div>
          </div>
        )}

        {/* Status */}
        <div>
          <label className="block text-xs font-medium text-[--color-text-muted] uppercase tracking-wider mb-2">
            Status
          </label>
          <Select
            value={alert.status}
            onChange={(e) => handleStatusChange(e.target.value)}
            disabled={patchAlert.isPending}
          >
            <option value={alert.status}>{STATUS_LABELS[alert.status]}</option>
            {validNextStatuses.map((s) => (
              <option key={s} value={s}>{STATUS_LABELS[s]}</option>
            ))}
          </Select>
        </div>

        {/* Note */}
        <div>
          <label className="block text-xs font-medium text-[--color-text-muted] uppercase tracking-wider mb-2">
            Investigation Note
          </label>
          <Textarea
            placeholder="Add your note… (Ctrl+Enter to save)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onBlur={saveNote}
            onKeyDown={handleNoteKeyDown}
            rows={4}
          />
          {patchAlert.isPending && (
            <p className="mt-1 text-xs text-[--color-text-muted]">Saving…</p>
          )}
        </div>

        {/* Actions */}
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => navigate(`/suppliers/${alert.supplier_id}`)}
            className="flex items-center gap-1.5 rounded-md border border-[--color-border] px-3 py-1.5 text-sm text-[--color-text-secondary] hover:text-[--color-text-primary] hover:bg-[--color-bg-elevated] transition-colors"
          >
            <ExternalLink className="h-4 w-4" /> View Supplier Profile
          </button>
          <button
            onClick={copyLink}
            className="flex items-center gap-1.5 rounded-md border border-[--color-border] px-3 py-1.5 text-sm text-[--color-text-secondary] hover:text-[--color-text-primary] hover:bg-[--color-bg-elevated] transition-colors"
          >
            {copied ? <Check className="h-4 w-4 text-green-400" /> : <Copy className="h-4 w-4" />}
            {copied ? "Copied!" : "Copy link"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Alerts Page ────────────────────────────────────────────────────────────────

export default function AlertsPage() {
  const [activeTab, setActiveTab] = useState<TabValue>("new");
  const [selectedAlert, setSelectedAlert] = useState<Alert | null>(null);
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);
  const [highlightedId, setHighlightedId] = useState<string | null>(null);
  const prevHighlightedRef = useRef<string | null>(null);

  const queryClient = useQueryClient();
  const { lastAlertEvent } = useWebSocket();

  // Fetch all alerts (status=all), filter client-side for tab counts
  const { data, isLoading } = useAlerts({ status: "all", per_page: 200 });
  const allAlerts = data?.data ?? [];

  // Tab counts
  const counts: Record<TabValue, number> = {
    new: allAlerts.filter((a) => a.status === "new").length,
    investigating: allAlerts.filter((a) => a.status === "investigating").length,
    resolved: allAlerts.filter((a) => a.status === "resolved").length,
    dismissed: allAlerts.filter((a) => a.status === "dismissed").length,
    all: allAlerts.length,
  };

  // Filtered alerts for active tab
  const filtered = activeTab === "all" ? allAlerts : allAlerts.filter((a) => a.status === activeTab);

  // WebSocket: new alert arrives
  useEffect(() => {
    if (!lastAlertEvent) return;
    const id = lastAlertEvent.data.alert_id;
    if (id === prevHighlightedRef.current) return;
    prevHighlightedRef.current = id;
    void queryClient.invalidateQueries({ queryKey: ["alerts"] });
    const t1 = setTimeout(() => setHighlightedId(id), 0);
    const t2 = setTimeout(() => setHighlightedId(null), 500);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, [lastAlertEvent, queryClient]);

  useEffect(() => {
    document.title = "Alerts — Supplier Risk Platform";
  }, []);

  function handleSelectAlert(alert: Alert) {
    setSelectedAlert(alert);
    setMobileDetailOpen(true);
  }

  const tabs = TABS.map((t) => ({ ...t, count: counts[t.value] }));

  return (
    <div className="flex h-[calc(100vh-0px)] flex-col pb-0">
      {/* Page header */}
      <div className="flex items-center justify-between px-6 py-5 border-b border-[--color-border] shrink-0">
        <h1
          className="text-2xl text-[--color-text-primary]"
          style={{ fontFamily: "'DM Serif Display', serif" }}
        >
          Alerts
        </h1>
      </div>

      {/* Tabs */}
      <div className="flex gap-0 border-b border-[--color-border] px-4 shrink-0">
        {tabs.map((tab) => (
          <button
            key={tab.value}
            onClick={() => setActiveTab(tab.value)}
            className={cn(
              "flex items-center gap-1.5 px-4 py-3 text-sm font-medium border-b-2 -mb-px transition-colors",
              activeTab === tab.value
                ? "border-[--color-brand] text-[--color-text-primary]"
                : "border-transparent text-[--color-text-secondary] hover:text-[--color-text-primary]",
            )}
          >
            {tab.label}
            {tab.count > 0 && (
              <span
                className={cn(
                  "rounded-full px-1.5 py-0.5 text-xs font-semibold",
                  activeTab === tab.value
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

      {/* Split panel */}
      <div className="flex flex-1 min-h-0">
        {/* Left: alert list */}
        <div className={cn(
          "flex-1 overflow-y-auto border-r border-[--color-border]",
          selectedAlert ? "hidden lg:block lg:w-2/5 lg:flex-none" : "w-full",
        )}>
          {isLoading && (
            <div className="space-y-0">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="px-4 py-4 border-b border-[--color-border] space-y-2">
                  <Skeleton className="h-4 w-1/3" />
                  <Skeleton className="h-3 w-2/3" />
                  <Skeleton className="h-3 w-16" />
                </div>
              ))}
            </div>
          )}

          {!isLoading && filtered.length === 0 && (
            <div className="flex flex-col items-center justify-center py-16 text-center px-6">
              <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-green-950">
                <Check className="h-6 w-6 text-green-400" />
              </div>
              <p className="font-semibold text-[--color-text-primary]">All clear</p>
              <p className="mt-1 text-sm text-[--color-text-secondary]">
                No alerts in this category.
              </p>
            </div>
          )}

          {!isLoading && filtered.map((alert) => (
            <AlertRow
              key={alert.alert_id}
              alert={alert}
              isSelected={selectedAlert?.alert_id === alert.alert_id}
              isHighlighted={highlightedId === alert.alert_id}
              onClick={() => handleSelectAlert(alert)}
            />
          ))}
        </div>

        {/* Right: detail panel — desktop */}
        {selectedAlert && (
          <div className="hidden lg:flex flex-col flex-1 overflow-hidden bg-[--color-bg-surface]">
            <AlertDetail
              alert={allAlerts.find((a) => a.alert_id === selectedAlert.alert_id) ?? selectedAlert}
              onClose={() => setSelectedAlert(null)}
            />
          </div>
        )}
      </div>

      {/* Mobile: full-screen sheet */}
      <Sheet open={mobileDetailOpen} onOpenChange={setMobileDetailOpen}>
        <SheetContent side="right" className="w-full max-w-full lg:hidden p-0">
          <SheetHeader className="sr-only">
            <SheetTitle>Alert Detail</SheetTitle>
          </SheetHeader>
          {selectedAlert && (
            <AlertDetail
              alert={allAlerts.find((a) => a.alert_id === selectedAlert.alert_id) ?? selectedAlert}
              onClose={() => setMobileDetailOpen(false)}
            />
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
