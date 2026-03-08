import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  ExternalLink,
  AlertTriangle,
  RefreshCw,
} from "lucide-react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ReferenceLine,
  ResponsiveContainer,
  BarChart,
  Bar,
  Cell,
} from "recharts";
import { useSupplier, useScoreHistory, useSupplierNews } from "@/hooks/useSupplier";
import { DataFreshnessBar } from "@/components/layout/DataFreshnessBar";
import { RiskBadge } from "@/components/ui/RiskBadge";
import { ScoreTrend } from "@/components/ui/ScoreTrend";
import { Skeleton } from "@/components/ui/SkeletonRow";
import { cn, formatScore, formatTimeAgo, getCountryFlag } from "@/lib/utils";
import { getRiskLevel, RISK_CONFIG } from "@/lib/risk";
import type { TopDriver, ScoreHistoryPoint } from "@/types/api";

// ── Risk level colour map ──────────────────────────────────────────────────────

const RISK_COLORS = {
  high: "#EF4444",
  medium: "#F59E0B",
  low: "#22C55E",
  insufficient_data: "#64748B",
} as const;

// ── Score Dial ─────────────────────────────────────────────────────────────────

const DIAL_RADIUS = 80;
const DIAL_CIRCUMFERENCE = 2 * Math.PI * DIAL_RADIUS;

interface ScoreDialProps {
  score: number | null;
  delta: number;
  modelVersion: string;
  scoredAt: string;
  completeness: number;
}

function ScoreDial({ score, delta, modelVersion, scoredAt, completeness }: ScoreDialProps) {
  const [animated, setAnimated] = useState(false);
  const level = getRiskLevel(score);
  const config = RISK_CONFIG[level];
  const color = RISK_COLORS[level];

  useEffect(() => {
    const t = setTimeout(() => setAnimated(true), 150);
    return () => clearTimeout(t);
  }, []);

  const offset = DIAL_CIRCUMFERENCE * (1 - (animated && score !== null ? score : 0) / 100);

  return (
    <div className="flex flex-col items-center py-6">
      <div className="relative w-48 h-48">
        <svg
          width="192"
          height="192"
          viewBox="0 0 192 192"
          className="-rotate-90"
        >
          {/* Track */}
          <circle
            cx="96"
            cy="96"
            r={DIAL_RADIUS}
            fill="none"
            stroke="#2D3147"
            strokeWidth="12"
          />
          {/* Score arc */}
          <circle
            cx="96"
            cy="96"
            r={DIAL_RADIUS}
            fill="none"
            stroke={color}
            strokeWidth="12"
            strokeLinecap="round"
            strokeDasharray={DIAL_CIRCUMFERENCE}
            strokeDashoffset={offset}
            style={{ transition: "stroke-dashoffset 0.6s ease-out" }}
          />
        </svg>
        {/* Centre text */}
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span
            className="text-6xl leading-none text-[--color-text-primary]"
            style={{ fontFamily: "'DM Serif Display', serif" }}
          >
            {formatScore(score)}
          </span>
          <span className={cn("mt-1 text-sm font-medium", config.textClass)}>
            {config.label} Risk
          </span>
          <ScoreTrend delta={delta} className="mt-1 text-xs" />
        </div>
      </div>
      <div className="mt-3 text-center space-y-0.5 text-xs text-[--color-text-muted]">
        <p>model: {modelVersion}</p>
        <p>scored: {formatTimeAgo(scoredAt)}</p>
        <p>completeness: {Math.round(completeness * 100)}%</p>
      </div>
    </div>
  );
}

// ── Signal Breakdown ───────────────────────────────────────────────────────────

const SIGNAL_LABELS: Record<string, string> = {
  financial: "Financial",
  news: "News Sentiment",
  shipping: "Shipping",
  geopolitical: "Geopolitical",
  macro: "Macroeconomic",
};

interface SignalBreakdownProps {
  breakdown: {
    financial: { score: number; weight: number };
    news: { score: number; weight: number };
    shipping: { score: number; weight: number };
    geopolitical: { score: number; weight: number };
    macro: { score: number; weight: number };
  };
}

