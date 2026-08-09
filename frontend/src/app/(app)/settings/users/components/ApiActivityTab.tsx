"use client";

import { useEffect, useState } from "react";
import { Modal } from "@/components/shared/Modal";
import { ApiActivityRow, integrationsApi } from "@/lib/integrationsApi";
import { ApiError } from "@/lib/api";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

import { clickableRow } from "@/lib/a11y";

export function ApiActivityTab() {
  const [rows, setRows] = useState<ApiActivityRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<ApiActivityRow | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      setRows(await integrationsApi.listApiActivity({ limit: 100 }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load activity");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    load();
  }, []);

  const keyLabel = (r: ApiActivityRow): string => {
    if (!r.service_account_name && !r.api_key_name) return "-";
    const sa = r.service_account_name ?? "(unknown SA)";
    const key = r.api_key_name ?? "(unknown key)";
    return `${sa} / ${key}`;
  };

  return (
    <div>
      <p className="text-sm text-gray-600 mb-4">
        Audit-log entries from API-key authenticated calls. Each row shows the service account,
        the key, and what they touched.
      </p>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm">
          {error}
        </div>
      )}

      {loading ? (
        <LoadingSpinner size="lg" />
      ) : rows.length === 0 ? (
        <div className="bg-white rounded-lg shadow p-6 text-center text-sm text-gray-500">
          No API activity yet.
        </div>
      ) : (
        <div className="bg-white rounded-lg shadow overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-50">
              <tr>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Time</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Key</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Entity</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {rows.map((r) => (
                <tr
                  key={r.id}
                  {...clickableRow(() => setSelected(r))}
                  className="hover:bg-gray-50 cursor-pointer"
                >
                  <td className="px-4 py-3 text-sm text-gray-500">
                    {new Date(r.timestamp).toLocaleString()}
                  </td>
                  <td className="px-4 py-3 text-sm">{keyLabel(r)}</td>
                  <td className="px-4 py-3 text-sm">
                    {r.entity_type}/{r.entity_id}
                  </td>
                  <td className="px-4 py-3 text-sm">{r.action}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Modal
        open={!!selected}
        title={
          selected
            ? `${selected.action} on ${selected.entity_type}/${selected.entity_id}`
            : ""
        }
        onClose={() => setSelected(null)}
        size="lg"
      >
        {selected && (
          <>
            <p className="text-xs text-gray-500">
              {new Date(selected.timestamp).toLocaleString()}
            </p>
            <p className="mb-4 text-xs text-gray-600">
              <span className="font-medium">{keyLabel(selected)}</span>
            </p>
            <pre className="bg-gray-50 border border-gray-200 rounded p-3 text-xs whitespace-pre-wrap break-all">
              {JSON.stringify(selected.details_json ?? {}, null, 2)}
            </pre>
          </>
        )}
      </Modal>
    </div>
  );
}
