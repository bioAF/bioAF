"use client";

import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type {
  SnapshotComparison as SnapshotComparisonData,
  ParameterDiff,
  ClusteringDiff,
  CommandDiff,
  CellCountPoint,
  AnalysisSnapshotDetail,
} from "@/lib/types";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  LineChart,
  Line,
} from "recharts";

const COLORS = ["#0ea5e9", "#8b5cf6", "#f59e0b", "#10b981", "#ef4444"];

interface SnapshotComparisonProps {
  url: string;
}

export default function SnapshotComparison({ url }: SnapshotComparisonProps) {
  const [data, setData] = useState<SnapshotComparisonData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadComparison();
  }, [url]);

  async function loadComparison() {
    try {
      const result = await api.get<SnapshotComparisonData>(url);
      setData(result);
    } catch (err) {
      setError("Failed to load comparison data");
      console.error(err);
    } finally {
      setLoading(false);
    }
  }

  if (loading) return <div className="text-center py-8 text-gray-500">Loading comparison...</div>;
  if (error) return <div className="text-center py-8 text-red-500">{error}</div>;
  if (!data) return null;

  const snapshots = data.snapshots;
  const snapshotMap = new Map(snapshots.map((s) => [s.id, s]));

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="grid gap-4" style={{ gridTemplateColumns: `repeat(${snapshots.length}, 1fr)` }}>
        {snapshots.map((snap, i) => (
          <div key={snap.id} className="p-3 rounded-lg border-2" style={{ borderColor: COLORS[i] }}>
            <div className="font-medium text-sm">{snap.label}</div>
            <div className="text-xs text-gray-500">
              {snap.user_name} &middot; {new Date(snap.created_at).toLocaleDateString()}
            </div>
            <div className="text-xs text-gray-500 mt-1">
              {snap.cell_count?.toLocaleString()} cells &middot; {snap.gene_count?.toLocaleString()} genes
            </div>
          </div>
        ))}
      </div>

      {/* Parameter Diff */}
      <ParameterDiffTable diffs={data.parameter_diff} snapshots={snapshots} />

      {/* Clustering Distribution Chart */}
      {data.clustering_diff.length > 0 && (
        <ClusterDistributionChart diffs={data.clustering_diff} snapshots={snapshots} />
      )}

      {/* Figure Comparison */}
      <FigureComparison snapshots={snapshots} />

      {/* Command Log Diff */}
      {data.command_log_diff && data.command_log_diff.length > 0 && (
        <CommandLogDiffTable diffs={data.command_log_diff} snapshots={snapshots} />
      )}

      {/* Cell Count Progression */}
      {data.cell_count_series.length > 1 && (
        <CellCountChart series={data.cell_count_series} />
      )}
    </div>
  );
}

