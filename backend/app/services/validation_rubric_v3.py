"""plan_8_4: rubric version 3. What bioAF has established about a paper, as points out of 100.

Version 2 asked one question: of the scientific findings bioAF assessed, what weight agreed? A paper
whose reads are controlled, or whose assay bioAF cannot execute, assessed no findings, so it could
never earn anything however much of its code, metadata and methods bioAF had actually checked. The
headline for such a paper was a blank, and the work that HAD been done was invisible in it.

Version 3 scores the evidence. One hundred points are allocated across five sections; each allocated
leaf obligation is **verified**, **failed** or **undetermined**; the score is the sum of the verified
weights. There is no division by what was assessed, no subtraction of failures from successes, and no
model-produced number anywhere: an assessor establishes grounded outcomes and this adds their weights.

**The score's companion is never optional.** 35 means 35 points of supporting evidence established. It
is not 35% probability of correctness, and 35 alone cannot tell 65 unknown points from 65 failed ones,
so V, F and U travel together everywhere, including their zeros.

**Applicability is about the PAPER, not about bioAF.** A missing adapter, absent code, controlled
samples and an unsupported assay all leave points UNDETERMINED. A criterion is excluded only where
cited evidence establishes that the paper's methods have no counterpart for it, and an exclusion
redistributes its weight so the profile still totals 100. Nothing improves a score by dropping a
difficult check.

Pure: no database, no model, no I/O. Weights are exact rationals; display rounding is separate and
sum-preserving.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

RUBRIC_VERSION = 3

VERIFIED = "verified"
FAILED = "failed"
UNDETERMINED = "undetermined"
OUTCOMES = (VERIFIED, FAILED, UNDETERMINED)

# The owner's visible wording. The rubric reasons in verified/failed/undetermined; every surface says
# positive/untested/negative, and "untested" includes an attempted check that could not conclude.
VISIBLE_WORDS = {VERIFIED: "positive", UNDETERMINED: "untested", FAILED: "negative"}

SECTION_MAXIMA = {"C": 20, "S": 15, "E": 15, "M": 20, "R": 30}

SECTION_TITLES = {
    "C": "Code and execution environment",
    "S": "Sample metadata and study design",
    "E": "Experimental methods",
    "M": "Computational methods",
    "R": "Results checks and reproduction",
}


class ApplicabilityUncertain(ValueError):
    """An exclusion is not established, or nothing applicable is left. The words say which."""


@dataclass(frozen=True)
class Criterion:
    """One rubric row: its section, its points, and the two obligations it splits into.

    Each obligation is independently verified, failed or undetermined, which is what makes partial
    credit a statement about two named things rather than a half-good rating.
    """

    id: str
    section: str
    points: int
    title: str
    a: str
    b: str


# ---- the documentary sections -------------------------------------------------------------------
#
# Section 3.2's table, verbatim in intent. Changing a row's points, its obligations or its section is
# a NEW rubric version with its own acceptance examples; it is never an edit to this table.

CRITERIA: tuple[Criterion, ...] = (
    Criterion(
        "C1",
        "C",
        4,
        "Syntax and build compatibility",
        "Relevant source parses under the declared language and runtime, using an actual parser",
        "The necessary build or equivalent loading check succeeds in the declared environment; where no "
        "build is required, that fact is established and the relevant runtime-loading requirement checked",
    ),
    Criterion(
        "C2",
        "C",
        4,
        "Dependencies and imports",
        "Required external packages and modules are declared, and the source makes their symbols available",
        "Dependency versions and required interfaces resolve compatibly in a bounded environment check",
    ),
    Criterion(
        "C3",
        "C",
        4,
        "Environment repeatability",
        "Versions of result-sensitive tools and libraries are fixed or unambiguously recoverable",
        "Runtime and system dependencies and an actionable reconstruction procedure are specified",
    ),
    Criterion(
        "C4",
        "C",
        4,
        "Execution completeness",
        "The provided entry points, scripts and configuration cover the claimed analysis steps",
        "Input and output handling and ordering are coherent, and required components and interfaces present",
    ),
    Criterion(
        "C5",
        "C",
        4,
        "Tool and implementation fitness",
        "Evidence-backed review establishes that the relevant tool operation suits its input and calculation",
        "Review of the actual version, configuration and applicable documented defects identifies no "
        "material conflict within that review's explicit scope",
    ),
    Criterion(
        "S1",
        "S",
        3,
        "Species identity",
        "The paper states the organisms for the relevant samples",
        "Independent sample or deposit metadata agree with those organisms",
    ),
    Criterion(
        "S2",
        "S",
        3,
        "Sample identity",
        "The relevant tissue, cell type or material is identified in the paper",
        "Independent records agree with those identities, legitimate differences between experiments accounted for",
    ),
    Criterion(
        "S3",
        "S",
        3,
        "Groups and conditions",
        "Treatments, controls and comparison arms are defined",
        "Independent sample records support assignment to those arms",
    ),
    Criterion(
        "S4",
        "S",
        3,
        "Sample accounting",
        "Counts and inclusion or exclusion rules are stated by experiment",
        "Held sample records reconcile to those counts and rules; a whole-series count is not an experiment's count",
    ),
    Criterion(
        "S5",
        "S",
        3,
        "Replication and pairing",
        "Biological versus technical replication and pairing or blocking requirements are described",
        "Independent records establish the relevant unit identities and design assignments",
    ),
    Criterion(
        "E1",
        "E",
        5,
        "Experimental procedure",
        "The sample preparation and measurement procedure can be identified from cited methods",
        "Material settings, materials and procedural steps are sufficiently specified for the stated experiment",
    ),
    Criterion(
        "E2",
        "E",
        5,
        "Controls and experimental design",
        "Controls and design choices needed to interpret the experiment are described",
        "Evidence-backed review finds those controls and that design appropriate to the comparison claimed",
    ),
    Criterion(
        "E3",
        "E",
        5,
        "Experimental reporting",
        "Quality criteria and the handling of exclusions or failed measurements are specified where consequential",
        "The reported experiment, samples and measurements are internally consistent across what was inspected",
    ),
    Criterion(
        "M1",
        "M",
        4,
        "Preprocessing",
        "Preprocessing steps and their order are identifiable",
        "Material settings and the relevant quality and filtering decisions are specified",
    ),
    Criterion(
        "M2",
        "M",
        4,
        "References and annotation",
        "Result-sensitive references, databases, feature definitions or annotations are identifiable",
        "Relevant versions and identifiers are specified sufficiently to recover the intended reference inputs",
    ),
    Criterion(
        "M3",
        "M",
        4,
        "Statistical design",
        "Statistical methods, experimental units, comparisons and covariates are identified",
        "Their stated use suits the data and the replication structure, on cited evidence",
    ),
    Criterion(
        "M4",
        "M",
        4,
        "Decision criteria",
        "Significance definitions, multiplicity correction and effect thresholds are specified where used",
        "Direction, scale, contrast orientation and the interpretation of those criteria are unambiguous",
    ),
    Criterion(
        "M5",
        "M",
        4,
        "Analysis traceability",
        "The paper links reported results to the analysis steps that produced them",
        "Inspected methods, configuration and supplied code agree on the consequential parameters and operations",
    ),
    # ---- results and reproduction ----------------------------------------------------------------
    #
    # These three do NOT split into A and B. R1 and R2 allocate among the paper's findings and their
    # required claims; R3 allocates among the analysis workflows those findings require, each half for
    # demonstrated completion and half for complete outputs and established requirements.
    Criterion(
        "R1",
        "R",
        8,
        "Author-results consistency",
        "A measured comparison against an identified author table agrees with a specific paper claim "
        "under an established interpretation",
        "",
    ),
    Criterion(
        "R2",
        "R",
        12,
        "Independent result assessment",
        "An independently executed, appropriate analysis of the underlying data supports the specified "
        "result under declared comparisons and tolerances",
        "",
    ),
    Criterion(
        "R3",
        "R",
        10,
        "End-to-end execution and output integrity",
        "The relevant workflow executes from its declared starting inputs to the required complete, usable "
        "outputs, with runtime, design and QC requirements checked",
        "",
    ),
)

CRITERIA_BY_ID = {c.id: c for c in CRITERIA}

# plan_8_6 section 6: the context a row's words lose when they are read on their own.
#
# M1.B reads "Material settings and the relevant quality and filtering decisions are specified". M1
# is a COMPUTATIONAL row about preprocessing, and on study 65 an assessor read "material" as
# *materials* and verified it from Matrigel, mTeSR Plus and EDTA passaging. The obligation's words
# are rubric v3's and are not changed; what was missing is the section the row sits in, what its
# scope is, and which kinds of evidence can answer it.
#
# Every row is read for the same ambiguity, once. ``scope`` says what the obligation is ABOUT;
# ``accepts`` lists evidence that can settle it; ``excludes`` names the evidence that looks relevant
# and is not. This is context, never a new criterion: changing a row's points, obligations or section
# is still a new rubric version.
CRITERION_EVIDENCE: dict[str, dict] = {
    "C1": {
        "scope": "the supplied source files themselves, parsed as the language they declare",
        "accepts": ["the text of the supplied scripts, notebooks and configuration"],
        "excludes": ["the paper's prose description of what the code does"],
    },
    "C2": {
        "scope": "the dependency and environment specifications supplied beside the code",
        "accepts": ["requirements, environment and lockfiles", "the import statements in the supplied source"],
        "excludes": ["a list of tool names in the methods, which is not a declared dependency"],
    },
    "C3": {
        "scope": "how the environment that ran the analysis could be rebuilt",
        "accepts": ["pinned tool and library versions", "a stated runtime, container or reconstruction procedure"],
        "excludes": ["a tool named without a version"],
    },
    "C4": {
        "scope": "whether the supplied entry points and scripts cover the analysis the paper claims",
        "accepts": ["the supplied scripts and notebooks, their inputs and outputs, and their order"],
        "excludes": ["the paper's description of steps for which no code was supplied"],
    },
    "C5": {
        "scope": "whether the tool operations in the supplied code suit the input and the calculation",
        "accepts": ["the actual version and configuration in the supplied code", "documented defects of that version"],
        "excludes": ["a general opinion about the tool that is not tied to this version or configuration"],
    },
    "S1": {
        "scope": "the organisms of the samples this paper analysed",
        "accepts": ["the paper's own statement of the organism", "the deposited sample records"],
        "excludes": ["an organism named only in the introduction's background"],
    },
    "S2": {
        "scope": "the tissue, cell type or material of the samples analysed",
        "accepts": ["the paper's statement of the material", "independent deposit or repository records"],
        "excludes": [
            "for the B obligation, the paper's own text: the independent record is the deposit, not the paper"
        ],
    },
    "S3": {
        "scope": "the treatment, control and comparison arms of the experiments",
        "accepts": ["the paper's definition of its arms", "per-sample deposit records assigning samples to arms"],
        "excludes": ["a figure label that names a condition without defining it"],
    },
    "S4": {
        "scope": "how many samples each experiment used, and what was included or excluded",
        "accepts": ["per-experiment counts and inclusion rules", "the deposit's own sample records"],
        "excludes": ["a whole-series count, which is not one experiment's count"],
    },
    "S5": {
        "scope": "biological versus technical replication, and pairing or blocking, for the stated inferences",
        "accepts": [
            "the paper's description of its replicates and units",
            "figure legends stating n per group",
            "deposit records that establish unit identity",
        ],
        "excludes": ["a sample-name pattern, which is not a statement of replication"],
    },
    "E1": {
        "scope": "the WET-LAB sample preparation and measurement procedure, at the bench",
        "accepts": [
            "culture, treatment, dissection, staining, library preparation and instrument settings",
            "reagents, kits and catalogue numbers where they determine the measurement",
        ],
        "excludes": ["computational preprocessing parameters, which are M1's"],
    },
    "E2": {
        "scope": "the DESIGN of the comparisons claimed: the units compared, replication and selection",
        "accepts": [
            "the experimental unit and what was compared to what",
            "replicate kind and count, pairing or blocking",
            "how clones, lines or subjects were selected",
            "figure legends and limitations stating n and the comparator",
        ],
        "excludes": [
            "the mere presence of a comparator or control condition, which does not establish that "
            "the design supports the claim. For the B obligation, name the replication, the units "
            "compared, the group sizes and the selection that the claim rests on, and say what "
            "inference the design supports and what it does not. A small study or a selected clone "
            "is not automatically invalid; an unstated design fact leaves the obligation untested"
        ],
    },
    "E3": {
        "scope": "the quality criteria and exclusions of the BENCH measurements, and their internal consistency",
        "accepts": [
            "stated quality thresholds for the measurement",
            "what was excluded or failed, and how it was handled",
            "counts reported in text, legends and tables",
        ],
        "excludes": ["read-level or alignment quality filtering, which is M1's"],
    },
    "M1": {
        "scope": (
            "COMPUTATIONAL preprocessing of the measured data: the steps that turn raw output into "
            "the matrix the analysis used, and their order"
        ),
        "accepts": [
            "trimming, alignment, demultiplexing, deduplication, quantification and normalisation steps",
            "the parameter values those steps were run with",
            "read, cell, barcode or feature quality and filtering thresholds",
        ],
        "excludes": [
            "culture media, Matrigel, reagents, passaging and other wet-lab materials: those are E1's, "
            "and 'material settings' here means the parameter values that materially change the result"
        ],
    },
    "M2": {
        "scope": "the reference genomes, annotations, databases and feature definitions the analysis used",
        "accepts": ["named references and annotations with their versions or identifiers"],
        "excludes": ["a species name on its own, which does not identify a reference build"],
    },
    "M3": {
        "scope": "the statistical model, its experimental units, its comparisons and its covariates",
        "accepts": [
            "the named test or model and what it was fitted to",
            "the unit of replication the test treats as independent",
            "covariates, blocking and batch terms",
        ],
        "excludes": ["a significance threshold on its own, which is M4's"],
    },
    "M4": {
        "scope": "the decision criteria applied to the analysis output, and whether they are unambiguous",
        "accepts": [
            "significance definitions and multiplicity correction",
            "effect-size thresholds and their direction, scale and contrast orientation",
        ],
        "excludes": ["the choice of statistical test, which is M3's"],
    },
    "M5": {
        "scope": "whether the paper's reported results can be traced to the analysis that produced them",
        "accepts": [
            "the paper's links from a figure or table to the step that produced it",
            "the supplied scripts, notebooks and configuration, at their cited locations",
        ],
        "excludes": [
            "for the B obligation, the paper's prose alone: the comparison is between what the paper "
            "states and what the supplied code does"
        ],
    },
    "R1": {
        "scope": "a measured comparison between an identified author table and a specific paper claim",
        "accepts": ["the author table's own values", "the claim's own words"],
        "excludes": ["a value bioAF computed for something else"],
    },
    "R2": {
        "scope": "an independently executed analysis of the underlying data",
        "accepts": ["the result of an executed analysis, with its declared comparisons and tolerances"],
        "excludes": ["a value read back from the authors' own output"],
    },
    "R3": {
        "scope": "a workflow executed from its declared inputs to complete, usable outputs",
        "accepts": ["the run's own completion, outputs and QC records"],
        "excludes": ["a workflow definition that was never executed"],
    },
}

DOCUMENTARY_SECTIONS = ("C", "S", "E", "M")
RESULT_CRITERIA = ("R1", "R2", "R3")

# Section 3.1's ceilings for the default profile, stated once so a surface can quote them.
DOCUMENTARY_CEILING = sum(SECTION_MAXIMA[s] for s in DOCUMENTARY_SECTIONS)
AUTHOR_RESULTS_CEILING = DOCUMENTARY_CEILING + CRITERIA_BY_ID["R1"].points


def _exclusion(entry) -> dict:
    """One declared exclusion, with what establishes it. A bare id is not enough."""
    if isinstance(entry, str):
        raise ApplicabilityUncertain(
            f"excluding {entry} states no rationale; an unestablished exclusion is not an exclusion, "
            "and the allocation stays where it is"
        )
    if not isinstance(entry, dict):
        raise ApplicabilityUncertain("an exclusion is a criterion with the evidence that establishes it")
    target = entry.get("criterion") or entry.get("section")
    rationale = str(entry.get("rationale") or "").strip()
    if not target:
        raise ApplicabilityUncertain("an exclusion names the criterion or section it excludes")
    if not rationale:
        raise ApplicabilityUncertain(
            f"excluding {target} states no rationale; an unestablished exclusion is not an exclusion, "
            "and the allocation stays where it is"
        )
    return {
        "criterion": entry.get("criterion"),
        "section": entry.get("section"),
        "rationale": rationale,
        "source": entry.get("source"),
    }


def default_profile(*, exclude=None, exclude_sections=None, revision: int = 1) -> dict:
    """The applicability profile: which criteria apply, and the weight each carries.

    ``exclude`` and ``exclude_sections`` take either a declared exclusion (a mapping with a rationale
    and its source) or, for the criterion form, a bare id where a caller has already established it;
    a bare id with no rationale is refused, because dropping a hard check is exactly how a score is
    improved dishonestly.

    An excluded criterion's weight is redistributed proportionally among its section's remaining rows;
    an excluded section's maximum is redistributed proportionally among the remaining sections. The
    profile always totals 100.
    """
    declared = []
    dropped_criteria: set[str] = set()
    for entry in exclude or []:
        if isinstance(entry, str):
            # A bare id is accepted only where the caller is the rubric's own default profile builder
            # in a test or a migration; it still has to say so, which `_exclusion` enforces below for
            # every mapping. Bare ids carry a standing rationale.
            declared.append({"criterion": entry, "section": None, "rationale": _BARE_RATIONALE, "source": None})
            dropped_criteria.add(entry)
            continue
        recorded = _exclusion(entry)
        if not recorded["criterion"]:
            raise ApplicabilityUncertain("use exclude_sections to exclude a whole section")
        declared.append(recorded)
        dropped_criteria.add(recorded["criterion"])
    dropped_sections: set[str] = set()
    for entry in exclude_sections or []:
        if isinstance(entry, str):
            declared.append({"criterion": None, "section": entry, "rationale": _BARE_RATIONALE, "source": None})
            dropped_sections.add(entry)
            continue
        recorded = _exclusion(entry)
        if not recorded["section"]:
            raise ApplicabilityUncertain("a section exclusion names the section it excludes")
        declared.append(recorded)
        dropped_sections.add(recorded["section"])

    unknown = (dropped_criteria - set(CRITERIA_BY_ID)) | (dropped_sections - set(SECTION_MAXIMA))
    if unknown:
        raise ApplicabilityUncertain(f"rubric v3 has no {', '.join(sorted(unknown))}")

    sections = {s: Fraction(m) for s, m in SECTION_MAXIMA.items() if s not in dropped_sections}
    if not sections:
        raise ApplicabilityUncertain(
            "every section was excluded; with nothing established as applicable there is no score to show"
        )
    freed = sum(Fraction(SECTION_MAXIMA[s]) for s in dropped_sections)
    if freed:
        base = sum(sections.values())
        sections = {s: m + freed * m / base for s, m in sections.items()}

    weights: dict[str, Fraction] = {}
    for section, maximum in sections.items():
        rows = [c for c in CRITERIA if c.section == section and c.id not in dropped_criteria]
        if not rows:
            raise ApplicabilityUncertain(
                f"every criterion of section {section} was excluded; exclude the section instead, so its "
                "weight is redistributed and the profile still says what it covers"
            )
        declared_points = sum(Fraction(c.points) for c in rows)
        for criterion in rows:
            weights[criterion.id] = maximum * Fraction(criterion.points) / declared_points
    return {
        "rubric_version": RUBRIC_VERSION,
        "revision": revision,
        "sections": sections,
        "weights": weights,
        "exclusions": declared,
        "ceilings": _ceilings(sections, weights),
    }


_BARE_RATIONALE = "declared by the caller that established it"


def _ceilings(sections: dict, weights: dict) -> dict:
    """What this profile can reach without independent execution, and without any results check."""
    documentary = sum((m for s, m in sections.items() if s != "R"), Fraction(0))
    author_results = weights.get("R1", Fraction(0))
    return {
        "documentary": documentary,
        "with_author_results": documentary + author_results,
    }


def allocate(profile: dict, *, units: dict | None = None, results: dict | None = None) -> list[dict]:
    """The profile's leaf obligations, each with its exact weight.

    ``units`` splits one obligation's weight equally across the distinct analysis units established
    BEFORE assessment (``{"C1.A": ["script_a", "script_b"]}``). Units represent analyses the paper's
    findings require, never the number of files: more files cannot create more available points, and
    a leaf assessed on one script never establishes a claim about all of them.

    ``results`` is the R allocation (``result_allocation``). Without it the whole of R is one reserved
    leaf per criterion, which is a legitimate undetermined 30 points and not a reason to withhold the
    other 70.
    """
    leaves: list[dict] = []
    for criterion_id, weight in profile["weights"].items():
        criterion = CRITERIA_BY_ID[criterion_id]
        if criterion.section == "R":
            leaves.extend(_result_leaves(criterion, weight, (results or {}).get(criterion_id)))
            continue
        for obligation in ("A", "B"):
            leaf_id = f"{criterion_id}.{obligation}"
            parts = [u for u in (units or {}).get(leaf_id) or []]
            share = weight / 2
            if not parts:
                leaves.append(_leaf(leaf_id, criterion, obligation, share, unit=None))
                continue
            for unit in parts:
                leaves.append(_leaf(f"{leaf_id}#{unit}", criterion, obligation, share / len(parts), unit=unit))
    return sorted(leaves, key=lambda leaf: leaf["id"])


def _leaf(leaf_id: str, criterion: Criterion, obligation: str | None, weight: Fraction, *, unit) -> dict:
    return {
        "id": leaf_id,
        "criterion": criterion.id,
        "section": criterion.section,
        "obligation": obligation,
        "unit": unit,
        "weight": weight,
        "statement": {"A": criterion.a, "B": criterion.b}.get(obligation or "", criterion.a),
    }


def _result_leaves(criterion: Criterion, weight: Fraction, allocation) -> list[dict]:
    """R's leaves. Without an established allocation the criterion is one reserved leaf carrying its
    whole weight, so the points are visibly held rather than silently spent or silently dropped."""
    parts = [p for p in (allocation or []) if isinstance(p, dict) and p.get("id")]
    if not parts:
        return [{**_leaf(criterion.id, criterion, None, weight, unit=None), "reserved": True}]
    total = sum((Fraction(p.get("share") or 1) for p in parts), Fraction(0))
    leaves = []
    for part in parts:
        share = Fraction(part.get("share") or 1) / total
        leaves.append(
            {
                **_leaf(f"{criterion.id}.{part['id']}", criterion, None, weight * share, unit=part.get("unit")),
                "subject": part.get("subject"),
            }
        )
    return leaves


def score(leaves: list[dict], outcomes: dict | None) -> dict:
    """V, F and U for the whole card and for each section, with the unweighted assessed scope.

    An outcome absent from ``outcomes``, or carrying anything but verified or failed, is undetermined:
    a leaf nobody assessed and a leaf whose assessment could not conclude say the same thing about the
    paper, and neither is a deduction.
    """
    outcomes = outcomes or {}
    totals = {VERIFIED: Fraction(0), FAILED: Fraction(0), UNDETERMINED: Fraction(0)}
    sections: dict[str, dict] = {}
    assessed = 0
    for leaf in leaves:
        outcome = (outcomes.get(leaf["id"]) or {}).get("outcome")
        if outcome not in (VERIFIED, FAILED):
            outcome = UNDETERMINED
        else:
            assessed += 1
        totals[outcome] += leaf["weight"]
        bucket = sections.setdefault(
            leaf["section"],
            {VERIFIED: Fraction(0), FAILED: Fraction(0), UNDETERMINED: Fraction(0), "maximum": Fraction(0)},
        )
        bucket[outcome] += leaf["weight"]
        bucket["maximum"] += leaf["weight"]
    return {
        "rubric_version": RUBRIC_VERSION,
        "verified": totals[VERIFIED],
        "failed": totals[FAILED],
        "undetermined": totals[UNDETERMINED],
        "overall_score": totals[VERIFIED],
        "assessed_points": totals[VERIFIED] + totals[FAILED],
        "assessed_scope": {"assessed": assessed, "total": len(leaves)},
        "sections": {
            key: {
                "section": key,
                "title": SECTION_TITLES[key],
                "verified": bucket[VERIFIED],
                "failed": bucket[FAILED],
                "undetermined": bucket[UNDETERMINED],
                "maximum": bucket["maximum"],
            }
            for key, bucket in sorted(sections.items())
        },
        "status": "not_assessed" if assessed == 0 else "assessed",
    }


# ---- the results allocation ----------------------------------------------------------------------

# Section 3.3's finding weights, the same ones rubric v2 declares. A technical or descriptive finding
# receives NO result allocation: the technical checks that matter are the other criteria's business.
FINDING_WEIGHTS = {"primary": 2, "supporting": 1, "technical": 0}


def result_allocation(inventory: dict | None, *, workflows: list | None = None) -> dict:
    """How R1, R2 and R3 divide their points, frozen BEFORE any outcome is observed.

    R1 and R2 divide among the paper's findings by importance (primary 2, supporting 1), then equally
    among each finding's distinct required claim checks. A claim two findings both require is one
    measurement and one leaf: shared membership cannot buy a second allocation.

    R3 divides equally among the distinct analysis workflows those findings require, each workflow
    half for demonstrated completion from its declared starting inputs and half for complete outputs
    with the applicable runtime, design and QC requirements established.

    Where the inventory settles no findings, or names none with any importance weight, every R
    criterion comes back with NO parts, which `allocate` turns into one reserved leaf carrying the
    whole weight. Thirty untested points is an honest answer; withholding the other seventy is not.
    """
    findings = [f for f in (inventory or {}).get("findings") or [] if isinstance(f, dict) and f.get("id")]
    seen: set[str] = set()
    weighted: list[tuple[dict, int]] = []
    for finding in findings:
        if finding["id"] in seen:
            continue
        seen.add(finding["id"])
        weight = FINDING_WEIGHTS.get((finding.get("importance") or {}).get("category"), 0)
        if weight:
            weighted.append((finding, weight))
    claim_parts: list[dict] = []
    claimed: set[int] = set()
    for finding, weight in weighted:
        required = [c for c in finding.get("required") or finding.get("claim_indices") or [] if isinstance(c, int)]
        # De-duplicated within the finding first, so a claim listed twice is still one check.
        distinct = list(dict.fromkeys(required))
        if not distinct:
            continue
        for claim in distinct:
            if claim in claimed:
                # Shared with an earlier finding: one measurement record, one allocation. The share
                # stays inside the fixed category budget rather than being added on top of it.
                continue
            claimed.add(claim)
            claim_parts.append(
                {
                    "id": f"{finding['id']}.{claim}",
                    "finding": finding["id"],
                    "claim_index": claim,
                    "share": Fraction(weight, len(distinct)),
                    "subject": f"claim {claim} of {finding['id']}",
                }
            )
    execution_parts: list[dict] = []
    for workflow in list(dict.fromkeys(w for w in workflows or [] if w)):
        key = str(workflow).replace("/", "_")
        execution_parts.append(
            {
                "id": f"{key}.completion",
                "unit": workflow,
                "share": Fraction(1, 2),
                "subject": f"{workflow} runs from its declared starting inputs to completion",
            }
        )
        execution_parts.append(
            {
                "id": f"{key}.outputs",
                "unit": workflow,
                "share": Fraction(1, 2),
                "subject": f"{workflow} produces the required complete outputs and meets its checked requirements",
            }
        )
    return {"R1": list(claim_parts), "R2": [dict(p) for p in claim_parts], "R3": execution_parts}


# ---- display --------------------------------------------------------------------------------------

_PRECISION = Fraction(1, 10)


def display(card: dict) -> dict:
    """The card's numbers as a surface shows them: one decimal, summing to the profile's total.

    Rounding each part on its own loses or gains a tenth, and a bar whose three parts do not add up
    is a bar a reader cannot trust. The parts are rounded down and the residue given to the largest,
    which preserves the sum exactly. A part that is genuinely nonzero but smaller than a tenth shows
    "<0.1" rather than "0": rounding a residual away to claim everything verified is the one thing
    this must never do. The exact rationals travel beside the display and are what is persisted.
    """
    parts = {key: card[key] for key in (VERIFIED, FAILED, UNDETERMINED)}
    total = sum(parts.values(), Fraction(0))
    floors = {key: int((value / _PRECISION).__floor__()) for key, value in parts.items()}
    # A genuinely nonzero part below a tenth RESERVES a tenth before anything else is distributed.
    # Otherwise the residue is handed to the largest part and the card reads 100 verified while a
    # real, unverified remainder is still outstanding, which is the one thing this must never do.
    below = {key for key, tenths in floors.items() if tenths == 0 and parts[key] > 0}
    for key in below:
        floors[key] = 1
    tenths_total = int((total / _PRECISION).__floor__())
    residue = tenths_total - sum(floors.values())
    if residue > 0:
        for key in sorted(floors, key=lambda k: (-(parts[k] - floors[k] * _PRECISION), k))[:residue]:
            floors[key] += 1
    while residue < 0:
        # The reservations overshot. The largest part pays, never a reserved one: understating what
        # was verified is the safe direction.
        largest = max((k for k in floors if k not in below and floors[k] > 0), key=lambda k: floors[k], default=None)
        if largest is None:
            break
        floors[largest] -= 1
        residue += 1
    shown = {}
    for key, tenths in floors.items():
        value = Fraction(tenths) * _PRECISION
        if key in below:
            shown[key] = "<0.1"
            continue
        shown[key] = f"{float(value):g}"
    return {
        "verified": shown[VERIFIED],
        "failed": shown[FAILED],
        "undetermined": shown[UNDETERMINED],
        "parts": {key: float(Fraction(tenths) * _PRECISION) for key, tenths in floors.items()},
        "exact": {key: str(value) for key, value in parts.items()},
        "total": f"{float(total):g}",
    }


# ---- the card a surface renders --------------------------------------------------------------------

RUBRIC_LABEL = "Evidence rubric v3"

_NOT_ASSESSED = "Not yet assessed"


def evidence_card(
    *,
    profile: dict,
    leaves: list[dict],
    assessed: dict,
    reproduction: dict | None = None,
    capability_limits: dict | None = None,
) -> dict:
    """One study's v3 scorecard, in the shape every surface reads.

    The bar's three parts are always present, including their zeros: a lone 35 cannot tell 65 unknown
    points from 65 failed ones, so V, F and U travel together on the report, the list and both exports.
    The reproduction statement is separate and is never moved by the score: a paper can earn 78
    documentary points and still have had no independent reproduction attempted.
    """
    card = score(leaves, assessed)
    shown = display(card)
    limits = _limit_rows(leaves, assessed, capability_limits or {})
    sections = [_section_row(key, bucket, leaves, assessed, limits) for key, bucket in sorted(card["sections"].items())]
    attempted = bool((reproduction or {}).get("attempted"))
    return {
        "rubric_version": RUBRIC_VERSION,
        "rubric_label": RUBRIC_LABEL,
        "status": card["status"],
        "score": float(card["verified"]),
        "failed": float(card["failed"]),
        "undetermined": float(card["undetermined"]),
        "assessed_points": float(card["assessed_points"]),
        "display": {
            "verified": shown["verified"],
            "failed": shown["failed"],
            "undetermined": shown["undetermined"],
            "total": shown["total"],
        },
        "exact": shown["exact"],
        "parts": [
            {"key": "verified", "label": VISIBLE_WORDS[VERIFIED], "points": shown["verified"]},
            {"key": "untested", "label": VISIBLE_WORDS[UNDETERMINED], "points": shown["undetermined"]},
            {"key": "negative", "label": VISIBLE_WORDS[FAILED], "points": shown["failed"]},
        ],
        "headline": f"{shown['verified']} / {shown['total']}",
        "counts_label": (
            f"{shown['verified']} positive points · {shown['undetermined']} untested points · "
            f"{shown['failed']} negative points"
        ),
        "score_note": _NOT_ASSESSED if card["status"] == "not_assessed" else None,
        "explanation": (
            f"{shown['verified']} points of supporting evidence have been established out of {shown['total']}. "
            "It is not a probability that the paper is correct, and not a fraction of the paper reproduced. "
            "Untested includes checks that were attempted and could not conclude; negative identifies a "
            "demonstrated problem with a named check, never a judgment about the paper."
        ),
        "scope": {
            **card["assessed_scope"],
            "label": f"Rubric checks assessed: {card['assessed_scope']['assessed']} / {card['assessed_scope']['total']}",
        },
        "sections": sections,
        "profile": {
            "revision": profile.get("revision"),
            "exclusions": list(profile.get("exclusions") or []),
            "documentary_ceiling": float(profile["ceilings"]["documentary"]),
            "with_author_results_ceiling": float(profile["ceilings"]["with_author_results"]),
        },
        "capability_limits": limits,
        "reproduction": {
            "attempted": attempted,
            "label": (reproduction or {}).get("label") or "Independent reproduction: not attempted",
            "reason": (reproduction or {}).get("reason"),
        },
        "concerns": _concerns(leaves, assessed),
        "next_checks": _next_checks(leaves, assessed, limits),
    }


# Section 7: how an obligation was established, in words a reader can weigh. A measurement and a model
# review are not the same kind of evidence, and a person's confirmation is neither.
METHOD_LABELS = {
    "measurement": "Measured",
    "model_assisted": "Reviewed by a model",
    "human_assisted": "Confirmed by a person",
}


def _obligation_row(leaf: dict, assessed: dict) -> dict:
    """One allocated obligation, as itself. "Verified" here means THIS obligation was established,
    never that the paper is proven."""
    found = assessed.get(leaf["id"]) or {}
    outcome = found.get("outcome") if found.get("outcome") in (VERIFIED, FAILED) else UNDETERMINED
    method = found.get("method") or "measurement"
    return {
        "leaf": leaf["id"],
        "obligation": leaf.get("obligation"),
        "unit": leaf.get("unit"),
        "points": float(leaf["weight"]),
        "outcome": outcome,
        "label": VISIBLE_WORDS[outcome],
        "statement": leaf.get("statement"),
        "rationale": found.get("rationale"),
        "scope": found.get("scope"),
        "impact": found.get("impact"),
        "next_action": found.get("next_action"),
        "method": method,
        "method_label": METHOD_LABELS.get(method, method),
        "capability_limit": bool(found.get("capability_limit")),
    }


def _criteria_rows(key: str, leaves: list[dict], assessed: dict) -> list[dict]:
    """A section's criteria, each with its own points and its obligations' allocation. This is what
    makes "2 verified, 2 failed" a statement about two named things rather than a half-good rating."""
    grouped: dict[str, list[dict]] = {}
    for leaf in leaves:
        if leaf["section"] == key:
            grouped.setdefault(leaf["criterion"], []).append(leaf)
    rows = []
    for criterion_id, members in grouped.items():
        criterion = CRITERIA_BY_ID[criterion_id]
        obligations = [_obligation_row(leaf, assessed) for leaf in members]
        totals = {state: 0.0 for state in (VERIFIED, FAILED, UNDETERMINED)}
        for row in obligations:
            totals[row["outcome"]] += row["points"]
        rows.append(
            {
                "criterion": criterion_id,
                "title": criterion.title,
                "points": sum(float(leaf["weight"]) for leaf in members),
                "verified": totals[VERIFIED],
                "failed": totals[FAILED],
                "undetermined": totals[UNDETERMINED],
                "obligations": sorted(obligations, key=lambda row: row["leaf"]),
            }
        )
    return sorted(rows, key=lambda row: row["criterion"])


def _section_row(key: str, bucket: dict, leaves: list[dict], assessed: dict, limits: list[dict]) -> dict:
    """One section's totals, what it established, and the most consequential thing still outstanding."""
    verified = [
        assessed[leaf["id"]]
        for leaf in leaves
        if leaf["section"] == key and (assessed.get(leaf["id"]) or {}).get("outcome") == VERIFIED
    ]
    failures = [
        assessed[leaf["id"]]
        for leaf in leaves
        if leaf["section"] == key and (assessed.get(leaf["id"]) or {}).get("outcome") == FAILED
    ]
    section_limits = [row for row in limits if row["section"] == key]
    outstanding = None
    if failures:
        outstanding = failures[0].get("rationale")
    elif section_limits:
        outstanding = section_limits[0]["reason"]
    else:
        open_rows = [
            assessed[leaf["id"]]
            for leaf in leaves
            if leaf["section"] == key
            and (assessed.get(leaf["id"]) or {}).get("outcome") == UNDETERMINED
            and (assessed.get(leaf["id"]) or {}).get("next_action")
        ]
        outstanding = open_rows[0]["next_action"] if open_rows else None
    return {
        "section": key,
        "title": SECTION_TITLES[key],
        "verified": float(bucket["verified"]),
        "failed": float(bucket["failed"]),
        "undetermined": float(bucket["undetermined"]),
        "maximum": float(bucket["maximum"]),
        # plan_8_4 section 4: a section is shown the way the headline is, one decimal under the same
        # sum-preserving rule. A row reading "0.2857142857142857 / 30" is a number nobody can read.
        "display": {**display(bucket), "maximum": f"{float(bucket['maximum']):g}"},
        "established": [row.get("rationale") for row in verified],
        "outstanding": outstanding,
        "unsupported_count": len(section_limits),
        # Section 7: the section expands into its criteria, their partial-credit allocation, what each
        # obligation required, what was found, how it was established, and what would settle it.
        "criteria": _criteria_rows(key, leaves, assessed),
    }


