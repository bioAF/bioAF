"""Bulk RNA-seq QC template.

Built-in template for nf-core/rnaseq-style runs. Reads MultiQC aggregate
stats (FastQC + STAR/Salmon mapping) for per-sample QC. Extractor lift
will follow the scRNA-seq lift; this module currently exposes the render
config + per-template quality rating.
"""

from __future__ import annotations

from typing import Any


def render_config() -> dict:
    return {
        "template": "bulk_rnaseq",
        "sections": [
            {
                "id": "hero",
                "layout": "hero",
                "metrics": ["total_samples", "total_sequences", "percent_duplicates", "percent_gc"],
            },
            {
                "id": "sequencing",
                "title": "Sequencing",
                "layout": "grid",
                "metrics": [
                    "total_samples",
                    "total_sequences",
                    "avg_sequence_length",
                    "percent_duplicates",
                    "percent_gc",
                ],
            },
            {
                "id": "mapping",
                "title": "Mapping",
                "layout": "grid",
                "metrics": ["reads_mapped_genome", "reads_mapped_genome_unique"],
            },
        ],
        "metrics": {
            "total_samples": {"label": "Samples", "format": "integer"},
            "total_sequences": {"label": "Total Sequences", "format": "integer"},
            "avg_sequence_length": {"label": "Avg Read Length", "format": "bp"},
            "percent_duplicates": {
                "label": "Duplicates",
                "format": "percent_pct",
                "thresholds": {"good": "<30", "warn": "<50"},
            },
            "percent_gc": {"label": "GC Content", "format": "percent_pct"},
            "reads_mapped_genome": {
                "label": "Reads Mapped to Genome",
                "format": "percent_decimal",
                "thresholds": {"good": ">=0.9", "warn": ">=0.5"},
            },
            "reads_mapped_genome_unique": {
                "label": "Reads Mapped to Genome (Unique)",
                "format": "percent_decimal",
                "thresholds": {"good": ">=0.7", "warn": ">=0.5"},
            },
        },
        "charts": [
            {"type": "star_alignment", "metric_key": "chart_data.star_alignment", "title": "STAR Alignment"},
            {"type": "base_quality", "metric_key": "chart_data.base_quality", "title": "Per-Base Sequence Quality"},
            {"type": "gc_content", "metric_key": "chart_data.gc_content", "title": "GC Content Distribution"},
            {"type": "duplication", "metric_key": "chart_data.duplication", "title": "Sequence Duplication Levels"},
        ],
        "plots": [
            {
                "file_glob": "multiqc/multiqc_plots/png/star_alignment_plot-pct.png",
                "title": "STAR Alignment",
                "type": "star_alignment",
            },
            {
                "file_glob": "multiqc/multiqc_plots/png/fastqc_per_base_sequence_quality_plot.png",
                "title": "Per-Base Sequence Quality",
                "type": "base_quality",
            },
            {
                "file_glob": "multiqc/multiqc_plots/png/fastqc_per_sequence_gc_content_plot_Percentages.png",
                "title": "GC Content Distribution",
                "type": "gc_content",
            },
            {
                "file_glob": "multiqc/multiqc_plots/png/fastqc_sequence_duplication_levels_plot.png",
                "title": "Sequence Duplication Levels",
                "type": "duplication",
            },
            {
                "file_glob": "multiqc/multiqc_plots/png/fastqc_sequence_counts_plot-cnt.png",
                "title": "Sequence Counts",
                "type": "seq_counts",
            },
            {
                "file_glob": "multiqc/multiqc_plots/png/general_stats_table.png",
                "title": "General Statistics",
                "type": "general_stats",
            },
        ],
    }


def compute_quality(metrics: dict[str, Any]) -> str:
    dup = metrics.get("percent_duplicates")
    gc = metrics.get("percent_gc")
    total = metrics.get("total_sequences")
    mapping = metrics.get("reads_mapped_genome")

    if total is None and dup is None and mapping is None:
        return "pending_review"

    if mapping is not None and mapping < 0.5:
        return "concerning"

    if total is not None:
        if dup is not None and dup < 30 and gc is not None and 35 <= gc <= 65:
            if mapping is None or mapping >= 0.7:
                return "good"
            return "acceptable"
        if dup is not None and dup < 50:
            return "acceptable"
        return "acceptable" if dup is None else "concerning"

    return "pending_review"


__all__ = ["render_config", "compute_quality"]
