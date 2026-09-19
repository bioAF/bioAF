"""plan_8_4 sections 6.1 and 6.2: the evidence bioAF already holds, mapped to rubric v3's obligations.

One rule decides every mapping, and it is deliberately unkind to bioAF:

- **verified** only where held evidence ESTABLISHES the obligation. A repository that exists is not
  code that parses; a retrieval that succeeded is not a syntax check; a model saying a methods section
  is "detailed enough" is one holistic opinion and answers no criterion this rubric declares.
- **failed** only where held evidence CONTRADICTS it, with the cause and its impact recorded. A
  measured species disagreement is a failure. A field bioAF's own extraction did not fill is not:
  that is bioAF's limitation, and section 3.4 says a limitation of bioAF produces undetermined points.
- **undetermined** everywhere else, including every obligation whose check is not implemented yet.
  Those are declared in ``CAPABILITY_LIMITS`` and listed on the report, so a grey obligation reads as
  a capability bioAF has not delivered rather than as a completed check that found nothing.

Pure: no database, no model, no I/O. Everything here is read from evidence the study already holds.
"""

from __future__ import annotations

from app.services.validation_rubric_v3 import FAILED, UNDETERMINED, VERIFIED

MEASUREMENT = "measurement"
MODEL_ASSISTED = "model_assisted"
HUMAN_ASSISTED = "human_assisted"

# An outcome that settled a claim against the authors' own published results, and one that did not.
_AGREES = "agree"
_DISAGREES = "disagree"


def _finding(outcome: str, rationale: str, *, scope: str, method: str = MEASUREMENT, **extra) -> dict:
    return {"outcome": outcome, "rationale": rationale, "scope": scope, "method": method, **extra}


def _open(rationale: str, *, scope: str, next_action: str, **extra) -> dict:
    """An obligation nothing established. It always says what would establish it, or that nothing can."""
    return _finding(UNDETERMINED, rationale, scope=scope, next_action=next_action, **extra)


# ---- what has no implemented check yet ------------------------------------------------------------
#
# Section 10: every unsupported obligation is enumerated. This is the honest inventory of what rubric
# v3 declares and bioAF cannot yet assess, and it is what the report lists as capability limits.

_CODE_LIMIT = (
    "bioAF holds no implemented check for this obligation. Establishing it means parsing, resolving "
    "and loading the paper's actual source in its declared environment, which runs untrusted code and "
    "belongs behind the existing isolated execution path and its approval."
)
_EXPERIMENTAL_LIMIT = (
    "bioAF holds no implemented check for this obligation. Establishing it means an evidence-backed "
    "review of the paper's experimental procedure against its cited methods, which is a bounded model "
    "judgment bioAF has not yet defined a contract for."
)
_COMPUTATIONAL_LIMIT = (
    "bioAF holds no implemented check for this obligation. Establishing it means an evidence-backed "
    "review of the paper's computational methods against its cited sources and supplied code."
)

CAPABILITY_LIMITS: dict[str, dict] = {
    # plan_8_4 milestone B: the static code obligations are implemented (`validation_code_checks`).
    # What remains a standing limit is the two that cannot be established by reading source at all.
    "C1.B": {"reason": _CODE_LIMIT},
    "C2.B": {"reason": _CODE_LIMIT},
    **{f"E{n}.{o}": {"reason": _EXPERIMENTAL_LIMIT} for n in range(1, 4) for o in "AB"},
    "S2.A": {
        "reason": "bioAF holds no implemented check that the paper identifies the tissue, cell type or "
        "material of the relevant samples apart from the conditions its contrasts name."
    },
    "S2.B": {
        "reason": "bioAF holds no implemented check that independent records agree with the paper's "
        "stated sample identities, with legitimate differences between experiments accounted for."
    },
    "S4.B": {
        "reason": "bioAF holds no implemented check reconciling held sample records to the counts and "
        "inclusion rules each experiment states."
    },
    "S5.A": {
        "reason": "bioAF holds no implemented check that the paper describes biological versus technical "
        "replication and its pairing or blocking requirements."
    },
    "M1.A": {"reason": _COMPUTATIONAL_LIMIT},
    "M1.B": {"reason": _COMPUTATIONAL_LIMIT},
    "M3.A": {"reason": _COMPUTATIONAL_LIMIT},
    "M3.B": {"reason": _COMPUTATIONAL_LIMIT},
    "M5.A": {"reason": _COMPUTATIONAL_LIMIT},
    "M5.B": {"reason": _COMPUTATIONAL_LIMIT},
    "R2": {
        "reason": "an independent result assessment requires an approved analysis run over acquired "
        "inputs. Nothing is assessed until one has run."
    },
    "R3": {"reason": "end-to-end execution requires an approved workflow run. Nothing is assessed until one has run."},
}


