"""change_7.5 section 2.5: support per check. Each claim is evaluated against four checks, each on its own.

Study 38's four differential-expression counts read "No supported metric ... the claim cannot be
compared": a claim's status came from its QC binding alone, and nothing evaluated a claim per check. A
blocked check hid an available one, and the authors' own result tables sat unused in the deposit.

The four checks, each ``available``, ``unavailable`` or ``unresolved`` with a reason naming the
requirement that decided it:

- **QC metric comparison**: a bound metric whose population, aggregation and denominator match.
- **Consistency with the authors' results**: a result table (or file) the authors published.
- **Reanalysis of processed data**: a processed matrix holding both arms, a sample mapping, a predicate.
- **Reanalysis from raw reads**: fetchable reads, a workflow for the experiment's assay, a usable
  reference, a predicate.

**Deterministic, with no model call.** It is recomputed whenever the evidence it reads changes.
**The requested route bounds what a run may execute; it does not change what is available.**
"""

from __future__ import annotations

QC_METRIC = "qc_metric"
AUTHOR_RESULTS = "author_results"
PROCESSED_REANALYSIS = "processed_reanalysis"
RAW_REANALYSIS = "raw_reanalysis"
CHECKS = (QC_METRIC, AUTHOR_RESULTS, PROCESSED_REANALYSIS, RAW_REANALYSIS)

AVAILABLE = "available"
UNAVAILABLE = "unavailable"
UNRESOLVED = "unresolved"

DIFFERENTIAL = "differential"
MEMBERSHIP = "membership"
SCALAR = "scalar"
SAMPLE_COUNT = "sample_count"

_SET_OUTPUTS = ("gene_set_size", "region_set_size", "peak_set_size")
_SAMPLE_WORDS = ("sample", "biops", "patient", "donor", "embryo", "subject", "individual", "participant", "mice", "animal")
_MATRIX_KINDS = ("matrix_counts", "matrix_normalized")
_TABLE_KINDS = ("de_table", "da_table")

# The unresolved requirements that do not keep a pair from being a candidate: the sample mapping is
# established when the input is chosen (by the model in autonomous mode, by a person at the gate), and
# what an unlisted deposit holds is established when it is acquired. Neither is an absence.
_PENDING_REQUIREMENTS = ("sample_mapping", "deposit_listing")


def _check(status: str, reason: str | None, requirement: str | None) -> dict:
    return {"status": status, "reason": reason, "requirement": requirement}


def claim_type(target: dict) -> str:
    """Which of the four claim types a claim is, read from the claim itself."""
    output = str(target.get("output_type") or "").lower()
    unit = str(target.get("unit") or "").lower()
    key = str(target.get("bound_key") or target.get("metric_key") or "").lower()
    has_contrast = isinstance(target.get("contrast_index"), int)
    if output in _SET_OUTPUTS or (has_contrast and target.get("claimed_value") is not None):
        return DIFFERENTIAL
    if has_contrast or (target.get("direction") and target.get("claimed_value") is None):
        return MEMBERSHIP
    if key == "total_samples" or (output == "count" and any(w in unit for w in _SAMPLE_WORDS)):
        return SAMPLE_COUNT
    return SCALAR


def _linked_deposits(experiment: dict | None, resources: list[dict], deposits: list[dict]) -> list[dict]:
    """The deposits holding this experiment's data: the ones linked to it, else every deposit the
    paper names when nothing is linked to any experiment."""
    by_id = {str(d.get("accession") or "").upper(): d for d in deposits if isinstance(d, dict)}
    experiment_id = (experiment or {}).get("id")
    linked = [
        r for r in resources if experiment_id in (r.get("reported_experiment_ids") or []) and r.get("type") == "sequencing_data"
    ]
    if not linked and not any(r.get("reported_experiment_ids") for r in resources):
        linked = [r for r in resources if r.get("type") == "sequencing_data"]
    chosen = [by_id.get(str(r.get("identifier") or "").upper(), {"accession": r.get("identifier")}) for r in linked]
    if not chosen and not resources:
        chosen = [d for d in deposits if isinstance(d, dict)]
    return [{**d, "_listing": _listing_for(d, resources)} for d in chosen]


