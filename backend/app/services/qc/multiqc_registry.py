"""Generic MultiQC metric extraction, shared by every pipeline type.

Every nf-core analysis pipeline finishes with MultiQC and writes
``multiqc_data.json``. The *container* is standard across pipelines and versions;
the *semantics* are not, because each tool names its own columns. So the unit of
reuse here is the **MultiQC module**, not the pipeline: map a module's columns to
the controlled QC vocabulary once, and every pipeline that runs that module is
covered, including ones nobody has installed yet.

This is the metric engine behind the ``generic`` template. The tailored templates
(scrnaseq, bulk_rnaseq, chipseq, atacseq) keep their own extractors; this exists
so a pipeline type without one still produces real numbers instead of nothing.

Three properties this module is deliberately built around, all learned from the
real reports in ``tests/fixtures/multiqc/``:

1. **Report structure drifts across MultiQC majors.** ``report_general_stats_data``
   is a list of per-sample dicts up to 1.23 and a dict keyed by module from 1.31.
2. **The same logical module appears under many section ids** (stage repeats,
   raw-vs-trimmed, merged-library infixes, Picard instance suffixes), so section
   ids are normalized before lookup.
3. **A wrong mapping is worse than a missing one.** Column matching is exact, an
   unmatched column degrades to None, and every unmapped numeric column is kept
   in an extras bucket rather than discarded.

Every metric emitted here is a QC floor (data quality and identity), never a
finding. See ``local/lit_validation/spec-06-validated-gate.md``: reproducing a
QC number is not reproducing a paper's result.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("bioaf.qc.multiqc_registry")

_SECTION_PREFIX = "multiqc_"
_STAGE_SUFFIX_RE = re.compile(r"_\d+$")
_INSTANCE_SUFFIX_RE = re.compile(r"-\d+")
_LIBRARY_INFIXES = ("mlib_", "mrep_")

# A section wider than this is a distribution (coverage histograms, saturation
# curves, per-contig idxstats), not a metric table. Harvesting it would bury the
# real numbers under thousands of bins.
_MAX_METRIC_COLUMNS = 40
# Above this share of numeric-looking column keys, the columns are histogram bins.
_NUMERIC_KEY_SHARE = 0.5
# Backstop so a pathological report cannot produce an unbounded extras blob.
_MAX_EXTRAS = 250


def normalize_section_id(section_id: str) -> str:
    """Reduce a MultiQC section id to the logical module it came from.

    ``multiqc_samtools_flagstat_2`` -> ``samtools_flagstat``;
    ``multiqc_picard-1_insertSize`` -> ``picard_insertsize``;
    ``multiqc_mlib_peak_count-plot`` -> ``peak_count-plot``.
    """
    name = (section_id or "").strip().lower()
    if name.startswith(_SECTION_PREFIX):
        name = name[len(_SECTION_PREFIX) :]
    name = _STAGE_SUFFIX_RE.sub("", name)
    name = _INSTANCE_SUFFIX_RE.sub("", name)
    for infix in _LIBRARY_INFIXES:
        if name.startswith(infix):
            name = name[len(infix) :]
    return name


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _numbers(section: dict, column: str) -> list[float]:
    """Every finite numeric value of ``column`` across the section's samples."""
    out: list[float] = []
    for sample in (section or {}).values():
        if isinstance(sample, dict) and column in sample and _is_number(sample[column]):
            out.append(float(sample[column]))
    return out


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


# --------------------------------------------------------------------------
# Section selection
# --------------------------------------------------------------------------

# A module whose selection cannot be "the unsuffixed section". FastQC is run on
# raw and trimmed reads and MultiQC's numbering does not say which is which, so
# pick by the physical invariant: trimming only removes reads, therefore raw is
# whichever section carries more.
_MAX_TOTAL_SELECTION: dict[str, str] = {"fastqc": "Total Sequences"}


