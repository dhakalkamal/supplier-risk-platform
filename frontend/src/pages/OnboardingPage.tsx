import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { Check, ArrowRight, Building2, Mail, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useUpdateAlertRules } from "@/hooks/useSettings";
import { apiFetch } from "@/lib/api";
import { cn, getCountryFlag } from "@/lib/utils";

// ── Step Indicator ─────────────────────────────────────────────────────────────

const STEPS = [
  { num: 1, label: "Welcome" },
  { num: 2, label: "Suppliers" },
  { num: 3, label: "Alerts" },
  { num: 4, label: "Done" },
];

function StepIndicator({ current }: { current: number }) {
  return (
    <div className="flex items-center justify-center gap-2 mb-10">
      {STEPS.map((step, i) => (
        <div key={step.num} className="flex items-center gap-2">
          <div
            className={cn(
              "flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold transition-colors",
              current === step.num
                ? "bg-[--color-brand] text-white"
                : current > step.num
                ? "bg-green-700 text-white"
                : "bg-[--color-bg-elevated] text-[--color-text-muted]",
            )}
          >
            {current > step.num ? <Check className="h-3.5 w-3.5" /> : step.num}
          </div>
          <span
            className={cn(
              "hidden sm:block text-xs font-medium",
              current === step.num ? "text-[--color-text-primary]" : "text-[--color-text-muted]",
            )}
          >
            {step.label}
          </span>
          {i < STEPS.length - 1 && (
            <div
              className={cn(
                "h-px w-8 mx-1",
                current > step.num ? "bg-green-700" : "bg-[--color-border]",
              )}
            />
          )}
        </div>
      ))}
    </div>
  );
}

// ── Step 1: Welcome ────────────────────────────────────────────────────────────

function WelcomeStep({ onNext }: { onNext: () => void }) {
  return (
    <div className="text-center max-w-lg">
      <div className="mb-6 flex justify-center">
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-[--color-brand]/20 border border-[--color-brand]/30">
          <Sparkles className="h-8 w-8 text-[--color-brand]" />
        </div>
      </div>
      <h1
        className="text-3xl text-[--color-text-primary] mb-3"
        style={{ fontFamily: "'DM Serif Display', serif" }}
      >
        Welcome to Supplier Risk
      </h1>
      <p className="text-[--color-text-secondary] mb-2">
        Monitor your supply chain health in real-time. We score every supplier 0–100 using
        financial filings, news sentiment, shipping signals, and geopolitical data.
      </p>
      <p className="text-sm text-[--color-text-muted] mb-8">
        Setup takes about 2 minutes. You can always change these settings later.
      </p>
      <div className="grid grid-cols-3 gap-4 mb-10 text-center">
        {[
          { icon: "📊", label: "Real-time scoring" },
          { icon: "🔔", label: "Instant alerts" },
          { icon: "📰", label: "News monitoring" },
        ].map(({ icon, label }) => (
          <div
            key={label}
            className="rounded-lg border border-[--color-border] bg-[--color-bg-surface] p-3"
          >
            <div className="text-2xl mb-1">{icon}</div>
            <p className="text-xs text-[--color-text-secondary]">{label}</p>
          </div>
        ))}
      </div>
      <Button onClick={onNext} className="w-full sm:w-auto px-8">
        Get started <ArrowRight className="ml-2 h-4 w-4" />
      </Button>
    </div>
  );
}

// ── Step 2: Add Suppliers ──────────────────────────────────────────────────────

interface ResolveCandidate {
  supplier_id: string;
  canonical_name: string;
  country: string;
  confidence: number;
}

