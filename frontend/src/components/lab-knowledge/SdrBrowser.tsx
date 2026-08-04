"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import { STATUS_STYLES, statusBadgeClass, statusLabel } from "@/lib/statusStyles";

interface UserSummary {
  id: number;
  name: string | null;
  email: string;
}

interface Category {
  id: number;
  name: string;
}

interface SupersessionLink {
  id: number;
  sdr_number: number;
  title: string;
  status: string;
}

interface Transition {
  id: number;
  from_status: string;
  to_status: string;
  note: string | null;
  transitioned_by: UserSummary | null;
  transitioned_at: string;
}

interface SdrSummary {
  id: number;
  sdr_number: number;
  title: string;
  status: string;
  category: Category | null;
  owner: UserSummary | null;
  trigger_date: string | null;
  created_at: string;
  updated_at: string;
}

interface SdrDetail extends SdrSummary {
  decision: string;
  justification: string;
  created_by: UserSummary | null;
  trigger_warning_sent_at: string | null;
  superseded_by: SupersessionLink | null;
  supersedes: SupersessionLink | null;
  transitions: Transition[];
}

interface SdrListResponse {
  sdrs: SdrSummary[];
  total: number;
  page: number;
  page_size: number;
}

const API_BASE = "/api/lab-knowledge";

// SDR status labels, sourced from the shared status registry so the dropdown
// and transition views stay in sync with the badge styling.
export const STATUS_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(STATUS_STYLES.sdr).map(([value, style]) => [value, style.label ?? value]),
);

export function sdrCode(n: number): string {
  return `SDR-${String(n).padStart(3, "0")}`;
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`text-xs px-2 py-0.5 rounded ${statusBadgeClass("sdr", status)}`}>
      {statusLabel("sdr", status)}
    </span>
  );
}

