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


# ---- change_7.5 section 2.3: a reference per experiment, as two stated parts ----

# The annotation bioAF pins for each assembly it supplies, read from the launch table's GTF URLs
# (`pipeline_run_service._ENSEMBL_REFERENCE_BY_GENOME`). A stated release is usable only when it is
# this one: bioAF never quietly swaps one release for another.
_PINNED_ANNOTATION = {
    "GRCh38": "Ensembl 112",
    "GRCm39": "Ensembl 112",
    "GRCm38": "Ensembl 102",
    "GRCh37": "Ensembl 87",
    "GRCz11": "Ensembl 112",
    "mRatBN7.2": "Ensembl 112",
    "WBcel235": "Ensembl 112",
    "BDGP6": "Ensembl 112",
    "TAIR10": "Ensembl Plants 59",
}

# Which assembly an annotation release belongs to, where it belongs to exactly one. From the
# providers' release notes: GENCODE human releases 1 to 19 annotate GRCh37 and 20 onwards GRCh38;
# GENCODE mouse M1 annotates NCBIM37 (mm9), M2 to M25 GRCm38 and M26 onwards GRCm39. Ensembl
# releases are per species: human 55 to 75 are GRCh37 and 76 onwards GRCh38; mouse 68 to 102 are
# GRCm38 and 104 onwards GRCm39. Release 103 for mouse is left unmapped rather than guessed.
_GENCODE = re.compile(r"\bgencode\b\D{0,12}?(?:release\s*|v(?:ersion)?\s*)?(?P<mouse>m)?(?P<n>\d{1,3})\b", re.I)
_ENSEMBL = re.compile(r"\bensembl\b(?!\s*plants)\D{0,12}?(?:release\s*|r|v)?(?P<n>\d{2,3})\b", re.I)
_MOUSE = ("mus musculus", "mouse", "murine")
_HUMAN = ("homo sapiens", "human")

# Workflows whose raw-read analysis quantifies genes, and therefore depends on the annotation too.
GENE_QUANTIFYING_WORKFLOWS = ("nf-core/rnaseq", "nf-core/scrnaseq")


def _species(organism: str | None) -> str | None:
    text = (organism or "").lower()
    if any(word in text for word in _MOUSE):
        return "mouse"
    if any(word in text for word in _HUMAN):
        return "human"
    return None


def parse_annotation(stated: str | None, *, organism: str | None = None) -> dict | None:
    """A stated annotation release, and the assembly it establishes, or None when it names no release.

    ``{"provider": "gencode" | "ensembl", "release": "M23" | "102", "label", "assembly"}``. ``assembly``
    is None where the release does not settle it (an Ensembl release with no organism, or one this
    table leaves unmapped).
    """
    text = stated or ""
    match = _GENCODE.search(text)
    if match:
        number = int(match.group("n"))
        mouse = bool(match.group("mouse"))
        if mouse:
            assembly = "mm9" if number == 1 else ("GRCm38" if 2 <= number <= 25 else "GRCm39")
            release = f"M{number}"
        else:
            assembly = "GRCh37" if number <= 19 else "GRCh38"
            release = str(number)
        return {"provider": "gencode", "release": release, "label": f"GENCODE {release}", "assembly": assembly}
    match = _ENSEMBL.search(text)
    if match:
        number = int(match.group("n"))
        species = _species(organism) or _species(text)
        assembly = None
        if species == "human":
            assembly = "GRCh37" if 55 <= number <= 75 else ("GRCh38" if number >= 76 else None)
        elif species == "mouse":
            assembly = "GRCm38" if 68 <= number <= 102 else ("GRCm39" if number >= 104 else None)
        return {"provider": "ensembl", "release": str(number), "label": f"Ensembl {number}", "assembly": assembly}
    return None