def _listing_for(deposit: dict, resources: list[dict]) -> dict:
    key = str(deposit.get("accession") or "").upper()
    resource = next((r for r in resources if str(r.get("identifier") or "").upper() == key), None)
    listing = dict((resource or {}).get("listing") or {})
    listing.setdefault("result_tables", list(deposit.get("result_tables") or []))
    listing.setdefault("kinds", dict((deposit.get("listing") or {}).get("kinds") or {}))
    return listing


def _predicate(target: dict, contrast: dict | None, *, needs_significance: bool) -> tuple[str, str | None]:
    """Whether the claim's statistical definition is one a route can implement."""
    from app.services.validation_claim_cutoffs import SIGNIFICANCE_KINDS, claim_cutoffs

    unresolved = (contrast or {}).get("thresholds_unresolved")
    if unresolved:
        return UNRESOLVED, str(unresolved)
    cutoffs = claim_cutoffs(target) or [c for c in (contrast or {}).get("cutoffs") or [] if isinstance(c, dict)]
    if needs_significance and not any(c.get("kind") in SIGNIFICANCE_KINDS for c in cutoffs):
        return UNAVAILABLE, "the claim states no significance cutoff, and bioAF supplies none"
    return AVAILABLE, None


def _finding_kind(workflow: str | None) -> str | None:
    from app.services.validation_level3_service import supported_finding_kinds

    kinds = supported_finding_kinds(workflow)
    return kinds[0] if len(kinds) == 1 else ("gene" if "gene" in kinds else None)


def _raw_reads(deposits: list[dict]) -> tuple[str, str, str | None]:
    if not deposits:
        return UNAVAILABLE, "the paper names no deposit holding this experiment's reads", "raw_reads"
    answers = [d.get("raw_data") for d in deposits]
    reachable = [
        d for d in deposits if d.get("raw_data") == "yes" and d.get("supported") == "yes" and d.get("access") != "controlled"
    ]
    if reachable:
        return AVAILABLE, None, None
    if any(d.get("raw_data") == "yes" for d in deposits):
        held = next(d for d in deposits if d.get("raw_data") == "yes")
        return (
            UNAVAILABLE,
            f"{held.get('accession')} holds the reads, and bioAF cannot acquire them "
            f"({'controlled access' if held.get('access') == 'controlled' else 'no adapter'})",
            "raw_reads",
        )
    if all(a == "no" for a in answers):
        return UNAVAILABLE, "no deposit of this experiment publishes raw reads", "raw_reads"
    return UNRESOLVED, "what this experiment's deposit holds is not established yet", "deposit_listing"


def _raw_route(target: dict, experiment: dict | None, deposits: list[dict], contrast: dict | None, *, kind: str) -> dict:
    """Reads, a workflow, a usable reference and a predicate. A requirement known to be absent decides
    before one not yet established: an unavailable reference closes the route before the deposit is
    listed."""
    from app.services.validation_reference import USABLE, operation_reference

    found: list[dict] = []
    status, reason, requirement = _raw_reads(deposits)
    found.append(_check(status, reason, requirement))
    workflow = (experiment or {}).get("workflow")
    if not workflow:
        found.append(
            _check(
                UNAVAILABLE,
                f"no workflow bioAF can run analyzes the experiment's assay ({(experiment or {}).get('assay') or 'not stated'})",
                "workflow",
            )
        )
    elif kind in (DIFFERENTIAL, MEMBERSHIP) and _finding_kind(workflow) is None:
        found.append(_check(UNAVAILABLE, f"{workflow} has no route for reproducing a differential finding", "workflow"))
    else:
        ref_status, ref_reason = operation_reference(
            (experiment or {}).get("reference") or {}, "raw_reanalysis", pipeline_key=workflow
        )
        if ref_status != USABLE:
            found.append(_check(UNRESOLVED if ref_status == UNRESOLVED else UNAVAILABLE, ref_reason, "reference"))
    if kind in (DIFFERENTIAL, MEMBERSHIP):
        pred_status, pred_reason = _predicate(target, contrast, needs_significance=kind == DIFFERENTIAL)
        found.append(_check(pred_status, pred_reason, "predicate" if pred_status != AVAILABLE else None))
    for wanted in (UNAVAILABLE, UNRESOLVED):
        for result in found:
            if result["status"] == wanted:
                return result
    return _check(AVAILABLE, None, None)


