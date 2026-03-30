"use client";

import { useState, useEffect, useCallback } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { PlotModal } from "@/components/shared/PlotModal";
import { ExportPdfButton } from "@/components/shared/ExportPdfButton";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { api, fileContentUrl } from "@/lib/api";
import type { QCDashboardSummary, QCDashboardResponse, QCMetrics } from "@/lib/types";
import {
  BarcodeRankChart,
  StarAlignmentChart,
  BaseQualityChart,
  GCContentChart,
  DuplicationChart,
} from "@/components/shared/QCCharts";

type MetricStatus = "good" | "warn" | "bad" | "neutral";

function metricColor(status: MetricStatus): string {
  switch (status) {
    case "good": return "bg-green-50 border-green-200";
    case "warn": return "bg-yellow-50 border-yellow-200";
    case "bad": return "bg-red-50 border-red-200";
    default: return "bg-gray-50 border-gray-200";
  }
}

function metricTextColor(status: MetricStatus): string {
  switch (status) {
    case "good": return "text-green-700";
    case "warn": return "text-yellow-700";
    case "bad": return "text-red-700";
    default: return "text-gray-900";
  }
}

function MetricCard({ label, value, status = "neutral" }: { label: string; value: string; status?: MetricStatus }) {
  return (
    <div className={`rounded-lg border p-3 ${metricColor(status)}`}>
      <p className="text-xs text-gray-500">{label}</p>
      <p className={`text-lg font-semibold ${metricTextColor(status)}`}>{value}</p>
    </div>
  );
}

function HeroMetric({ label, value, status = "neutral" }: { label: string; value: string; status?: MetricStatus }) {
  return (
    <div className="text-center">
      <p className="text-sm text-gray-500 mb-1">{label}</p>
      <p className={`text-3xl font-bold ${metricTextColor(status)}`}>{value}</p>
    </div>
  );
}

function SectionHeader({ title }: { title: string }) {
  return <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide mt-6 mb-3">{title}</h3>;
}

function pctStr(val: number): string {
  return `${(val * 100).toFixed(1)}%`;
}

function rateSaturation(val: number | null): MetricStatus {
  if (val == null) return "neutral";
  if (val >= 0.8) return "good";
  if (val >= 0.5) return "warn";
  return "bad";
}

function rateQ30(val: number | null): MetricStatus {
  if (val == null) return "neutral";
  if (val >= 0.9) return "good";
  if (val >= 0.8) return "warn";
  return "bad";
}

function rateBarcodes(val: number | null): MetricStatus {
  if (val == null) return "neutral";
  if (val >= 0.95) return "good";
  if (val >= 0.9) return "warn";
  return "bad";
}

function rateMapping(val: number | null): MetricStatus {
  if (val == null) return "neutral";
  if (val >= 0.9) return "good";
  if (val >= 0.7) return "warn";
  return "bad";
}

function rateGenes(val: number | null): MetricStatus {
  if (val == null) return "neutral";
  if (val > 1000) return "good";
  if (val > 500) return "warn";
  return "bad";
}

function rateMito(val: number | null): MetricStatus {
  if (val == null) return "neutral";
  if (val < 5) return "good";
  if (val < 10) return "warn";
  return "bad";
}

function rateDuplication(val: number | null): MetricStatus {
  if (val == null) return "neutral";
  if (val < 30) return "good";
  if (val < 50) return "warn";
  return "bad";
}

function rateGC(val: number | null): MetricStatus {
  if (val == null) return "neutral";
  if (val >= 35 && val <= 65) return "good";
  if (val >= 25 && val <= 75) return "warn";
  return "bad";
}

function PlotImage({ fileId, title, onExpand }: { fileId: number; title: string; onExpand: (url: string) => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    // Built client-side only (useEffect) to avoid hydration mismatch.
    setUrl(fileContentUrl(fileId));
  }, [fileId]);

  return (
    <div className="relative bg-gray-100 rounded min-h-[12rem] flex items-center justify-center group">
      {error ? (
        <span className="text-gray-400 text-sm">Failed to load plot</span>
      ) : url ? (
        <>
          <img
            src={url}
            alt={title}
            className="w-full rounded"
            onError={() => setError(true)}
          />
          <button
            onClick={() => onExpand(url)}
            className="absolute top-2 right-2 p-1.5 bg-white/80 rounded shadow opacity-0 group-hover:opacity-100 transition-opacity hover:bg-white"
            title="Expand plot"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5v-4m0 4h-4m4 0l-5-5" />
            </svg>
          </button>
        </>
      ) : (
        <span className="text-gray-400 text-sm">Loading plot...</span>
      )}
    </div>
  );
}

