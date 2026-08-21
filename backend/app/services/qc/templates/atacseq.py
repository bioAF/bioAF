"""ATAC-seq QC template.

Built-in template for nf-core/atacseq-style runs (lit_validation Phase 4, breadth
item #2). A sibling of the chipseq template: same MultiQC toolchain (FastQC +
BWA/Bowtie2 + samtools + Picard MarkDuplicates + MACS2 + FRiP), but ATAC-seq has
no antibody/immunoprecipitation, so there are no NSC/RSC strand-cross-correlation
metrics; instead its distinctive signal is chromatin accessibility (peaks + FRiP)
and TSS enrichment.

Counts are aggregated per-sample by MEAN (the lit_validation calibration decision).

Fidelity note (same posture as chipseq): the SHARED metrics reuse the real MultiQC
conventions the bulk template was verified against; the ATAC-core section/key names
(MACS2 peak count, FRiP, TSS enrichment) are best-effort pending a real
nf-core/atacseq run fixture, so the parser is defensive (multi-candidate keys +
report_general_stats_data fallback, honest-None on miss).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_run import PipelineRun
from app.services.qc.extractors.gcs_helpers import get_results_bucket
from app.services.qc.multiqc_registry import Roster, read_depth_and_samples
from app.services.qc.roster import roster_for_run

logger = logging.getLogger("bioaf.qc.atacseq")

EMPTY_METRICS: dict[str, Any] = {
    "total_samples": None,
    "total_sequences": None,
    "avg_sequence_length": None,
    "percent_duplicates": None,
    "percent_gc": None,
    "reads_mapped_genome": None,
    # ATAC-core
    "peak_count": None,
    "frip": None,
    "tss_enrichment": None,
}

MULTIQC_PLOTS: list[tuple[str, str, str]] = [
    ("fastqc_per_base_sequence_quality_plot.png", "Per-Base Sequence Quality", "base_quality"),
    ("fastqc_per_sequence_gc_content_plot_Percentages.png", "GC Content Distribution", "gc_content"),
    ("fastqc_sequence_duplication_levels_plot.png", "Sequence Duplication Levels", "duplication"),
    ("samtools_alignment_plot-pct.png", "Alignment (samtools)", "samtools_alignment"),
    ("picard_deduplication-pct.png", "Duplication (Picard)", "picard_dedup"),
    ("macs2_peak_count_plot.png", "Peak Count (MACS2)", "peak_count"),
    ("frip_score_plot.png", "FRiP Score", "frip"),
]


def render_config() -> dict:
    return {
        "template": "atacseq",
        "sections": [
            {
                "id": "hero",
                "layout": "hero",
                "metrics": ["total_samples", "peak_count", "frip", "tss_enrichment"],
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
                "metrics": ["reads_mapped_genome"],
            },
            {
                "id": "accessibility",
                "title": "Chromatin Accessibility",
                "layout": "grid",
                "metrics": ["peak_count", "frip", "tss_enrichment"],
            },
        ],
        "metrics": {
            "total_samples": {"label": "Samples", "format": "integer"},
            "total_sequences": {"label": "Mean Reads / Sample", "format": "integer"},
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
            "peak_count": {"label": "Peaks (MACS2)", "format": "integer"},
            "frip": {
                "label": "FRiP",
                "format": "percent_decimal",
                "thresholds": {"good": ">=0.2", "warn": ">=0.05"},
            },
            "tss_enrichment": {
                "label": "TSS Enrichment",
                "format": "decimal",
                "thresholds": {"good": ">=6", "warn": ">=4"},
            },
        },
        "charts": [
            {"type": "base_quality", "metric_key": "chart_data.base_quality", "title": "Per-Base Sequence Quality"},
            {"type": "gc_content", "metric_key": "chart_data.gc_content", "title": "GC Content Distribution"},
            {"type": "duplication", "metric_key": "chart_data.duplication", "title": "Sequence Duplication Levels"},
        ],
        "plots": [
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
        ],
    }


def compute_quality(metrics: dict[str, Any]) -> str:
    frip = metrics.get("frip")
    tss = metrics.get("tss_enrichment")
    peaks = metrics.get("peak_count")
    mapping = metrics.get("reads_mapped_genome")

    if frip is None and tss is None and peaks is None and mapping is None:
        return "pending_review"

    # Poor accessibility signal is the ATAC-specific failure: no peaks, low FRiP, or low TSS enrichment.
    if peaks is not None and peaks <= 0:
        return "concerning"
    if frip is not None and frip < 0.05:
        return "concerning"
    if tss is not None and tss < 4:
        return "concerning"
    if mapping is not None and mapping < 0.5:
        return "concerning"

    good_signal = (frip is not None and frip >= 0.2) or (tss is not None and tss >= 6)
    if good_signal and (mapping is None or mapping >= 0.7):
        return "good"
    if peaks is not None and peaks > 0:
        return "acceptable"
    return "pending_review"


# ---- MultiQC parsing (pure; mirrors the sibling chipseq/bulk_rnaseq templates) ----


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _field(sample: dict, *keys: str) -> float | None:
    for k in keys:
        if k in sample:
            n = _num(sample[k])
            if n is not None:
                return n
    return None


def _numbers(section: dict, *keys: str) -> list[float]:
    out: list[float] = []
    for v in section.values():
        if isinstance(v, dict):
            n = _field(v, *keys)
            if n is not None:
                out.append(n)
    return out


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _pick_raw_fastqc(raw: dict) -> dict:
    """Pick the RAW FastQC section by the physical invariant "raw has >= reads than
    trimmed" (max total across multiqc_fastqc*), not by the section suffix."""
    sections = [v for k, v in raw.items() if isinstance(v, dict) and k.startswith("multiqc_fastqc")]
    best: dict = {}
    best_total = -1.0
    for sec in sections:
        total = sum(_numbers(sec, "Total Sequences", "total_sequences"))
        if total > best_total:
            best_total = total
            best = sec
    return best