function AddSuppliersStep({ onNext, onSkip }: { onNext: () => void; onSkip: () => void }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ResolveCandidate[]>([]);
  const [added, setAdded] = useState<string[]>([]);
  const [searching, setSearching] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  function handleQueryChange(q: string) {
    setQuery(q);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (q.length < 2) {
      setResults([]);
      return;
    }
    debounceRef.current = setTimeout(() => {
      setSearching(true);
      apiFetch<{ candidates: ResolveCandidate[] }>(
        `/api/v1/suppliers/resolve?q=${encodeURIComponent(q)}&limit=5`,
      )
        .then((data) => setResults(data.candidates))
        .catch(() => setResults([]))
        .finally(() => setSearching(false));
    }, 300);
  }

  function addSupplier(candidate: ResolveCandidate) {
    apiFetch("/api/v1/portfolio/suppliers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ supplier_id: candidate.supplier_id }),
    })
      .then(() => {
        setAdded((prev) => [...prev, candidate.canonical_name]);
        setQuery("");
        setResults([]);
      })
      .catch(() => {
        // Non-blocking — user can skip
      });
  }

  return (
    <div className="w-full max-w-lg">
      <div className="mb-2 flex justify-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-[--color-bg-elevated] border border-[--color-border]">
          <Building2 className="h-6 w-6 text-[--color-text-secondary]" />
        </div>
      </div>
      <h2
        className="text-2xl text-[--color-text-primary] text-center mb-1"
        style={{ fontFamily: "'DM Serif Display', serif" }}
      >
        Add your first suppliers
      </h2>
      <p className="text-sm text-[--color-text-secondary] text-center mb-6">
        Search for companies to monitor. You can add more later from the Suppliers page.
      </p>

      <div className="relative mb-3">
        <Input
          placeholder="Search by company name…"
          value={query}
          onChange={(e) => handleQueryChange(e.target.value)}
          autoFocus
        />
        {searching && (
          <div className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 animate-spin rounded-full border-2 border-[--color-brand] border-t-transparent" />
        )}
      </div>

      {results.length > 0 && (
        <div className="mb-4 rounded-lg border border-[--color-border] bg-[--color-bg-surface] overflow-hidden divide-y divide-[--color-border]">
          {results.map((r) => (
            <button
              key={r.supplier_id}
              onClick={() => addSupplier(r)}
              className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-[--color-bg-elevated] transition-colors"
            >
              <div>
                <p className="text-sm font-medium text-[--color-text-primary]">
                  {getCountryFlag(r.country)} {r.canonical_name}
                </p>
                <p className="text-xs text-[--color-text-muted]">{r.country}</p>
              </div>
              <span className="text-xs text-[--color-text-muted]">
                {Math.round(r.confidence * 100)}% match
              </span>
            </button>
          ))}
        </div>
      )}

      {added.length > 0 && (
        <div className="mb-4 space-y-1.5">
          {added.map((name) => (
            <div
              key={name}
              className="flex items-center gap-2 rounded-md bg-green-950/50 border border-green-800 px-3 py-2 text-sm text-green-300"
            >
              <Check className="h-4 w-4 shrink-0" />
              {name} added to portfolio
            </div>
          ))}
        </div>
      )}

      <div className="flex gap-3 mt-4">
        <Button onClick={onSkip} variant="outline" className="flex-1">
          Skip for now
        </Button>
        <Button onClick={onNext} className="flex-1">
          {added.length > 0 ? `Continue (${added.length} added)` : "Continue"}
          <ArrowRight className="ml-2 h-4 w-4" />
        </Button>
      </div>
      {added.length === 0 && (
        <p className="mt-2 text-center text-xs text-[--color-text-muted]">
          You can add suppliers later from the Suppliers page
        </p>
      )}
    </div>
  );
}

// ── Step 3: Alert Config ───────────────────────────────────────────────────────