function ParameterDiffTable({
  diffs,
  snapshots,
}: {
  diffs: ParameterDiff[];
  snapshots: AnalysisSnapshotDetail[];
}) {
  if (diffs.length === 0) return null;

  return (
    <div>
      <h3 className="text-sm font-semibold mb-3 text-gray-700">Parameter Differences</h3>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm border rounded-lg overflow-hidden">
          <thead className="bg-gray-50">
            <tr>
              <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Parameter</th>
              {snapshots.map((snap, i) => (
                <th scope="col" key={snap.id} className="px-3 py-2 text-left text-xs font-medium uppercase" style={{ color: COLORS[i] }}>
                  {snap.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {diffs.map((diff) => (
              <tr key={diff.parameter_path} className={diff.changed ? "bg-yellow-50" : ""}>
                <td className="px-3 py-2 font-mono text-xs text-gray-600">{diff.parameter_path}</td>
                {snapshots.map((snap) => (
                  <td key={snap.id} className="px-3 py-2 text-xs">
                    {diff.values[snap.id] !== null && diff.values[snap.id] !== undefined
                      ? String(diff.values[snap.id])
                      : <span className="text-gray-500">Not set</span>}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ClusterDistributionChart({
  diffs,
  snapshots,
}: {
  diffs: ClusteringDiff[];
  snapshots: AnalysisSnapshotDetail[];
}) {
  // Show the first clustering that has differences
  const diff = diffs[0];
  if (!diff) return null;

  // Build chart data: each cluster label is a data point
  const clusterLabels = Object.keys(diff.distributions[snapshots[0].id] || {}).sort(
    (a, b) => Number(a) - Number(b)
  );

  const chartData = clusterLabels.map((label) => {
    const point: Record<string, string | number> = { cluster: label };
    for (const snap of snapshots) {
      point[snap.label] = diff.distributions[snap.id]?.[label] ?? 0;
    }
    return point;
  });

  return (
    <div>
      <h3 className="text-sm font-semibold mb-3 text-gray-700">
        Cluster Distribution: {diff.clustering_name}
      </h3>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="cluster" label={{ value: "Cluster", position: "insideBottom", offset: -5 }} />
          <YAxis label={{ value: "Cells", angle: -90, position: "insideLeft" }} />
          <Tooltip />
          <Legend />
          {snapshots.map((snap, i) => (
            <Bar key={snap.id} dataKey={snap.label} fill={COLORS[i]} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function FigureComparison({ snapshots }: { snapshots: AnalysisSnapshotDetail[] }) {
  const withFigures = snapshots.filter((s) => s.figure_url);
  if (withFigures.length === 0) return null;

  return (
    <div>
      <h3 className="text-sm font-semibold mb-3 text-gray-700">Figures</h3>
      <div className="grid gap-4" style={{ gridTemplateColumns: `repeat(${withFigures.length}, 1fr)` }}>
        {withFigures.map((snap, i) => (
          <div key={snap.id} className="border rounded-lg overflow-hidden">
            <div className="p-2 bg-gray-50 text-xs font-medium" style={{ color: COLORS[i] }}>
              {snap.label}
            </div>
            <img src={snap.figure_url!} alt={snap.label} className="w-full" />
          </div>
        ))}
      </div>
    </div>
  );
}

function CommandLogDiffTable({
  diffs,
  snapshots,
}: {
  diffs: CommandDiff[];
  snapshots: AnalysisSnapshotDetail[];
}) {
  return (
    <div>
      <h3 className="text-sm font-semibold mb-3 text-gray-700">Command Log Diff (Seurat)</h3>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm border rounded-lg overflow-hidden">
          <thead className="bg-gray-50">
            <tr>
              <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Command</th>
              {snapshots.map((snap, i) => (
                <th scope="col" key={snap.id} className="px-3 py-2 text-left text-xs font-medium uppercase" style={{ color: COLORS[i] }}>
                  {snap.label}
                </th>
              ))}
              <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {diffs.map((diff) => (
              <tr
                key={diff.command_name}
                className={
                  diff.present_in.length < snapshots.length
                    ? "bg-green-50"
                    : diff.params_differ
                    ? "bg-yellow-50"
                    : ""
                }
              >
                <td className="px-3 py-2 font-mono text-xs">{diff.command_name}</td>
                {snapshots.map((snap) => (
                  <td key={snap.id} className="px-3 py-2 text-xs">
                    {diff.present_in.includes(snap.id) ? (
                      diff.params_differ && diff.params?.[snap.id] ? (
                        <span className="text-yellow-700">
                          {Object.entries(diff.params[snap.id])
                            .map(([k, v]) => `${k}=${String(v)}`)
                            .join(", ")}
                        </span>
                      ) : (
                        <span className="text-green-600">&#10003;</span>
                      )
                    ) : (
                      <span className="text-gray-500">Not set</span>
                    )}
                  </td>
                ))}
                <td className="px-3 py-2 text-xs">
                  {diff.present_in.length < snapshots.length ? (
                    <span className="text-green-600 font-medium">NEW</span>
                  ) : diff.params_differ ? (
                    <span className="text-yellow-600 font-medium">CHANGED</span>
                  ) : (
                    <span className="text-gray-500">same</span>
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

function CellCountChart({ series }: { series: CellCountPoint[] }) {
  const chartData = series.map((p) => ({
    label: p.label,
    cells: p.cell_count,
  }));

  return (
    <div>
      <h3 className="text-sm font-semibold mb-3 text-gray-700">Cell Count Progression</h3>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} />
          <YAxis />
          <Tooltip />
          <Line type="monotone" dataKey="cells" stroke="#0ea5e9" strokeWidth={2} dot={{ r: 4 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