def _find_section(raw: dict, *names: str) -> dict:
    """First raw-data section matching one of ``names``, tolerating MultiQC numeric suffixes."""
    for name in names:
        sec = raw.get(name)
        if isinstance(sec, dict) and sec:
            return sec
    best: dict = {}
    for name in names:
        for k, v in raw.items():
            if isinstance(v, dict) and v and (k == name or k.startswith(name + "_")):
                if len(v) > len(best):
                    best = v
    return best


def _plot_values(section: dict) -> list[float]:
    """Numeric values from a MultiQC bar-plot raw-data section ({sample: {series: value}}); nf-core
    stores MACS2 peak count + FRiP this way (verified against the real chipseq run-22 output)."""
    out: list[float] = []
    for v in (section or {}).values():
        if isinstance(v, dict):
            for iv in v.values():
                n = _num(iv)
                if n is not None:
                    out.append(n)
        else:
            n = _num(v)
            if n is not None:
                out.append(n)
    return out


def _scan_general_stats(gstats: Any, *keys: str) -> list[float]:
    """Fuzzy scan of report_general_stats_data (list of per-sample column-group dicts) for any of the
    given keys as a case-insensitive substring. Fallback for ATAC-core metrics whose names vary."""
    wanted = [k.lower() for k in keys]
    out: list[float] = []
    if not isinstance(gstats, list):
        return out
    for group in gstats:
        if not isinstance(group, dict):
            continue
        for sample_vals in group.values():
            if not isinstance(sample_vals, dict):
                continue
            for col, val in sample_vals.items():
                cl = str(col).lower()
                if any(w in cl for w in wanted):
                    n = _num(val)
                    if n is not None:
                        out.append(n)
    return out


