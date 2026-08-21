"""ChIP-seq QC template.

Built-in template for nf-core/chipseq-style runs (lit_validation Phase 4, breadth
item #1). Like bulk_rnaseq it reads MultiQC aggregate stats from
``multiqc_data.json``, but a ChIP-seq run's toolchain differs: alignment is
BWA + samtools (not STAR), duplicates come from Picard MarkDuplicates, and the
ChIP-specific quality signals come from phantompeakqualtools (NSC/RSC) and MACS2
(peak count) + a FRiP score.

Counts are aggregated per-sample by MEAN, the basis a paper's per-sample claim is
compared against (the lit_validation calibration decision, 2026-07-08).

Fidelity note: the SHARED metrics (FastQC read depth/GC/length, samtools mapping,
Picard duplication) reuse the same real MultiQC conventions the bulk template was
verified against (the GSE309060 run-17 fixture). The ChIP-CORE section/key names
(phantompeakqualtools, MACS2 peak count, FRiP custom content) are best-effort
pending a real nf-core/chipseq run fixture, so the parser is defensive
(multi-candidate keys, honest-None on miss) rather than pinned to one exact schema.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_run import PipelineRun
from app.services.qc.extractors.gcs_helpers import get_results_bucket
from app.services.qc.multiqc_registry import read_depth_and_samples, roster_from_emitted

logger = logging.getLogger("bioaf.qc.chipseq")

EMPTY_METRICS: dict[str, Any] = {
    "total_samples": None,
    "total_sequences": None,
    "avg_sequence_length": None,
    "percent_duplicates": None,
    "percent_gc": None,
    "reads_mapped_genome": None,
    "reads_mapped_genome_unique": None,
    # ChIP-core
    "peak_count": None,
    "frip": None,
    "nsc": None,
    "rsc": None,
}

# The standard nf-core/chipseq MultiQC PNG set; the dashboard service collects
# whichever of these the run produced (matched by basename anywhere under multiqc/).
MULTIQC_PLOTS: list[tuple[str, str, str]] = [
    ("fastqc_per_base_sequence_quality_plot.png", "Per-Base Sequence Quality", "base_quality"),
    ("fastqc_per_sequence_gc_content_plot_Percentages.png", "GC Content Distribution", "gc_content"),
    ("fastqc_sequence_duplication_levels_plot.png", "Sequence Duplication Levels", "duplication"),
    ("samtools_alignment_plot-pct.png", "Alignment (samtools)", "samtools_alignment"),
    ("picard_deduplication-pct.png", "Duplication (Picard)", "picard_dedup"),
    ("phantompeakqualtools_nsc_rsc_plot.png", "Strand Cross-Correlation (NSC/RSC)", "nsc_rsc"),
    ("macs2_peak_count_plot.png", "Peak Count (MACS2)", "peak_count"),
    ("frip_score_plot.png", "FRiP Score", "frip"),
]


def render_config() -> dict:
    return {
        "template": "chipseq",
        "sections": [
            {
                "id": "hero",
                "layout": "hero",
                "metrics": ["total_samples", "peak_count", "frip", "reads_mapped_genome"],
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
                "id": "chip",
                "title": "ChIP Enrichment",
                "layout": "grid",
                "metrics": ["peak_count", "frip", "nsc", "rsc"],
            },
        ],
        "metrics": {
            "total_samples": {"label": "Samples", "format": "integer"},
            # Aggregated per-sample by mean, matching the per-sample basis the
            # lit_validation comparison uses.
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
                "thresholds": {"good": ">=0.01", "warn": ">=0.005"},
            },
            "nsc": {"label": "NSC", "format": "decimal", "thresholds": {"good": ">=1.05", "warn": ">=1.0"}},
            "rsc": {"label": "RSC", "format": "decimal", "thresholds": {"good": ">=0.8", "warn": ">=0.5"}},
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
            {
                "file_glob": "multiqc/multiqc_plots/png/phantompeakqualtools_nsc_rsc_plot.png",
                "title": "Strand Cross-Correlation (NSC/RSC)",
                "type": "nsc_rsc",
            },
        ],
    }


def compute_quality(metrics: dict[str, Any]) -> str:
    frip = metrics.get("frip")
    nsc = metrics.get("nsc")
    peaks = metrics.get("peak_count")
    mapping = metrics.get("reads_mapped_genome")

    if frip is None and nsc is None and peaks is None and mapping is None:
        return "pending_review"

    # Poor enrichment is the ChIP-specific failure signal: few/no peaks, very low
    # FRiP, or NSC below the phantompeakqualtools threshold.
    if peaks is not None and peaks <= 0:
        return "concerning"
    if frip is not None and frip < 0.005:
        return "concerning"
    if mapping is not None and mapping < 0.5:
        return "concerning"

    good_enrichment = (frip is not None and frip >= 0.01) or (nsc is not None and nsc >= 1.05)
    if good_enrichment and (mapping is None or mapping >= 0.7):
        return "good"
    if peaks is not None and peaks > 0:
        return "acceptable"
    return "pending_review"


# ---- MultiQC parsing (pure) ----


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
    """First numeric value among the given keys for one sample record."""
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
    """nf-core FastQC runs on raw + trimmed reads; MultiQC stores them as
    multiqc_fastqc / multiqc_fastqc_1. Trimming only removes reads, so the RAW
    section is the one with the higher total read count -- identify it by that
    physical invariant, not by the section suffix. Read depth is a pre-trim
    (raw) quantity."""
    sections = [v for k, v in raw.items() if isinstance(v, dict) and k.startswith("multiqc_fastqc")]
    best: dict = {}
    best_total = -1.0
    for sec in sections:
        total = sum(_numbers(sec, "Total Sequences", "total_sequences"))
        if total > best_total:
            best_total = total
            best = sec
    return best


def _plot_values(section: dict) -> list[float]:
    """Extract numeric values from a MultiQC bar-plot raw-data section.

    nf-core/chipseq stores MACS2 peak count + FRiP as bar-plot data under
    ``multiqc_peak_count-plot`` / ``multiqc_frip_score-plot``, shaped
    ``{sample: {series: value}}`` (the inner key is the sample/series LABEL, not a
    metric column) -- verified against the real run-22 multiqc_data.json. So take
    the numeric value regardless of the inner key name, unlike ``_numbers`` which
    matches a named column."""
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
    """Fuzzy scan of MultiQC's report_general_stats_data (a list of per-sample
    column-group dicts) for any of the given keys, matched case-insensitively as
    a substring so custom-content column ids (e.g. 'frip_score', 'FRiP') are
    found without pinning one exact name. Used as a fallback for the ChIP-core
    metrics, whose section/key names vary across chipseq versions."""
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


def read_multiqc_metrics(multiqc_json_text: str, *, emitted_roster: list[str] | None = None) -> dict[str, Any]:
    """Parse per-sample FastQC (raw) + samtools + Picard + phantompeakqualtools +
    MACS2/FRiP from a ChIP-seq multiqc_data.json, aggregating per-sample by mean.
    Mapping/duplication percentages are converted to the scale the controlled QC
    vocabulary uses (fraction for mapping, percent for duplication)."""
    try:
        data = json.loads(multiqc_json_text)
    except Exception as e:
        logger.warning("chipseq MultiQC JSON parsing failed: %s", e)
        return dict(EMPTY_METRICS)

    metrics = dict(EMPTY_METRICS)
    raw = data.get("report_saved_raw_data") or {}
    gstats = data.get("report_general_stats_data")

    # -- FastQC (raw): depth / GC / length / (fallback) duplication --
    fastqc = _pick_raw_fastqc(raw)
    if fastqc:
        total_seqs = _numbers(fastqc, "Total Sequences", "total_sequences")
        gc = _numbers(fastqc, "%GC", "percent_gc")
        dedup = _numbers(fastqc, "total_deduplicated_percentage")
        lengths = _numbers(fastqc, "avg_sequence_length")
        if total_seqs:
            # Per-SAMPLE derivation (aligner roster, raw FastQC counts, lanes
            # added and mates collapsed). ChIP runs are paired-end, so counting
            # FastQC entries reported twice the samples that exist.
            depth, total_samples, _sources = read_depth_and_samples(data, emitted_roster=emitted_roster)
            if depth is not None:
                metrics["total_sequences"] = depth
            if total_samples is not None:
                metrics["total_samples"] = total_samples
        if gc:
            metrics["percent_gc"] = round(_mean(gc), 1)
        if dedup:
            # FastQC reports % remaining after dedup; duplication is the complement.
            metrics["percent_duplicates"] = round(_mean([100.0 - d for d in dedup]), 1)
        if lengths:
            metrics["avg_sequence_length"] = round(_mean(lengths), 1)

    # -- samtools flagstat: mapping rate (prefer the pre-filter alignment view) --
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

    # -- Picard MarkDuplicates: authoritative duplication (0-1 -> percent) --
    picard = _find_section(raw, "multiqc_picard_dups", "picard_dups", "multiqc_picard_deduplication")
    if picard:
        dups = _numbers(picard, "PERCENT_DUPLICATION", "percent_duplication")
        if dups:
            # Picard reports a 0-1 fraction; the vocab's percent_duplicates is 0-100.
            metrics["percent_duplicates"] = round(_mean([d * 100.0 for d in dups]), 1)

    # -- phantompeakqualtools: NSC / RSC (strand cross-correlation) --
    phantom = _find_section(raw, "multiqc_phantompeakqualtools", "phantompeakqualtools", "multiqc_spp")
    nsc = _numbers(phantom, "NSC", "nsc") if phantom else []
    rsc = _numbers(phantom, "RSC", "rsc") if phantom else []
    if not nsc:
        nsc = _scan_general_stats(gstats, "nsc")
    if not rsc:
        rsc = _scan_general_stats(gstats, "rsc")
    if nsc:
        metrics["nsc"] = round(_mean(nsc), 3)
    if rsc:
        metrics["rsc"] = round(_mean(rsc), 3)

    # -- MACS2 peak count + FRiP: nf-core/chipseq stores these as MultiQC bar-plot data under
    # `multiqc_peak_count-plot` / `multiqc_frip_score-plot` ({sample: {series: value}}), NOT a flat
    # per-sample column (verified against the real run-22 output). Parse the plot values; fall back to
    # older names + the general-stats scan. Only the IP samples appear here (controls have no peaks).
    # chipseq names the section `multiqc_peak_count-plot`; atacseq prefixes it `_mlib_` (merged library),
    # verified against real run-22 (chipseq) + run-24 (atacseq) output. Accept both.
    peaks = _plot_values(
        _find_section(
            raw,
            "multiqc_peak_count-plot",
            "multiqc_mlib_peak_count-plot",
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
            raw, "multiqc_frip_score-plot", "multiqc_mlib_frip_score-plot", "multiqc_frip_score", "frip_score"
        )
    )
    if not frip:
        frip = _scan_general_stats(gstats, "frip")
    if frip:
        # FRiP is a 0-1 fraction; some reports express it as a 0-100 percent.
        vals = [f / 100.0 if f > 1.0 else f for f in frip]
        metrics["frip"] = round(_mean(vals), 4)

    return metrics


def _find_section(raw: dict, *names: str) -> dict:
    """Return the first raw-data section matching one of ``names``, tolerating
    MultiQC's numeric suffixes (e.g. multiqc_samtools_flagstat_1). Sections are
    dicts keyed by sample id."""
    for name in names:
        sec = raw.get(name)
        if isinstance(sec, dict) and sec:
            return sec
    # suffix-tolerant fallback: pick the largest matching section.
    best: dict = {}
    for name in names:
        for k, v in raw.items():
            if isinstance(v, dict) and v and (k == name or k.startswith(name + "_")):
                if len(v) > len(best):
                    best = v
    return best


async def extract(
    session: AsyncSession,
    run: PipelineRun,
    *,
    skip_cache: bool = False,
    results_bucket: str | None = None,
) -> dict[str, Any]:
    """Extract ChIP-seq QC metrics from GCS for the given run.

    Checks for a cached qc_metrics.json first; on miss, locates the run's
    multiqc_data.json anywhere under multiqc/ (nf-core/chipseq nests it under the
    peak-type dir, e.g. multiqc/narrowPeak/...), parses it, and writes the cache
    back. results_bucket can be passed to let callers mock it out.
    """
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

        # nf-core/chipseq nests MultiQC under the peak-type dir
        # (multiqc/narrowPeak/... or broadPeak/...); locate it anywhere under multiqc/.
        multiqc_objs = await adapter.list_objects(f"{base}{prefix}multiqc/")
        json_uris = [o.storage_uri for o in multiqc_objs if o.storage_uri.endswith("multiqc_data.json")]
        multiqc_uri = json_uris[0] if json_uris else None

        metrics = dict(EMPTY_METRICS)
        if multiqc_uri:
            logger.info("Found chipseq multiqc_data.json for run %d at %s", run.id, multiqc_uri[len(base) :])
            text = await adapter.read_text(multiqc_uri)
            metrics = read_multiqc_metrics(text, emitted_roster=roster_from_emitted(run.samplesheet_emitted_json))
            try:
                # Generic MultiQC report_plot_data parser (shared with the RNA-seq templates).
                from app.services.qc.templates.scrnaseq import read_multiqc_chart_data

                chart_data = read_multiqc_chart_data(text)
                if chart_data:
                    metrics["chart_data"] = chart_data
            except Exception as e:
                logger.warning("chipseq chart data extraction failed for run %d: %s", run.id, e)
        else:
            logger.info("No multiqc_data.json found under multiqc/ for run %d", run.id)

        has_any = any(v is not None for k, v in metrics.items() if k != "chart_data")
        if not has_any:
            logger.info("No chipseq metrics found for run %d", run.id)
            return dict(EMPTY_METRICS)

        await adapter.write_text(cache_uri, json.dumps(metrics, indent=2), content_type="application/json")
        logger.info("Wrote qc_metrics.json cache for run %d", run.id)
        return metrics

    except Exception as e:
        logger.warning("ChIP-seq metric extraction from storage failed for run %d: %s", run.id, e)
        return dict(EMPTY_METRICS)


def generate_summary(metrics: dict[str, Any]) -> str:
    """Plain-English summary for a ChIP-seq run."""
    n = metrics.get("total_samples")
    total = metrics.get("total_sequences")

    if n and total is not None:
        summary = f"This ChIP-seq run analyzed **{n} sample{'' if n == 1 else 's'}** with a mean of **{total:,} reads per sample**."
    elif n:
        summary = f"This ChIP-seq run analyzed **{n} sample{'' if n == 1 else 's'}**."
    elif total is not None:
        summary = f"This ChIP-seq run had a mean of **{total:,} reads per sample**."
    else:
        summary = "No metrics available."

    mapping = metrics.get("reads_mapped_genome")
    if mapping is not None:
        summary += f" **{mapping * 100:.1f}%** of reads mapped to the genome."

    peaks = metrics.get("peak_count")
    if peaks is not None:
        summary += f" A mean of **{peaks:,} peaks** were called per sample."

    frip = metrics.get("frip")
    if frip is not None:
        summary += f" Mean FRiP is **{frip * 100:.2f}%**."

    nsc = metrics.get("nsc")
    rsc = metrics.get("rsc")
    if nsc is not None and rsc is not None:
        summary += f" Strand cross-correlation NSC **{nsc:.2f}** / RSC **{rsc:.2f}**."

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