def select_sections(raw: dict, module: str) -> list[dict]:
    """The section(s) of ``raw`` that represent ``module``, at most one.

    Stage repeats (``samtools_flagstat`` / ``_1`` / ``_2``) are the same tool run
    at different filtering stages with genuinely different totals, so they are
    never blended: the unsuffixed section wins.
    """
    candidates = [
        (section_id, body)
        for section_id, body in (raw or {}).items()
        if isinstance(body, dict) and body and normalize_section_id(section_id) == module
    ]
    if not candidates:
        return []

    column = _MAX_TOTAL_SELECTION.get(module)
    if column:
        scored = [(sum(_numbers(body, column)), section_id, body) for section_id, body in candidates]
        best = max(scored, key=lambda item: (item[0], item[1]))
        return [best[2]]

    unsuffixed = [(sid, body) for sid, body in candidates if not _STAGE_SUFFIX_RE.search(sid)]
    pool = unsuffixed or candidates
    return [min(pool, key=lambda item: item[0])[1]]


# --------------------------------------------------------------------------
# general_stats, across the 1.31 shape change
# --------------------------------------------------------------------------


def _general_stats_sections(data: dict) -> list[dict]:
    """MultiQC's own normalized summary, whatever shape this version wrote it in."""
    stats = (data or {}).get("report_general_stats_data")
    if isinstance(stats, list):
        return [s for s in stats if isinstance(s, dict)]
    if isinstance(stats, dict):
        return [s for s in stats.values() if isinstance(s, dict)]
    return []


def read_general_stats(data: dict, column: str) -> list[float]:
    """Values for ``column`` from the general-stats table.

    This is the one place a looser match is allowed: MultiQC qualifies
    general-stats column ids by module and version (``star-mapped_percent``,
    ``FastQC (raw)_mqc-generalstats-fastqc_raw-total_sequences``). The match is
    anchored on a separator so ``mapped_percent`` cannot swallow
    ``multimapped_percent``.
    """
    wanted = column.lower()
    out: list[float] = []
    for section in _general_stats_sections(data):
        for sample in section.values():
            if not isinstance(sample, dict):
                continue
            for key, value in sample.items():
                k = str(key).lower()
                if (k == wanted or k.endswith(f"-{wanted}")) and _is_number(value):
                    out.append(float(value))
    return out


# --------------------------------------------------------------------------
# The module registry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnMapping:
    """One module's claim on a controlled QC key.

    ``columns`` are exact MultiQC column ids in preference order (a module's
    column set differs across MultiQC majors, so several exact names are listed
    rather than matching loosely). ``priority`` breaks ties when more than one
    module can supply the same key; lower wins.
    """

    module: str
    columns: tuple[str, ...]
    key: str
    transform: str = "identity"  # identity | pct_to_frac | frac_to_pct | complement_pct
    priority: int = 0
    rounding: int = 1
    verified: bool = False  # proven against a real report in tests/fixtures/multiqc