def evaluate_checks(
    target: dict,
    *,
    experiment: dict | None,
    resources: list[dict] | None,
    deposits: list[dict] | None,
    supplements: list[dict] | None,
    contrast: dict | None,
) -> dict:
    """The claim's four checks, each ``{status, reason, requirement}``."""
    resources = [r for r in resources or [] if isinstance(r, dict)]
    deposits = [d for d in deposits or [] if isinstance(d, dict)]
    rows = [s for s in supplements or [] if isinstance(s, dict)]
    kind = claim_type(target)
    linked = _linked_deposits(experiment, resources, deposits)
    tables = [t for d in linked for t in d["_listing"].get("result_tables") or []]
    tables += [s.get("label") for s in rows if s.get("role") == "results_table" and s.get("resolved")]
    matrices = [d for d in linked if any(k in (d["_listing"].get("kinds") or {}) for k in _MATRIX_KINDS)]
    matrices += [s for s in rows if s.get("role") == "expression_matrix" and s.get("resolved")]
    listed = [d for d in linked if d["_listing"].get("kinds") or d.get("preprocessed_data") in ("yes", "no")]

    if experiment is None and kind != SAMPLE_COUNT:
        unlinked = _check(UNRESOLVED, "the claim is not linked to an experiment", "experiment")
        return {
            QC_METRIC: _qc(target, kind, unlinked),
            AUTHOR_RESULTS: unlinked,
            PROCESSED_REANALYSIS: unlinked,
            RAW_REANALYSIS: unlinked,
        }

    if kind in (DIFFERENTIAL, MEMBERSHIP):
        pred_status, pred_reason = _predicate(target, contrast, needs_significance=kind == DIFFERENTIAL)
        if tables:
            consistency = (
                _check(AVAILABLE, f"the authors published {tables[0]}", None)
                if pred_status == AVAILABLE
                else _check(pred_status, pred_reason, "predicate")
            )
        elif linked and not listed:
            consistency = _check(UNRESOLVED, "what this experiment's deposit holds is not established yet", "deposit_listing")
        else:
            consistency = _check(UNAVAILABLE, "no result table is published for this claim's experiment", "result_table")

        if matrices:
            processed = (
                _check(UNRESOLVED, "the sample mapping is established when the input is chosen", "sample_mapping")
                if pred_status == AVAILABLE
                else _check(pred_status, pred_reason, "predicate")
            )
        elif (linked and not listed) or not linked and not deposits and not resources:
            processed = _check(UNRESOLVED, "what this experiment's deposit holds is not established yet", "deposit_listing")
        else:
            processed = _check(UNAVAILABLE, "no processed matrix holding both arms is published", "processed_matrix")
        raw = _raw_route(target, experiment, linked, contrast, kind=kind)
        return {
            QC_METRIC: _check(UNAVAILABLE, "no QC metric measures a differential result", "qc_binding"),
            AUTHOR_RESULTS: consistency,
            PROCESSED_REANALYSIS: processed,
            RAW_REANALYSIS: raw,
        }

    if kind == SAMPLE_COUNT:
        registered = [d for d in deposits if d.get("registered_samples")]
        metadata = [s for s in rows if s.get("role") == "sample_metadata" and s.get("resolved")]
        consistency = (
            _check(AVAILABLE, f"{registered[0].get('accession')} registers {registered[0].get('registered_samples')} samples", None)
            if registered
            else (
                _check(AVAILABLE, f"{metadata[0].get('label')} lists the samples", None)
                if metadata
                else _check(UNRESOLVED, "no sample metadata table or registered sample list is established", "sample_metadata")
            )
        )
        none = _check(UNAVAILABLE, "no analysis computes a count of samples", "not_applicable")
        return {
            QC_METRIC: _check(UNAVAILABLE, "no QC metric measures a count of samples", "qc_binding"),
            AUTHOR_RESULTS: consistency,
            PROCESSED_REANALYSIS: none,
            RAW_REANALYSIS: dict(none),
        }

    # A scalar measured quantity: the QC comparison is the run's, and raw reanalysis is that same run.
    run = _raw_route(target, experiment, linked, contrast, kind=kind)
    qc = _qc(target, kind, run)
    peak_like = str(target.get("bound_key") or target.get("metric_key") or "").startswith("peak")
    measured = [d for d in linked if (d["_listing"].get("kinds") or {}).get("peaks")] if peak_like else []
    consistency = (
        _check(AVAILABLE, f"{measured[0].get('accession')} deposits the peak set this counts", None)
        if measured
        else _check(UNAVAILABLE, "no deposited or attached file measures this quantity", "measured_file")
    )
    return {
        QC_METRIC: qc,
        AUTHOR_RESULTS: consistency,
        PROCESSED_REANALYSIS: _check(UNAVAILABLE, "bioAF computes no QC metric from processed files", "not_applicable"),
        RAW_REANALYSIS: {**qc, "reason": f"the same run as the QC metric comparison; not a separate check ({qc['reason'] or qc['status']})"},
    }