function SignalBreakdown({ breakdown }: SignalBreakdownProps) {
  return (
    <div className="space-y-3 py-4">
      {(Object.entries(breakdown) as [string, { score: number; weight: number }][]).map(
        ([key, { score, weight }]) => {
          const level = getRiskLevel(score);
          const barColor = RISK_COLORS[level];
          return (
            <div key={key}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm text-[--color-text-secondary]">
                  {SIGNAL_LABELS[key] ?? key}
                </span>
                <div className="flex items-center gap-3">
                  <span className="text-sm font-semibold text-[--color-text-primary]">
                    {score}
                  </span>
                  <span className="text-xs text-[--color-text-muted] w-8 text-right">
                    {Math.round(weight * 100)}%
                  </span>
                </div>
              </div>
              <div className="h-1.5 w-full rounded-full bg-[--color-bg-elevated]">
                <div
                  className="h-1.5 rounded-full transition-all duration-500"
                  style={{ width: `${score}%`, backgroundColor: barColor }}
                />
              </div>
            </div>
          );
        },
      )}
    </div>
  );
}

// ── Score History Chart ────────────────────────────────────────────────────────

interface ScoreHistoryChartProps {
  scores: ScoreHistoryPoint[];
  riskLevel: string;
}

interface ChartTooltipPayload {
  value: number;
  payload: ScoreHistoryPoint;
}

interface ChartTooltipProps {
  active?: boolean;
  payload?: ChartTooltipPayload[];
  label?: string;
}

function CustomTooltip({ active, payload, label }: ChartTooltipProps) {
  if (!active || !payload?.length) return null;
  const point = payload[0];
  const pointLevel = getRiskLevel(point.value);
  return (
    <div className="rounded-lg border border-[--color-border] bg-[--color-bg-elevated] px-3 py-2 shadow-lg">
      <p className="text-xs text-[--color-text-muted]">{label}</p>
      <p className="text-sm font-semibold text-[--color-text-primary]">Score: {point.value}</p>
      <RiskBadge level={pointLevel} className="mt-1" />
    </div>
  );
}