def assess_evidence(
    *,
    plan: dict | None,
    evidence: dict | None,
    claims: list[dict] | None = None,
    inventory: dict | None = None,
) -> dict:
    """Every rubric v3 leaf this build can settle from the study's held evidence, keyed by leaf id.

    A leaf absent from the result is undetermined, which is what ``score`` reads it as. Present-and-
    undetermined is used where bioAF looked and could not establish the obligation, so the difference
    between "not implemented" and "checked and open" stays visible in the detail.
    """
    plan = plan or {}
    evidence = evidence or {}
    experiments = [e for e in plan.get("reported_experiments") or [] if isinstance(e, dict)]
    contrasts = [c for c in (plan.get("differential_design") or {}).get("contrasts") or [] if isinstance(c, dict)]
    assessed: dict[str, dict] = {}
    assessed.update(_species(experiments, plan, evidence))
    assessed.update(_groups(contrasts, evidence))
    assessed.update(_accounting(experiments, plan))
    assessed.update(_units(evidence))
    assessed.update(_references(experiments))
    assessed.update(_decision_criteria(claims or [], contrasts))
    assessed.update(_author_results(claims or [], inventory))
    assessed.update(_code(evidence))
    for leaf_id, limit in CAPABILITY_LIMITS.items():
        assessed.setdefault(
            leaf_id,
            _finding(UNDETERMINED, limit["reason"], scope="not assessed", method=MEASUREMENT, capability_limit=True),
        )
    return assessed


def _code(evidence: dict) -> dict:
    """plan_8_4 milestone B: the code section, from the source this study actually holds.

    ``evidence["code_inspection"]`` is what an inspection stage recorded: the source text it extracted,
    the manifests beside it, any evidence-backed fitness review, and the result of an approved isolated
    run. Nothing else in the study's evidence says anything about the code: a repository that exists, a
    file that was retrieved and a role that says "code" are not source text, and the checks say so by
    leaving every obligation grey.
    """
    from app.services.validation_code_checks import assess_code

    inspection = evidence.get("code_inspection") or {}
    if not isinstance(inspection, dict):
        return {}
    return assess_code(
        sources=inspection.get("sources"),
        manifests=inspection.get("manifests"),
        defects=inspection.get("reviews"),
        execution=inspection.get("execution"),
    )


def _species(experiments: list[dict], plan: dict, evidence: dict) -> dict:
    stated = [str(e.get("organism") or "").strip() for e in experiments]
    sheet = str((plan.get("sample_sheet") or {}).get("organism") or "").strip()
    named = [o for o in stated if o] or ([sheet] if sheet else [])
    scope = f"{len(experiments)} reported {'experiment' if len(experiments) == 1 else 'experiments'}"
    # Every relevant experiment states one, or, where the plan records no experiments at all, the
    # study's sample sheet does. One experiment left blank is not "the paper states the organism".
    every_experiment = bool(stated) and all(stated)
    if every_experiment or (not stated and bool(sheet)):
        found = _finding(
            VERIFIED,
            f"the paper states the organism for every relevant experiment: {', '.join(sorted(set(named)))}",
            scope=scope,
        )
    else:
        found = _open(
            "bioAF's read of the paper recorded no organism for every relevant experiment",
            scope=scope,
            next_action="read the paper's samples section again, or record the organism at the gate",
        )
    check = (evidence.get("precompute_checks") or {}).get("species_matches") or {}
    verdict = check.get("verdict")
    detail = str(check.get("detail") or "").strip()
    if verdict == "ok":
        agreement = _finding(
            VERIFIED,
            detail or "the deposited sample records state the organisms the paper states",
            scope="the sample records bioAF holds",
        )
    elif verdict == "mismatch":
        agreement = _finding(
            FAILED,
            detail or "the deposited sample records contradict the organism the paper states",
            scope="the sample records bioAF holds",
            impact="an analysis against the paper's stated organism would answer confidently about the wrong species",
        )
    else:
        agreement = _open(
            detail or "bioAF holds no sample records stating an organism to compare",
            scope="the sample records bioAF holds",
            next_action="acquire the deposit's sample metadata",
        )
    return {"S1.A": found, "S1.B": agreement}