def supplied_references(pipeline_key: str | None = None, *, reference_datasets: list | None = None) -> list[dict]:
    """The references bioAF can supply to a workflow: ``[{"assembly", "annotation", "source"}]``.

    Nothing listed this per workflow before; each genome-aligning workflow now declares the same list:
    bioAF's launch table with its pinned annotation, and the organisation's own Reference Datasets
    whose name or version names an assembly (and, where it names one, an annotation release).
    """
    declared = [
        {"assembly": assembly, "annotation": _PINNED_ANNOTATION.get(assembly), "source": "bioaf"}
        for assembly in sorted(supplied_assemblies(pipeline_key))
    ]
    for dataset in reference_datasets or []:
        words = f"{getattr(dataset, 'name', '')} {getattr(dataset, 'version', '')}"
        named = [a for a in assemblies_named(words) if not a["historical"]]
        if not named:
            continue
        annotation = parse_annotation(words)
        declared.append(
            {
                "assembly": named[0]["assembly"],
                "annotation": annotation["label"] if annotation else None,
                "source": f"reference_dataset:{getattr(dataset, 'id', '')}",
            }
        )
    return declared


def _part(stated: str | None, quote: str | None, **fields) -> dict:
    return {"stated": stated, "quote": quote, "resolved": None, "status": None, "reason": None, **fields}


def experiment_reference(experiment: dict, *, pipeline_key: str | None, supplied: list[dict]) -> dict:
    """An experiment's reference resolved against what a workflow can use, per part, never defaulted.

    Statuses, per part: ``usable`` (resolved to a reference bioAF supplies to this workflow),
    ``unavailable`` (stated, and bioAF cannot supply it), ``unresolved`` (the paper's statements
    contradict each other, or name nothing recognisable) and ``unstated``.
    """
    ref = experiment.get("reference") or {}
    assembly_in = ref.get("assembly") or {}
    annotation_in = ref.get("annotation") or {}
    stated_assembly = assembly_in.get("stated")
    stated_annotation = annotation_in.get("stated")
    assembly = _part(stated_assembly, assembly_in.get("quote"))
    annotation = _part(stated_annotation, annotation_in.get("quote"))
    target = pipeline_key or "this analysis"
    available = {r["assembly"] for r in supplied}

    release = parse_annotation(stated_annotation, organism=experiment.get("organism")) if stated_annotation else None
    named = assemblies_named(stated_assembly) if stated_assembly else []
    from_release = release["assembly"] if release else None

    # The assembly.
    if named:
        first = named[0]
        if from_release and first["assembly"] != from_release:
            assembly.update(
                status=UNRESOLVED,
                reason=(
                    f"the paper states {first['assembly']} (\"{assembly['quote'] or stated_assembly}\") and an "
                    f"annotation release that belongs to {from_release} (\"{annotation['quote'] or stated_annotation}\")"
                ),
            )
        elif first["historical"] or first["assembly"] not in available:
            assembly.update(
                status=UNAVAILABLE,
                reason=f"the paper states {first['assembly']}; bioAF cannot supply {first['assembly']} to {target}",
            )
        else:
            assembly.update(status=USABLE, resolved=first["assembly"])
    elif stated_assembly:
        assembly.update(
            status=UNRESOLVED, reason=f"the paper's reference ('{stated_assembly}') names no assembly bioAF recognises"
        )
    elif from_release:
        if from_release in available:
            assembly.update(status=USABLE, resolved=from_release, established_from="annotation release")
        else:
            assembly.update(
                status=UNAVAILABLE,
                established_from="annotation release",
                reason=f"the paper's annotation belongs to {from_release}; bioAF cannot supply {from_release} to {target}",
            )
    else:
        assembly.update(status=UNSTATED, reason="the paper does not state its reference")

    # The annotation.
    resolved_assembly = assembly["resolved"]
    pinned = next((r["annotation"] for r in supplied if r["assembly"] == resolved_assembly and r["annotation"]), None)
    offered = sorted({r["annotation"] for r in supplied if r["assembly"] == resolved_assembly and r["annotation"]})
    if stated_annotation:
        label = release["label"] if release else stated_annotation
        if assembly["status"] == UNRESOLVED and release and from_release != (named[0]["assembly"] if named else None):
            annotation.update(status=UNRESOLVED, reason=assembly["reason"])
        elif resolved_assembly and label in offered:
            annotation.update(status=USABLE, resolved=label)
        else:
            supplies = f"bioAF supplies {', '.join(offered)} for {resolved_assembly}" if offered else "bioAF supplies none"
            annotation.update(
                status=UNAVAILABLE,
                reason=f"the paper states {label}; {supplies}, and never swaps one release for another",
            )
    elif resolved_assembly and pinned:
        annotation.update(
            status=USABLE,
            resolved=pinned,
            assumption=f"the paper states no annotation release, so bioAF's pinned {pinned} for {resolved_assembly} is used",
        )
    else:
        annotation.update(status=UNSTATED, reason="the paper does not state its annotation")

    return {"assembly": assembly, "annotation": annotation, "workflow": pipeline_key}