def read_multiqc_metrics(multiqc_json_text: str, *, run_roster: Roster | None = None) -> dict[str, Any]:
    """Parse per-sample FastQC (raw) + samtools + Picard + MACS2/FRiP + TSS enrichment from an
    ATAC-seq multiqc_data.json, aggregating per-sample by mean."""
    try:
        data = json.loads(multiqc_json_text)
    except Exception as e:
        logger.warning("atacseq MultiQC JSON parsing failed: %s", e)
        return dict(EMPTY_METRICS)

    metrics = dict(EMPTY_METRICS)
    raw = data.get("report_saved_raw_data") or {}
    gstats = data.get("report_general_stats_data")

    fastqc = _pick_raw_fastqc(raw)
    if fastqc:
        total_seqs = _numbers(fastqc, "Total Sequences", "total_sequences")
        gc = _numbers(fastqc, "%GC", "percent_gc")
        dedup = _numbers(fastqc, "total_deduplicated_percentage")
        lengths = _numbers(fastqc, "avg_sequence_length")
        if total_seqs:
            # Per-SAMPLE derivation (aligner roster, raw FastQC counts, lanes
            # added and mates collapsed). ATAC runs are paired-end, so counting
            # FastQC entries reported twice the samples that exist.
            depth, total_samples, _sources = read_depth_and_samples(data, run_roster=run_roster)
            if depth is not None:
                metrics["total_sequences"] = depth
            if total_samples is not None:
                metrics["total_samples"] = total_samples
        if gc:
            metrics["percent_gc"] = round(_mean(gc), 1)
        if dedup:
            metrics["percent_duplicates"] = round(_mean([100.0 - d for d in dedup]), 1)
        if lengths:
            metrics["avg_sequence_length"] = round(_mean(lengths), 1)

    flagstat = _find_section(raw, "multiqc_samtools_flagstat", "samtools_flagstat")
    if flagstat:
        fracs: list[float] = []
        for v in flagstat.values():
            if not isinstance(v, dict):
                continue
            pct = _field(v, "mapped_passed_pct")
            if pct is not None:
                fracs.append(pct / 100.0)
                continue
            mapped = _field(v, "mapped_passed", "mapped")
            total = _field(v, "total_passed", "total")
            if mapped is not None and total:
                fracs.append(mapped / total)
        if fracs:
            metrics["reads_mapped_genome"] = round(_mean(fracs), 4)

    picard = _find_section(raw, "multiqc_picard_dups", "picard_dups", "multiqc_picard_deduplication")
    if picard:
        dups = _numbers(picard, "PERCENT_DUPLICATION", "percent_duplication")
        if dups:
            metrics["percent_duplicates"] = round(_mean([d * 100.0 for d in dups]), 1)

    # MACS2 peak count + FRiP: MultiQC bar-plot data under `multiqc_peak_count-plot` /
    # `multiqc_frip_score-plot` ({sample: {series: value}}), like chipseq; fall back to older names + stats.
    # atacseq prefixes the peak/FRiP plot sections `_mlib_` (merged library), verified against real run-24
    # output; chipseq uses the bare name. Accept both.
    peaks = _plot_values(
        _find_section(
            raw,
            "multiqc_mlib_peak_count-plot",
            "multiqc_peak_count-plot",
            "multiqc_macs2_peak_count",
            "macs2_peak_count",
        )
    )
    if not peaks:
        peaks = _scan_general_stats(gstats, "peak_count", "num_peaks", "n_peaks")
    if peaks:
        metrics["peak_count"] = int(round(_mean(peaks)))

    frip = _plot_values(
        _find_section(
            raw, "multiqc_mlib_frip_score-plot", "multiqc_frip_score-plot", "multiqc_frip_score", "frip_score"
        )
    )
    if not frip:
        frip = _scan_general_stats(gstats, "frip")
    if frip:
        vals = [f / 100.0 if f > 1.0 else f for f in frip]
        metrics["frip"] = round(_mean(vals), 4)

    # TSS enrichment (ATAC-distinctive; sparsely present, so best-effort over named sections + stats).
    tss_section = _find_section(raw, "multiqc_tss_enrichment", "tss_enrichment", "multiqc_ataqv")
    tss = _numbers(tss_section, "tss_enrichment", "tss_score", "tsse") if tss_section else []
    if not tss:
        tss = _scan_general_stats(gstats, "tss_enrichment", "tss_score", "tsse")
    if tss:
        metrics["tss_enrichment"] = round(_mean(tss), 2)

    return metrics


