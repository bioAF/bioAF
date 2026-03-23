"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { Breadcrumb } from "@/components/layout/Breadcrumb";
import { isAuthenticated } from "@/lib/auth";
import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";
import { ContentLoading } from "@/components/shared/ContentLoading";

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

export default function AuditLogPage() {
  const router = useRouter();
  const { canAccess, loading: permLoading } = usePermissions();
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [entityType, setEntityType] = useState("");
  const [action, setAction] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [exporting, setExporting] = useState(false);
  const pageSize = 25;

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    if (permLoading) return;
    if (!canAccess("audit_log", "view")) { router.push("/dashboard"); return; }
  }, [router, permLoading, canAccess]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      let url = `/api/audit?page=${page}&page_size=${pageSize}`;
      if (entityType) url += `&entity_type=${entityType}`;
      if (action) url += `&action=${action}`;
      if (startDate) url += `&start_date=${startDate}`;
      if (endDate) url += `&end_date=${endDate}`;
      const data = await api.get<{ entries: AuditEntry[]; total: number }>(url);
      setEntries(data.entries);
      setTotal(data.total);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [page, entityType, action, startDate, endDate]);

  useEffect(() => { load(); }, [load]);

  const totalPages = Math.ceil(total / pageSize);

  async function handleExport() {
    setExporting(true);
    try {
      let url = `/api/audit/export?format=csv`;
      if (entityType) url += `&entity_type=${entityType}`;
      if (action) url += `&action=${action}`;
      if (startDate) url += `&start_date=${startDate}`;
      if (endDate) url += `&end_date=${endDate}`;
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL || ""}${url}`, {
        headers: {
          Authorization: `Bearer ${localStorage.getItem("bioaf_token")}`,
        },
      });
      if (!response.ok) throw new Error("Export failed");
      const blob = await response.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "audit_log.csv";
      a.click();
      URL.revokeObjectURL(a.href);
    } catch {
      // ignore
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
      if (k === "description" || k === "target_email") continue;
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
  ];

  const actions = [
    "create", "update", "delete", "login", "logout", "status_change",
    "launch", "invite", "deactivate", "verify_email",
    "download", "read", "view", "session",
    "change_password", "admin_reset_password", "resend_invite", "lock",
  ];

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <Breadcrumb />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="flex items-center justify-between mb-6">
            <h1 className="text-2xl font-bold text-gray-900">Audit Log</h1>
            <button
              onClick={handleExport}
              disabled={exporting}
              className="px-4 py-2 text-sm bg-white border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50"
            >
              {exporting ? "Exporting..." : "Export CSV"}
            </button>
          </div>

          <div className="flex flex-wrap gap-3 mb-4">
            <select
              value={entityType}
              onChange={(e) => { setEntityType(e.target.value); setPage(1); }}
              className="border border-gray-300 rounded px-3 py-1.5 text-sm"
            >
              <option value="">All entity types</option>
              {entityTypes.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            <select
              value={action}
              onChange={(e) => { setAction(e.target.value); setPage(1); }}
              className="border border-gray-300 rounded px-3 py-1.5 text-sm"
            >
              <option value="">All actions</option>
              {actions.map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
            <input
              type="date"
              value={startDate}
              onChange={(e) => { setStartDate(e.target.value); setPage(1); }}
              className="border border-gray-300 rounded px-3 py-1.5 text-sm"
              placeholder="Start date"
            />
            <input
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

          <div className="text-xs text-gray-500 mb-2">
            {total} {total === 1 ? "entry" : "entries"} total
          </div>

          <div className="bg-white rounded-lg border border-gray-200 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-gray-50">
                  <th className="text-left px-4 py-3 font-medium text-gray-700">Time</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-700">User</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-700">Entity</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-700">Action</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-700">Details</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan={5} className="px-4 py-8"><ContentLoading /></td></tr>
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
                      </td>
                      <td className="px-4 py-2.5 text-gray-600 text-xs">
                        <span className="bg-gray-100 px-1.5 py-0.5 rounded">{entry.entity_type}</span>
                        <span className="ml-1 font-mono text-gray-400">#{entry.entity_id}</span>
                      </td>
                      <td className="px-4 py-2.5">
                        <span className={`text-xs px-2 py-0.5 rounded ${
                          entry.action === "delete" ? "bg-red-100 text-red-700" :
                          entry.action === "create" ? "bg-green-100 text-green-700" :
                          entry.action === "login" ? "bg-blue-100 text-blue-700" :
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
      </div>
    </div>
  );
}
