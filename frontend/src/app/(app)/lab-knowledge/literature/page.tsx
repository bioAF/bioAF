"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Breadcrumb } from "@/components/layout/Breadcrumb";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { AssociatePaperModal } from "@/components/literature/AssociatePaperModal";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { ErrorState } from "@/components/shared/ErrorState";
import { api } from "@/lib/api";
import { statusBadgeClass } from "@/lib/statusStyles";
import { isAuthenticated, getCurrentUser } from "@/lib/auth";
import {
  cleanText,
  formatAssociation,
  formatAuthors,
  formatYear,
  literature,
  type Paper,
  type PaperFilters,
  type Provenance,
  type ReadingStatusValue,
} from "@/lib/literature";
import type {
  ExperimentListResponse,
  ProjectListResponse,
} from "@/lib/types";

type StatusFlag = "active" | "dismissed" | "unread" | "reading" | "read";

const PROVENANCE_LABELS: Record<Provenance, string> = {
  user_upload: "Uploaded",
  source_search: "From search",
  lit_review_run: "AI Lit Review",
};

const READING_LABELS: Record<ReadingStatusValue, string> = {
  unread: "Unread",
  reading: "Reading",
  read: "Read",
};

const DEFAULT_TOGGLES: Record<StatusFlag, boolean> = {
  active: true,
  dismissed: false,
  unread: true,
  reading: true,
  read: true,
};

