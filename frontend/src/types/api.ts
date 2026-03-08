import type { RiskLevel } from "@/lib/risk";

// ── Envelope types ────────────────────────────────────────────────────────────

export interface Meta {
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

export interface DataResponse<T> {
  data: T;
}

export interface ListResponse<T> {
  data: T[];
  meta: Meta;
}

// ── Portfolio ─────────────────────────────────────────────────────────────────

export interface PortfolioSummary {
  total_suppliers: number;
  high_risk_count: number;
  medium_risk_count: number;
  low_risk_count: number;
  unread_alerts_count: number;
  average_portfolio_score: number;
  score_trend_7d: "improving" | "worsening" | "stable";
  last_scored_at: string;
  plan_supplier_limit: number;
  plan_supplier_used: number;
}

export interface SupplierSummary {
  portfolio_supplier_id: string;
  supplier_id: string;
  canonical_name: string;
  custom_name: string | null;
  country: string;
  industry_code: string;
  industry_name: string;
  internal_id: string | null;
  tags: string[];
  risk_score: number | null;
  risk_level: RiskLevel;
  score_7d_delta: number;
  score_trend: "increasing" | "decreasing" | "stable";
  unread_alerts_count: number;
  last_score_updated_at: string;
  data_completeness: number;
  added_to_portfolio_at: string;
}

// ── Supplier ──────────────────────────────────────────────────────────────────

export interface SignalBreakdownItem {
  score: number;
  weight: number;
  data_available: boolean;
}

export interface TopDriver {
  signal_name: string;
  display_name: string;
  category: string;
  contribution: number;
  direction: "increases_risk" | "decreases_risk";
  raw_value: number;
  explanation: string;
}

export interface CurrentScore {
  score: number;
  risk_level: RiskLevel;
  model_version: string;
  scored_at: string;
  data_completeness: number;
  financial_data_is_stale?: boolean;
  signal_breakdown: {
    financial: SignalBreakdownItem;
    news: SignalBreakdownItem;
    shipping: SignalBreakdownItem;
    geopolitical: SignalBreakdownItem;
    macro: SignalBreakdownItem;
  };
  top_drivers: TopDriver[];
}

export interface SupplierProfile {
  supplier_id: string;
  canonical_name: string;
  aliases: string[];
  country: string;
  industry_code: string;
  industry_name: string;
  duns_number: string | null;
  cik: string | null;
  website: string | null;
  primary_location: {
    city: string;
    country: string;
    lat: number;
    lng: number;
  } | null;
  is_public_company: boolean;
  in_portfolio: boolean;
  portfolio_supplier_id: string | null;
  current_score: CurrentScore | null;
}

export interface ScoreHistoryPoint {
  date: string;
  score: number;
  risk_level: RiskLevel;
  model_version: string;
}

export interface ScoreHistory {
  supplier_id: string;
  days_requested: number;
  days_available: number;
  scores: ScoreHistoryPoint[];
}

export interface NewsArticle {
  article_id: string;
  title: string;
  url: string;
  source_name: string;
  source_credibility: number;
  published_at: string;
  sentiment_score: number;
  sentiment_label: "positive" | "negative" | "neutral";
  sentiment_model: string;
  topics: string[];
  score_contribution: number;
  content_available: boolean;
}

// ── Alerts ────────────────────────────────────────────────────────────────────

export type AlertStatus = "new" | "investigating" | "resolved" | "dismissed";
export type AlertSeverity = "low" | "medium" | "high" | "critical";
export type AlertType =
  | "score_spike"
  | "high_threshold"
  | "event_detected"
  | "sanctions_hit";

export interface Alert {
  alert_id: string;
  supplier_id: string;
  supplier_name: string;
  alert_type: AlertType;
  severity: AlertSeverity;
  title: string;
  message: string;
  metadata: Record<string, unknown>;
  status: AlertStatus;
  note: string | null;
  fired_at: string;
  read_at: string | null;
  resolved_at: string | null;
}

// ── WebSocket events ──────────────────────────────────────────────────────────

export interface AlertFiredEvent {
  type: "alert.fired";
  data: {
    alert_id: string;
    supplier_id: string;
    supplier_name: string;
    alert_type: AlertType;
    severity: AlertSeverity;
    title: string;
    fired_at: string;
  };
}

export interface ScoreUpdatedEvent {
  type: "score.updated";
  data: {
    supplier_id: string;
    new_score: number;
    previous_score: number;
    risk_level: RiskLevel;
    scored_at: string;
  };
}

export type WsEvent =
  | AlertFiredEvent
  | ScoreUpdatedEvent
  | { type: "ping"; timestamp: string }
  | { type: "auth.expired" };

// ── Settings ──────────────────────────────────────────────────────────────────

export interface AlertRules {
  score_spike_threshold: number;
  high_risk_threshold: number;
  channels: {
    email: {
      enabled: boolean;
      recipients: string[];
    };
    slack: {
      enabled: boolean;
      webhook_url: string | null;
      webhook_verified: boolean;
    };
    webhook: {
      enabled: boolean;
      url: string | null;
      secret: string | null;
    };
  };
  updated_at: string;
}

export interface TenantUser {
  user_id: string;
  email: string;
  role: "admin" | "viewer";
  created_at: string;
  last_active_at: string;
}

export interface PendingInvite {
  invite_id: string;
  email: string;
  role: "admin" | "viewer";
  expires_at: string;
}

// ── Import ────────────────────────────────────────────────────────────────────

export interface ImportJob {
  import_id: string;
  status: "processing" | "completed" | "failed";
  total_rows: number;
  resolved_count: number;
  added_count: number;
  duplicate_count: number;
  unresolved_count: number;
  error_count: number;
  plan_limit_skipped_count: number;
  unresolved_items: Array<{
    row: number;
    raw_name: string;
    country: string | null;
    reason: string;
    best_candidate: string | null;
    best_confidence: number;
  }>;
  started_at: string;
  completed_at: string | null;
}

// ── Resolution ────────────────────────────────────────────────────────────────

export interface ResolveResult {
  resolved: boolean;
  supplier_id: string | null;
  canonical_name: string | null;
  country: string | null;
  confidence: number;
  match_method: string;
  alternatives: Array<{
    supplier_id: string;
    canonical_name: string;
    country: string;
    confidence: number;
  }>;
}
