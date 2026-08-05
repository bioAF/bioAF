"use client";

import { useState, useMemo } from "react";
import type { AnalysisSnapshot } from "@/lib/types";

interface SnapshotComparisonTableProps {
  snapshots: AnalysisSnapshot[];
  onCompare: (ids: number[]) => void;
}

type SortKey = "label" | "created_at" | "user_name" | "object_type" | "cell_count" | "gene_count" | "cluster_count";
type SortDir = "asc" | "desc";

export default function SnapshotComparisonTable({ snapshots, onCompare }: SnapshotComparisonTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>("created_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [selected, setSelected] = useState<Set<number>>(new Set());

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  function toggleSelection(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else if (next.size < 5) next.add(id);
      return next;
    });
  }

  const sorted = useMemo(() => {
    return [...snapshots].sort((a, b) => {
      const aVal = a[sortKey];
      const bVal = b[sortKey];
      if (aVal == null && bVal == null) return 0;
      if (aVal == null) return 1;
      if (bVal == null) return -1;
      const cmp = aVal < bVal ? -1 : aVal > bVal ? 1 : 0;
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [snapshots, sortKey, sortDir]);

  const SortHeader = ({ label, field }: { label: string; field: SortKey }) => (
    <th scope="col"
      className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase cursor-pointer hover:text-gray-700"
      onClick={() => handleSort(field)}
    >
      {label} {sortKey === field && (sortDir === "asc" ? "\u25B2" : "\u25BC")}
    </th>
  );

  return (
    <div>
      {selected.size >= 2 && (
        <div className="mb-3 flex items-center gap-3">
          <button
            onClick={() => onCompare(Array.from(selected))}
            className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700"
          >
            Compare Selected ({selected.size})
          </button>
          <button
            onClick={() => setSelected(new Set())}
            className="text-gray-500 text-sm hover:text-gray-700"
          >
            Clear
          </button>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm bg-white border rounded-lg overflow-hidden">
          <thead className="bg-gray-50">
            <tr>
              <th scope="col" className="px-3 py-2 w-8"></th>
              <th scope="col" className="px-3 py-2 w-8"></th>
              <SortHeader label="Label" field="label" />
              <SortHeader label="Date" field="created_at" />
              <SortHeader label="User" field="user_name" />
              <SortHeader label="Type" field="object_type" />
              <SortHeader label="Cells" field="cell_count" />
              <SortHeader label="Genes" field="gene_count" />
              <SortHeader label="Clusters" field="cluster_count" />
              <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Figure</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {sorted.map((snap) => (
              <tr
                key={snap.id}
                className={`hover:bg-gray-50 ${selected.has(snap.id) ? "bg-bioaf-50" : ""} ${
                  snap.starred ? "font-medium" : ""
                }`}
              >
                <td className="px-3 py-2">
                  <input
                    type="checkbox"
                    checked={selected.has(snap.id)}
                    onChange={() => toggleSelection(snap.id)}
                    className="h-4 w-4 text-bioaf-600 rounded border-gray-300"
                  />
                </td>
                <td className="px-3 py-2 text-lg">
                  {snap.starred ? <span className="text-yellow-500">{"\u2605"}</span> : ""}
                </td>
                <td className="px-3 py-2 font-medium">{snap.label}</td>
                <td className="px-3 py-2 text-gray-500">{new Date(snap.created_at).toLocaleDateString()}</td>
                <td className="px-3 py-2 text-gray-500">{snap.user_name}</td>
                <td className="px-3 py-2">
                  <span
                    className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                      snap.object_type === "anndata"
                        ? "bg-green-100 text-green-800"
                        : "bg-purple-100 text-purple-800"
                    }`}
                  >
                    {snap.object_type === "anndata" ? "AnnData" : "Seurat"}
                  </span>
                </td>
                <td className="px-3 py-2 text-right">{snap.cell_count?.toLocaleString() ?? "—"}</td>
                <td className="px-3 py-2 text-right">{snap.gene_count?.toLocaleString() ?? "—"}</td>
                <td className="px-3 py-2 text-right">{snap.cluster_count ?? "—"}</td>
                <td className="px-3 py-2">
                  {snap.figure_url && (
                    <div className="w-8 h-8 rounded border overflow-hidden">
                      <img src={snap.figure_url} alt="" className="w-full h-full object-cover" />
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
