"""change_7.5 section 1.3: a reference only where an operation needs one, and never a default.

Study 38's paper states "mm9 (ChIP-seq) / GENCODE M23 (RNA-seq transcriptome)". bioAF knew no mm9,
wrote "could not map the paper's reference genome ... the analysis run will use a default", and the
default was real: with no genome a launch keeps the pipeline's seeded parameters, and nf-core/rnaseq is
seeded with GRCh38, so a mouse RNA-seq run would have aligned to human.

**The record says what the paper states.** An older assembly of an organism bioAF carries is recognised
as an assembly (mm9, hg18, rn6, ...), so the record reads "the paper states mm9; bioAF cannot supply mm9
to nf-core/rnaseq" rather than "could not map". Nothing substitutes the nearest current assembly.

**A reference matters only to the operations that depend on one.** Consistency with an author table
and reanalysis of a processed gene matrix read the table's own identifiers and need none; reanalysis
from raw reads, and QC metrics computed from a run, need an assembly bioAF can supply.

**Stage 1 still holds one reference per paper.** A paper-level reference naming an assembly bioAF
cannot supply refuses reference-dependent operations for the whole paper, even for an experiment that
used another reference. That is conservative; section 2.3 stores a reference per experiment.
"""

from __future__ import annotations

import re

USABLE = "usable"
UNAVAILABLE = "unavailable"
UNRESOLVED = "unresolved"
UNSTATED = "unstated"

REFERENCE_UNAVAILABLE = "reference_unavailable"

# The CURRENT assembly of each organism bioAF aligns against, as the paper may spell it. Only these
# resolve to a launch token: Zv9 is not GRCz11 and Rnor_6.0 is not mRatBN7.2, so folding an older
# spelling onto the current token would align against a genome the paper never used.
CURRENT_ASSEMBLIES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("grch38", "hg38"), "GRCh38"),
    (("grch37", "hg19"), "GRCh37"),
    (("grcm39", "mm39"), "GRCm39"),
    (("grcm38", "mm10"), "GRCm38"),
    (("t2t", "chm13"), "T2T-CHM13"),
    (("grcz11", "danrer11"), "GRCz11"),
    (("mratbn7", "mratbn7.2", "rn7"), "mRatBN7.2"),
    # BDGP6 is the assembly FAMILY: Ensembl publishes point releases (BDGP6.32, BDGP6.46) that share
    # a coordinate system and differ in annotation.
    (("bdgp6", "dm6"), "BDGP6"),
    (("wbcel235", "ce11"), "WBcel235"),
    (("tair10",), "TAIR10"),
)

# Older assemblies of the same organisms. Recognised so the record can name what the paper states;
# bioAF supplies none of them, and adding them to its catalog is a non-goal (plan_7_5).
HISTORICAL_ASSEMBLIES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("mm9", "ncbim37"), "mm9"),
    (("mm8", "ncbim36"), "mm8"),
    (("hg18", "ncbi36"), "hg18"),
    (("hg17", "ncbi35"), "hg17"),
    (("rn6", "rnor_6.0", "rnor6"), "rn6"),
    (("rn5", "rnor_5.0", "rnor5"), "rn5"),
    (("rn4",), "rn4"),
    (("danrer10", "grcz10"), "danRer10"),
    (("danrer7", "zv9"), "danRer7"),
    (("dm3", "bdgp5"), "dm3"),
    (("ce10", "ws220"), "ce10"),
    (("tair9",), "TAIR9"),
)

# What each operation needs of a reference (section 1.3's table). Region-to-gene assignment needs the
# table's own assembly and its annotation.
_NEEDS_ASSEMBLY = {
    "author_results": False,
    "processed_reanalysis": False,
    "region_assignment": True,
    "raw_reanalysis": True,
    "qc_from_run": True,
}

_OPERATION_WORDS = "reanalysis from raw reads and QC metrics from a run"


def requires_reference(operation: str) -> bool:
    """Whether ``operation`` depends on an assembly. An unknown operation is assumed to."""
    return _NEEDS_ASSEMBLY.get(operation, True)


def _anchored(needle: str) -> re.Pattern:
    """A spelling matched as a whole token: "mm9" in "mm9 (ChIP-seq)" but never in "mm90"."""
    return re.compile(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])")