def _qc(target: dict, kind: str, run: dict) -> dict:
    """The QC metric comparison for a scalar claim: the binding's match, then the run that computes it."""
    if kind != SCALAR:
        return _check(UNAVAILABLE, "no QC metric measures this kind of claim", "qc_binding")
    if target.get("bound_by") == "binding_failed":
        return _check(UNRESOLVED, "the binding call could not be read", "qc_binding")
    match = (target.get("binding_facts") or {}).get("match") or {}
    if not target.get("bound_key"):
        if match and match.get("ok") is False:
            return _check(match.get("status") or UNAVAILABLE, match.get("reason"), "qc_binding")
        if target.get("bound_by") == "model":
            return _check(UNAVAILABLE, "bioAF computes no QC metric that measures this claim", "qc_binding")
        from app.services.validation_classifier_service import _resolve_key

        mapped, _advisory = _resolve_key(target.get("metric_key"))
        if mapped is None:
            return _check(UNAVAILABLE, "bioAF computes no QC metric that measures this claim", "qc_binding")
    if run["status"] != AVAILABLE:
        return _check(run["status"], run["reason"], run["requirement"])
    return _check(AVAILABLE, None, None)


def execution_checks(route: str | None) -> tuple[str, ...]:
    """The checks a run on this route may execute. Consistency runs on its own and is never one."""
    if route == "deposit":
        return (PROCESSED_REANALYSIS,)
    if route == "pipeline":
        return (RAW_REANALYSIS, QC_METRIC)
    return (PROCESSED_REANALYSIS, RAW_REANALYSIS, QC_METRIC)


def candidate_pairs(targets: list[dict], checks: list[dict], *, route: str | None) -> list[dict]:
    """The (claim, check) pairs a run on this route could execute: available, or unresolved only for a
    requirement the run itself establishes (the sample mapping, what a deposit holds)."""
    pairs: list[dict] = []
    allowed = execution_checks(route)
    for index, (target, claim_checks) in enumerate(zip(targets, checks)):
        kind = claim_type(target)
        for check in allowed:
            if check == QC_METRIC and kind != SCALAR:
                continue
            if check == RAW_REANALYSIS and kind == SCALAR:
                continue  # the QC comparison is that run
            result = (claim_checks or {}).get(check) or {}
            if result.get("status") == AVAILABLE or (
                result.get("status") == UNRESOLVED and result.get("requirement") in _PENDING_REQUIREMENTS
            ):
                pairs.append(
                    {
                        "claim_index": index,
                        "check": check,
                        "status": result.get("status"),
                        "requirement": result.get("requirement"),
                        "kind": kind,
                    }
                )
    return pairs
