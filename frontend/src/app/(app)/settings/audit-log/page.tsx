"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Breadcrumb } from "@/components/layout/Breadcrumb";
import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { useToast } from "@/components/shared/Toast";
import { ErrorState } from "@/components/shared/ErrorState";
import { StaleNotice } from "@/components/shared/StaleNotice";
import { useResource } from "@/hooks/useResource";

interface AuditUser {
  id: number;
  email: string;
  name: string | null;
}

interface AuditEntry {
  id: number;
  timestamp: string;
  user: AuditUser | null;
  entity_type: string;
  entity_id: number;
  action: string;
  details: Record<string, unknown> | null;
  previous_value: Record<string, unknown> | null;
}

interface AuditFilters {
  entityType: string;
  action: string;
  startDate: string;
  endDate: string;
}

/** The filter part of a list or export query, so both read the same entries. */
function auditFilterQuery(filters: AuditFilters): string {
  let query = "";
  if (filters.entityType) query += `&entity_type=${filters.entityType}`;
  if (filters.action) query += `&action=${filters.action}`;
  if (filters.startDate) query += `&start_date=${filters.startDate}`;
  if (filters.endDate) query += `&end_date=${filters.endDate}`;
  return query;
}

export default function AuditLogPage() {
  const toast = useToast();
  const router = useRouter();
  const { canAccess, loading: permLoading } = usePermissions();
  // The last successful total, which keeps pagination in place while the next page loads.
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [entityType, setEntityType] = useState("");
  const [action, setAction] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [exporting, setExporting] = useState(false);
  const pageSize = 25;
  const filters = useMemo(
    () => ({ entityType, action, startDate, endDate }),
    [entityType, action, startDate, endDate],
  );

  useEffect(() => {
    if (permLoading) return;
    if (!canAccess("audit_log", "view")) { router.push("/dashboard"); return; }
  }, [router, permLoading, canAccess]);

  // Keyed by page and filters (the same filter query the export uses): only the current key's
  // answer is shown, so a slow answer for an earlier page or filter cannot replace it.
  const listPath = `/api/audit?page=${page}&page_size=${pageSize}${auditFilterQuery(filters)}`;
  const list = useResource(
    listPath,
    useCallback(
      (signal: AbortSignal) => api.get<{ entries: AuditEntry[]; total: number }>(listPath, { signal }),
      [listPath],
    ),
    {
      onSuccess: (data) => setTotal(data.total),
      onError: (e) => logError("loading the audit log", e),
    },
  );
  const entries: AuditEntry[] = list.data?.entries ?? [];
  const loading = list.loading;
  const loadError = list.error ? loadFailureMessage("The audit log") : null;
  const load = list.refresh;

  const totalPages = Math.ceil(total / pageSize);

  async function handleExport() {
    setExporting(true);
    try {
      await api.download(`/api/audit/export?format=csv${auditFilterQuery(filters)}`, "GET", undefined, {
        filename: "audit_log.csv",
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not export the audit log.");
    } finally {
      setExporting(false);
    }
  }

  function formatDetails(entry: AuditEntry): string {
    if (!entry.details) return "";
    // Prefer human-readable description if available
    if (entry.details.description) return String(entry.details.description);
    const parts: string[] = [];
    for (const [k, v] of Object.entries(entry.details)) {
      // via_assistant is surfaced as a badge next to the user, not repeated in the details text.
      if (k === "description" || k === "target_email" || k === "via_assistant") continue;
      if (v !== null && v !== undefined) {
        parts.push(`${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`);
      }
    }
    return parts.join(", ");
  }

  const entityTypes = [
    "experiment", "sample", "user", "auth", "pipeline_run",
    "pipeline_catalog", "project", "file", "batch", "system",
    "notebook", "reference", "session_credential",
    "environment_version", "backup", "user_quota",
  ];

  const actions = [
    "create", "update", "delete", "login", "login_failed", "logout",
    "status_change", "role_change",
    "launch", "invite", "deactivate", "verify_email",
    "download", "read", "view", "session", "session_access",
    "change_password", "admin_reset_password", "resend_invite", "lock",
    "build", "build_succeeded", "build_failed",
    "backup_completed", "backup_failed",
    "quota_exceeded",
  ];

  return (
    <>
      <Breadcrumb />
      <main className="flex-1 overflow-y-auto p-6">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Audit Log</h1>
            <p data-testid="page-description" className="text-sm text-gray-500 mt-1">
              A record of who did what in this instance, exportable for compliance review.
            </p>
          </div>
          <button
            onClick={handleExport}
            disabled={exporting}
            className="px-4 py-2 text-sm bg-white border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50"
          >
            {exporting ? "Exporting..." : "Export CSV"}
          </button>
        </div>

        <div className="flex flex-wrap gap-3 mb-4">
          <select aria-label="Entity type"
            value={entityType}
            onChange={(e) => { setEntityType(e.target.value); setPage(1); }}
            className="border border-gray-300 rounded px-3 py-1.5 text-sm"
          >
            <option value="">All entity types</option>
            {entityTypes.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          <select aria-label="Action"
            value={action}
            onChange={(e) => { setAction(e.target.value); setPage(1); }}
            className="border border-gray-300 rounded px-3 py-1.5 text-sm"
          >
            <option value="">All actions</option>
            {actions.map((a) => (
              <option key={a} value={a}>{a}</option>
            ))}
          </select>
          <input aria-label="Start date"
            type="date"
            value={startDate}
            onChange={(e) => { setStartDate(e.target.value); setPage(1); }}
            className="border border-gray-300 rounded px-3 py-1.5 text-sm"
            placeholder="Start date"
          />
          <input aria-label="End date"
            type="date"
            value={endDate}
            onChange={(e) => { setEndDate(e.target.value); setPage(1); }}
            className="border border-gray-300 rounded px-3 py-1.5 text-sm"
            placeholder="End date"
          />
          {(entityType || action || startDate || endDate) && (
            <button
              onClick={() => { setEntityType(""); setAction(""); setStartDate(""); setEndDate(""); setPage(1); }}
              className="text-sm text-gray-500 hover:text-gray-700 underline"
            >
              Clear filters
            </button>
          )}
        </div>

        {list.stale && <StaleNotice what="The audit log" onRetry={() => void load()} />}

        {/*
          `total` is whatever the last SUCCESSFUL load returned, so with an error
          occupying the table below it read as a live count of the current filter:
          "1,284 entries total" over an empty, failed table. A count nobody could
          verify against anything on screen is worse than no count.
        */}
        {!loadError && (
          <div className="text-xs text-gray-500 mb-2">
            {total} {total === 1 ? "entry" : "entries"} total
          </div>
        )}

        <div className="bg-white rounded-lg border border-gray-200 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-gray-50">
                <th scope="col" className="text-left px-4 py-3 font-medium text-gray-700">Time</th>
                <th scope="col" className="text-left px-4 py-3 font-medium text-gray-700">User</th>
                <th scope="col" className="text-left px-4 py-3 font-medium text-gray-700">Entity</th>
                <th scope="col" className="text-left px-4 py-3 font-medium text-gray-700">Action</th>
                <th scope="col" className="text-left px-4 py-3 font-medium text-gray-700">Details</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={5} className="px-4 py-8"><ContentLoading variant="table" /></td></tr>
              ) : loadError && !list.stale ? (
                <tr><td colSpan={5} className="px-4 py-8">
                  <ErrorState message={loadError} onRetry={() => void load()} />
                </td></tr>
              ) : entries.length === 0 ? (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-gray-500">No audit log entries</td></tr>
              ) : (
                entries.map((entry) => (
                  <tr key={entry.id} className="border-b hover:bg-gray-50">
                    <td className="px-4 py-2.5 text-gray-500 text-xs whitespace-nowrap">
                      {new Date(entry.timestamp).toLocaleString()}
                    </td>
                    <td className="px-4 py-2.5 text-gray-900 text-xs">
                      {entry.user?.email || "system"}
                      {entry.details?.via_assistant === true && (
                        <span
                          data-testid="via-assistant-badge"
                          title="This action was taken by the user through the assistant."
                          className="ml-1.5 inline-block bg-bioaf-50 text-bioaf-700 border border-bioaf-200 rounded px-1.5 py-0.5 text-[10px] font-medium align-middle"
                        >
                          via assistant
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-gray-600 text-xs">
                      <span className="bg-gray-100 px-1.5 py-0.5 rounded">{entry.entity_type}</span>
                      <span className="ml-1 font-mono text-gray-500">#{entry.entity_id}</span>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`text-xs px-2 py-0.5 rounded ${
                        entry.action === "delete" || entry.action === "backup_failed" || entry.action === "build_failed" ? "bg-red-100 text-red-700" :
                        entry.action === "create" || entry.action === "backup_completed" || entry.action === "build_succeeded" ? "bg-green-100 text-green-700" :
                        entry.action === "login" || entry.action === "logout" ? "bg-blue-100 text-blue-700" :
                        entry.action === "login_failed" || entry.action === "quota_exceeded" ? "bg-amber-100 text-amber-700" :
                        entry.action === "role_change" ? "bg-purple-100 text-purple-700" :
                        "bg-gray-100 text-gray-700"
                      }`}>
                        {entry.action}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-gray-500 text-xs max-w-xs truncate">
                      {formatDetails(entry)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {totalPages > 1 && (
          <div className="flex justify-center gap-2 mt-4">
            <button
              disabled={page <= 1}
              onClick={() => setPage(page - 1)}
              className="px-3 py-1 border rounded text-sm disabled:opacity-50"
            >
              Previous
            </button>
            <span className="px-3 py-1 text-sm text-gray-600">
              Page {page} of {totalPages}
            </span>
            <button
              disabled={page >= totalPages}
              onClick={() => setPage(page + 1)}
              className="px-3 py-1 border rounded text-sm disabled:opacity-50"
            >
              Next
            </button>
          </div>
        )}
      </main>
    </>
  );
}