export default function LiteratureLibraryPage() {
  const router = useRouter();
  const user = getCurrentUser();
  const canUpload =
    user?.role_name === "admin" ||
    user?.role_name === "comp_bio" ||
    user?.role_name === "bench";
  const isAdmin = user?.role_name === "admin";

  const [papers, setPapers] = useState<Paper[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [provenance, setProvenance] = useState<Provenance | "">("");
  const [toggles, setToggles] = useState<Record<StatusFlag, boolean>>(
    DEFAULT_TOGGLES,
  );
  const [filterProjectId, setFilterProjectId] = useState("");
  const [filterExperimentId, setFilterExperimentId] = useState("");
  const [sort, setSort] = useState<"added" | "title" | "year" | "comments">(
    "added",
  );
  const [projects, setProjects] = useState<{ id: number; name: string }[]>([]);
  const [experiments, setExperiments] = useState<{ id: number; name: string }[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  // Associate modal state (the picker lives in AssociatePaperModal).
  const [linkingPaperIds, setLinkingPaperIds] = useState<number[]>([]);
  const [dismissBusy, setDismissBusy] = useState(false);
  const [confirmingBulkDismiss, setConfirmingBulkDismiss] = useState(false);

  useEffect(() => {
    if (!isAuthenticated()) return;
    api
      .get<ProjectListResponse>("/api/projects?page_size=100")
      .then((data) =>
        setProjects(data.projects.map((p) => ({ id: p.id, name: p.name }))),
      )
      .catch(() => {});
    api
      .get<ExperimentListResponse>("/api/experiments?page_size=100")
      .then((data) =>
        setExperiments(data.experiments.map((e) => ({ id: e.id, name: e.name }))),
      )
      .catch(() => {});
  }, []);

  const readingSelection = useMemo<ReadingStatusValue[]>(() => {
    const out: ReadingStatusValue[] = [];
    if (toggles.unread) out.push("unread");
    if (toggles.reading) out.push("reading");
    if (toggles.read) out.push("read");
    return out;
  }, [toggles]);

  useEffect(() => {
    if (!isAuthenticated()) return;
    setLoading(true);
    const filters: PaperFilters = {
      sort,
      page: 1,
      page_size: 100,
      include_active: toggles.active,
      include_dismissed: toggles.dismissed,
      reading_status: readingSelection,
    };
    if (provenance) filters.provenance = provenance;
    if (filterProjectId) filters.project_id = Number(filterProjectId);
    if (filterExperimentId) filters.experiment_id = Number(filterExperimentId);
    literature
      .listPapers(filters)
      .then((data) => {
        setPapers(data.items);
        setTotal(data.total);
        setSelectedIds(new Set());
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load the library."))
      .finally(() => setLoading(false));
  }, [
    provenance,
    sort,
    toggles,
    filterProjectId,
    filterExperimentId,
    readingSelection,
  ]);

  const refresh = () => {
    const filters: PaperFilters = {
      sort,
      page: 1,
      page_size: 100,
      include_active: toggles.active,
      include_dismissed: toggles.dismissed,
      reading_status: readingSelection,
    };
    if (provenance) filters.provenance = provenance;
    if (filterProjectId) filters.project_id = Number(filterProjectId);
    if (filterExperimentId) filters.experiment_id = Number(filterExperimentId);
    setLoading(true);
    literature
      .listPapers(filters)
      .then((data) => {
        setPapers(data.items);
        setTotal(data.total);
        setSelectedIds(new Set());
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load the library."))
      .finally(() => setLoading(false));
  };

  const toggleSelect = (id: number) =>
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const toggleSelectAll = () => {
    if (selectedIds.size === papers.length) setSelectedIds(new Set());
    else setSelectedIds(new Set(papers.map((p) => p.id)));
  };

  const openAssociate = (paperIds: number[]) => {
    if (paperIds.length === 0) return;
    setLinkingPaperIds(paperIds);
  };

  const closeAssociate = () => setLinkingPaperIds([]);

  const performBulkDismiss = async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    setDismissBusy(true);
    try {
      await literature.bulkDismiss(ids);
      setConfirmingBulkDismiss(false);
      setSelectedIds(new Set());
      refresh();
    } finally {
      setDismissBusy(false);
    }
  };

  const setFlag = (flag: StatusFlag, value: boolean) =>
    setToggles((prev) => ({ ...prev, [flag]: value }));

  const renderAssociations = (paper: Paper) => {
    if (!paper.associations || paper.associations.length === 0) {
      return (
        <button
          onClick={(e) => {
            e.stopPropagation();
            openAssociate([paper.id]);
          }}
          className="text-bioaf-600 hover:underline text-xs"
        >
          Associate
        </button>
      );
    }
    return (
      <div className="flex flex-wrap gap-1 items-center">
        {paper.associations.map((a) => (
          <span
            key={a.id}
            className="px-1.5 py-0.5 text-xs rounded bg-indigo-50 text-indigo-800"
          >
            {formatAssociation(a)}
          </span>
        ))}
        <button
          onClick={(e) => {
            e.stopPropagation();
            openAssociate([paper.id]);
          }}
          className="text-bioaf-600 hover:underline text-xs"
        >
          +
        </button>
      </div>
    );
  };

  return (
    <>
      <Breadcrumb />
      <main className="flex-1 overflow-y-auto p-6">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold">Literature Library</h1>
          <div className="flex gap-2">
            <button
              onClick={() => router.push("/lab-knowledge/literature/recommendations")}
              className="border border-gray-300 px-4 py-2 rounded-md hover:bg-gray-50"
            >
              AI Literature Review
            </button>
            <button
              onClick={() => router.push("/lab-knowledge/literature/searches")}
              className="border border-gray-300 px-4 py-2 rounded-md hover:bg-gray-50"
            >
              Searches
            </button>
            <button
              onClick={() => router.push("/lab-knowledge/literature/sources")}
              className="border border-gray-300 px-4 py-2 rounded-md hover:bg-gray-50"
            >
              Sources
            </button>
            {canUpload && (
              <button
                onClick={() => router.push("/lab-knowledge/literature/upload")}
                className="bg-bioaf-600 text-white px-4 py-2 rounded-md hover:bg-bioaf-700"
              >
                Upload paper
              </button>
            )}
          </div>
        </div>

        <div className="flex flex-wrap gap-3 mb-4 items-end">
          <div>
            <label className="block text-xs text-gray-600 mb-1">Project</label>
            <select
              value={filterProjectId}
              onChange={(e) => {
                setFilterProjectId(e.target.value);
                setFilterExperimentId("");
              }}
              className="border border-gray-300 rounded px-3 py-2"
            >
              <option value="">All projects</option>
              {projects.map((p) => (
                <option key={p.id} value={String(p.id)}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-600 mb-1">Experiment</label>
            <select
              value={filterExperimentId}
              onChange={(e) => setFilterExperimentId(e.target.value)}
              className="border border-gray-300 rounded px-3 py-2"
            >
              <option value="">All experiments</option>
              {experiments
                .filter((e) => {
                  if (!filterProjectId) return true;
                  return projects.some(
                    (p) => String(p.id) === filterProjectId,
                  );
                })
                .map((e) => (
                  <option key={e.id} value={String(e.id)}>
                    {e.name}
                  </option>
                ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-600 mb-1">Provenance</label>
            <select
              value={provenance}
              onChange={(e) =>
                setProvenance(e.target.value as Provenance | "")
              }
              className="border border-gray-300 rounded px-3 py-2"
            >
              <option value="">All</option>
              <option value="user_upload">Uploaded by humans</option>
              <option value="source_search">From searches</option>
              <option value="lit_review_run">From AI Lit Review</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-600 mb-1">Sort</label>
            <select
              value={sort}
              onChange={(e) =>
                setSort(
                  e.target.value as
                    | "added"
                    | "title"
                    | "year"
                    | "comments",
                )
              }
              className="border border-gray-300 rounded px-3 py-2"
            >
              <option value="added">Date added</option>
              <option value="title">Title</option>
              <option value="year">Year</option>
              <option value="comments">Comment count</option>
            </select>
          </div>
          <span className="ml-auto text-sm text-gray-500 self-center">
            {total} papers
          </span>
        </div>

        <div className="flex flex-wrap gap-2 mb-4 text-xs">
          <span className="text-gray-500 self-center">Show:</span>
          {(["active", "dismissed"] as StatusFlag[]).map((f) => (
            <button
              key={f}
              onClick={() => setFlag(f, !toggles[f])}
              className={`px-2 py-1 rounded border ${
                toggles[f]
                  ? "bg-bioaf-600 text-white border-bioaf-600"
                  : "bg-white text-gray-600 border-gray-300"
              }`}
            >
              {f === "active" ? "Active" : "Dismissed"}
            </button>
          ))}
          <span className="text-gray-300 self-center">|</span>
          {(["unread", "reading", "read"] as StatusFlag[]).map((f) => (
            <button
              key={f}
              onClick={() => setFlag(f, !toggles[f])}
              className={`px-2 py-1 rounded border ${
                toggles[f]
                  ? "bg-bioaf-600 text-white border-bioaf-600"
                  : "bg-white text-gray-600 border-gray-300"
              }`}
            >
              {f === "unread"
                ? "Unread"
                : f === "reading"
                  ? "Reading"
                  : "Read"}
            </button>
          ))}
        </div>

        {selectedIds.size > 0 && (
          <div className="bg-bioaf-50 border border-bioaf-200 rounded-md p-3 mb-4 flex items-center gap-3">
            <span className="text-sm text-gray-700">
              {selectedIds.size} selected
            </span>
            <button
              onClick={() => openAssociate(Array.from(selectedIds))}
              className="px-3 py-1.5 bg-bioaf-600 text-white rounded-md text-sm hover:bg-bioaf-700"
            >
              Associate
            </button>
            <button
              onClick={() => setConfirmingBulkDismiss(true)}
              disabled={dismissBusy}
              className="px-3 py-1.5 bg-amber-600 text-white rounded-md text-sm hover:bg-amber-700 disabled:opacity-50"
            >
              {dismissBusy ? "Dismissing..." : "Dismiss"}
            </button>
            <button
              onClick={() => setSelectedIds(new Set())}
              className="text-gray-500 hover:text-gray-700 text-sm"
            >
              Clear
            </button>
          </div>
        )}

        {loading ? (
          <LoadingSpinner />
        ) : error ? (
          <ErrorState
            message="Couldn't load the library."
            details={error}
            onRetry={refresh}
          />
        ) : papers.length === 0 ? (
          <div className="border border-dashed border-gray-300 rounded p-12 text-center text-gray-500">
            No papers match these filters. Use Upload, Search, or run a Lit
            Review for an experiment to populate the library.
          </div>
        ) : (
          <div className="bg-white rounded-lg shadow overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 w-10">
                    <input
                      type="checkbox"
                      checked={
                        papers.length > 0 &&
                        selectedIds.size === papers.length
                      }
                      onChange={toggleSelectAll}
                    />
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Title
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase w-40">
                    Authors
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase w-16">
                    Year
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase w-32">
                    Source
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase w-44">
                    Flags
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase w-16">
                    Comments
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {papers.map((p) => (
                  <tr
                    key={p.id}
                    className="hover:bg-gray-50 cursor-pointer align-top"
                    onClick={() =>
                      router.push(`/lab-knowledge/literature/papers/${p.id}`)
                    }
                  >
                    <td
                      className="px-4 py-3"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <input
                        type="checkbox"
                        checked={selectedIds.has(p.id)}
                        onChange={() => toggleSelect(p.id)}
                      />
                    </td>
                    <td className="px-4 py-3 text-sm">
                      <div className="font-medium text-bioaf-700">
                        {cleanText(p.title)}
                      </div>
                      {p.journal && (
                        <div className="text-xs text-gray-500 mt-0.5">
                          {cleanText(p.journal)}
                        </div>
                      )}
                      <div className="text-xs mt-1.5">
                        {renderAssociations(p)}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-600">
                      {formatAuthors(p.authors)}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-600">
                      {formatYear(p.publication_date)}
                    </td>
                    <td className="px-4 py-3 text-xs">
                      <span
                        className={`inline-block px-2 py-0.5 rounded ${statusBadgeClass("literatureProvenance", p.provenance)}`}
                      >
                        {PROVENANCE_LABELS[p.provenance]}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-xs">
                      <div className="flex flex-wrap gap-1">
                        {p.reading_status && (
                          <span
                            className={`px-1.5 py-0.5 rounded ${statusBadgeClass("literatureReading", p.reading_status)}`}
                          >
                            {READING_LABELS[p.reading_status]}
                          </span>
                        )}
                        {p.dismissed && (
                          <span className="px-1.5 py-0.5 rounded bg-red-100 text-red-700">
                            Dismissed
                          </span>
                        )}
                        {p.has_full_text && (
                          <span className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">
                            Full text
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-600">
                      {p.comment_count}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <AssociatePaperModal
          paperIds={linkingPaperIds}
          onClose={closeAssociate}
          onAssociated={refresh}
        />
        <ConfirmDialog
          open={confirmingBulkDismiss}
          title="Dismiss papers"
          message={`Dismiss ${selectedIds.size} ${selectedIds.size === 1 ? "paper" : "papers"}? They leave your active Library and are excluded from future AI Literature Review. An admin can reverse this later.`}
          confirmLabel="Dismiss"
          variant="danger"
          busy={dismissBusy}
          onConfirm={performBulkDismiss}
          onCancel={() => setConfirmingBulkDismiss(false)}
        />
      </main>
    </>
  );
}