function AlertConfigStep({ onNext, onSkip }: { onNext: () => void; onSkip: () => void }) {
  const updateRules = useUpdateAlertRules();
  const [input, setInput] = useState("");
  const [emails, setEmails] = useState<string[]>([]);
  const [error, setError] = useState(false);

  function isValidEmail(email: string) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  }

  function addEmail() {
    const val = input.trim();
    if (!val) return;
    if (!isValidEmail(val)) {
      setError(true);
      return;
    }
    if (emails.includes(val) || emails.length >= 10) return;
    setEmails((prev) => [...prev, val]);
    setInput("");
    setError(false);
  }

  function handleSave() {
    updateRules
      .mutateAsync({
        channels: {
          email: { enabled: emails.length > 0, recipients: emails },
          slack: { enabled: false, webhook_url: null, webhook_verified: false },
          webhook: { enabled: false, url: null, secret: null },
        },
      })
      .then(() => onNext())
      .catch(() => onNext()); // best-effort — don't block
  }

  return (
    <div className="w-full max-w-lg">
      <div className="mb-2 flex justify-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-[--color-bg-elevated] border border-[--color-border]">
          <Mail className="h-6 w-6 text-[--color-text-secondary]" />
        </div>
      </div>
      <h2
        className="text-2xl text-[--color-text-primary] text-center mb-1"
        style={{ fontFamily: "'DM Serif Display', serif" }}
      >
        Who should get alerts?
      </h2>
      <p className="text-sm text-[--color-text-secondary] text-center mb-6">
        Add email addresses to receive risk alerts. You can configure Slack and other channels
        in Settings.
      </p>

      {emails.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-3">
          {emails.map((email) => (
            <span
              key={email}
              className="flex items-center gap-1 rounded-full bg-[--color-bg-elevated] border border-[--color-border] px-2.5 py-0.5 text-xs text-[--color-text-secondary]"
            >
              {email}
              <button onClick={() => setEmails((prev) => prev.filter((e) => e !== email))}>
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="flex gap-2 mb-1">
        <Input
          type="email"
          placeholder="alerts@yourcompany.com"
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            setError(false);
          }}
          onKeyDown={(e) => e.key === "Enter" && addEmail()}
          className={cn(error && "border-red-500")}
          autoFocus
        />
        <Button variant="outline" size="sm" onClick={addEmail}>
          Add
        </Button>
      </div>
      {error && <p className="text-xs text-red-400 mb-2">Invalid email address</p>}

      <div className="flex gap-3 mt-6">
        <Button onClick={onSkip} variant="outline" className="flex-1">
          Skip for now
        </Button>
        <Button onClick={handleSave} className="flex-1" disabled={updateRules.isPending}>
          {updateRules.isPending ? "Saving…" : "Save & continue"}
          <ArrowRight className="ml-2 h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

// ── Step 4: Done ───────────────────────────────────────────────────────────────

function DoneStep({ onComplete }: { onComplete: () => void }) {
  return (
    <div className="text-center max-w-md">
      <div className="mb-6 flex justify-center">
        <div className="flex h-16 w-16 items-center justify-center rounded-full bg-green-900 border-2 border-green-600">
          <Check className="h-8 w-8 text-green-400" />
        </div>
      </div>
      <h2
        className="text-3xl text-[--color-text-primary] mb-3"
        style={{ fontFamily: "'DM Serif Display', serif" }}
      >
        You're all set!
      </h2>
      <p className="text-[--color-text-secondary] mb-2">
        Your account is configured. We're already gathering signals for your portfolio.
      </p>
      <p className="text-sm text-[--color-text-muted] mb-8">
        Scores are updated every 6 hours. You'll receive an alert the first time a supplier
        crosses your risk threshold.
      </p>
      <Button onClick={onComplete} className="w-full sm:w-auto px-8">
        Go to Dashboard <ArrowRight className="ml-2 h-4 w-4" />
      </Button>
    </div>
  );
}

// ── Onboarding Page ────────────────────────────────────────────────────────────

export default function OnboardingPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);

  useEffect(() => {
    document.title = "Welcome — Supplier Risk Platform";
    if (localStorage.getItem("onboarding_complete")) {
      navigate("/dashboard", { replace: true });
    }
  }, [navigate]);

  function complete() {
    localStorage.setItem("onboarding_complete", "true");
    navigate("/dashboard", { replace: true });
  }

  return (
    <div className="min-h-screen bg-[--color-bg-base] flex flex-col items-center justify-center px-4 py-12">
      <div className="w-full max-w-2xl">
        <StepIndicator current={step} />
        <div className="flex justify-center">
          {step === 1 && <WelcomeStep onNext={() => setStep(2)} />}
          {step === 2 && (
            <AddSuppliersStep onNext={() => setStep(3)} onSkip={() => setStep(3)} />
          )}
          {step === 3 && (
            <AlertConfigStep onNext={() => setStep(4)} onSkip={() => setStep(4)} />
          )}
          {step === 4 && <DoneStep onComplete={complete} />}
        </div>
      </div>
    </div>
  );
}