# Ordered by (key, priority). Only add a mapping you can name the semantics of:
# a column that means something *close* belongs in extras, not here.
#
# Known hazard, deliberately accepted: `samtools_flagstat.mapped_passed_pct` is
# the share of records IN THE BAM that are mapped. For an aligner that emits only
# mapped reads (STAR's default RNA-seq output) that is trivially 100%, so an
# aligner-reported rate is preferred when the report carries one.
_REGISTRY: tuple[ColumnMapping, ...] = (
    # -- depth / composition (FastQC is the cross-pipeline constant) --
    ColumnMapping("fastqc", ("Total Sequences", "total_sequences"), "total_sequences", rounding=0, verified=True),
    ColumnMapping("fastqc", ("%GC", "percent_gc"), "percent_gc", verified=True),
    ColumnMapping("fastqc", ("avg_sequence_length",), "avg_sequence_length", verified=True),
    # -- duplication: Picard's alignment-level rate beats FastQC's estimate --
    ColumnMapping(
        "picard_dups",
        ("PERCENT_DUPLICATION",),
        "percent_duplicates",
        transform="frac_to_pct",
        priority=0,
        verified=True,
    ),
    ColumnMapping(
        "fastqc",
        ("total_deduplicated_percentage",),
        "percent_duplicates",
        transform="complement_pct",
        priority=1,
        verified=True,
    ),
    # -- genome mapping rate --
    ColumnMapping(
        "star",
        ("mapped_percent",),
        "reads_mapped_genome",
        transform="pct_to_frac",
        priority=0,
        rounding=4,
        verified=True,
    ),
    ColumnMapping(
        "bowtie2",
        ("overall_alignment_rate",),
        "reads_mapped_genome",
        transform="pct_to_frac",
        priority=1,
        rounding=4,
    ),
    ColumnMapping(
        "hisat2", ("overall_alignment_rate",), "reads_mapped_genome", transform="pct_to_frac", priority=1, rounding=4
    ),
    ColumnMapping(
        "bismark_alignment",
        ("percent_aligned",),
        "reads_mapped_genome",
        transform="pct_to_frac",
        priority=1,
        rounding=4,
    ),
    ColumnMapping(
        "samtools_flagstat",
        ("mapped_passed_pct",),
        "reads_mapped_genome",
        transform="pct_to_frac",
        priority=2,
        rounding=4,
        verified=True,
    ),
    # -- unique mapping rate --
    ColumnMapping(
        "star",
        ("uniquely_mapped_percent",),
        "reads_mapped_genome_unique",
        transform="pct_to_frac",
        rounding=4,
        verified=True,
    ),
)

_TRANSFORMS = {
    "identity": lambda v: v,
    "pct_to_frac": lambda v: v / 100.0,
    "frac_to_pct": lambda v: v * 100.0,
    "complement_pct": lambda v: 100.0 - v,
}

# Every controlled key this engine can emit. All are QC floor by construction.
GENERIC_METRIC_KEYS: tuple[str, ...] = tuple(dict.fromkeys(m.key for m in _REGISTRY)) + ("total_samples",)

EMPTY_METRICS: dict[str, Any] = {key: None for key in GENERIC_METRIC_KEYS}


# --------------------------------------------------------------------------
# Per-sample sequencing depth
# --------------------------------------------------------------------------

# Sections that are per-SAMPLE by construction: they are written after lanes are
# merged and mates paired, so their keys are the real sample roster. FastQC is
# deliberately absent: it has one entry per file.
_ROSTER_MODULES: tuple[str, ...] = (
    "star",
    "samtools_stats",
    "samtools_flagstat",
    "bismark_alignment",
    "bowtie2",
    "hisat2",
    "salmon",
)


def sample_roster(raw: dict) -> list[str]:
    """The run's real sample ids, or [] when no per-sample section is present."""
    for module in _ROSTER_MODULES:
        for section in select_sections(raw or {}, module):
            names = sorted(str(name) for name in section)
            if names:
                return names
    return []


def _attribute(entry_name: str, roster: list[str]) -> str | None:
    """The roster sample an entry belongs to: the longest id it starts with.

    FastQC decorates the sample id per file (`SAMPLE-101` -> `SAMPLE-101_1`,
    `SRX...REP1_T1` -> `SRX...REP1_T1_2`), so prefix matching attributes files to
    samples without having to know each pipeline's naming scheme. Longest wins so
    `sample_1` cannot swallow `sample_10`'s files.
    """
    best: str | None = None
    for sample in roster:
        if entry_name.startswith(sample) and (best is None or len(sample) > len(best)):
            best = sample
    return best