function DashboardDetail({ dashboard, onBack, onRegenerate, regenerating, onExpandPlot }: {
  dashboard: QCDashboardResponse;
  onBack: () => void;
  onRegenerate: (runId: number) => void;
  regenerating: boolean;
  onExpandPlot: (url: string, title: string) => void;
}) {
  const m = dashboard.metrics;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <button onClick={onBack} className="text-blue-600 text-sm hover:underline">
          Back to list
        </button>
        <ExportPdfButton
          targetId="qc-dashboard-content"
          filename={`qc-dashboard-run-${dashboard.pipeline_run_id}.pdf`}
        />
      </div>

      <div id="qc-dashboard-content" className="bg-white rounded-lg shadow p-6">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-lg font-bold">QC Dashboard - Run #{dashboard.pipeline_run_id}</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={() => onRegenerate(dashboard.pipeline_run_id)}
              disabled={regenerating}
              className="px-3 py-1 text-xs font-medium text-gray-600 bg-gray-100 rounded hover:bg-gray-200 disabled:opacity-50 print:hidden"
              data-html2canvas-ignore="true"
            >
              {regenerating ? "Regenerating..." : "Regenerate"}
            </button>
            <QualityBadge rating={m.quality_rating} />
          </div>
        </div>

        {dashboard.summary_text && (
          <p className="text-sm text-gray-600 mb-6" dangerouslySetInnerHTML={{ __html: dashboard.summary_text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>") }} />
        )}

        {/* Hero metrics */}
        <div className="bg-gray-50 rounded-lg p-6 mb-6">
          <div className="flex justify-around items-center">
            {m.cell_count != null && (
              <HeroMetric label="Estimated Number of Cells" value={m.cell_count.toLocaleString()} />
            )}
            {m.median_reads_per_cell != null && (
              <HeroMetric label="Mean Reads per Cell" value={m.mean_reads_per_cell != null ? m.mean_reads_per_cell.toLocaleString() : m.median_reads_per_cell.toLocaleString()} />
            )}
            {m.median_genes_per_cell != null && (
              <HeroMetric label="Median Genes per Cell" value={m.median_genes_per_cell.toLocaleString()} status={rateGenes(m.median_genes_per_cell)} />
            )}
          </div>
        </div>

        {/* Cells section */}
        {(m.cell_count != null || m.total_genes_detected != null || m.median_umi_per_cell != null || m.umis_in_cells != null) && (
          <>
            <SectionHeader title="Cells" />
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {m.cell_count != null && (
                <MetricCard label="Estimated Number of Cells" value={m.cell_count.toLocaleString()} />
              )}
              {m.total_genes_detected != null && (
                <MetricCard label="Total Genes Detected" value={m.total_genes_detected.toLocaleString()} />
              )}
              {m.median_genes_per_cell != null && (
                <MetricCard label="Median Genes/Cell" value={m.median_genes_per_cell.toLocaleString()} status={rateGenes(m.median_genes_per_cell)} />
              )}
              {m.mean_genes_per_cell != null && (
                <MetricCard label="Mean Genes/Cell" value={m.mean_genes_per_cell.toLocaleString()} />
              )}
              {m.median_umi_per_cell != null && (
                <MetricCard label="Median UMI/Cell" value={m.median_umi_per_cell.toLocaleString()} />
              )}
              {m.mean_umi_per_cell != null && (
                <MetricCard label="Mean UMI/Cell" value={m.mean_umi_per_cell.toLocaleString()} />
              )}
              {m.median_reads_per_cell != null && (
                <MetricCard label="Median Reads/Cell" value={m.median_reads_per_cell.toLocaleString()} />
              )}
              {m.mean_reads_per_cell != null && (
                <MetricCard label="Mean Reads/Cell" value={m.mean_reads_per_cell.toLocaleString()} />
              )}
              {m.umis_in_cells != null && (
                <MetricCard label="UMIs in Cells" value={m.umis_in_cells.toLocaleString()} />
              )}
              {m.mito_pct_median != null && (
                <MetricCard label="Mito % Median" value={`${m.mito_pct_median.toFixed(1)}%`} status={rateMito(m.mito_pct_median)} />
              )}
              {m.doublet_score_median != null && (
                <MetricCard label="Doublet Score" value={m.doublet_score_median.toFixed(3)} />
              )}
            </div>
          </>
        )}

        {/* Sequencing section */}
        {(m.number_of_reads != null || m.saturation != null || m.valid_barcodes != null || m.q30_bases_barcode != null) && (
          <>
            <SectionHeader title="Sequencing" />
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {m.number_of_reads != null && (
                <MetricCard label="Number of Reads" value={m.number_of_reads.toLocaleString()} />
              )}
              {m.valid_barcodes != null && (
                <MetricCard label="Valid Barcodes" value={pctStr(m.valid_barcodes)} status={rateBarcodes(m.valid_barcodes)} />
              )}
              {m.saturation != null && (
                <MetricCard label="Sequencing Saturation" value={pctStr(m.saturation)} status={rateSaturation(m.saturation)} />
              )}
              {m.q30_bases_barcode != null && (
                <MetricCard label="Q30 Bases in Barcode+UMI" value={pctStr(m.q30_bases_barcode)} status={rateQ30(m.q30_bases_barcode)} />
              )}
              {m.q30_bases_rna_read != null && (
                <MetricCard label="Q30 Bases in RNA Read" value={pctStr(m.q30_bases_rna_read)} status={rateQ30(m.q30_bases_rna_read)} />
              )}
            </div>
          </>
        )}

        {/* Mapping section */}
        {(m.reads_mapped_genome != null || m.reads_mapped_genome_unique != null) && (
          <>
            <SectionHeader title="Mapping" />
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {m.reads_mapped_genome != null && (
                <MetricCard label="Reads Mapped to Genome" value={pctStr(m.reads_mapped_genome)} status={rateMapping(m.reads_mapped_genome)} />
              )}
              {m.reads_mapped_genome_unique != null && (
                <MetricCard label="Reads Mapped (Unique)" value={pctStr(m.reads_mapped_genome_unique)} status={rateMapping(m.reads_mapped_genome_unique)} />
              )}
            </div>
          </>
        )}

        {/* Bulk/FastQC section */}
        {(m.total_sequences != null || m.percent_duplicates != null || m.percent_gc != null) && (
          <>
            <SectionHeader title="Read Quality" />
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {m.total_sequences != null && (
                <MetricCard label="Total Sequences" value={m.total_sequences.toLocaleString()} />
              )}
              {m.total_samples != null && (
                <MetricCard label="Samples" value={String(m.total_samples)} />
              )}
              {m.percent_duplicates != null && (
                <MetricCard label="Duplication" value={`${m.percent_duplicates.toFixed(1)}%`} status={rateDuplication(m.percent_duplicates)} />
              )}
              {m.percent_gc != null && (
                <MetricCard label="GC Content" value={`${m.percent_gc.toFixed(0)}%`} status={rateGC(m.percent_gc)} />
              )}
              {m.avg_sequence_length != null && (
                <MetricCard label="Avg Read Length" value={`${m.avg_sequence_length.toFixed(0)} bp`} />
              )}
            </div>
          </>
        )}

        {/* Interactive Charts */}
        {(m.barcode_rank_data || m.chart_data) && (
          <>
            <SectionHeader title="Interactive Charts" />
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {m.barcode_rank_data && m.barcode_rank_data.length > 0 && (
                <div className="lg:col-span-2">
                  <BarcodeRankChart data={m.barcode_rank_data} />
                </div>
              )}
              {m.chart_data?.star_alignment && (
                <StarAlignmentChart data={m.chart_data.star_alignment} />
              )}
              {m.chart_data?.base_quality && (
                <BaseQualityChart data={m.chart_data.base_quality} />
              )}
              {m.chart_data?.gc_content && (
                <GCContentChart data={m.chart_data.gc_content} />
              )}
              {m.chart_data?.duplication && (
                <DuplicationChart data={m.chart_data.duplication} />
              )}
            </div>
          </>
        )}

        {/* Static Plots (PNG fallback) */}
        {dashboard.plots.length > 0 && (
          <>
            <SectionHeader title="Plots" />
            <div className="grid grid-cols-2 gap-4">
              {dashboard.plots.map((plot, i) => (
                <div key={i} className="border rounded-lg p-3">
                  <p className="text-sm font-medium mb-2">{plot.title}</p>
                  <PlotImage
                    fileId={plot.file_id}
                    title={plot.title}
                    onExpand={(url) => onExpandPlot(url, plot.title)}
                  />
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function QualityBadge({ rating }: { rating: string }) {
  const colorClass = (() => {
    switch (rating) {
      case "excellent": return "bg-green-100 text-green-700";
      case "good": return "bg-blue-100 text-blue-700";
      case "acceptable": return "bg-yellow-100 text-yellow-700";
      case "pending_review": return "bg-gray-100 text-gray-700";
      default: return "bg-red-100 text-red-700";
    }
  })();

  return (
    <span className={`px-3 py-1 rounded-full text-sm font-medium ${colorClass}`}>
      {rating}
    </span>
  );
}

export default function QCDashboardsPage() {
  const [dashboards, setDashboards] = useState<QCDashboardSummary[]>([]);
  const [selected, setSelected] = useState<QCDashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [regenerating, setRegenerating] = useState(false);
  const [expandedPlot, setExpandedPlot] = useState<{ url: string; title: string } | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const data = await api.get<QCDashboardSummary[]>("/api/qc-dashboards");
        setDashboards(data);
      } catch {
        // ignore
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const viewDashboard = async (id: number) => {
    try {
      const data = await api.get<QCDashboardResponse>(`/api/qc-dashboards/${id}`);
      setSelected(data);
    } catch {
      // ignore
    }
  };

  const regenerateQc = async (runId: number) => {
    setRegenerating(true);
    try {
      const data = await api.post<QCDashboardResponse>(`/api/qc-dashboards/regenerate/${runId}`, {});
      setSelected(data);
      const updated = await api.get<QCDashboardSummary[]>("/api/qc-dashboards");
      setDashboards(updated);
    } catch {
      // ignore
    } finally {
      setRegenerating(false);
    }
  };

  const handleExpandPlot = useCallback((url: string, title: string) => {
    setExpandedPlot({ url, title });
  }, []);

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-6">QC Dashboards</h1>

          {selected ? (
            <DashboardDetail
              dashboard={selected}
              onBack={() => setSelected(null)}
              onRegenerate={regenerateQc}
              regenerating={regenerating}
              onExpandPlot={handleExpandPlot}
            />
          ) : loading ? (
            <ContentLoading />
          ) : dashboards.length === 0 ? (
            <p className="text-gray-400 text-sm">
              No QC dashboards yet. They are generated automatically when pipeline runs complete.
            </p>
          ) : (
            <div className="bg-white rounded-lg shadow divide-y divide-gray-200">
              {dashboards.map((d) => (
                <div
                  key={d.id}
                  onClick={() => viewDashboard(d.id)}
                  className="p-4 flex items-center justify-between hover:bg-gray-50 cursor-pointer"
                >
                  <div>
                    <p className="font-medium text-sm">Run #{d.pipeline_run_id}</p>
                    <p className="text-xs text-gray-400">
                      Generated {d.generated_at ? new Date(d.generated_at).toLocaleDateString() : "N/A"}
                      {d.cell_count != null && ` | ${d.cell_count.toLocaleString()} cells`}
                    </p>
                  </div>
                  <QualityBadge rating={d.quality_rating} />
                </div>
              ))}
            </div>
          )}
        </main>
      </div>

      {expandedPlot && (
        <PlotModal
          url={expandedPlot.url}
          title={expandedPlot.title}
          onClose={() => setExpandedPlot(null)}
        />
      )}
    </div>
  );
}