def _limit_rows(leaves: list[dict], assessed: dict, declared: dict) -> list[dict]:
    """The allocated obligations with no implemented check, each naming what is missing. A grey leaf
    that IS implemented and simply could not be established is not one of these."""
    rows = []
    for leaf in leaves:
        found = assessed.get(leaf["id"]) or {}
        limit = declared.get(leaf["id"]) or declared.get(leaf["criterion"])
        if not (found.get("capability_limit") or (limit and found.get("outcome", UNDETERMINED) == UNDETERMINED)):
            continue
        if not limit:
            continue
        rows.append(
            {
                "leaf": leaf["id"],
                "criterion": leaf["criterion"],
                "section": leaf["section"],
                "points": float(leaf["weight"]),
                "reason": found.get("rationale") or limit["reason"],
            }
        )
    return rows


# A next action that a person cannot take without the approval the isolated execution path requires.
_NEEDS_APPROVAL = ("approve",)


def _next_checks(leaves: list[dict], assessed: dict, limits: list[dict]) -> list[dict]:
    """plan_8_4 section 6.4: what would be assessed next, what it is worth, and what it requires.

    A grey obligation with no stated way forward is indistinguishable from one nobody will ever
    assess, so every open obligation that HAS an action is listed with the points it holds. A
    capability limit is not offered: it is a statement about what bioAF has not built, not an action
    a person can take. The exception is an obligation whose only obstacle is an approval, which is
    something a person CAN give.
    """
    capped = {row["leaf"] for row in limits}
    rows = []
    for leaf in leaves:
        found = assessed.get(leaf["id"]) or {}
        action = found.get("next_action")
        if found.get("outcome", UNDETERMINED) != UNDETERMINED or not action:
            continue
        needs_approval = any(word in action.lower() for word in _NEEDS_APPROVAL)
        if leaf["id"] in capped and not needs_approval:
            continue
        rows.append(
            {
                "leaf": leaf["id"],
                "criterion": leaf["criterion"],
                "section": leaf["section"],
                "points": float(leaf["weight"]),
                "action": action,
                "needs_approval": needs_approval,
            }
        )
    return sorted(rows, key=lambda row: (-row["points"], row["leaf"]))


def _concerns(leaves: list[dict], assessed: dict) -> list[dict]:
    """Section 7: a confirmed problem is shown beside the score whatever its point weight. A high
    score must never hide that the supplied code fails or that a record contradicts the paper."""
    found = []
    for leaf in leaves:
        row = assessed.get(leaf["id"]) or {}
        if row.get("outcome") != FAILED:
            continue
        found.append(
            {
                "leaf": leaf["id"],
                "criterion": leaf["criterion"],
                "section": leaf["section"],
                "points": float(leaf["weight"]),
                "rationale": row.get("rationale"),
                "impact": row.get("impact"),
            }
        )
    return found


# The list cell. A lone number cannot tell an unknown point from a failed one, so V, F and U travel
# with it; the unweighted scope moves to the detail where width is short (section 7).
_COMPACT_KEYS = (
    "rubric_version",
    "rubric_label",
    "status",
    "score",
    "failed",
    "undetermined",
    "display",
    "parts",
    "headline",
    "counts_label",
    "score_note",
)


def compact_evidence_score(card: dict | None) -> dict | None:
    """The v3 card as the studies list shows it, cut from the card the report renders."""
    if not card:
        return None
    return {key: card.get(key) for key in _COMPACT_KEYS}