def read_depth_and_samples(data: dict) -> tuple[int | None, int | None, dict[str, str]]:
    """Per-sample raw read depth (mean across samples) and the sample count.

    Depth stays the RAW, pre-trim FastQC count because that is what the
    controlled key means and what papers report; the aligner's own total is
    post-trim and would silently change the quantity. The aligner is used only to
    establish the sample roster.

    Within a sample the DISTINCT per-file counts are summed: paired mates report
    identical counts and collapse, separate lanes differ and add. Two lanes with
    exactly equal read counts are indistinguishable from a mate pair and collapse;
    that is preferred over trusting FastQC's per-pipeline name suffixes.
    """
    raw = (data or {}).get("report_saved_raw_data")
    if not isinstance(raw, dict):
        raw = {}

    sources: dict[str, str] = {}
    roster = sample_roster(raw)

    fastqc_sections = select_sections(raw, "fastqc")
    entries: dict[str, float] = {}
    if fastqc_sections:
        for name, sample in fastqc_sections[0].items():
            if isinstance(sample, dict):
                for column in ("Total Sequences", "total_sequences"):
                    if column in sample and _is_number(sample[column]):
                        entries[str(name)] = float(sample[column])
                        break

    if not entries:
        # No read-level section. Report the roster size when there is one, and no
        # depth rather than a number derived from something else.
        total_samples = len(roster) or None
        if total_samples:
            sources["total_samples"] = _roster_module(raw) or "aligner"
        return None, total_samples, sources

    if not roster:
        # No trustworthy grouping. Fall back to the per-file mean rather than
        # inventing one from entry names.
        sources["total_sequences"] = "fastqc"
        sources["total_samples"] = "fastqc"
        return int(round(_mean(list(entries.values())))), len(entries), sources

    grouped: dict[str, set[float]] = {sample: set() for sample in roster}
    for name, value in entries.items():
        owner = _attribute(name, roster)
        # An entry matching no roster sample is still real data; keep it as its
        # own group so it cannot silently vanish from the depth.
        grouped.setdefault(owner if owner is not None else f"\0{name}", set()).add(value)

    per_sample = [sum(values) for values in grouped.values() if values]
    if not per_sample:
        return None, len(roster) or None, sources

    sources["total_sequences"] = "fastqc"
    sources["total_samples"] = _roster_module(raw) or "fastqc"
    return int(round(_mean(per_sample))), len(per_sample), sources


def _roster_module(raw: dict) -> str | None:
    for module in _ROSTER_MODULES:
        for section in select_sections(raw or {}, module):
            if section:
                return module
    return None


# --------------------------------------------------------------------------
# Extras
# --------------------------------------------------------------------------


def _looks_like_a_distribution(section: dict) -> bool:
    """Whether a section is curve/histogram data rather than a metric table."""
    columns: set[str] = set()
    for sample in section.values():
        if isinstance(sample, dict):
            columns.update(str(c) for c in sample)
    if not columns:
        return True
    if len(columns) > _MAX_METRIC_COLUMNS:
        return True
    numeric_keys = sum(1 for c in columns if _looks_numeric(c))
    return numeric_keys / len(columns) > _NUMERIC_KEY_SHARE


def _looks_numeric(text: str) -> bool:
    try:
        float(text)
    except (TypeError, ValueError):
        return False
    return True


def harvest_extras(raw: dict, exclude: frozenset[tuple[str, str]] = frozenset()) -> dict[str, float]:
    """Per-sample means of every numeric column with no controlled key.

    Keyed ``{module}.{column}`` so it can never collide with a controlled key,
    and so a scientist can see which tool reported it. ``exclude`` holds the
    (module, column) pairs that already won a controlled key.
    """
    extras: dict[str, float] = {}
    for section_id, body in sorted((raw or {}).items()):
        if not isinstance(body, dict) or not body:
            continue
        module = normalize_section_id(section_id)
        # Bar-plot sections are keyed by sample label, not by metric, and belong
        # to the per-type overlays (peak counts, FRiP) rather than here.
        if module.endswith("-plot") or _looks_like_a_distribution(body):
            continue
        columns: list[str] = []
        for sample in body.values():
            if isinstance(sample, dict):
                for column in sample:
                    if column not in columns:
                        columns.append(str(column))
        for column in columns:
            if (module, column) in exclude:
                continue
            values = _numbers(body, column)
            if not values:
                continue
            extras[f"{module}.{column}"] = round(_mean(values), 4)
    if len(extras) > _MAX_EXTRAS:
        kept = sorted(extras)[:_MAX_EXTRAS]
        logger.info("MultiQC extras truncated from %d to %d entries", len(extras), _MAX_EXTRAS)
        return {k: extras[k] for k in kept}
    return extras


