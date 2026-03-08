import { useState, useEffect } from "react";
import type { AlertRules } from "@/types/api";
import { NavLink } from "react-router-dom";
import { Check, X, Loader2 } from "lucide-react";
import { useAlertRules, useUpdateAlertRules } from "@/hooks/useSettings";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

function SettingsNav() {
  const baseClass = "px-4 py-2 text-sm font-medium rounded-md transition-colors";
  const inactive = "text-[--color-text-secondary] hover:text-[--color-text-primary]";

  return (
    <nav className="flex gap-1 border-b border-[--color-border] px-6 mb-6">
      <NavLink
        to="/settings"
        end
        className={({ isActive }) => cn(baseClass, "border-b-2 -mb-px rounded-none", isActive ? "border-[--color-brand] text-[--color-text-primary]" : "border-transparent " + inactive)}
      >
        Alert Rules
      </NavLink>
      <NavLink
        to="/settings/users"
        className={({ isActive }) => cn(baseClass, "border-b-2 -mb-px rounded-none", isActive ? "border-[--color-brand] text-[--color-text-primary]" : "border-transparent " + inactive)}
      >
        Users
      </NavLink>
    </nav>
  );
}

// ── Tag input for email recipients ─────────────────────────────────────────────

function TagInput({
  values,
  onChange,
  placeholder,
  validate,
}: {
  values: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
  validate?: (v: string) => boolean;
}) {
  const [input, setInput] = useState("");
  const [error, setError] = useState(false);

  function add() {
    const val = input.trim();
    if (!val) return;
    if (validate && !validate(val)) { setError(true); return; }
    if (values.includes(val) || values.length >= 10) return;
    onChange([...values, val]);
    setInput("");
    setError(false);
  }

  return (
    <div>
      <div className="flex flex-wrap gap-2 mb-2">
        {values.map((v) => (
          <span
            key={v}
            className="flex items-center gap-1 rounded-full bg-[--color-bg-elevated] border border-[--color-border] px-2.5 py-0.5 text-xs text-[--color-text-secondary]"
          >
            {v}
            <button onClick={() => onChange(values.filter((x) => x !== v))}>
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <Input
          placeholder={placeholder}
          value={input}
          onChange={(e) => { setInput(e.target.value); setError(false); }}
          onKeyDown={(e) => e.key === "Enter" && add()}
          className={cn(error && "border-red-500")}
        />
        <Button variant="outline" size="sm" onClick={add}>Add</Button>
      </div>
      {error && <p className="mt-1 text-xs text-red-400">Invalid format</p>}
    </div>
  );
}

// ── Alert Rules Form (receives loaded rules as props) ──────────────────────────

function AlertRulesForm({ rules }: { rules: AlertRules }) {
  const updateRules = useUpdateAlertRules();

  const [spikeThreshold, setSpikeThreshold] = useState(rules.score_spike_threshold);
  const [highThreshold, setHighThreshold] = useState(rules.high_risk_threshold);
  const [emailEnabled, setEmailEnabled] = useState(rules.channels.email.enabled);
  const [recipients, setRecipients] = useState<string[]>(rules.channels.email.recipients);
  const [slackEnabled, setSlackEnabled] = useState(rules.channels.slack.enabled);
  const [slackWebhook, setSlackWebhook] = useState(rules.channels.slack.webhook_url ?? "");
  const [webhookTestStatus, setWebhookTestStatus] = useState<"idle" | "testing" | "ok" | "fail">("idle");
  const [saved, setSaved] = useState(false);

  function isValidEmail(email: string) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  }

  async function handleSave() {
    await updateRules.mutateAsync({
      score_spike_threshold: spikeThreshold,
      high_risk_threshold: highThreshold,
      channels: {
        email: { enabled: emailEnabled, recipients },
        slack: { enabled: slackEnabled, webhook_url: slackWebhook || null, webhook_verified: false },
        webhook: { enabled: false, url: null, secret: null },
      },
    });
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  async function testWebhook() {
    setWebhookTestStatus("testing");
    try {
      await updateRules.mutateAsync({
        channels: {
          email: { enabled: emailEnabled, recipients },
          slack: { enabled: true, webhook_url: slackWebhook, webhook_verified: false },
          webhook: { enabled: false, url: null, secret: null },
        },
      });
      setWebhookTestStatus("ok");
    } catch {
      setWebhookTestStatus("fail");
    }
    setTimeout(() => setWebhookTestStatus("idle"), 3000);
  }


  return (
    <div className="max-w-xl space-y-8">
      {/* Thresholds */}
      <section>
        <h2 className="text-sm font-semibold text-[--color-text-secondary] uppercase tracking-wider mb-4">
          Thresholds
        </h2>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-[--color-text-primary] mb-1.5">
              Score spike threshold (5–50 points)
            </label>
            <p className="text-xs text-[--color-text-muted] mb-2">
              Fire an alert when a supplier's score rises by this many points in 7 days
            </p>
            <Input
              type="number"
              min={5}
              max={50}
              value={spikeThreshold}
              onChange={(e) => setSpikeThreshold(Number(e.target.value))}
              className="w-32"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-[--color-text-primary] mb-1.5">
              High risk threshold (50–95)
            </label>
            <p className="text-xs text-[--color-text-muted] mb-2">
              Fire an alert when a score exceeds this value
            </p>
            <Input
              type="number"
              min={50}
              max={95}
              value={highThreshold}
              onChange={(e) => setHighThreshold(Number(e.target.value))}
              className="w-32"
            />
          </div>
        </div>
      </section>

      {/* Email */}
      <section>
        <h2 className="text-sm font-semibold text-[--color-text-secondary] uppercase tracking-wider mb-4">
          Email Notifications
        </h2>
        <label className="flex items-center gap-2 mb-4 cursor-pointer">
          <input
            type="checkbox"
            checked={emailEnabled}
            onChange={(e) => setEmailEnabled(e.target.checked)}
            className="rounded accent-[--color-brand]"
          />
          <span className="text-sm text-[--color-text-primary]">Enabled</span>
        </label>
        {emailEnabled && (
          <div>
            <label className="block text-sm font-medium text-[--color-text-secondary] mb-1.5">
              Recipients
            </label>
            <TagInput
              values={recipients}
              onChange={setRecipients}
              placeholder="email@company.com"
              validate={isValidEmail}
            />
          </div>
        )}
      </section>

      {/* Slack */}
      <section>
        <h2 className="text-sm font-semibold text-[--color-text-secondary] uppercase tracking-wider mb-4">
          Slack Notifications
        </h2>
        <label className="flex items-center gap-2 mb-4 cursor-pointer">
          <input
            type="checkbox"
            checked={slackEnabled}
            onChange={(e) => setSlackEnabled(e.target.checked)}
            className="rounded accent-[--color-brand]"
          />
          <span className="text-sm text-[--color-text-primary]">Enabled</span>
        </label>
        {slackEnabled && (
          <div>
            <label className="block text-sm font-medium text-[--color-text-secondary] mb-1.5">
              Webhook URL
            </label>
            <div className="flex gap-2">
              <Input
                placeholder="https://hooks.slack.com/services/…"
                value={slackWebhook}
                onChange={(e) => setSlackWebhook(e.target.value)}
              />
              <Button
                variant="outline"
                size="sm"
                onClick={testWebhook}
                disabled={!slackWebhook || webhookTestStatus === "testing"}
                className="shrink-0"
              >
                {webhookTestStatus === "testing" && <Loader2 className="h-4 w-4 animate-spin" />}
                {webhookTestStatus === "ok" && <Check className="h-4 w-4 text-green-400" />}
                {webhookTestStatus === "fail" && <X className="h-4 w-4 text-red-400" />}
                {webhookTestStatus === "idle" && "Test"}
              </Button>
            </div>
            {webhookTestStatus === "ok" && (
              <p className="mt-1 text-xs text-green-400">Webhook verified ✓</p>
            )}
            {webhookTestStatus === "fail" && (
              <p className="mt-1 text-xs text-red-400">Webhook failed — check the URL</p>
            )}
          </div>
        )}
      </section>

      {/* Save */}
      <Button onClick={handleSave} disabled={updateRules.isPending}>
        {updateRules.isPending ? (
          <><Loader2 className="h-4 w-4 animate-spin" /> Saving…</>
        ) : saved ? (
          <><Check className="h-4 w-4" /> Saved</>
        ) : (
          "Save changes"
        )}
      </Button>
      {updateRules.isError && (
        <p className="text-sm text-red-400">Failed to save. Please try again.</p>
      )}
    </div>
  );
}

function AlertRulesTab() {
  const { data: rules, isLoading } = useAlertRules();
  if (isLoading) {
    return (
      <div className="space-y-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-12 rounded-lg bg-[--color-bg-elevated] animate-pulse" />
        ))}
      </div>
    );
  }
  if (!rules) return null;
  return <AlertRulesForm key={rules.updated_at} rules={rules} />;
}

// ── Settings Page ──────────────────────────────────────────────────────────────

export default function SettingsPage() {
  useEffect(() => {
    document.title = "Settings — Supplier Risk Platform";
  }, []);

  return (
    <div className="pb-12">
      <div className="px-6 py-6 border-b border-[--color-border]">
        <h1
          className="text-2xl text-[--color-text-primary]"
          style={{ fontFamily: "'DM Serif Display', serif" }}
        >
          Settings
        </h1>
      </div>
      <div className="pt-6">
        <SettingsNav />
        <div className="px-6">
          <AlertRulesTab />
        </div>
      </div>
    </div>
  );
}
