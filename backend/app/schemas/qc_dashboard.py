from datetime import datetime
from typing import Any

from pydantic import BaseModel


class QCMetrics(BaseModel):
    cell_count: int | None = None
    median_reads_per_cell: float | None = None
    median_genes_per_cell: float | None = None
    median_umi_per_cell: float | None = None
    mito_pct_median: float | None = None
    doublet_score_median: float | None = None
    saturation: float | None = None
    # Sequencing metrics
    number_of_reads: int | None = None
    valid_barcodes: float | None = None
    q30_bases_barcode: float | None = None
    q30_bases_rna_read: float | None = None
    # Mapping metrics
    reads_mapped_genome: float | None = None
    reads_mapped_genome_unique: float | None = None
    # Mean values and totals
    mean_reads_per_cell: float | None = None
    mean_umi_per_cell: float | None = None
    mean_genes_per_cell: float | None = None
    total_genes_detected: int | None = None
    umis_in_cells: int | None = None
    # Bulk/FastQC metrics
    total_sequences: int | None = None
    percent_duplicates: float | None = None
    percent_gc: float | None = None
    avg_sequence_length: float | None = None
    total_samples: int | None = None
    quality_rating: str = "concerning"
    # Chart data for interactive rendering
    barcode_rank_data: list[list[int]] | None = None
    chart_data: dict | None = None


class QCPlot(BaseModel):
    plot_type: str
    title: str
    file_id: int
    download_url: str | None = None


class QCMetricSpec(BaseModel):
    label: str
    format: str = "raw"
    thresholds: dict[str, str] | None = None


class QCSection(BaseModel):
    id: str
    title: str | None = None
    layout: str | None = None
    metrics: list[str] = []


class QCChartSpec(BaseModel):
    type: str
    metric_key: str | None = None
    title: str | None = None


class QCPlotSpec(BaseModel):
    file_glob: str
    title: str
    type: str


class QCDashboardConfig(BaseModel):
    template: str
    sections: list[QCSection] = []
    metrics: dict[str, QCMetricSpec] = {}
    charts: list[QCChartSpec] = []
    plots: list[QCPlotSpec] = []


class QCDashboardResponse(BaseModel):
    id: int
    pipeline_run_id: int
    experiment_id: int | None
    metrics: QCMetrics
    raw_metrics: dict[str, Any] = {}
    summary_text: str
    plots: list[QCPlot] = []
    qc_config: QCDashboardConfig
    status: str
    generated_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class QCDashboardSummary(BaseModel):
    id: int
    pipeline_run_id: int
    quality_rating: str
    cell_count: int | None
    status: str
    generated_at: datetime | None

    model_config = {"from_attributes": True}