_PATTERNS = [
    (needles, token, historical)
    for table, historical in ((CURRENT_ASSEMBLIES, False), (HISTORICAL_ASSEMBLIES, True))
    for needles, token in table
]
_COMPILED = [([_anchored(n) for n in needles], token, historical) for needles, token, historical in _PATTERNS]


def assemblies_named(text) -> list[dict]:
    """Every assembly ``text`` names, in the order the PAPER names them.

    ``[{"assembly": "GRCm38", "historical": False}, ...]``. Two spellings of one assembly ("GRCh38
    (hg38)") are one entry. Matching is word-anchored, so a spelling inside another token is not an
    assembly.
    """
    lowered = str(text or "").lower()
    if not lowered.strip():
        return []
    found: list[tuple[int, str, bool]] = []
    for patterns, token, historical in _COMPILED:
        positions = [m.start() for p in patterns for m in [p.search(lowered)] if m]
        if positions:
            found.append((min(positions), token, historical))
    return [{"assembly": token, "historical": historical} for _at, token, historical in sorted(found)]


def supplied_assemblies(pipeline_key: str | None = None) -> set[str]:
    """The assemblies bioAF can supply to a workflow. Stage 1: the launch's reference table, the same
    for every workflow; section 2.3 declares a list per workflow."""
    from app.services.pipeline_run_service import _ENSEMBL_REFERENCE_BY_GENOME

    return set(_ENSEMBL_REFERENCE_BY_GENOME)


def paper_reference(reference_build: str | None, pipeline_key: str | None) -> dict:
    """The paper's reference, resolved against what bioAF can supply, and never defaulted.

    Returns ``{"status", "stated", "assembly", "reason"}``. ``assembly`` is the launch token only when
    the status is ``usable``; ``stated`` is the assembly the record names.
    """
    raw = " ".join(str(reference_build or "").split())
    if not raw:
        return {
            "status": UNSTATED,
            "stated": None,
            "assembly": None,
            "reason": "the paper does not state its reference",
        }
    named = assemblies_named(raw)
    if not named:
        return {
            "status": UNRESOLVED,
            "stated": raw,
            "assembly": None,
            "reason": f"the paper's reference ('{raw}') names no assembly bioAF recognises",
        }
    supplied = supplied_assemblies(pipeline_key)
    target = pipeline_key or "this analysis"
    missing = next((n["assembly"] for n in named if n["historical"] or n["assembly"] not in supplied), None)
    if missing:
        return {
            "status": UNAVAILABLE,
            "stated": missing,
            "assembly": None,
            "reason": f"the paper states {missing}; bioAF cannot supply {missing} to {target}",
            "workflow": target,
        }
    return {"status": USABLE, "stated": named[0]["assembly"], "assembly": named[0]["assembly"], "reason": None}


def reference_blocker(reference: dict) -> str | None:
    """The plan blocker for a reference that is not usable, or None when it is."""
    if reference["status"] == USABLE:
        return None
    if reference["status"] == UNAVAILABLE:
        opening = (
            f"The paper states {reference['stated']}. bioAF cannot supply {reference['stated']} to "
            f"{reference.get('workflow') or 'this analysis'}"
        )
        return (
            f"{opening}, so {_OPERATION_WORDS} are refused for this paper; bioAF never substitutes another "
            "assembly. Checks that need no reference are unaffected."
        )
    return (
        f"The {reference['reason'].removeprefix('the ')}, so it is unresolved: {_OPERATION_WORDS} are refused "
        "for this paper, and no default is used. Checks that need no reference are unaffected."
    )


def reference_limitation(reference: dict, *, operation: str) -> dict:
    """The ``reference_unavailable`` limitation for a refused reference-dependent operation."""
    return {
        "kind": REFERENCE_UNAVAILABLE,
        "resource": reference.get("stated") or "this paper's reference",
        "operation": operation,
        "detail": (
            f"{reference['reason'][0].upper()}{reference['reason'][1:]}, so bioAF did not run {_OPERATION_WORDS}. "
            "bioAF never substitutes a default or another assembly; checks that need no reference are unaffected."
        ),
    }
