import { useState, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient, useQuery } from "@tanstack/react-query";
import { ArrowLeft, Upload, CheckCircle, AlertTriangle, X, FileText } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { useDebounce } from "@/hooks/useDebounce";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/SkeletonRow";
import { RiskBadge } from "@/components/ui/RiskBadge";
import { cn, getCountryFlag } from "@/lib/utils";
import { getRiskLevel } from "@/lib/risk";
import type { ResolveResult, ImportJob, DataResponse } from "@/types/api";

type Mode = "single" | "bulk";
type SingleStep = 1 | 2 | 3;

// ── Single Add Flow ────────────────────────────────────────────────────────────

interface ResolvedCandidate {
  supplier_id: string;
  canonical_name: string;
  country: string | null;
  confidence: number;
}

function SingleAddFlow() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [step, setStep] = useState<SingleStep>(1);
  const [searchInput, setSearchInput] = useState("");
  const [selected, setSelected] = useState<ResolvedCandidate | null>(null);
  const [internalId, setInternalId] = useState("");
  const [customName, setCustomName] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const debouncedSearch = useDebounce(searchInput, 300);

  const { data: resolveData, isLoading: searching } = useQuery({
    queryKey: ["resolve", debouncedSearch],
    queryFn: () =>
      apiFetch<DataResponse<ResolveResult>>("/api/v1/suppliers/resolve", {
        method: "POST",
        body: JSON.stringify({ name: debouncedSearch }),
      }).then((r) => r.data),
    enabled: debouncedSearch.length >= 2,
    staleTime: 30_000,
  });

  // Build candidate list from resolve result
  const candidates: ResolvedCandidate[] = [];
  if (resolveData?.resolved && resolveData.supplier_id) {
    candidates.push({
      supplier_id: resolveData.supplier_id,
      canonical_name: resolveData.canonical_name ?? "",
      country: resolveData.country,
      confidence: resolveData.confidence,
    });
  }
  resolveData?.alternatives.forEach((alt) => {
    if (!candidates.find((c) => c.supplier_id === alt.supplier_id)) {
      candidates.push({ ...alt });
    }
  });
  const topCandidates = candidates.slice(0, 5);

  function handleSelect(candidate: ResolvedCandidate) {
    setSelected(candidate);
    setStep(2);
  }

  function addTag() {
    const tag = tagInput.trim();
    if (tag && !tags.includes(tag) && tags.length < 10) {
      setTags((t) => [...t, tag]);
      setTagInput("");
    }
  }

  async function handleConfirmAdd() {
    if (!selected) return;
    setAdding(true);
    setError(null);
    try {
      await apiFetch("/api/v1/portfolio/suppliers", {
        method: "POST",
        body: JSON.stringify({
          supplier_id: selected.supplier_id,
          internal_id: internalId || undefined,
          custom_name: customName || undefined,
          tags: tags.length > 0 ? tags : undefined,
        }),
      });
      await queryClient.invalidateQueries({ queryKey: ["portfolio"] });
      navigate("/suppliers");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add supplier");
      setAdding(false);
    }
  }

  return (
    <div className="max-w-lg mx-auto">
      {/* Step indicator */}
      <div className="flex items-center gap-2 mb-8">
        {([1, 2, 3] as SingleStep[]).map((s) => (
          <div key={s} className="flex items-center gap-2">
            <div
              className={cn(
                "h-7 w-7 rounded-full flex items-center justify-center text-xs font-semibold",
                step >= s
                  ? "bg-[--color-brand] text-white"
                  : "bg-[--color-bg-elevated] text-[--color-text-muted]",
              )}
            >
              {s}
            </div>
            {s < 3 && <div className={cn("h-px w-8", step > s ? "bg-[--color-brand]" : "bg-[--color-border]")} />}
          </div>
        ))}
        <span className="ml-2 text-sm text-[--color-text-muted]">
          {step === 1 ? "Search" : step === 2 ? "Confirm" : "Details"}
        </span>
      </div>

      {/* Step 1: Search */}
      {step === 1 && (
        <div>
          <h2 className="text-lg font-semibold text-[--color-text-primary] mb-4">
            Search for a supplier
          </h2>
          <Input
            placeholder="Type company name… (min 2 characters)"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            autoFocus
          />
          <div className="mt-3 space-y-2">
            {searching && debouncedSearch.length >= 2 && (
              <div className="space-y-2">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className="h-14 w-full rounded-lg" />
                ))}
              </div>
            )}
            {!searching && topCandidates.map((candidate) => {
              const flag = candidate.country ? getCountryFlag(candidate.country) : "";
              return (
                <button
                  key={candidate.supplier_id}
                  onClick={() => handleSelect(candidate)}
                  className="w-full flex items-center justify-between rounded-lg border border-[--color-border] bg-[--color-bg-surface] px-4 py-3 text-left hover:bg-[--color-bg-elevated] hover:border-[--color-brand] transition-colors"
                >
                  <div>
                    <p className="text-sm font-medium text-[--color-text-primary]">
                      {candidate.canonical_name}
                    </p>
                    <p className="text-xs text-[--color-text-muted]">
                      {flag} {candidate.country}
                    </p>
                  </div>
                  <span className="text-xs text-[--color-text-muted] shrink-0">
                    {Math.round(candidate.confidence * 100)}% match
                  </span>
                </button>
              );
            })}
            {!searching && debouncedSearch.length >= 2 && topCandidates.length === 0 && (
              <p className="text-sm text-[--color-text-muted] text-center py-4">
                No matches found for "{debouncedSearch}". Try a different name.
              </p>
            )}
          </div>
        </div>
      )}

      {/* Step 2: Confirm */}
      {step === 2 && selected && (
        <div>
          <h2 className="text-lg font-semibold text-[--color-text-primary] mb-4">
            Confirm supplier
          </h2>
          <div className="rounded-xl border border-[--color-brand] bg-[--color-bg-surface] p-5 mb-6">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="font-semibold text-[--color-text-primary]">
                  {selected.canonical_name}
                </p>
                <p className="text-sm text-[--color-text-muted] mt-0.5">
                  {selected.country && `${getCountryFlag(selected.country)} ${selected.country}`}
                </p>
                <p className="text-xs text-[--color-text-muted] mt-2">
                  {Math.round(selected.confidence * 100)}% match confidence
                </p>
              </div>
              <RiskBadge level={getRiskLevel(null)} />
            </div>
          </div>
          <div className="flex gap-3">
            <Button variant="outline" onClick={() => setStep(1)}>Back</Button>
            <Button onClick={() => setStep(3)}>Add to Portfolio</Button>
          </div>
        </div>
      )}

      {/* Step 3: Optional metadata */}
      {step === 3 && selected && (
        <div>
          <h2 className="text-lg font-semibold text-[--color-text-primary] mb-1">
            Optional details
          </h2>
          <p className="text-sm text-[--color-text-secondary] mb-6">
            These help you identify this supplier in your portfolio.
          </p>

          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-[--color-text-secondary] mb-1.5">
                Internal ID
              </label>
              <Input
                placeholder="e.g. VEND-0042"
                value={internalId}
                onChange={(e) => setInternalId(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-[--color-text-secondary] mb-1.5">
                Custom name (optional)
              </label>
              <Input
                placeholder={selected.canonical_name}
                value={customName}
                onChange={(e) => setCustomName(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-[--color-text-secondary] mb-1.5">
                Tags
              </label>
              <div className="flex flex-wrap gap-2 mb-2">
                {tags.map((tag) => (
                  <span
                    key={tag}
                    className="flex items-center gap-1 rounded-full bg-[--color-bg-elevated] border border-[--color-border] px-2.5 py-0.5 text-xs text-[--color-text-secondary]"
                  >
                    {tag}
                    <button onClick={() => setTags((t) => t.filter((x) => x !== tag))}>
                      <X className="h-3 w-3" />
                    </button>
                  </span>
                ))}
              </div>
              <div className="flex gap-2">
                <Input
                  placeholder="Add tag…"
                  value={tagInput}
                  onChange={(e) => setTagInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && addTag()}
                />
                <Button variant="outline" size="sm" onClick={addTag}>Add</Button>
              </div>
            </div>
          </div>

          {error && (
            <div className="mt-4 rounded-lg border border-red-800 bg-red-950/50 px-4 py-3 text-sm text-red-300">
              {error}
            </div>
          )}

          <div className="flex gap-3 mt-6">
            <Button variant="outline" onClick={() => setStep(2)}>Back</Button>
            <Button onClick={handleConfirmAdd} disabled={adding}>
              {adding ? "Adding…" : "Confirm Add"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Bulk Import Flow ───────────────────────────────────────────────────────────

function BulkImportFlow() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [isDragging, setIsDragging] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [importId, setImportId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { data: importStatus } = useQuery({
    queryKey: ["import", importId],
    queryFn: () =>
      apiFetch<DataResponse<ImportJob>>(`/api/v1/portfolio/imports/${importId}`).then(
        (r) => r.data,
      ),
    enabled: !!importId && step === 3,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "processing" ? 2000 : false;
    },
  });

  useEffect(() => {
    if (importStatus?.status === "completed" || importStatus?.status === "failed") {
      setStep(4);
      void queryClient.invalidateQueries({ queryKey: ["portfolio"] });
    }
  }, [importStatus, queryClient]);

  function downloadTemplate() {
    const csv = "name,country,internal_id,tags\n\"Example Supplier Ltd\",US,VEND-001,\"critical;tier-1\"\n";
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "supplier-import-template.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  function validateFile(f: File): string | null {
    if (f.size > 5 * 1024 * 1024) return "File must be smaller than 5MB.";
    if (!f.name.endsWith(".csv") && f.type !== "text/csv") return "File must be a CSV.";
    return null;
  }

  function handleFile(f: File) {
    const err = validateFile(f);
    if (err) { setFileError(err); return; }
    setFileError(null);
    setFile(f);
    setStep(2);
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setIsDragging(false);
    const f = e.dataTransfer.files[0];
    if (f) handleFile(f);
  }

  async function handleUpload() {
    if (!file) return;
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await apiFetch<DataResponse<{ import_id: string }>>("/api/v1/portfolio/suppliers/import", {
        method: "POST",
        body: form,
        headers: {},
      });
      setImportId(res.data.import_id);
      setStep(3);
    } catch (err) {
      setFileError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  const unresolved = importStatus?.unresolved_items ?? [];

  return (
    <div className="max-w-2xl mx-auto">
      {step === 1 && (
        <div>
          <h2 className="text-lg font-semibold text-[--color-text-primary] mb-2">
            Bulk import from CSV
          </h2>
          <p className="text-sm text-[--color-text-secondary] mb-6">
            Download the template, fill in your suppliers, then upload.
          </p>
          <Button variant="outline" onClick={downloadTemplate} className="mb-6">
            <FileText className="h-4 w-4" />
            Download CSV template
          </Button>

          {/* Drop zone */}
          <div
            onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={cn(
              "cursor-pointer rounded-xl border-2 border-dashed p-12 text-center transition-colors",
              isDragging
                ? "border-[--color-brand] bg-[--color-brand]/5"
                : "border-[--color-border] hover:border-[--color-text-muted]",
            )}
          >
            <Upload className="mx-auto mb-3 h-8 w-8 text-[--color-text-muted]" />
            <p className="text-sm font-medium text-[--color-text-primary]">
              Drag & drop your CSV here
            </p>
            <p className="mt-1 text-xs text-[--color-text-muted]">
              or click to browse · max 5MB · max 500 rows
            </p>
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }}
            />
          </div>
          {fileError && (
            <p className="mt-3 text-sm text-red-400">{fileError}</p>
          )}
        </div>
      )}

      {step === 2 && file && (
        <div>
          <h2 className="text-lg font-semibold text-[--color-text-primary] mb-4">
            Ready to upload
          </h2>
          <div className="rounded-lg border border-[--color-border] bg-[--color-bg-surface] p-4 flex items-center gap-3 mb-6">
            <FileText className="h-8 w-8 text-[--color-text-muted] shrink-0" />
            <div>
              <p className="text-sm font-medium text-[--color-text-primary]">{file.name}</p>
              <p className="text-xs text-[--color-text-muted]">
                {(file.size / 1024).toFixed(1)} KB
              </p>
            </div>
            <button onClick={() => { setFile(null); setStep(1); }} className="ml-auto text-[--color-text-muted] hover:text-[--color-text-primary]">
              <X className="h-4 w-4" />
            </button>
          </div>
          {fileError && <p className="mb-4 text-sm text-red-400">{fileError}</p>}
          <div className="flex gap-3">
            <Button variant="outline" onClick={() => setStep(1)}>Back</Button>
            <Button onClick={handleUpload} disabled={uploading}>
              {uploading ? "Uploading…" : "Start Import"}
            </Button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="text-center py-12">
          <div className="mx-auto mb-4 h-12 w-12 rounded-full border-4 border-[--color-brand] border-t-transparent animate-spin" />
          <p className="font-semibold text-[--color-text-primary]">Processing import…</p>
          <p className="mt-1 text-sm text-[--color-text-muted]">
            {importStatus?.resolved_count ?? 0} of {importStatus?.total_rows ?? "?"} resolved
          </p>
        </div>
      )}

      {step === 4 && importStatus && (
        <div>
          <div className="flex items-center gap-3 mb-6">
            {importStatus.status === "completed" ? (
              <CheckCircle className="h-8 w-8 text-green-400" />
            ) : (
              <AlertTriangle className="h-8 w-8 text-red-400" />
            )}
            <div>
              <h2 className="text-lg font-semibold text-[--color-text-primary]">
                {importStatus.status === "completed" ? "Import complete" : "Import failed"}
              </h2>
              <p className="text-sm text-[--color-text-secondary]">
                {importStatus.added_count} of {importStatus.total_rows} suppliers added ·{" "}
                {importStatus.unresolved_count} unresolved
              </p>
            </div>
          </div>

          {unresolved.length > 0 && (
            <div className="mb-6">
              <h3 className="mb-3 text-sm font-semibold text-[--color-text-secondary]">
                Unresolved rows
              </h3>
              <div className="rounded-lg border border-[--color-border] divide-y divide-[--color-border]">
                {unresolved.map((item) => (
                  <div key={item.row} className="flex items-center justify-between px-4 py-3">
                    <div>
                      <p className="text-sm text-[--color-text-primary]">{item.raw_name}</p>
                      {item.best_candidate && (
                        <p className="text-xs text-[--color-text-muted]">
                          Closest: {item.best_candidate} ({Math.round(item.best_confidence * 100)}%)
                        </p>
                      )}
                    </div>
                    <span className="text-xs text-amber-400">⚠ Unresolved</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="flex gap-3">
            <Button onClick={() => navigate("/suppliers")}>View Portfolio</Button>
            <Button variant="outline" onClick={() => { setStep(1); setFile(null); setImportId(null); }}>
              Import another
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Add Supplier Page ──────────────────────────────────────────────────────────

export default function AddSupplierPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>("single");

  useEffect(() => {
    document.title = "Add Supplier — Supplier Risk Platform";
  }, []);

  return (
    <div className="pb-12">
      <div className="px-6 py-5 border-b border-[--color-border]">
        <button
          onClick={() => navigate(-1)}
          className="flex items-center gap-1.5 text-sm text-[--color-text-secondary] hover:text-[--color-text-primary] mb-4"
        >
          <ArrowLeft className="h-4 w-4" /> Back
        </button>
        <h1
          className="text-2xl text-[--color-text-primary]"
          style={{ fontFamily: "'DM Serif Display', serif" }}
        >
          Add Supplier
        </h1>

        {/* Mode toggle */}
        <div className="flex gap-1 mt-4 rounded-lg bg-[--color-bg-elevated] p-1 w-fit">
          {(["single", "bulk"] as Mode[]).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={cn(
                "rounded-md px-4 py-1.5 text-sm font-medium transition-colors",
                mode === m
                  ? "bg-[--color-bg-surface] text-[--color-text-primary] shadow-sm"
                  : "text-[--color-text-secondary] hover:text-[--color-text-primary]",
              )}
            >
              {m === "single" ? "Add one" : "Bulk import CSV"}
            </button>
          ))}
        </div>
      </div>

      <div className="px-6 py-8">
        {mode === "single" ? <SingleAddFlow /> : <BulkImportFlow />}
      </div>
    </div>
  );
}
