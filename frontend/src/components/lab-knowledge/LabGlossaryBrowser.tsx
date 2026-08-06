"use client";

import { useConfirm } from "@/hooks/useConfirm";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";

import { clickableCard } from "@/lib/a11y";
import { useDismissOnEscape } from "@/hooks/useDismissOnEscape";

interface UserSummary {
  id: number;
  name: string | null;
  email: string;
}

interface GlossaryTerm {
  id: number;
  term: string;
  definition: string;
  aliases: string[] | null;
  category: string | null;
  context: string | null;
  source: string;
  created_by: UserSummary | null;
  created_at: string;
  updated_at: string;
}

interface TermListResponse {
  terms: GlossaryTerm[];
  total: number;
  page: number;
  page_size: number;
}

interface ScanJob {
  id: number;
  scan_type: string;
  status: string;
  proposed_new_count: number | null;
  proposed_changed_count: number | null;
}

interface Proposal {
  id: number;
  term: string;
  proposed_definition: string;
  proposed_aliases: string[] | null;
  proposed_category: string | null;
  proposed_context: string | null;
  proposal_type: string;
  existing_term_id: number | null;
  existing_definition: string | null;
  source_description: string | null;
  previously_rejected: boolean;
  review_status: string;
}

interface ProposalListResponse {
  job: ScanJob;
  new_terms: Proposal[];
  changed_terms: Proposal[];
}

const API_BASE = "/api/lab-knowledge";
const SOURCE_LABELS: Record<string, string> = {
  manual: "Manual",
  import: "Import",
  llm_scan: "AI scan",
};