async def extract(
    session: AsyncSession,
    run: PipelineRun,
    *,
    skip_cache: bool = False,
    results_bucket: str | None = None,
) -> dict[str, Any]:
    """Extract ATAC-seq QC metrics from GCS for the given run (multiqc_data.json anywhere under multiqc/)."""
    if results_bucket is None:
        results_bucket = await get_results_bucket(session)
    if not results_bucket:
        logger.warning("No results bucket configured, cannot extract metrics")
        return dict(EMPTY_METRICS)

    try:
        from app.adapters.models import StorageObjectNotFound
        from app.adapters.registry import get_storage_adapter

        adapter = get_storage_adapter()
        prefix = f"experiments/{run.experiment_id}/pipeline-runs/{run.id}/"
        base = adapter.build_uri(results_bucket, "")

        cache_uri = f"{base}{prefix}qc_metrics.json"
        if not skip_cache:
            try:
                cached = json.loads(await adapter.read_text(cache_uri))
                logger.info("Using cached qc_metrics.json for run %d", run.id)
                return cached
            except StorageObjectNotFound:
                pass

        multiqc_objs = await adapter.list_objects(f"{base}{prefix}multiqc/")
        json_uris = [o.storage_uri for o in multiqc_objs if o.storage_uri.endswith("multiqc_data.json")]
        multiqc_uri = json_uris[0] if json_uris else None

        metrics = dict(EMPTY_METRICS)
        if multiqc_uri:
            logger.info("Found atacseq multiqc_data.json for run %d at %s", run.id, multiqc_uri[len(base) :])
            text = await adapter.read_text(multiqc_uri)
            metrics = read_multiqc_metrics(text, run_roster=await roster_for_run(session, run))
            try:
                from app.services.qc.templates.scrnaseq import read_multiqc_chart_data

                chart_data = read_multiqc_chart_data(text)
                if chart_data:
                    metrics["chart_data"] = chart_data
            except Exception as e:
                logger.warning("atacseq chart data extraction failed for run %d: %s", run.id, e)
        else:
            logger.info("No multiqc_data.json found under multiqc/ for run %d", run.id)

        has_any = any(v is not None for k, v in metrics.items() if k != "chart_data")
        if not has_any:
            logger.info("No atacseq metrics found for run %d", run.id)
            return dict(EMPTY_METRICS)

        await adapter.write_text(cache_uri, json.dumps(metrics, indent=2), content_type="application/json")
        logger.info("Wrote qc_metrics.json cache for run %d", run.id)
        return metrics

    except Exception as e:
        logger.warning("ATAC-seq metric extraction from storage failed for run %d: %s", run.id, e)
        return dict(EMPTY_METRICS)


def generate_summary(metrics: dict[str, Any]) -> str:
    """Plain-English summary for an ATAC-seq run."""
    n = metrics.get("total_samples")
    total = metrics.get("total_sequences")

    if n and total is not None:
        summary = f"This ATAC-seq run analyzed **{n} sample{'' if n == 1 else 's'}** with a mean of **{total:,} reads per sample**."
    elif n:
        summary = f"This ATAC-seq run analyzed **{n} sample{'' if n == 1 else 's'}**."
    elif total is not None:
        summary = f"This ATAC-seq run had a mean of **{total:,} reads per sample**."
    else:
        summary = "No metrics available."

    mapping = metrics.get("reads_mapped_genome")
    if mapping is not None:
        summary += f" **{mapping * 100:.1f}%** of reads mapped to the genome."

    peaks = metrics.get("peak_count")
    if peaks is not None:
        summary += f" A mean of **{peaks:,} accessible peaks** were called per sample."

    frip = metrics.get("frip")
    if frip is not None:
        summary += f" Mean FRiP is **{frip * 100:.1f}%**."

    tss = metrics.get("tss_enrichment")
    if tss is not None:
        summary += f" TSS enrichment **{tss:.1f}**."

    dup = metrics.get("percent_duplicates")
    if dup is not None:
        health = "low" if dup < 30 else "moderate" if dup < 50 else "high"
        summary += f" Duplication rate is **{dup:.1f}%** ({health})."

    quality = metrics.get("quality_rating", "pending_review")
    summary += f" Overall quality: **{quality.capitalize()}**."
    return summary


__all__ = [
    "render_config",
    "compute_quality",
    "read_multiqc_metrics",
    "extract",
    "generate_summary",
    "EMPTY_METRICS",
    "MULTIQC_PLOTS",
]