def _groups(contrasts: list[dict], evidence: dict) -> dict:
    scope = f"{len(contrasts)} {'contrast' if len(contrasts) == 1 else 'contrasts'}"
    missing = [
        c.get("name") or "an unnamed contrast"
        for c in contrasts
        if not (str(c.get("test_condition") or "").strip() and str(c.get("reference_condition") or "").strip())
    ]
    if contrasts and not missing:
        defined = _finding(
            VERIFIED,
            "every comparison the paper's claims rest on names its test and its reference arm",
            scope=scope,
        )
    else:
        defined = _open(
            (
                f"{', '.join(missing)} names no test or reference arm"
                if missing
                else "the paper's claims rest on no defined comparison"
            ),
            scope=scope,
            next_action="record the arms at the gate, or read the paper's design again",
        )
    validation = ((evidence.get("input_choice") or {}).get("mapping_validation")) or {}
    rows = [r for r in validation.get("mapping") or [] if isinstance(r, dict)]
    if validation.get("status") == "accepted" and rows:
        supported = _finding(
            VERIFIED,
            f"every one of the {len(rows)} chosen columns was placed in an arm the sample records support",
            scope=f"{len(rows)} columns of the chosen input",
            method=HUMAN_ASSISTED if validation.get("assistance") else MEASUREMENT,
        )
    elif validation:
        supported = _open(
            "; ".join(validation.get("reasons") or []) or "the sample mapping is not accepted",
            scope="the chosen input's columns",
            next_action="resolve the mapping's refusals at the gate",
        )
    else:
        supported = _open(
            "no analysis input was chosen, so no sample records were placed against the paper's arms",
            scope="no chosen input",
            next_action="acquire an analysis input and map its columns",
        )
    return {"S3.A": defined, "S3.B": supported}


def _accounting(experiments: list[dict], plan: dict) -> dict:
    count = (plan.get("sample_sheet") or {}).get("sample_count")
    per_experiment = [e.get("sample_count") for e in experiments]
    stated = [c for c in [*per_experiment, count] if isinstance(c, int) and c > 0]
    if stated:
        return {
            "S4.A": _finding(
                VERIFIED,
                f"the paper states how many samples the study used ({stated[0]})",
                scope="the study's stated sample counts",
            )
        }
    return {
        "S4.A": _open(
            "bioAF's read of the paper recorded no sample count for the relevant experiments",
            scope="the study's stated sample counts",
            next_action="read the paper's samples section again",
        )
    }


def _units(evidence: dict) -> dict:
    validation = ((evidence.get("input_choice") or {}).get("mapping_validation")) or {}
    unresolved = [str(c) for c in validation.get("units_unresolved") or []]
    if validation and not unresolved and validation.get("status") == "accepted":
        return {
            "S5.B": _finding(
                VERIFIED,
                "the biological unit each chosen column came from is established from the sample records",
                scope="the chosen input's columns",
                method=HUMAN_ASSISTED if validation.get("assistance") else MEASUREMENT,
            )
        }
    if unresolved:
        return {
            "S5.B": _open(
                f"nothing the study cites states which biological unit {', '.join(unresolved)} came from",
                scope="the chosen input's columns",
                next_action=f"record the biological unit of {', '.join(unresolved)}, with the evidence for it",
            )
        }
    return {
        "S5.B": _open(
            "no analysis input was chosen, so no column's biological unit was established",
            scope="no chosen input",
            next_action="acquire an analysis input and map its columns",
        )
    }


