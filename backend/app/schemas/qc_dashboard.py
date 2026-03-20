from datetime import datetime

from pydantic import BaseModel


class QCMetrics(BaseModel):
    cell_count: int | None = None
    median_reads_per_cell: float | None = None
    median_genes_per_cell: float | None = None
    median_umi_per_cell: float | None = None
    mito_pct_median: float | None = None
    doublet_score_median: float | None = None
    saturation: float | None = None
    # Bulk/FastQC metrics
    total_sequences: int | None = None
    percent_duplicates: float | None = None
    percent_gc: float | None = None
    avg_sequence_length: float | None = None
    total_samples: int | None = None
    quality_rating: str = "concerning"


class QCPlot(BaseModel):
    plot_type: str
    title: str
    file_id: int
    download_url: str | None = None


class QCDashboardResponse(BaseModel):
    id: int
    pipeline_run_id: int
    experiment_id: int | None
    metrics: QCMetrics
    summary_text: str
    plots: list[QCPlot] = []
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
