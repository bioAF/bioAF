"""change_7.5 section 2.5: each claim is evaluated against four checks, each on its own.

Study 38's four differential-expression counts read "No supported metric ... the claim cannot be
compared", because a claim's status came from its QC binding alone. The authors' own result tables sat
in the deposit. A declined QC binding closes only the QC check; consistency is available whenever its
table exists; the requested route bounds what a run may execute and never what is available.

Deterministic, with no model call.
"""

from app.services.validation_checks import (
    AUTHOR_RESULTS,
    AVAILABLE,
    PROCESSED_REANALYSIS,
    QC_METRIC,
    RAW_REANALYSIS,
    UNAVAILABLE,
    UNRESOLVED,
    candidate_pairs,
    claim_type,
    evaluate_checks,
)

_P = [{"kind": "pvalue", "operator": "<", "value": 0.01}]


def _experiment(**over):
    usable = {"status": "usable", "resolved": "GRCh38", "reason": None}
    return {
        "id": "e1",
        "assay": "bulk RNA-seq",
        "workflow": "nf-core/rnaseq",
        "reference": {"assembly": dict(usable), "annotation": {**usable, "resolved": "Ensembl 112"}},
        **over,
    }


def _resource(**listing):
    return {
        "identifier": "GSE1",
        "archive": "geo",
        "type": "sequencing_data",
        "reported_experiment_ids": ["e1"],
        "listing": {"kinds": {}, "result_tables": [], **listing},
        "bioaf": {"retrievable": "yes", "analyzable": "yes", "limitation": None},
    }


_DEPOSIT = {"accession": "GSE1", "archive": "geo", "exists": "yes", "access": "public", "supported": "yes",
            "raw_data": "yes", "preprocessed_data": "yes"}


def _differential(**over):
    return {"claim_text": "257 genes were up", "claimed_value": 257, "output_type": "gene_set_size",
            "contrast_index": 0, "cutoffs": _P, "reported_experiment_id": "e1", "bound_by": "model", **over}


def _checks(target, **kw):
    return evaluate_checks(
        target,
        experiment=kw.get("experiment", _experiment()),
        resources=kw.get("resources", [_resource(kinds={"matrix_normalized": 1, "de_table": 1}, result_tables=["t.txt.gz"])]),
        deposits=kw.get("deposits", [_DEPOSIT]),
        supplements=kw.get("supplements", []),
        contrast=kw.get("contrast", {"name": "KO vs WT", "cutoffs": _P}),
    )


# ---- claim types ----


def test_claim_types_are_read_from_the_claim_itself():
    assert claim_type(_differential()) == "differential"
    assert claim_type({"claim_text": "SAMD1 target genes are up in KO", "direction": "up", "contrast_index": 0}) == "membership"
    assert claim_type({"metric_key": "total_samples", "claimed_value": 54, "unit": "samples", "output_type": "count"}) == "sample_count"
    assert claim_type({"metric_key": "peak_count", "claimed_value": 8000, "unit": "peaks"}) == "scalar"


# ---- a differential count or set ----


def test_a_differential_count_has_no_qc_metric():
    checks = _checks(_differential())
    assert checks[QC_METRIC]["status"] == UNAVAILABLE
    assert "differential" in checks[QC_METRIC]["reason"]


def test_consistency_is_available_when_the_authors_result_table_exists():
    assert _checks(_differential())[AUTHOR_RESULTS]["status"] == AVAILABLE
    no_table = _checks(_differential(), resources=[_resource(kinds={"matrix_normalized": 1})])
    assert no_table[AUTHOR_RESULTS]["status"] == UNAVAILABLE


def test_processed_reanalysis_waits_on_the_sample_mapping():
    checks = _checks(_differential())
    assert checks[PROCESSED_REANALYSIS]["status"] == UNRESOLVED
    assert checks[PROCESSED_REANALYSIS]["requirement"] == "sample_mapping"


def test_processed_reanalysis_needs_a_processed_matrix():
    checks = _checks(_differential(), resources=[_resource(kinds={"coverage": 12}, result_tables=["t.txt.gz"])])
    assert checks[PROCESSED_REANALYSIS]["status"] == UNAVAILABLE
    assert checks[PROCESSED_REANALYSIS]["requirement"] == "processed_matrix"


def test_raw_reanalysis_needs_a_usable_reference():
    unavailable = {"status": "unavailable", "resolved": None, "reason": "the paper states GENCODE M23; bioAF supplies Ensembl 102"}
    experiment = _experiment(reference={"assembly": {"status": "usable", "resolved": "GRCm38"}, "annotation": unavailable})
    checks = _checks(_differential(), experiment=experiment)
    assert checks[RAW_REANALYSIS]["status"] == UNAVAILABLE
    assert checks[RAW_REANALYSIS]["requirement"] == "reference"
    assert "GENCODE M23" in checks[RAW_REANALYSIS]["reason"]
    # The reference decides nothing else.
    assert checks[AUTHOR_RESULTS]["status"] == AVAILABLE


def test_raw_reanalysis_needs_fetchable_reads():
    deposit = {**_DEPOSIT, "raw_data": "no"}
    assert _checks(_differential(), deposits=[deposit])[RAW_REANALYSIS]["requirement"] == "raw_reads"