def _references(experiments: list[dict]) -> dict:
    references = [(e.get("id"), (e.get("reference") or {})) for e in experiments]
    relevant = [(eid, r) for eid, r in references if r]
    if not relevant:
        return {
            "M2.A": _open(
                "bioAF's read recorded no reference inputs for the relevant experiments",
                scope="the reported experiments",
                next_action="read the paper's methods again",
            )
        }
    stated = [
        (eid, r)
        for eid, r in relevant
        if str((r.get("assembly") or {}).get("stated") or "").strip()
        or str((r.get("annotation") or {}).get("stated") or "").strip()
    ]
    if stated:
        words = ", ".join(
            sorted(
                {
                    str((r.get("assembly") or {}).get("stated") or (r.get("annotation") or {}).get("stated"))
                    for _, r in stated
                }
            )
        )
        identified = _finding(
            VERIFIED,
            f"the paper names the reference its results depend on ({words})",
            scope=f"{len(relevant)} reported {'experiment' if len(relevant) == 1 else 'experiments'}",
        )
    else:
        identified = _open(
            "the paper names no reference for the relevant experiments",
            scope=f"{len(relevant)} reported experiments",
            next_action="read the paper's methods again",
        )
    assumed = [
        eid
        for eid, r in relevant
        for part in ("assembly", "annotation")
        if (r.get(part) or {}).get("assumption") or not str((r.get(part) or {}).get("stated") or "").strip()
    ]
    if identified["outcome"] == VERIFIED and not assumed:
        versions = _finding(
            VERIFIED,
            "the paper states both the assembly and the annotation release its results depend on",
            scope=f"{len(relevant)} reported experiments",
        )
    else:
        versions = _open(
            "the paper does not state every result-sensitive reference release; bioAF supplied one of its own",
            scope=f"{len(relevant)} reported experiments",
            next_action="record the annotation release the authors used, with the evidence for it",
        )
    return {"M2.A": identified, "M2.B": versions}


def _decision_criteria(claims: list[dict], contrasts: list[dict]) -> dict:
    """M4: the thresholds a paper's results rest on, and whether their reading is unambiguous."""
    with_cutoffs = [c for c in contrasts if (c.get("cutoffs") or c.get("thresholds"))]
    if contrasts and len(with_cutoffs) == len(contrasts):
        specified = _finding(
            VERIFIED,
            "every comparison the paper's claims rest on states its significance or effect threshold",
            scope=f"{len(contrasts)} {'contrast' if len(contrasts) == 1 else 'contrasts'}",
        )
    else:
        specified = _open(
            "not every comparison the paper's claims rest on states a threshold",
            scope=f"{len(contrasts)} contrasts",
            next_action="record the cutoff from the paper's methods, with its quote",
        )
    ambiguous = [
        c
        for c in claims
        if ((c.get("consistency") or {}).get("filter_semantics") or {}).get("unresolved")
        or (c.get("predicate_status") == "not_checkable")
    ]
    if specified["outcome"] == VERIFIED and claims and not ambiguous:
        unambiguous = _finding(
            VERIFIED,
            "every threshold's direction, scale and orientation reads one way",
            scope=f"{len(claims)} claims",
        )
    elif ambiguous:
        unambiguous = _open(
            f"{len(ambiguous)} of the paper's claims leave their threshold's reading open",
            scope=f"{len(claims)} claims",
            next_action="record which reading the paper meant, with the evidence for it",
        )
    else:
        unambiguous = _open(
            "bioAF established no reading of the paper's thresholds to check",
            scope=f"{len(claims)} claims",
            next_action="read the paper's claims again",
        )
    return {"M4.A": specified, "M4.B": unambiguous}