export function LabGlossaryBrowser({ focusTermId }: { focusTermId?: number }) {
  const { canAccess } = usePermissions();
  const canManage = canAccess("lab_glossary", "manage");
  const canDelete = canAccess("lab_glossary", "delete");

  const [terms, setTerms] = useState<GlossaryTerm[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [pendingCount, setPendingCount] = useState(0);
  const [pendingJobIds, setPendingJobIds] = useState<number[]>([]);
  const [activeScanJobId, setActiveScanJobId] = useState<number | null>(null);
  const [scanError, setScanError] = useState<string | null>(null);

  const [selected, setSelected] = useState<GlossaryTerm | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [showScan, setShowScan] = useState(false);
  const [reviewJobId, setReviewJobId] = useState<number | null>(null);

  const fetchTerms = useCallback(async () => {
    setLoading(true);
    setError(null);
    const params = new URLSearchParams();
    if (query.trim()) params.set("q", query.trim());
    if (sourceFilter) params.set("source", sourceFilter);
    try {
      const data = await api.get<TermListResponse>(`${API_BASE}/glossary?${params.toString()}`);
      setTerms(data.terms);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load glossary");
    } finally {
      setLoading(false);
    }
  }, [query, sourceFilter]);

  const fetchPending = useCallback(async () => {
    try {
      const data = await api.get<{ pending_review_count: number; job_ids?: number[] }>(
        `${API_BASE}/glossary/pending`,
      );
      setPendingCount(data.pending_review_count);
      setPendingJobIds(data.job_ids ?? []);
    } catch {
      /* non-critical */
    }
  }, []);

  useEffect(() => {
    fetchTerms();
  }, [fetchTerms]);

  useEffect(() => {
    fetchPending();
  }, [fetchPending]);

  useEffect(() => {
    if (focusTermId && terms.length) {
      const match = terms.find((t) => t.id === focusTermId);
      if (match) setSelected(match);
    }
  }, [focusTermId, terms]);

  // Poll a dispatched AI scan until it finishes so the "scan is running" banner
  // clears and the freshly proposed terms surface in the pending-review banner.
  useEffect(() => {
    if (activeScanJobId === null) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const job = await api.get<ScanJob>(`${API_BASE}/glossary/scan/${activeScanJobId}`);
        if (cancelled) return;
        if (job.status === "complete" || job.status === "failed") {
          setActiveScanJobId(null);
          if (job.status === "complete") {
            fetchPending();
          } else {
            setScanError("The AI glossary scan could not be completed.");
          }
        }
      } catch {
        /* transient; keep polling */
      }
    };
    poll();
    const t = setInterval(poll, 3000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [activeScanJobId, fetchPending]);

  // NOTE: deliberately no `if (loading) return ...` early return here. `query` is a
  // dependency of the fetch, so every keystroke sets loading=true; returning early
  // unmounted the whole toolbar, taking the search input (and the caret) with it, so
  // only the first character of a search ever landed. The loading state is rendered
  // in the results region instead, below, and the toolbar stays mounted.
  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <input
            type="search"
            placeholder="Search glossary..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="border rounded px-3 py-1.5 text-sm w-64"
            aria-label="Search glossary"
          />
          <select
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
            className="border rounded px-2 py-1.5 text-sm"
            aria-label="Filter by source"
          >
            <option value="">All sources</option>
            <option value="manual">Manual</option>
            <option value="import">Import</option>
            <option value="llm_scan">AI scan</option>
          </select>
        </div>
        {canManage && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setShowAdd(true)}
              className="bg-bioaf-600 text-white rounded px-4 py-1.5 text-sm font-medium"
            >
              Add Term
            </button>
            <button
              type="button"
              onClick={() => setShowImport(true)}
              className="border rounded px-3 py-1.5 text-sm"
            >
              Import CSV
            </button>
            <button
              type="button"
              onClick={() => setShowScan(true)}
              className="border rounded px-3 py-1.5 text-sm"
              title="Use your org's LLM provider to propose glossary terms"
            >
              AI Scan
            </button>
          </div>
        )}
      </div>

      {activeScanJobId !== null && (
        <div
          data-testid="scan-running-banner"
          className="bg-blue-50 border border-blue-200 text-blue-800 rounded px-4 py-2 mb-4 text-sm flex items-center gap-2"
        >
          <span
            className="inline-block h-3 w-3 rounded-full border-2 border-blue-400 border-t-transparent animate-spin"
            aria-hidden="true"
          />
          AI glossary scan is running. Proposed terms will appear here for your review when it
          finishes.
        </div>
      )}

      {canManage && pendingCount > 0 && (
        <button
          type="button"
          onClick={() => {
            if (pendingJobIds.length) setReviewJobId(pendingJobIds[0]);
          }}
          disabled={pendingJobIds.length === 0}
          className="block w-full text-left bg-amber-50 border border-amber-200 text-amber-800 rounded px-4 py-2 mb-4 text-sm hover:bg-amber-100 disabled:cursor-default"
        >
          {pendingCount} proposed term{pendingCount === 1 ? "" : "s"} awaiting review. Click to
          review them.
        </button>
      )}

      {scanError && <div className="text-red-600 text-sm mb-3">{scanError}</div>}
      {error && <div className="text-red-600 text-sm mb-3">{error}</div>}

      {loading ? (
        <div data-testid="glossary-loading" className="text-gray-500 py-12 text-center">
          Loading glossary...
        </div>
      ) : terms.length === 0 ? (
        <div className="text-gray-500 py-12 text-center">
          No terms yet. {canManage ? "Add one manually, import a CSV, or run an AI scan." : ""}
        </div>
      ) : (
        <ul className="divide-y border rounded">
          {terms.map((t) => (
            <li
              key={t.id}
              {...clickableCard(() => setSelected(t))}
              className="p-3 hover:bg-gray-50 cursor-pointer"
            >
              <div className="flex items-baseline justify-between">
                <span className="font-semibold">{t.term}</span>
                <span className="text-xs text-gray-500">{SOURCE_LABELS[t.source] ?? t.source}</span>
              </div>
              <p className="text-sm text-gray-700 mt-0.5">{t.definition}</p>
              <div className="text-xs text-gray-500 mt-1">
                {t.aliases?.length ? <span>aka {t.aliases.join(", ")} </span> : null}
                {t.category ? <span className="ml-2">[{t.category}]</span> : null}
              </div>
            </li>
          ))}
        </ul>
      )}

      {selected && (
        <TermDetailPanel
          term={selected}
          canManage={canManage}
          canDelete={canDelete}
          onClose={() => setSelected(null)}
          onChanged={() => {
            setSelected(null);
            fetchTerms();
          }}
        />
      )}

      {showAdd && (
        <AddTermModal
          onClose={() => setShowAdd(false)}
          onSaved={() => {
            setShowAdd(false);
            fetchTerms();
          }}
        />
      )}

      {showImport && (
        <ImportModal
          onClose={() => setShowImport(false)}
          onImported={(jobId) => {
            setShowImport(false);
            fetchPending();
            setReviewJobId(jobId);
          }}
        />
      )}

      {showScan && (
        <ScanModal
          onClose={() => setShowScan(false)}
          onStarted={(job) => {
            setShowScan(false);
            setScanError(null);
            setActiveScanJobId(job.id);
          }}
        />
      )}

      {reviewJobId !== null && (
        <ReviewPanel
          jobId={reviewJobId}
          onClose={() => setReviewJobId(null)}
          onReviewed={() => {
            setReviewJobId(null);
            fetchTerms();
            fetchPending();
          }}
        />
      )}
    </div>
  );
}