def test_raw_reanalysis_is_available_with_reads_a_workflow_a_reference_and_a_predicate():
    assert _checks(_differential())[RAW_REANALYSIS]["status"] == AVAILABLE


def test_an_unresolved_significance_leaves_the_checks_that_need_it_unresolved():
    contrast = {"name": "KO vs WT", "cutoffs": _P, "thresholds_unresolved": "two readings"}
    checks = _checks(_differential(), contrast=contrast)
    assert checks[AUTHOR_RESULTS]["status"] == UNRESOLVED
    assert checks[RAW_REANALYSIS]["status"] == UNRESOLVED
    assert checks[RAW_REANALYSIS]["requirement"] == "predicate"


def test_an_unlisted_deposit_is_unresolved_never_unavailable():
    checks = _checks(_differential(), resources=[], deposits=[])
    assert checks[PROCESSED_REANALYSIS]["status"] == UNRESOLVED
    assert checks[PROCESSED_REANALYSIS]["requirement"] == "deposit_listing"


# ---- a scalar measured quantity ----


def _scalar(**over):
    return {"metric_key": "peak_count", "claimed_value": 8000, "unit": "peaks", "reported_experiment_id": "e1",
            "bound_key": "peak_count", "bound_by": "model",
            "binding_facts": {"match": {"ok": True}}, **over}


def test_a_matched_binding_makes_the_qc_check_available():
    experiment = _experiment(workflow="nf-core/chipseq", assay="ChIP-seq")
    checks = _checks(_scalar(), experiment=experiment)
    assert checks[QC_METRIC]["status"] == AVAILABLE
    assert checks[RAW_REANALYSIS]["reason"].startswith("the same run as the QC metric comparison")


def test_a_declined_binding_closes_only_the_qc_check():
    experiment = _experiment(workflow="nf-core/chipseq", assay="ChIP-seq")
    declined = _scalar(bound_key=None, binding_facts=None)
    checks = _checks(declined, experiment=experiment, resources=[_resource(kinds={"peaks": 1})])
    assert checks[QC_METRIC]["status"] == UNAVAILABLE
    assert checks[AUTHOR_RESULTS]["status"] == AVAILABLE  # the deposited peak set measures it


def test_a_binding_whose_facts_did_not_match_carries_the_mismatch():
    facts = {"match": {"ok": False, "status": "unavailable", "reason": "a value for one sample is not a mean over all of them"}}
    checks = _checks(_scalar(bound_key=None, binding_facts=facts), experiment=_experiment(workflow="nf-core/chipseq"))
    assert checks[QC_METRIC]["status"] == UNAVAILABLE
    assert "one sample" in checks[QC_METRIC]["reason"]


# ---- a sample or population count ----


def test_a_sample_count_is_checked_against_the_registered_samples():
    target = {"metric_key": "total_samples", "claimed_value": 54, "unit": "samples", "output_type": "count",
              "reported_experiment_id": "e1"}
    checks = _checks(target, deposits=[{**_DEPOSIT, "registered_samples": 54}])
    assert checks[AUTHOR_RESULTS]["status"] == AVAILABLE
    assert checks[QC_METRIC]["status"] == UNAVAILABLE
    assert checks[PROCESSED_REANALYSIS]["status"] == UNAVAILABLE
    assert checks[RAW_REANALYSIS]["status"] == UNAVAILABLE


# ---- candidates: the requested route bounds execution, not availability ----


def test_the_route_bounds_which_pairs_are_candidates():
    targets = [_differential()]
    checks = [_checks(targets[0])]
    deposit_pairs = candidate_pairs(targets, checks, route="deposit")
    assert [(p["claim_index"], p["check"]) for p in deposit_pairs] == [(0, PROCESSED_REANALYSIS)]
    pipeline_pairs = candidate_pairs(targets, checks, route="pipeline")
    assert [(p["claim_index"], p["check"]) for p in pipeline_pairs] == [(0, RAW_REANALYSIS)]
    # Availability itself is unchanged by the route.
    assert checks[0][RAW_REANALYSIS]["status"] == AVAILABLE


def test_consistency_is_never_a_candidate():
    targets = [_differential()]
    checks = [_checks(targets[0])]
    assert all(p["check"] != AUTHOR_RESULTS for p in candidate_pairs(targets, checks, route="both"))


def test_a_pair_unavailable_on_the_route_is_not_a_candidate():
    targets = [_differential()]
    checks = [_checks(targets[0], resources=[_resource(kinds={"coverage": 2})])]
    assert candidate_pairs(targets, checks, route="deposit") == []


def test_an_absent_requirement_decides_before_one_not_yet_established():
    """The reference is known to be unavailable before the deposit is listed; raw reanalysis is then
    unavailable, not waiting on the listing."""
    unavailable = {"status": "unavailable", "resolved": None, "reason": "the paper states mm9; bioAF cannot supply mm9"}
    experiment = _experiment(workflow="nf-core/chipseq", reference={"assembly": unavailable, "annotation": {"status": "unstated"}})
    checks = _checks(_differential(), experiment=experiment, resources=[_resource()], deposits=[])
    assert checks[PROCESSED_REANALYSIS]["requirement"] == "deposit_listing"
    assert checks[RAW_REANALYSIS]["status"] == UNAVAILABLE
    assert checks[RAW_REANALYSIS]["requirement"] == "reference"