export function SdrBrowser() {
  const router = useRouter();
  const { canAccess } = usePermissions();
  const canAuthor = canAccess("sdr", "author");
  const canManage = canAccess("sdr", "manage");

  const [sdrs, setSdrs] = useState<SdrSummary[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [sort, setSort] = useState("number");
  const [showHistorical, setShowHistorical] = useState(false);

  const [showCreate, setShowCreate] = useState(false);
  const [showCategories, setShowCategories] = useState(false);

  const fetchSdrs = useCallback(async () => {
    setLoading(true);
    setError(null);
    const params = new URLSearchParams();
    if (query.trim()) params.set("q", query.trim());
    if (statusFilter) params.set("status", statusFilter);
    if (categoryFilter) params.set("category_id", categoryFilter);
    if (sort) params.set("sort", sort);
    if (showHistorical) params.set("include_historical", "true");
    try {
      const data = await api.get<SdrListResponse>(`${API_BASE}/sdrs?${params.toString()}`);
      setSdrs(data.sdrs);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load decision records");
    } finally {
      setLoading(false);
    }
  }, [query, statusFilter, categoryFilter, sort, showHistorical]);

  const fetchCategories = useCallback(async () => {
    try {
      setCategories(await api.get<Category[]>(`${API_BASE}/sdr-categories`));
    } catch {
      /* non-critical */
    }
  }, []);

  useEffect(() => {
    fetchSdrs();
  }, [fetchSdrs]);

  useEffect(() => {
    fetchCategories();
  }, [fetchCategories]);

  // NOTE: deliberately no `if (loading) return ...` early return here. `query` is a
  // dependency of the fetch, so every keystroke sets loading=true; returning early
  // unmounted the whole toolbar, taking the search input (and the caret) with it, so
  // only the first character of a search ever landed. The loading state is rendered
  // in the results region instead, below, and the toolbar stays mounted.
  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3 flex-wrap">
          <input
            type="search"
            placeholder="Search decision records..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="border rounded px-3 py-1.5 text-sm w-64"
            aria-label="Search decision records"
          />
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="border rounded px-2 py-1.5 text-sm"
            aria-label="Filter by status"
          >
            <option value="">All statuses</option>
            {Object.entries(STATUS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            className="border rounded px-2 py-1.5 text-sm"
            aria-label="Filter by category"
          >
            <option value="">All categories</option>
            {categories.map((c) => (
              <option key={c.id} value={String(c.id)}>
                {c.name}
              </option>
            ))}
          </select>
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value)}
            className="border rounded px-2 py-1.5 text-sm"
            aria-label="Sort by"
          >
            <option value="number">Number (newest)</option>
            <option value="title">Title</option>
            <option value="updated">Last updated</option>
          </select>
          <label className="flex items-center gap-1.5 text-sm text-gray-600">
            <input
              type="checkbox"
              checked={showHistorical}
              onChange={(e) => setShowHistorical(e.target.checked)}
            />
            Show superseded/repealed
          </label>
        </div>
        {(canAuthor || canManage) && (
          <div className="flex items-center gap-2">
            {canAuthor && (
              <button
                type="button"
                onClick={() => setShowCreate(true)}
                className="bg-bioaf-600 text-white rounded px-4 py-1.5 text-sm font-medium"
              >
                New SDR
              </button>
            )}
            {canManage && (
              <button
                type="button"
                onClick={() => setShowCategories(true)}
                className="border rounded px-3 py-1.5 text-sm"
              >
                Categories
              </button>
            )}
          </div>
        )}
      </div>

      {error && <div className="text-red-600 text-sm mb-3">{error}</div>}

      {loading ? (
        <div data-testid="sdr-loading" className="text-gray-500 py-12 text-center">
          Loading decision records...
        </div>
      ) : sdrs.length === 0 ? (
        <div className="text-gray-500 py-12 text-center">
          No decision records yet. {canAuthor ? "Create one to capture a scientific decision." : ""}
        </div>
      ) : (
        <table className="w-full text-sm border rounded">
          <thead className="bg-gray-50 text-left text-xs text-gray-500">
            <tr>
              <th className="p-2 w-20">Number</th>
              <th className="p-2">Title</th>
              <th className="p-2 w-36">Category</th>
              <th className="p-2 w-36">Status</th>
              <th className="p-2 w-32">Owner</th>
              <th className="p-2 w-28">Trigger</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {sdrs.map((s) => (
              <tr
                key={s.id}
                onClick={() => router.push(`/lab-knowledge/decision-records/${s.id}`)}
                className="hover:bg-gray-50 cursor-pointer"
              >
                <td className="p-2 font-mono text-xs">{sdrCode(s.sdr_number)}</td>
                <td className="p-2 font-medium">{s.title}</td>
                <td className="p-2 text-gray-600">{s.category?.name ?? "-"}</td>
                <td className="p-2">
                  <StatusBadge status={s.status} />
                </td>
                <td className="p-2 text-gray-600">{s.owner?.name ?? s.owner?.email ?? "-"}</td>
                <td className="p-2 text-gray-600">{s.trigger_date ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {showCreate && (
        <CreateSdrModal
          categories={categories}
          onClose={() => setShowCreate(false)}
          onSaved={() => {
            setShowCreate(false);
            fetchSdrs();
          }}
        />
      )}

      {showCategories && (
        <CategoryManagerModal
          categories={categories}
          onClose={() => setShowCategories(false)}
          onChanged={() => fetchCategories()}
        />
      )}
    </div>
  );
}

export function SdrDetailView({
  sdrId,
  onDeleted,
}: {
  sdrId: number;
  onDeleted: () => void;
}) {
  const { canAccess } = usePermissions();
  const canAuthor = canAccess("sdr", "author");
  const canManage = canAccess("sdr", "manage");

  const [sdr, setSdr] = useState<SdrDetail | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setSdr(await api.get<SdrDetail>(`${API_BASE}/sdrs/${sdrId}`));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load SDR");
    }
  }, [sdrId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    api
      .get<Category[]>(`${API_BASE}/sdr-categories`)
      .then(setCategories)
      .catch(() => setCategories([]));
  }, []);

  const refresh = async () => {
    await load();
  };

  const transition = async (to: string, extra?: Record<string, unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await api.post(`${API_BASE}/sdrs/${sdrId}/transition`, { to_status: to, ...extra });
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Transition failed");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!window.confirm("Permanently delete this decision record?")) return;
    await api.delete(`${API_BASE}/sdrs/${sdrId}`);
    onDeleted();
  };

  if (!sdr) {
    return (
      <p data-testid="sdr-detail-loading" className="text-gray-500">
        {err ?? "Loading..."}
      </p>
    );
  }

  const isTerminal = sdr.status === "superseded" || sdr.status === "repealed";

  return (
    <div className="bg-white rounded shadow p-6 max-w-3xl">
      <div className="mb-3">
        <span className="font-mono text-xs text-gray-400">{sdrCode(sdr.sdr_number)}</span>
        <h2 className="text-xl font-bold">{sdr.title}</h2>
        <div className="mt-1">
          <StatusBadge status={sdr.status} />
          {sdr.category && <span className="ml-2 text-xs text-gray-500">{sdr.category.name}</span>}
        </div>
      </div>

      {err && <div className="text-red-600 text-sm mb-3">{err}</div>}

      {editing ? (
          <EditSdrForm
            sdr={sdr}
            categories={categories}
            onCancel={() => setEditing(false)}
            onSaved={async () => {
              setEditing(false);
              await refresh();
            }}
          />
        ) : (
          <>
            <section className="mb-4">
              <h3 className="text-xs uppercase text-gray-400 mb-1">Decision</h3>
              <p className="text-sm text-gray-800 whitespace-pre-wrap">{sdr.decision}</p>
            </section>
            <section className="mb-4">
              <h3 className="text-xs uppercase text-gray-400 mb-1">Justification</h3>
              <p className="text-sm text-gray-800 whitespace-pre-wrap">{sdr.justification}</p>
            </section>

            <dl className="text-xs text-gray-500 space-y-1 mb-4">
              <div>Owner: {sdr.owner?.name ?? sdr.owner?.email ?? "-"}</div>
              <div>Created by: {sdr.created_by?.name ?? sdr.created_by?.email ?? "-"}</div>
              {sdr.trigger_date && <div>Re-assessment trigger: {sdr.trigger_date}</div>}
            </dl>

            {(sdr.superseded_by || sdr.supersedes) && (
              <section className="mb-4 text-sm">
                {sdr.superseded_by && (
                  <p className="text-gray-600">
                    Superseded by{" "}
                    <span className="font-mono text-xs">{sdrCode(sdr.superseded_by.sdr_number)}</span>:{" "}
                    {sdr.superseded_by.title}
                  </p>
                )}
                {sdr.supersedes && (
                  <p className="text-gray-600">
                    Supersedes{" "}
                    <span className="font-mono text-xs">{sdrCode(sdr.supersedes.sdr_number)}</span>:{" "}
                    {sdr.supersedes.title}
                  </p>
                )}
              </section>
            )}

            {!isTerminal && (canAuthor || canManage) && (
              <SdrActions
                sdr={sdr}
                canAuthor={canAuthor}
                canManage={canManage}
                busy={busy}
                onTransition={transition}
                onEdit={() => setEditing(true)}
                onDelete={remove}
                onChanged={refresh}
                setErr={setErr}
              />
            )}

            <section className="border-t pt-4 mt-4">
              <h3 className="text-xs uppercase text-gray-400 mb-2">Status History</h3>
              <ul className="space-y-2">
                {sdr.transitions.map((t) => (
                  <li key={t.id} className="text-xs text-gray-600">
                    <span className="font-medium">
                      {STATUS_LABELS[t.from_status] ?? t.from_status} -&gt;{" "}
                      {STATUS_LABELS[t.to_status] ?? t.to_status}
                    </span>{" "}
                    <span className="text-gray-400">
                      {new Date(t.transitioned_at).toLocaleString()} ·{" "}
                      {t.transitioned_by?.name ?? t.transitioned_by?.email ?? "System"}
                    </span>
                    {t.note && <p className="text-gray-500 mt-0.5">{t.note}</p>}
                  </li>
                ))}
              </ul>
            </section>
          </>
        )}
    </div>
  );
}

function SdrActions({
  sdr,
  canAuthor,
  canManage,
  busy,
  onTransition,
  onEdit,
  onDelete,
  onChanged,
  setErr,
}: {
  sdr: SdrDetail;
  canAuthor: boolean;
  canManage: boolean;
  busy: boolean;
  onTransition: (to: string, extra?: Record<string, unknown>) => Promise<void>;
  onEdit: () => void;
  onDelete: () => void;
  onChanged: () => Promise<void>;
  setErr: (s: string | null) => void;
}) {
  const [showSupersede, setShowSupersede] = useState(false);
  const [showUphold, setShowUphold] = useState(false);
  const [showOwner, setShowOwner] = useState(false);

  return (
    <div className="border-t pt-4 mt-2 flex flex-wrap items-center gap-3">
      {sdr.status === "draft" && (canAuthor || canManage) && (
        <button
          type="button"
          disabled={busy}
          onClick={() => onTransition("active")}
          className="text-sm text-green-700"
        >
          Activate
        </button>
      )}
      {sdr.status === "active" && (canAuthor || canManage) && (
        <button
          type="button"
          disabled={busy}
          onClick={() => onTransition("flagged_for_review")}
          className="text-sm text-amber-700"
        >
          Flag for Review
        </button>
      )}
      {sdr.status === "flagged_for_review" && (canAuthor || canManage) && (
        <button
          type="button"
          disabled={busy}
          onClick={() => setShowUphold(true)}
          className="text-sm text-green-700"
        >
          Uphold Decision
        </button>
      )}
      {(sdr.status === "active" || sdr.status === "flagged_for_review") && canManage && (
        <>
          <button
            type="button"
            disabled={busy}
            onClick={() => setShowSupersede(true)}
            className="text-sm text-blue-700"
          >
            Supersede
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onTransition("repealed")}
            className="text-sm text-red-600"
          >
            Repeal
          </button>
        </>
      )}
      {(canAuthor || canManage) && (
        <button type="button" onClick={onEdit} className="text-sm text-bioaf-600">
          Edit
        </button>
      )}
      {canManage && (
        <button type="button" onClick={() => setShowOwner(true)} className="text-sm text-bioaf-600">
          Reassign Owner
        </button>
      )}
      {canManage && (
        <button type="button" onClick={onDelete} className="text-sm text-red-600">
          Delete
        </button>
      )}

      {showUphold && (
        <UpholdModal
          onClose={() => setShowUphold(false)}
          onConfirm={async (note) => {
            setShowUphold(false);
            await onTransition("active", { note });
          }}
        />
      )}
      {showSupersede && (
        <SupersedeModal
          sdr={sdr}
          onClose={() => setShowSupersede(false)}
          onConfirm={async (targetId) => {
            setShowSupersede(false);
            await onTransition("superseded", { superseded_by_sdr_id: targetId });
          }}
        />
      )}
      {showOwner && (
        <OwnerModal
          sdrId={sdr.id}
          onClose={() => setShowOwner(false)}
          onSaved={async () => {
            setShowOwner(false);
            setErr(null);
            await onChanged();
          }}
          setErr={setErr}
        />
      )}
    </div>
  );
}

function ModalShell({
  title,
  children,
  onClose,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-[60]"
      onClick={onClose}
    >
      <div className="bg-white rounded-lg w-[30rem] p-6" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-bold mb-4">{title}</h2>
        {children}
      </div>
    </div>
  );
}

function CreateSdrModal({
  categories,
  onClose,
  onSaved,
}: {
  categories: Category[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [decision, setDecision] = useState("");
  const [justification, setJustification] = useState("");
  const [triggerDate, setTriggerDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    if (!title.trim() || !decision.trim() || !justification.trim()) {
      setErr("Title, decision, and justification are required.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await api.post(`${API_BASE}/sdrs`, {
        title: title.trim(),
        category_id: categoryId ? Number(categoryId) : null,
        decision: decision.trim(),
        justification: justification.trim(),
        trigger_date: triggerDate || null,
      });
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Save failed");
      setBusy(false);
    }
  };

  return (
    <ModalShell title="New Decision Record" onClose={onClose}>
      <div className="space-y-3">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Title"
          aria-label="Title"
          maxLength={200}
          className="border rounded px-3 py-1.5 text-sm w-full"
        />
        <select
          value={categoryId}
          onChange={(e) => setCategoryId(e.target.value)}
          aria-label="Category"
          className="border rounded px-2 py-1.5 text-sm w-full"
        >
          <option value="">No category</option>
          {categories.map((c) => (
            <option key={c.id} value={String(c.id)}>
              {c.name}
            </option>
          ))}
        </select>
        <textarea
          value={decision}
          onChange={(e) => setDecision(e.target.value)}
          placeholder="Decision (what was decided)"
          aria-label="Decision"
          className="border rounded px-3 py-1.5 text-sm w-full"
        />
        <textarea
          value={justification}
          onChange={(e) => setJustification(e.target.value)}
          placeholder="Justification (why this decision was made)"
          aria-label="Justification"
          className="border rounded px-3 py-1.5 text-sm w-full"
        />
        <label className="block text-xs text-gray-500">
          Re-assessment trigger date (optional)
          <input
            type="date"
            value={triggerDate}
            onChange={(e) => setTriggerDate(e.target.value)}
            aria-label="Trigger date"
            className="border rounded px-3 py-1.5 text-sm w-full mt-1"
          />
        </label>
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
          {busy ? "Saving..." : "Create"}
        </button>
      </div>
    </ModalShell>
  );
}

function EditSdrForm({
  sdr,
  categories,
  onCancel,
  onSaved,
}: {
  sdr: SdrDetail;
  categories: Category[];
  onCancel: () => void;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState(sdr.title);
  const [categoryId, setCategoryId] = useState(sdr.category ? String(sdr.category.id) : "");
  const [decision, setDecision] = useState(sdr.decision);
  const [justification, setJustification] = useState(sdr.justification);
  const [triggerDate, setTriggerDate] = useState(sdr.trigger_date ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.patch(`${API_BASE}/sdrs/${sdr.id}`, {
        title: title.trim(),
        category_id: categoryId ? Number(categoryId) : null,
        decision,
        justification,
        trigger_date: triggerDate || null,
      });
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Save failed");
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        aria-label="Title"
        maxLength={200}
        className="border rounded px-3 py-1.5 text-sm w-full"
      />
      <select
        value={categoryId}
        onChange={(e) => setCategoryId(e.target.value)}
        aria-label="Category"
        className="border rounded px-2 py-1.5 text-sm w-full"
      >
        <option value="">No category</option>
        {categories.map((c) => (
          <option key={c.id} value={String(c.id)}>
            {c.name}
          </option>
        ))}
      </select>
      <textarea
        value={decision}
        onChange={(e) => setDecision(e.target.value)}
        aria-label="Decision"
        className="border rounded px-3 py-1.5 text-sm w-full"
      />
      <textarea
        value={justification}
        onChange={(e) => setJustification(e.target.value)}
        aria-label="Justification"
        className="border rounded px-3 py-1.5 text-sm w-full"
      />
      <label className="block text-xs text-gray-500">
        Re-assessment trigger date
        <input
          type="date"
          value={triggerDate}
          onChange={(e) => setTriggerDate(e.target.value)}
          aria-label="Trigger date"
          className="border rounded px-3 py-1.5 text-sm w-full mt-1"
        />
      </label>
      {err && <div className="text-red-600 text-sm">{err}</div>}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={save}
          disabled={busy}
          className="bg-bioaf-600 text-white text-sm rounded px-4 py-1.5 disabled:opacity-50"
        >
          Save
        </button>
        <button type="button" onClick={onCancel} className="text-sm px-3 py-1.5">
          Cancel
        </button>
      </div>
    </div>
  );
}

function UpholdModal({
  onClose,
  onConfirm,
}: {
  onClose: () => void;
  onConfirm: (note: string) => void;
}) {
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <ModalShell title="Uphold Decision" onClose={onClose}>
      <p className="text-sm text-gray-500 mb-3">
        Record why the decision still holds. A note is required.
      </p>
      <textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        aria-label="Upheld note"
        placeholder="Decision upheld because..."
        className="border rounded px-3 py-1.5 text-sm w-full"
      />
      {err && <div className="text-red-600 text-sm mt-2">{err}</div>}
      <div className="flex justify-end gap-2 mt-4">
        <button type="button" onClick={onClose} className="text-sm px-3 py-1.5">
          Cancel
        </button>
        <button
          type="button"
          onClick={() => (note.trim() ? onConfirm(note.trim()) : setErr("A note is required."))}
          className="bg-bioaf-600 text-white text-sm rounded px-4 py-1.5"
        >
          Uphold
        </button>
      </div>
    </ModalShell>
  );
}

function SupersedeModal({
  sdr,
  onClose,
  onConfirm,
}: {
  sdr: SdrDetail;
  onClose: () => void;
  onConfirm: (targetId: number) => void;
}) {
  const [candidates, setCandidates] = useState<SdrSummary[]>([]);
  const [targetId, setTargetId] = useState("");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<SdrListResponse>(`${API_BASE}/sdrs?page_size=200`)
      .then((d) => setCandidates(d.sdrs.filter((c) => c.id !== sdr.id)))
      .catch(() => setCandidates([]));
  }, [sdr.id]);

  return (
    <ModalShell title="Supersede Decision Record" onClose={onClose}>
      <p className="text-sm text-gray-500 mb-3">
        Choose the SDR that replaces {sdrCode(sdr.sdr_number)}. The link is recorded on both records.
      </p>
      <select
        value={targetId}
        onChange={(e) => setTargetId(e.target.value)}
        aria-label="Superseding SDR"
        className="border rounded px-2 py-1.5 text-sm w-full"
      >
        <option value="">Select superseding SDR...</option>
        {candidates.map((c) => (
          <option key={c.id} value={String(c.id)}>
            {sdrCode(c.sdr_number)}: {c.title}
          </option>
        ))}
      </select>
      {err && <div className="text-red-600 text-sm mt-2">{err}</div>}
      <div className="flex justify-end gap-2 mt-4">
        <button type="button" onClick={onClose} className="text-sm px-3 py-1.5">
          Cancel
        </button>
        <button
          type="button"
          onClick={() =>
            targetId ? onConfirm(Number(targetId)) : setErr("Select a superseding SDR.")
          }
          className="bg-bioaf-600 text-white text-sm rounded px-4 py-1.5"
        >
          Supersede
        </button>
      </div>
    </ModalShell>
  );
}

function OwnerModal({
  sdrId,
  onClose,
  onSaved,
  setErr,
}: {
  sdrId: number;
  onClose: () => void;
  onSaved: () => void;
  setErr: (s: string | null) => void;
}) {
  const [ownerUserId, setOwnerUserId] = useState("");
  const [localErr, setLocalErr] = useState<string | null>(null);

  const save = async () => {
    if (!ownerUserId.trim() || !/^\d+$/.test(ownerUserId.trim())) {
      setLocalErr("Enter a numeric user ID.");
      return;
    }
    try {
      await api.patch(`${API_BASE}/sdrs/${sdrId}/owner`, { owner_user_id: Number(ownerUserId) });
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Reassign failed");
    }
  };

  return (
    <ModalShell title="Reassign Owner" onClose={onClose}>
      <p className="text-sm text-gray-500 mb-3">
        The new owner receives a notification and becomes the steward for re-assessment triggers.
      </p>
      <input
        value={ownerUserId}
        onChange={(e) => setOwnerUserId(e.target.value)}
        placeholder="New owner user ID"
        aria-label="New owner user ID"
        className="border rounded px-3 py-1.5 text-sm w-full"
      />
      {localErr && <div className="text-red-600 text-sm mt-2">{localErr}</div>}
      <div className="flex justify-end gap-2 mt-4">
        <button type="button" onClick={onClose} className="text-sm px-3 py-1.5">
          Cancel
        </button>
        <button
          type="button"
          onClick={save}
          className="bg-bioaf-600 text-white text-sm rounded px-4 py-1.5"
        >
          Reassign
        </button>
      </div>
    </ModalShell>
  );
}

function CategoryManagerModal({
  categories,
  onClose,
  onChanged,
}: {
  categories: Category[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const add = async () => {
    if (!name.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await api.post(`${API_BASE}/sdr-categories`, { name: name.trim() });
      setName("");
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Add failed");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: number) => {
    setErr(null);
    try {
      await api.delete(`${API_BASE}/sdr-categories/${id}`);
      onChanged();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Delete failed";
      setErr(msg.includes("409") ? "This category is in use; reassign its SDRs first." : msg);
    }
  };

  return (
    <ModalShell title="SDR Categories" onClose={onClose}>
      <ul className="divide-y border rounded mb-3">
        {categories.length === 0 ? (
          <li className="p-2 text-sm text-gray-400">No categories.</li>
        ) : (
          categories.map((c) => (
            <li key={c.id} className="p-2 flex items-center justify-between text-sm">
              <span>{c.name}</span>
              <button
                type="button"
                onClick={() => remove(c.id)}
                className="text-xs text-red-600"
                aria-label={`Delete ${c.name}`}
              >
                Delete
              </button>
            </li>
          ))
        )}
      </ul>
      <div className="flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="New category name"
          aria-label="New category name"
          className="border rounded px-3 py-1.5 text-sm flex-1"
        />
        <button
          type="button"
          onClick={add}
          disabled={busy}
          className="bg-bioaf-600 text-white text-sm rounded px-4 py-1.5 disabled:opacity-50"
        >
          Add
        </button>
      </div>
      {err && <div className="text-red-600 text-sm mt-2">{err}</div>}
      <div className="flex justify-end mt-4">
        <button type="button" onClick={onClose} className="text-sm px-3 py-1.5">
          Close
        </button>
      </div>
    </ModalShell>
  );
}