function ScoreHistoryChart({ scores, riskLevel }: ScoreHistoryChartProps) {
  const [window, setWindow] = useState<7 | 30 | 90 | 365>(90);
  const color = RISK_COLORS[riskLevel as keyof typeof RISK_COLORS] ?? "#64748B";
  const gradientId = `scoreGrad-${riskLevel}`;

  const sliced = scores.slice(-window);

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-[--color-text-secondary] uppercase tracking-wider">
          Score History
        </h3>
        <div className="flex gap-1">
          {([7, 30, 90, 365] as const).map((w) => (
            <button
              key={w}
              onClick={() => setWindow(w)}
              className={cn(
                "rounded px-2 py-1 text-xs font-medium transition-colors",
                window === w
                  ? "bg-[--color-brand] text-white"
                  : "text-[--color-text-muted] hover:text-[--color-text-secondary]",
              )}
            >
              {w === 365 ? "1y" : `${w}d`}
            </button>
          ))}
        </div>
      </div>

      {sliced.length < 2 ? (
        <div className="flex h-48 items-center justify-center rounded-lg border border-[--color-border] bg-[--color-bg-surface]">
          <p className="text-sm text-[--color-text-muted]">Gathering data — chart available soon</p>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <AreaChart data={sliced} margin={{ top: 5, right: 5, bottom: 0, left: -10 }}>
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={color} stopOpacity={0.25} />
                <stop offset="95%" stopColor={color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#2D3147" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fill: "#475569", fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(d: string) => {
                const dt = new Date(d);
                return dt.toLocaleDateString("en-US", { month: "short", day: "numeric" });
              }}
              interval="preserveStartEnd"
            />
            <YAxis
              domain={[0, 100]}
              tick={{ fill: "#475569", fontSize: 11 }}
              tickLine={false}
              axisLine={false}
            />
            <RechartsTooltip content={<CustomTooltip />} />
            <ReferenceLine y={40} stroke="#F59E0B" strokeDasharray="4 4" strokeOpacity={0.6} />
            <ReferenceLine y={70} stroke="#EF4444" strokeDasharray="4 4" strokeOpacity={0.6} />
            <Area
              type="monotone"
              dataKey="score"
              stroke={color}
              strokeWidth={2}
              fill={`url(#${gradientId})`}
              dot={false}
              activeDot={{ r: 4, fill: color }}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

// ── SHAP Waterfall ─────────────────────────────────────────────────────────────

interface ShapTooltipProps {
  active?: boolean;
  payload?: Array<{ payload: { explanation: string; name: string } }>;
}

function ShapTooltip({ active, payload }: ShapTooltipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div className="max-w-xs rounded-lg border border-[--color-border] bg-[--color-bg-elevated] px-3 py-2 shadow-lg">
      <p className="text-xs text-[--color-text-secondary]">{payload[0].payload.explanation}</p>
    </div>
  );
}

function ShapWaterfall({ drivers }: { drivers: TopDriver[] }) {
  const [selected, setSelected] = useState<TopDriver | null>(null);

  const data = [...drivers]
    .sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution))
    .map((d) => ({
      name: d.display_name,
      value: d.direction === "increases_risk" ? d.contribution : -d.contribution,
      explanation: d.explanation,
      direction: d.direction,
      raw: d,
    }));

  const maxAbs = Math.max(...data.map((d) => Math.abs(d.value)), 5);

  return (
    <div>
      <h3 className="mb-4 text-sm font-semibold text-[--color-text-secondary] uppercase tracking-wider">
        What's Driving This Score
      </h3>
      <div className="overflow-x-auto">
        <ResponsiveContainer width="100%" height={Math.max(180, data.length * 38)}>
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 0, right: 20, bottom: 0, left: 0 }}
            onClick={(e) => {
              const chart = e as unknown as {
                activePayload?: Array<{ payload: (typeof data)[0] }>;
              };
              if (chart?.activePayload?.[0]) {
                setSelected(chart.activePayload[0].payload.raw);
              }
            }}
          >
            <XAxis
              type="number"
              domain={[-maxAbs, maxAbs]}
              tick={{ fill: "#475569", fontSize: 11 }}
              tickLine={false}
              axisLine={false}
            />
            <YAxis
              type="category"
              dataKey="name"
              width={160}
              tick={{ fill: "#94A3B8", fontSize: 11 }}
              tickLine={false}
              axisLine={false}
            />
            <ReferenceLine x={0} stroke="#2D3147" strokeWidth={1.5} />
            <RechartsTooltip content={<ShapTooltip />} />
            <Bar dataKey="value" radius={[0, 3, 3, 0]} cursor="pointer">
              {data.map((entry, i) => (
                <Cell
                  key={i}
                  fill={entry.direction === "increases_risk" ? "#EF4444" : "#22C55E"}
                  fillOpacity={0.85}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      {selected && (
        <div className="mt-3 rounded-lg border border-[--color-border] bg-[--color-bg-elevated] p-3">
          <p className="text-xs font-medium text-[--color-text-secondary] mb-1">{selected.display_name}</p>
          <p className="text-sm text-[--color-text-primary]">{selected.explanation}</p>
          <button
            onClick={() => setSelected(null)}
            className="mt-2 text-xs text-[--color-text-muted] hover:text-[--color-text-secondary]"
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}

// ── News Feed ──────────────────────────────────────────────────────────────────

type SentimentFilter = "all" | "negative" | "positive" | "neutral";

function NewsFeed({ supplierId }: { supplierId: string }) {
  const [sentiment, setSentiment] = useState<SentimentFilter>("all");
  const [page, setPage] = useState(1);

  const { data, isLoading } = useSupplierNews(supplierId, {
    sentiment: sentiment === "all" ? undefined : sentiment,
    per_page: 10,
    page,
  });

  const articles = data?.data ?? [];
  const hasMore = data ? page < data.meta.total_pages : false;

  const sentimentTabs: { value: SentimentFilter; label: string }[] = [
    { value: "all", label: "All" },
    { value: "negative", label: "Negative" },
    { value: "positive", label: "Positive" },
    { value: "neutral", label: "Neutral" },
  ];

  const sentimentColor = (label: string) => {
    if (label === "negative") return "text-red-400";
    if (label === "positive") return "text-green-400";
    return "text-[--color-text-muted]";
  };

  const credibilityColor = (score: number) => {
    if (score >= 0.8) return "bg-green-500";
    if (score >= 0.5) return "bg-amber-500";
    return "bg-red-500";
  };

  return (
    <div>
      <h3 className="mb-3 text-sm font-semibold text-[--color-text-secondary] uppercase tracking-wider">
        Recent News
      </h3>
      {/* Filter tabs */}
      <div className="flex gap-1 mb-4 border-b border-[--color-border]">
        {sentimentTabs.map((tab) => (
          <button
            key={tab.value}
            onClick={() => { setSentiment(tab.value); setPage(1); }}
            className={cn(
              "px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
              sentiment === tab.value
                ? "border-[--color-brand] text-[--color-text-primary]"
                : "border-transparent text-[--color-text-secondary] hover:text-[--color-text-primary]",
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {isLoading && (
        <div className="space-y-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="space-y-2">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-3 w-1/2" />
            </div>
          ))}
        </div>
      )}

      {!isLoading && articles.length === 0 && (
        <p className="py-8 text-center text-sm text-[--color-text-muted]">No articles found.</p>
      )}

      <div className="space-y-4">
        {articles.map((article) => (
          <div
            key={article.article_id}
            className="rounded-lg border border-[--color-border] bg-[--color-bg-surface] p-4"
          >
            <div className="flex items-start justify-between gap-2 mb-2">
              <div className="flex items-center gap-2 text-xs text-[--color-text-muted]">
                <span
                  className={cn("h-2 w-2 rounded-full shrink-0", credibilityColor(article.source_credibility))}
                  title={`Credibility: ${Math.round(article.source_credibility * 100)}%`}
                />
                <span>{article.source_name}</span>
                <span>·</span>
                <span>{formatTimeAgo(article.published_at)}</span>
              </div>
              <span className={cn("text-xs font-medium shrink-0", sentimentColor(article.sentiment_label))}>
                {article.sentiment_label}
              </span>
            </div>
            <a
              href={article.url}
              target="_blank"
              rel="noopener noreferrer"
              className="group flex items-start gap-1 text-sm font-medium text-[--color-text-primary] hover:text-[--color-brand]"
            >
              {article.title}
              <ExternalLink className="mt-0.5 h-3.5 w-3.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
            </a>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {article.topics.map((topic) => (
                <span
                  key={topic}
                  className="rounded-full bg-[--color-bg-elevated] px-2 py-0.5 text-xs text-[--color-text-muted]"
                >
                  {topic}
                </span>
              ))}
              {article.score_contribution > 0 && (
                <span className="text-xs text-red-400">+{article.score_contribution} pts</span>
              )}
            </div>
          </div>
        ))}
      </div>

      {hasMore && (
        <button
          onClick={() => setPage((p) => p + 1)}
          className="mt-4 w-full rounded-md border border-[--color-border] py-2 text-sm text-[--color-text-secondary] hover:bg-[--color-bg-elevated] transition-colors"
        >
          Load more articles
        </button>
      )}
    </div>
  );
}

// ── Supplier Profile Page ──────────────────────────────────────────────────────

export default function SupplierProfilePage() {
  const { supplierId } = useParams<{ supplierId: string }>();
  const navigate = useNavigate();

  const { data: supplier, isLoading, error } = useSupplier(supplierId ?? "");
  const score = supplier?.current_score;
  const riskLevel = score ? getRiskLevel(score.score) : "insufficient_data";

  const { data: historyData } = useScoreHistory(supplierId ?? "", 90);

  useEffect(() => {
    if (supplier) {
      document.title = `${supplier.canonical_name} — Supplier Risk Platform`;
    }
  }, [supplier]);

  if (isLoading) {
    return (
      <div className="p-6 space-y-6">
        <Skeleton className="h-6 w-40" />
        <div className="grid md:grid-cols-2 gap-6">
          <Skeleton className="h-64 rounded-xl" />
          <Skeleton className="h-64 rounded-xl" />
        </div>
        <Skeleton className="h-56 rounded-xl" />
      </div>
    );
  }

  if (error || !supplier) {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-4">
        <AlertTriangle className="h-10 w-10 text-red-400" />
        <p className="text-[--color-text-secondary]">Supplier not found.</p>
        <button
          onClick={() => navigate(-1)}
          className="text-sm text-[--color-brand] hover:underline"
        >
          Go back
        </button>
      </div>
    );
  }

  const flag = getCountryFlag(supplier.country);

  return (
    <div className="pb-12">
      {/* Back link */}
      <div className="px-6 pt-5 pb-4 border-b border-[--color-border]">
        <button
          onClick={() => navigate(-1)}
          className="flex items-center gap-1.5 text-sm text-[--color-text-secondary] hover:text-[--color-text-primary] mb-4"
        >
          <ArrowLeft className="h-4 w-4" /> Back to portfolio
        </button>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span role="img" aria-label={supplier.country} className="text-2xl">{flag}</span>
              <h1
                className="text-2xl text-[--color-text-primary] leading-tight"
                style={{ fontFamily: "'DM Serif Display', serif" }}
              >
                {supplier.canonical_name}
              </h1>
            </div>
            <p className="text-sm text-[--color-text-muted]">
              {supplier.country} · {supplier.industry_name}
              {supplier.duns_number && ` · DUNS: ${supplier.duns_number}`}
            </p>
            {supplier.aliases.length > 0 && (
              <p className="text-xs text-[--color-text-muted] mt-1">
                Also known as: {supplier.aliases.join(", ")}
              </p>
            )}
          </div>
          <RiskBadge level={riskLevel} />
        </div>
      </div>

      {/* Data freshness bar */}
      <DataFreshnessBar scoredAt={score?.scored_at} />

      {/* Staleness banners */}
      {score?.financial_data_is_stale && (
        <div className="mx-6 mt-4 rounded-lg border border-amber-800 bg-amber-950/50 px-4 py-3 text-sm text-amber-300">
          Financial data may be outdated. SEC filing may be overdue.
        </div>
      )}
      {score && score.data_completeness < 0.5 && (
        <div className="mx-6 mt-4 rounded-lg border border-[--color-border] bg-[--color-bg-elevated] px-4 py-3 text-sm text-[--color-text-secondary]">
          Score based on {Math.round(score.data_completeness * 100)}% of available signals. Some data sources are unavailable.
        </div>
      )}

      <div className="px-6 py-6 space-y-8">
        {/* Score dial + signal breakdown */}
        <div className="grid md:grid-cols-2 gap-6">
          <div className="rounded-xl border border-[--color-border] bg-[--color-bg-surface]">
            {score ? (
              <ScoreDial
                score={score.score}
                delta={0}
                modelVersion={score.model_version}
                scoredAt={score.scored_at}
                completeness={score.data_completeness}
              />
            ) : (
              <div className="flex h-64 items-center justify-center">
                <div className="text-center">
                  <RefreshCw className="mx-auto mb-2 h-8 w-8 text-[--color-text-muted]" />
                  <p className="text-sm text-[--color-text-muted]">Score not yet available</p>
                  <p className="text-xs text-[--color-text-muted] mt-1">
                    Gathering data — check back in 24 hours
                  </p>
                </div>
              </div>
            )}
          </div>

          {score && (
            <div className="rounded-xl border border-[--color-border] bg-[--color-bg-surface] px-6">
              <h3 className="pt-4 text-sm font-semibold text-[--color-text-secondary] uppercase tracking-wider">
                Signal Breakdown
              </h3>
              <SignalBreakdown breakdown={score.signal_breakdown} />
            </div>
          )}
        </div>

        {/* Score history */}
        {historyData && historyData.scores.length > 0 && (
          <div className="rounded-xl border border-[--color-border] bg-[--color-bg-surface] p-6">
            <ScoreHistoryChart scores={historyData.scores} riskLevel={riskLevel} />
          </div>
        )}

        {/* SHAP waterfall */}
        {score && score.top_drivers.length > 0 && (
          <div className="rounded-xl border border-[--color-border] bg-[--color-bg-surface] p-6">
            <ShapWaterfall drivers={score.top_drivers} />
          </div>
        )}

        {/* News feed */}
        <div className="rounded-xl border border-[--color-border] bg-[--color-bg-surface] p-6">
          <NewsFeed supplierId={supplierId ?? ""} />
        </div>

        {/* Supplier metadata */}
        {supplier.website && (
          <div className="flex items-center gap-2 text-sm text-[--color-text-secondary]">
            <ExternalLink className="h-4 w-4" />
            <a
              href={supplier.website}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-[--color-brand]"
            >
              {supplier.website}
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