function TermDetailPanel({
  term,
  canManage,
  canDelete,
  onClose,
  onChanged,
}: {
  term: GlossaryTerm;
  canManage: boolean;
  canDelete: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  useDismissOnEscape(true, () => onClose());
  const confirm = useConfirm();
  const [editing, setEditing] = useState(false);
  const [definition, setDefinition] = useState(term.definition);
  const [category, setCategory] = useState(term.category ?? "");
  const [context, setContext] = useState(term.context ?? "");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      await api.patch(`${API_BASE}/glossary/${term.id}`, {
        definition,
        category: category || null,
        context: context || null,
      });
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    const ok = await confirm({
      title: "Permanently remove this term?",
      message: "This cannot be undone.",
      confirmLabel: "Remove",
      variant: "danger",
    });
    if (!ok) return;
    await api.delete(`${API_BASE}/glossary/${term.id}`);
    onChanged();
  };

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg w-[32rem] max-h-[85vh] overflow-y-auto p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-4">
          <h2 className="text-xl font-bold">{term.term}</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="text-gray-500">
            x
          </button>
        </div>

        {editing ? (
          <div className="space-y-3">
            <textarea
              value={definition}
              onChange={(e) => setDefinition(e.target.value)}
              aria-label="Definition"
              className="border rounded px-3 py-1.5 text-sm w-full"
            />
            <input
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              placeholder="Category"
              aria-label="Category"
              className="border rounded px-3 py-1.5 text-sm w-full"
            />
            <textarea
              value={context}
              onChange={(e) => setContext(e.target.value)}
              placeholder="Context"
              aria-label="Context"
              className="border rounded px-3 py-1.5 text-sm w-full"
            />
            <div className="flex gap-2">
              <button
                type="button"
                onClick={save}
                disabled={busy}
                className="bg-bioaf-600 text-white text-sm rounded px-4 py-1.5 disabled:opacity-50"
              >
                Save
              </button>
              <button type="button" onClick={() => setEditing(false)} className="text-sm px-3 py-1.5">
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <>
            <p className="text-sm text-gray-700 mb-3">{term.definition}</p>
            {term.aliases?.length ? (
              <p className="text-xs text-gray-500 mb-1">Aliases: {term.aliases.join(", ")}</p>
            ) : null}
            {term.category ? <p className="text-xs text-gray-500 mb-1">Category: {term.category}</p> : null}
            {term.context ? <p className="text-xs text-gray-500 mb-1">Context: {term.context}</p> : null}
            <p className="text-xs text-gray-500 mt-2">Source: {SOURCE_LABELS[term.source] ?? term.source}</p>

            {(canManage || canDelete) && (
              <div className="border-t pt-4 mt-4 flex items-center gap-4">
                {canManage && (
                  <button type="button" onClick={() => setEditing(true)} className="text-sm text-bioaf-600">
                    Edit
                  </button>
                )}
                {canDelete && (
                  <button type="button" onClick={remove} className="text-sm text-red-600">
                    Delete
                  </button>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function AddTermModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  useDismissOnEscape(true, () => onClose());
  const [term, setTerm] = useState("");
  const [definition, setDefinition] = useState("");
  const [aliases, setAliases] = useState("");
  const [category, setCategory] = useState("");
  const [context, setContext] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    if (!term.trim() || !definition.trim()) {
      setErr("Term and definition are required.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await api.post(`${API_BASE}/glossary`, {
        term: term.trim(),
        definition: definition.trim(),
        aliases: aliases.trim() ? aliases.split(",").map((a) => a.trim()).filter(Boolean) : null,
        category: category.trim() || null,
        context: context.trim() || null,
      });
      onSaved();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Save failed";
      setErr(msg.includes("409") ? "A term with this name already exists." : msg);
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-lg w-[30rem] p-6" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-bold mb-4">Add Term</h2>
        <div className="space-y-3">
          <input
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            placeholder="Term"
            aria-label="Term"
            className="border rounded px-3 py-1.5 text-sm w-full"
          />
          <textarea
            value={definition}
            onChange={(e) => setDefinition(e.target.value)}
            placeholder="Definition"
            aria-label="Definition"
            className="border rounded px-3 py-1.5 text-sm w-full"
          />
          <input
            value={aliases}
            onChange={(e) => setAliases(e.target.value)}
            placeholder="Aliases (comma-separated)"
            aria-label="Aliases"
            className="border rounded px-3 py-1.5 text-sm w-full"
          />
          <input
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="Category (optional)"
            aria-label="Category"
            className="border rounded px-3 py-1.5 text-sm w-full"
          />
          <textarea
            value={context}
            onChange={(e) => setContext(e.target.value)}
            placeholder="Context (optional)"
            aria-label="Context"
            className="border rounded px-3 py-1.5 text-sm w-full"
          />
          {err && <div className="text-red-600 text-sm">{err}</div>}
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button type="button" onClick={onClose} className="text-sm px-3 py-1.5">
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={busy}
            className="bg-bioaf-600 text-white text-sm rounded px-4 py-1.5 disabled:opacity-50"
          >
            {busy ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ImportModal({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: (jobId: number) => void;
}) {
  useDismissOnEscape(true, () => onClose());
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    if (!file) {
      setErr("Choose a CSV or TSV file.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const job = await api.upload<ScanJob>(`${API_BASE}/glossary/import`, file);
      onImported(job.id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Import failed");
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-lg w-[30rem] p-6" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-bold mb-2">Import Glossary CSV</h2>
        <p className="text-sm text-gray-500 mb-4">
          Required columns: term, definition. Optional: aliases, category, context. Imported rows
          are reviewed before they are added.
        </p>
        <input
          type="file"
          accept=".csv,.tsv,text/csv,text/tab-separated-values"
          aria-label="CSV file"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="text-sm block"
        />
        {err && <div className="text-red-600 text-sm mt-3">{err}</div>}
        <div className="flex justify-end gap-2 mt-5">
          <button type="button" onClick={onClose} className="text-sm px-3 py-1.5">
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={busy}
            className="bg-bioaf-600 text-white text-sm rounded px-4 py-1.5 disabled:opacity-50"
          >
            {busy ? "Importing..." : "Import"}
          </button>
        </div>
      </div>
    </div>
  );
}

interface ExperimentOption {
  id: number;
  name: string;
  project: { id: number; name: string } | null;
}

interface DataSearchItem {
  kind: "file" | "lab_document";
  id: number;
  name: string;
  file_type: string | null;
  size_bytes: number | null;
  updated_at: string;
  href: string;
  experiment_id: number | null;
  source: string | null;
}

function ScanModal({
  onClose,
  onStarted,
}: {
  onClose: () => void;
  onStarted: (job: ScanJob) => void;
}) {
  useDismissOnEscape(true, () => onClose());
  // "topic" was removed (LK-SPEC-D, D1); "experiment" reuses the Experiment
  // Review material, "document" picks a Lab Knowledge document OR a Data & Files
  // file via search.
  const [scanType, setScanType] = useState("experiment");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Experiment source.
  const [experiments, setExperiments] = useState<ExperimentOption[]>([]);
  const [experimentId, setExperimentId] = useState("");

  // Document source: search-and-select across both stores (D2).
  const [docQuery, setDocQuery] = useState("");
  const [docResults, setDocResults] = useState<DataSearchItem[]>([]);
  const [docSelected, setDocSelected] = useState<DataSearchItem | null>(null);

  useEffect(() => {
    api
      .get<{ experiments: ExperimentOption[] }>("/api/experiments?page_size=500")
      .then((d) =>
        setExperiments(
          d.experiments.map((e) => ({
            id: e.id,
            name: e.name,
            project: e.project ? { id: e.project.id, name: e.project.name } : null,
          })),
        ),
      )
      .catch(() => setExperiments([]));
  }, []);

  // Debounced unified document/file search (D2 + D3 share the endpoint).
  useEffect(() => {
    if (scanType !== "document") return;
    const q = docQuery.trim();
    if (!q) {
      setDocResults([]);
      return;
    }
    const handle = setTimeout(() => {
      api
        .get<{ items: DataSearchItem[] }>(`/api/files/search?q=${encodeURIComponent(q)}`)
        .then((d) => setDocResults(d.items))
        .catch(() => setDocResults([]));
    }, 200);
    return () => clearTimeout(handle);
  }, [docQuery, scanType]);

  const scanInputFor = (): string | null => {
    if (scanType === "experiment") return experimentId || null;
    if (scanType === "document") return docSelected ? `${docSelected.kind}:${docSelected.id}` : null;
    return null; // platform_wide
  };

  const canSubmit =
    !busy &&
    (scanType === "platform_wide" ||
      (scanType === "experiment" && !!experimentId) ||
      (scanType === "document" && !!docSelected));

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const job = await api.post<ScanJob>(`${API_BASE}/glossary/scan`, {
        scan_type: scanType,
        scan_input: scanInputFor(),
      });
      onStarted(job);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not start scan");
      setBusy(false);
    }
  };

  const labelFor = (e: ExperimentOption) => (e.project ? `${e.project.name} > ${e.name}` : e.name);

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-lg w-[30rem] p-6" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-bold mb-2">Run AI Glossary Scan</h2>
        <p className="text-sm text-gray-500 mb-4">
          This is an AI scan. It uses your organization&apos;s active LLM provider (the same
          connection as the AI Literature Review and AI pipeline review) to read the selected
          source and propose glossary terms. Nothing is added automatically: every proposed term
          comes back to you for review first.
        </p>
        <div className="space-y-3">
          <select
            value={scanType}
            onChange={(e) => setScanType(e.target.value)}
            aria-label="Scan type"
            className="border rounded px-2 py-1.5 text-sm w-full"
          >
            <option value="experiment">From an experiment</option>
            <option value="document">From a document</option>
            <option value="platform_wide">Platform-wide</option>
          </select>

          {scanType === "experiment" && (
            <select
              value={experimentId}
              onChange={(e) => setExperimentId(e.target.value)}
              aria-label="Experiment"
              className="border rounded px-2 py-1.5 text-sm w-full"
            >
              <option value="">Select an experiment</option>
              {experiments.map((e) => (
                <option key={e.id} value={String(e.id)}>
                  {labelFor(e)}
                </option>
              ))}
            </select>
          )}

          {scanType === "document" && (
            <div>
              <input
                value={docQuery}
                onChange={(e) => {
                  setDocQuery(e.target.value);
                  setDocSelected(null);
                }}
                placeholder="Search Lab Knowledge documents and Data & Files"
                aria-label="Search documents and files"
                className="border rounded px-3 py-1.5 text-sm w-full"
              />
              {docResults.length > 0 && (
                <ul className="mt-2 max-h-48 overflow-y-auto border rounded divide-y">
                  {docResults.map((r) => (
                    <li key={`${r.kind}:${r.id}`}>
                      <button
                        type="button"
                        onClick={() => setDocSelected(r)}
                        className={`w-full text-left px-3 py-2 text-sm hover:bg-gray-50 ${
                          docSelected && docSelected.kind === r.kind && docSelected.id === r.id
                            ? "bg-blue-50"
                            : ""
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate">{r.name}</span>
                          <span className="text-xs shrink-0 rounded px-1.5 py-0.5 bg-gray-100 text-gray-600">
                            {r.kind === "lab_document" ? "Lab Document" : "File"}
                          </span>
                        </div>
                        {(r.file_type || r.experiment_id != null) && (
                          <div className="text-xs text-gray-500 mt-0.5">
                            {[r.file_type, r.experiment_id != null ? `Experiment ${r.experiment_id}` : null]
                              .filter(Boolean)
                              .join(" · ")}
                          </div>
                        )}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {docQuery.trim() && docResults.length === 0 && (
                <p className="text-xs text-gray-500 mt-2">No matching documents or files.</p>
              )}
            </div>
          )}

          <p className="text-xs text-gray-500">
            The scan runs in the background. A banner will show while it is running, and you will be
            notified when the proposed terms are ready to review.
          </p>
          {err && <div className="text-red-600 text-sm">{err}</div>}
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button type="button" onClick={onClose} className="text-sm px-3 py-1.5">
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!canSubmit}
            className="bg-bioaf-600 text-white text-sm rounded px-4 py-1.5 disabled:opacity-50"
          >
            {busy ? "Starting..." : "Start AI Scan"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ReviewPanel({
  jobId,
  onClose,
  onReviewed,
}: {
  jobId: number;
  onClose: () => void;
  onReviewed: () => void;
}) {
  useDismissOnEscape(true, () => onClose());
  const [data, setData] = useState<ProposalListResponse | null>(null);
  const [decisions, setDecisions] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<ProposalListResponse>(`${API_BASE}/glossary/scan/${jobId}/proposals`)
      .then(setData)
      .catch(() => setData(null));
  }, [jobId]);

  const decide = (id: number, decision: string) =>
    setDecisions((prev) => ({ ...prev, [id]: decision }));

  const commit = async (bulk?: "accept" | "reject") => {
    setBusy(true);
    try {
      await api.post(`${API_BASE}/glossary/scan/${jobId}/review`, {
        decisions: Object.entries(decisions).map(([proposal_id, decision]) => ({
          proposal_id: Number(proposal_id),
          decision,
        })),
        accept_all_remaining: bulk === "accept",
        reject_all_remaining: bulk === "reject",
      });
      onReviewed();
    } finally {
      setBusy(false);
    }
  };

  if (!data) {
    return (
      <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
        <div className="bg-white rounded-lg p-6" onClick={(e) => e.stopPropagation()}>
          <p data-testid="review-loading" className="text-gray-500">Loading proposals...</p>
        </div>
      </div>
    );
  }

  const rowClass = (id: number) =>
    decisions[id] === "accepted"
      ? "bg-green-50"
      : decisions[id] === "rejected"
        ? "bg-red-50 line-through"
        : "";

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-white rounded-lg w-[44rem] max-h-[85vh] overflow-y-auto p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-4">
          <h2 className="text-lg font-bold">Review Proposed Terms</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="text-gray-500">
            x
          </button>
        </div>

        <h3 className="font-semibold text-sm mb-2">New Terms ({data.new_terms.length})</h3>
        {data.new_terms.length === 0 ? (
          <p className="text-sm text-gray-500 mb-4">None.</p>
        ) : (
          <ul className="divide-y border rounded mb-5">
            {data.new_terms.map((p) => (
              <li key={p.id} className={`p-3 ${rowClass(p.id)}`}>
                <div className="flex items-baseline justify-between">
                  <span className="font-medium">
                    {p.term}
                    {p.previously_rejected && (
                      <span className="ml-2 text-xs text-amber-700">previously rejected</span>
                    )}
                  </span>
                  <span className="flex gap-2">
                    <button type="button" onClick={() => decide(p.id, "accepted")} className="text-xs text-green-700">
                      Accept
                    </button>
                    <button type="button" onClick={() => decide(p.id, "rejected")} className="text-xs text-red-600">
                      Reject
                    </button>
                  </span>
                </div>
                <p className="text-sm text-gray-700 mt-0.5">{p.proposed_definition}</p>
                {p.source_description && (
                  <p className="text-xs text-gray-500 mt-1">{p.source_description}</p>
                )}
              </li>
            ))}
          </ul>
        )}

        <h3 className="font-semibold text-sm mb-2">Changed Terms ({data.changed_terms.length})</h3>
        {data.changed_terms.length === 0 ? (
          <p className="text-sm text-gray-500 mb-4">None.</p>
        ) : (
          <ul className="divide-y border rounded mb-5">
            {data.changed_terms.map((p) => (
              <li key={p.id} className={`p-3 ${rowClass(p.id)}`}>
                <div className="flex items-baseline justify-between">
                  <span className="font-medium">{p.term}</span>
                  <span className="flex gap-2">
                    <button type="button" onClick={() => decide(p.id, "accepted")} className="text-xs text-green-700">
                      Accept
                    </button>
                    <button
                      type="button"
                      onClick={() => decide(p.id, "kept_existing")}
                      className="text-xs text-gray-600"
                    >
                      Keep Existing
                    </button>
                    <button type="button" onClick={() => decide(p.id, "rejected")} className="text-xs text-red-600">
                      Reject
                    </button>
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-3 mt-1">
                  <div className="text-xs">
                    <span className="text-gray-500">Current</span>
                    <p className="text-gray-600">{p.existing_definition}</p>
                  </div>
                  <div className="text-xs">
                    <span className="text-gray-500">Proposed</span>
                    <p className="text-gray-700">{p.proposed_definition}</p>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}

        <div className="flex justify-end gap-2 border-t pt-4">
          <button type="button" onClick={() => commit("reject")} disabled={busy} className="text-sm px-3 py-1.5">
            Reject All Remaining
          </button>
          <button
            type="button"
            onClick={() => commit()}
            disabled={busy}
            className="border rounded text-sm px-4 py-1.5"
          >
            Apply Decisions
          </button>
          <button
            type="button"
            onClick={() => commit("accept")}
            disabled={busy}
            className="bg-bioaf-600 text-white text-sm rounded px-4 py-1.5 disabled:opacity-50"
          >
            Accept All Remaining
          </button>
        </div>
      </div>
    </div>
  );
}