# --------------------------------------------------------------------------
# The entry point
# --------------------------------------------------------------------------


def parse_multiqc_metrics(data: dict) -> dict[str, Any]:
    """Map one parsed ``multiqc_data.json`` onto the controlled QC vocabulary.

    Returns the controlled keys (None where nothing supplied them) plus
    ``metric_sources`` (which module supplied each) and ``additional_metrics``
    (everything numeric that has no controlled key).
    """
    metrics: dict[str, Any] = dict(EMPTY_METRICS)
    metrics["metric_sources"] = {}
    metrics["additional_metrics"] = {}

    raw = (data or {}).get("report_saved_raw_data")
    if not isinstance(raw, dict):
        raw = {}

    winners: set[tuple[str, str]] = set()
    claimed: dict[str, int] = {}

    for mapping in _REGISTRY:
        sections = select_sections(raw, mapping.module)
        for section in sections:
            for column in mapping.columns:
                values = _numbers(section, column)
                if not values:
                    continue
                # A higher-priority module already supplied this key.
                if mapping.key in claimed and claimed[mapping.key] <= mapping.priority:
                    break
                value = _TRANSFORMS[mapping.transform](_mean(values))
                metrics[mapping.key] = int(round(value)) if mapping.rounding == 0 else round(value, mapping.rounding)
                metrics["metric_sources"][mapping.key] = mapping.module
                claimed[mapping.key] = mapping.priority
                winners.add((mapping.module, column))
                break

    # Fall back to MultiQC's own normalized summary for anything still missing.
    for mapping in _REGISTRY:
        if metrics.get(mapping.key) is not None:
            continue
        for column in mapping.columns:
            values = read_general_stats(data, column)
            if not values:
                continue
            value = _TRANSFORMS[mapping.transform](_mean(values))
            metrics[mapping.key] = int(round(value)) if mapping.rounding == 0 else round(value, mapping.rounding)
            metrics["metric_sources"][mapping.key] = "general_stats"
            break

    # Depth and sample count are derived per SAMPLE, not per FastQC file entry,
    # so mates and lanes cannot distort them. This overrides the registry's
    # file-level `total_sequences` above.
    depth, total_samples, depth_sources = read_depth_and_samples(data)
    if depth is not None:
        metrics["total_sequences"] = depth
    metrics["total_samples"] = total_samples
    metrics["metric_sources"].update(depth_sources)

    metrics["additional_metrics"] = harvest_extras(raw, exclude=frozenset(winners))
    return metrics


def _count_samples(raw: dict) -> int | None:
    """Deprecated file-level count, kept only as the no-roster fallback path."""
    fastqc = select_sections(raw, "fastqc")
    if fastqc:
        return len(fastqc[0]) or None
    widest = 0
    for mapping in _REGISTRY:
        for section in select_sections(raw, mapping.module):
            widest = max(widest, len(section))
    return widest or None


__all__ = [
    "ColumnMapping",
    "EMPTY_METRICS",
    "GENERIC_METRIC_KEYS",
    "harvest_extras",
    "normalize_section_id",
    "parse_multiqc_metrics",
    "read_depth_and_samples",
    "read_general_stats",
    "sample_roster",
    "select_sections",
]
