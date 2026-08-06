"use client";

import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { AnalysisSnapshot } from "@/lib/types";

interface SnapshotTimelineProps {
  experimentId?: number;
  projectId?: number;
}

export default function SnapshotTimeline({ experimentId, projectId }: SnapshotTimelineProps) {
  const [snapshots, setSnapshots] = useState<AnalysisSnapshot[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [showComparison, setShowComparison] = useState(false);

  useEffect(() => {
    loadSnapshots();
  }, [experimentId, projectId]);

  async function loadSnapshots() {
    try {
      const params = new URLSearchParams();
      if (experimentId) params.set("experiment_id", String(experimentId));
      if (projectId) params.set("project_id", String(projectId));

      const data = await api.get<{ snapshots: AnalysisSnapshot[]; total: number }>(
        `/api/snapshots?${params.toString()}`
      );
      setSnapshots(data.snapshots);
    } catch (err) {
      console.error("Failed to load snapshots", err);
    } finally {
      setLoading(false);
    }
  }

  async function toggleStar(id: number) {
    try {
      const updated = await api.post<AnalysisSnapshot>(`/api/snapshots/${id}/star`);
      setSnapshots((prev) => prev.map((s) => (s.id === id ? { ...s, starred: updated.starred } : s)));
    } catch (err) {
      console.error("Failed to toggle star", err);
    }
  }

  function toggleSelection(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else if (next.size < 5) {
        next.add(id);
      }
      return next;
    });
  }

  // Group by notebook_session_id
  const groups = new Map<string, AnalysisSnapshot[]>();
  for (const snap of snapshots) {
    const key = snap.notebook_session_id ? `session-${snap.notebook_session_id}` : "other";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(snap);
  }

  if (loading) {
    return (
      <div className="bg-white rounded-lg shadow p-8 text-center text-gray-500">
        Loading snapshots...
      </div>
    );
  }

  if (snapshots.length === 0) {
    return (
      <div className="bg-white rounded-lg shadow p-8 text-center text-gray-500">
        <p className="text-lg font-medium mb-2">No Analysis Snapshots</p>
        <p>
          Use <code className="bg-gray-100 px-1 rounded">bioaf.snapshot(adata, label=&quot;...&quot;)</code>{" "}
          in a notebook to capture snapshots.
        </p>
      </div>
    );
  }

  const typeBadge = (type: string) => (
    <span
      className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
        type === "anndata" ? "bg-green-100 text-green-800" : "bg-purple-100 text-purple-800"
      }`}
    >
      {type === "anndata" ? "AnnData" : "Seurat"}
    </span>
  );

  return (
    <div className="space-y-6">
      {/* Compare button */}
      {selected.size >= 2 && (
        <div className="flex items-center gap-3">
          <button
            onClick={() => setShowComparison(true)}
            className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700"
          >
            Compare Selected ({selected.size})
          </button>
          <button
            onClick={() => setSelected(new Set())}
            className="text-gray-500 text-sm hover:text-gray-700"
          >
            Clear selection
          </button>
        </div>
      )}

      {/* Comparison modal */}
      {showComparison && (
        <ComparisonModal
          ids={Array.from(selected)}
          snapshots={snapshots.filter((s) => selected.has(s.id))}
          onClose={() => setShowComparison(false)}
        />
      )}

      {/* Timeline groups */}
      {Array.from(groups.entries()).map(([groupKey, groupSnaps]) => (
        <div key={groupKey} className="bg-white rounded-lg shadow">
          <div className="p-4 border-b bg-gray-50 rounded-t-lg">
            <h3 className="text-sm font-medium text-gray-700">
              {groupKey === "other"
                ? "Ungrouped Snapshots"
                : `Session ${groupSnaps[0].notebook_session_id}`}
              <span className="ml-2 text-gray-500 font-normal">
                {groupSnaps[0].user_name} &middot; {groupSnaps.length} snapshot{groupSnaps.length !== 1 ? "s" : ""}
              </span>
            </h3>
          </div>
          <div className="divide-y divide-gray-100">
            {groupSnaps.map((snap) => (
              <div
                key={snap.id}
                className={`flex items-center gap-4 px-4 py-3 hover:bg-gray-50 ${
                  selected.has(snap.id) ? "bg-bioaf-50" : ""
                }`}
              >
                {/* Checkbox */}
                <input
                  type="checkbox"
                  aria-label={`Select snapshot ${snap.label}`}
                  checked={selected.has(snap.id)}
                  onChange={() => toggleSelection(snap.id)}
                  className="h-4 w-4 text-bioaf-600 rounded border-gray-300"
                />

                {/* Star */}
                <button
                  onClick={() => toggleStar(snap.id)}
                  className={`text-lg ${snap.starred ? "text-yellow-500" : "text-gray-300 hover:text-yellow-400"}`}
                  title={snap.starred ? "Unstar" : "Star"}
                >
                  {snap.starred ? "\u2605" : "\u2606"}
                </button>

                {/* Label and info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm truncate">{snap.label}</span>
                    {typeBadge(snap.object_type)}
                  </div>
                  {snap.notes && (
                    <p className="text-xs text-gray-500 mt-0.5 truncate">{snap.notes}</p>
                  )}
                </div>

                {/* Counts */}
                <div className="text-xs text-gray-500 text-right whitespace-nowrap">
                  <div>{snap.cell_count?.toLocaleString() ?? "—"} cells</div>
                  <div>{snap.cluster_count ?? "—"} clusters</div>
                </div>

                {/* Figure thumbnail */}
                {snap.figure_url && (
                  <div className="w-10 h-10 rounded border overflow-hidden flex-shrink-0">
                    <img src={snap.figure_url} alt="" className="w-full h-full object-cover" />
                  </div>
                )}

                {/* Timestamp */}
                <div className="text-xs text-gray-500 whitespace-nowrap">
                  {new Date(snap.created_at).toLocaleString()}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

interface ComparisonModalProps {
  ids: number[];
  snapshots: AnalysisSnapshot[];
  onClose: () => void;
}

function ComparisonModal({ ids, snapshots, onClose }: ComparisonModalProps) {
  const [comparisonUrl] = useState(
    `/api/snapshots/compare?ids=${ids.join(",")}`
  );

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/50 flex items-start justify-center pt-8">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-6xl mx-4 max-h-[90vh] overflow-y-auto">
        <div className="sticky top-0 bg-white border-b px-6 py-4 flex items-center justify-between z-10">
          <h2 className="text-lg font-semibold">
            Comparing {snapshots.length} Snapshots
          </h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-600 text-xl">
            &times;
          </button>
        </div>
        <div className="p-6">
          <SnapshotComparisonContent url={comparisonUrl} snapshotLabels={snapshots} />
        </div>
      </div>
    </div>
  );
}

function SnapshotComparisonContent({
  url,
  snapshotLabels,
}: {
  url: string;
  snapshotLabels: AnalysisSnapshot[];
}) {
  // Lazy import the comparison component
  const [SnapshotComparison, setComponent] = useState<React.ComponentType<{ url: string }> | null>(null);

  useEffect(() => {
    import("@/components/SnapshotComparison").then((mod) => {
      setComponent(() => mod.default);
    });
  }, []);

  if (!SnapshotComparison) {
    return <div className="text-center py-8 text-gray-500">Loading comparison...</div>;
  }

  return <SnapshotComparison url={url} />;
}