def operation_reference(reference: dict, operation: str, *, pipeline_key: str | None) -> tuple[str, str | None]:
    """Whether an operation can proceed on this reference: ``(status, reason)``.

    Consistency with an author table and reanalysis of a processed matrix need no reference. Reanalysis
    from raw reads (and the QC metrics a run computes) needs a usable assembly, and a usable annotation
    too when the workflow quantifies genes.
    """
    if not requires_reference(operation):
        return USABLE, None
    assembly = reference.get("assembly") or {}
    if assembly.get("status") != USABLE:
        # Open question 5's recommendation: an unstated reference leaves the check unresolved. The
        # organism alone never establishes an assembly.
        return _operation_status(assembly)
    if operation in ("raw_reanalysis", "qc_from_run") and pipeline_key in GENE_QUANTIFYING_WORKFLOWS:
        annotation = reference.get("annotation") or {}
        if annotation.get("status") != USABLE:
            return _operation_status(annotation)
    return USABLE, None


def _operation_status(part: dict) -> tuple[str, str | None]:
    status = part.get("status") or UNSTATED
    return (UNRESOLVED if status == UNSTATED else status), part.get("reason")


def experiment_reference_blocker(experiment: dict) -> str | None:
    """The plan blocker for an experiment whose reference cannot be used to reanalyze its raw reads, or
    None. Names the experiment and the part that decided it."""
    workflow = experiment.get("workflow")
    status, reason = operation_reference(experiment.get("reference") or {}, "raw_reanalysis", pipeline_key=workflow)
    if status == USABLE:
        return None
    label = f"Experiment {experiment.get('id')} ({experiment.get('assay') or 'assay not stated'})"
    return (
        f"{label}: {reason or 'its reference is not established'}, so {_OPERATION_WORDS} are refused for it; "
        "bioAF never substitutes another reference. Checks that need no reference are unaffected."
    )


def experiment_reference_limitation(experiment: dict, *, pipeline_key: str | None, operation: str) -> dict | None:
    """The ``reference_unavailable`` limitation for a raw-reads operation on this experiment's own
    reference, or None when it is usable. The part that decided it is named as the resource."""
    reference = experiment.get("reference") or {}
    status, reason = operation_reference(reference, "raw_reanalysis", pipeline_key=pipeline_key)
    if status == USABLE:
        return None
    assembly = reference.get("assembly") or {}
    part = assembly if assembly.get("status") != USABLE else (reference.get("annotation") or {})
    reason = reason or "the paper does not state its reference"
    return {
        "kind": REFERENCE_UNAVAILABLE,
        "resource": part.get("stated") or "this experiment's reference",
        "operation": operation,
        "detail": (
            f"{reason[0].upper()}{reason[1:]}, so bioAF did not run {_OPERATION_WORDS} for experiment "
            f"{experiment.get('id')} ({experiment.get('assay') or 'assay not stated'}). bioAF never substitutes "
            "a default or another reference; checks that need no reference are unaffected."
        ),
    }