def _author_results(claims: list[dict], inventory: dict | None) -> dict:
    """R1: each allocated claim check, from the comparison against the authors' own published table."""
    from app.services.validation_rubric_v3 import result_allocation

    by_index = {c.get("index"): c for c in claims if isinstance(c, dict) and isinstance(c.get("index"), int)}
    if not by_index:
        by_index = {position: c for position, c in enumerate(claims) if isinstance(c, dict)}
    assessed: dict[str, dict] = {}
    for part in result_allocation(inventory)["R1"]:
        claim = by_index.get(part["claim_index"]) or {}
        outcome = ((claim.get("consistency") or {}).get("outcome")) if claim else None
        reason = str((claim.get("consistency") or {}).get("reason") or "").strip() if claim else ""
        leaf_id = f"R1.{part['id']}"
        if outcome == _AGREES:
            assessed[leaf_id] = _finding(
                VERIFIED,
                reason or "the authors' own published table agrees with this claim under its stated predicate",
                scope=part["subject"],
            )
        elif outcome == _DISAGREES:
            assessed[leaf_id] = _finding(
                FAILED,
                reason or "the authors' own published table disagrees with this claim under its stated predicate",
                scope=part["subject"],
                impact="the paper's text and its own published results do not state the same number",
            )
        else:
            assessed[leaf_id] = _open(
                reason or "this claim has not been compared with the authors' published results",
                scope=part["subject"],
                next_action="check this claim against the table the paper binds it to",
            )
    return assessed


# ---- applicability -------------------------------------------------------------------------------
#
# plan_8_4 section 3.5: applicability is about the PAPER's work, never about bioAF's adapters. A
# criterion is excluded only where cited evidence establishes that the paper's methods have no
# counterpart for it, and the exclusion redistributes its weight so the profile still totals 100.
#
# The one exclusion bioAF can establish deterministically today: a reference genome or annotation has
# no counterpart in an analysis that sequences nothing. A western blot, a live-cell tracking assay, an
# immunofluorescence image and a qRT-PCR reaction have feature definitions (an antibody, a primer pair)
# and no genomic reference to state, and requiring one of them would be requiring a fact that does not
# exist. This is deliberately NOT keyed on whether bioAF has an adapter: an assay bioAF cannot execute
# still has a reference to state, and an unsupported assay is a limitation of bioAF's, not the paper's.

_NON_GENOMIC = (
    "western blot",
    "immunoblot",
    "immunofluorescence",
    "immunohistochem",
    "microscopy",
    "imaging",
    "tracking",
    "qrt-pcr",
    "qpcr",
    "rt-pcr",
    "elisa",
    "flow cytometry",
    "atomic force microscopy",
    "electrophysiolog",
    "patch clamp",
    "mass spectrometr",
)


def _is_non_genomic(assay: str) -> bool:
    text = (assay or "").strip().lower()
    return bool(text) and any(word in text for word in _NON_GENOMIC)


def profile_for(*, plan: dict | None) -> dict:
    """The applicability profile for this paper: the default, with what its methods have no counterpart
    for excluded, each exclusion carrying the evidence that establishes it.

    Uncertainty never excludes. A paper whose assays bioAF could not identify keeps every criterion, so
    a hard check can never be dropped by failing to read the paper well enough to name its methods.
    """
    from app.services.validation_rubric_v3 import ApplicabilityUncertain, default_profile

    experiments = [e for e in (plan or {}).get("reported_experiments") or [] if isinstance(e, dict)]
    assays = [str(e.get("assay") or "").strip() for e in experiments]
    named = [a for a in assays if a]
    exclusions = []
    if named and len(named) == len(assays) and all(_is_non_genomic(a) for a in named):
        exclusions.append(
            {
                "criterion": "M2",
                "rationale": (
                    "every experiment this paper reports measures something other than sequence ("
                    + ", ".join(sorted(set(named)))
                    + "), so there is no result-sensitive genome or annotation release for it to state"
                ),
                "source": "the assays the paper's own methods state for each reported experiment",
            }
        )
    try:
        return default_profile(exclude=exclusions)
    except ApplicabilityUncertain:
        # An exclusion that cannot be made leaves the allocation where it is, which is the safe
        # direction: a criterion that stays allocated is grey, and grey costs nothing.
        return default_profile()
