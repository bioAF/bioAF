"""change_7.5 sections 2.5 and 2.6 in the report: every claim shows its four checks, its experiment and
whether this run selected it; unselected claims are "Not assessed in this run" with the reason; a stale
artifact is shown as history, never as current.

Labels here are pending the owner's sign-off, item by item.
"""

from app.services.validation_report_summary import summarize

_P = [{"kind": "pvalue", "operator": "<", "value": 0.01}]

_CHECKS_DE = {
    "qc_metric": {"status": "unavailable", "reason": "no QC metric measures a differential result", "requirement": "qc_binding"},
    "author_results": {"status": "available", "reason": "the authors published t.txt.gz", "requirement": None},
    "processed_reanalysis": {"status": "unresolved", "reason": "the sample mapping is established when the input is chosen", "requirement": "sample_mapping"},
    "raw_reanalysis": {"status": "unavailable", "reason": "the paper states GENCODE M23", "requirement": "reference"},
}
_CHECKS_PEAKS = {
    "qc_metric": {"status": "unavailable", "reason": "a value for one sample is not a mean over all of them", "requirement": "qc_binding"},
    "author_results": {"status": "unavailable", "reason": "no deposited or attached file measures this quantity", "requirement": "measured_file"},
    "processed_reanalysis": {"status": "unavailable", "reason": "bioAF computes no QC metric from processed files", "requirement": "not_applicable"},
    "raw_reanalysis": {"status": "unavailable", "reason": "the same run as the QC metric comparison", "requirement": "qc_binding"},
}

PLAN = {
    "reported_experiments": [
        {"id": "e1", "assay": "ChIP-seq", "workflow": "nf-core/chipseq",
         "reference": {"assembly": {"stated": "mm9", "status": "unavailable", "reason": "bioAF cannot supply mm9"},
                       "annotation": {"stated": None, "status": "unstated", "reason": "the paper does not state its annotation"}}},
        {"id": "e2", "assay": "bulk RNA-seq", "workflow": "nf-core/rnaseq",
         "reference": {"assembly": {"stated": None, "status": "usable", "resolved": "GRCm38", "established_from": "annotation release"},
                       "annotation": {"stated": "GENCODE M23", "status": "unavailable", "reason": "bioAF supplies Ensembl 102"}}},
    ],
    "differential_design": {"contrasts": [{"name": "KO vs WT", "cutoffs": _P}]},
    "analysis_selection": {
        "current": {"revision": 2, "reported_experiment_id": "e2", "claim_index": 1, "check": "processed_reanalysis",
                    "contrast_index": 0, "workflow": "nf-core/rnaseq", "decided_by": "model",
                    "reason": "the authors' table can be compared", "confidence": 0.8},
        "history": [{"revision": 1, "claim_index": 2, "superseded": True}],
        "unassessed": [{"claim_index": 0, "reason": "not selected for this run; QC metric comparison: a value for one sample"},
                       {"claim_index": 2, "reason": "not selected for this run; its checks are available"}],
    },
}
TARGETS = [
    {"claim_text": "we called 8000 peaks", "claimed_value": 8000, "unit": "peaks", "reported_experiment_id": "e1",
     "checks": _CHECKS_PEAKS, "bound_by": "model"},
    {"claim_text": "257 genes were up", "claimed_value": 257, "contrast_index": 0, "cutoffs": _P,
     "reported_experiment_id": "e2", "checks": _CHECKS_DE, "bound_by": "model"},
    {"claim_text": "524 genes were down", "claimed_value": 524, "contrast_index": 0, "cutoffs": _P,
     "reported_experiment_id": "e2", "checks": _CHECKS_DE, "bound_by": "model"},
]
EVIDENCE = {
    "selection_history": [
        {"revision": 1, "artifacts": {"level3": {"x": 1}}, "invalidated_by": ["claim"], "at": "2026-09-12T00:00:00+00:00"}
    ],
    # change_7.5 section 4: the selected claim checked against the authors' table, and its reanalysis.
    "author_consistency": {"records": [{
        "claim_index": 1, "table": "t.txt.gz", "source": "deposit", "outcome": "unresolved",
        "reason": "the table's ratio orientation is not established by its header, a legend, the methods or a confirmation",
        "columns": {"id": "gene", "lfc": "log2FoldChange", "pvalue": "pvalue", "padj": "padj"},
        "rows_tested": 20000, "rows_passing": None, "rows_missing": 312, "count_range": None,
        "duplicates_disagreeing": [], "assumptions": [],
        "candidates": [{"interpretation": "the table is KO over WT", "count": 257},
                       {"interpretation": "the table is WT over KO", "count": 524}],
    }]},
    "level3": {"claim_index": 1, "source": "deposit", "contrast": "KO vs WT"},
    "level3_result": {"concordance": None, "claim_count": {
        "count": 250, "status": "fails", "words": "250 against the claim's exactly 257",
        "label": "Reanalysis count, from a different method than the paper's"}},
}


def _summary():
    return summarize(study={"state": "plan_ready"}, evidence=EVIDENCE, plan=PLAN, targets=TARGETS, issues=[])


def test_every_claim_shows_its_four_checks_with_labels():
    claim = _summary()["claims"][1]
    assert [c["key"] for c in claim["checks"]] == ["qc_metric", "author_results", "processed_reanalysis", "raw_reanalysis"]
    labels = {c["key"]: (c["label"], c["status_label"]) for c in claim["checks"]}
    assert labels["qc_metric"] == ("QC metric comparison", "Unavailable")
    assert labels["author_results"] == ("Consistency with the authors' results", "Available")
    assert labels["processed_reanalysis"] == ("Reanalysis of processed data", "Unresolved")
    assert labels["raw_reanalysis"] == ("Reanalysis from raw reads", "Unavailable")
    assert claim["checks"][3]["reason"] == "the paper states GENCODE M23"


def test_each_claim_names_its_experiment():
    claims = _summary()["claims"]
    assert claims[0]["experiment"] == {"id": "e1", "assay": "ChIP-seq"}
    assert claims[1]["experiment"] == {"id": "e2", "assay": "bulk RNA-seq"}


def test_the_selected_claim_and_the_unassessed_ones_say_so():
    claims = _summary()["claims"]
    assert claims[1]["selection"] == {"status": "selected", "label": "Selected for this run", "check_label": "Reanalysis of processed data", "reason": "the authors' table can be compared"}
    assert claims[0]["selection"]["status"] == "unassessed"
    assert claims[0]["selection"]["label"] == "Not assessed in this run"
    assert "a value for one sample" in claims[0]["selection"]["reason"]


def test_the_selection_is_summarised_with_its_revision_and_who_decided():
    selection = _summary()["selection"]
    assert selection["revision"] == 2
    assert selection["workflow"] == "nf-core/rnaseq"
    assert selection["check_label"] == "Reanalysis of processed data"
    assert selection["decided_by"] == "model"
    assert selection["superseded_revisions"] == 1


def test_a_stale_artifact_is_history_never_current():
    [entry] = _summary()["selection_history"]
    assert entry["revision"] == 1
    assert entry["artifacts"] == ["level3"]
    assert entry["label"] == "Computed for an earlier selection (revision 1)"


def test_each_experiment_shows_its_reference_parts():
    [chip, rna] = _summary()["experiments"]
    assert chip["workflow"] == "nf-core/chipseq"
    assert chip["reference"][0] == {"part": "assembly", "stated": "mm9", "resolved": None, "status": "unavailable",
                                    "status_label": "Unavailable", "reason": "bioAF cannot supply mm9", "established_from": None}
    assert rna["reference"][0]["established_from"] == "annotation release"


def test_a_legacy_plan_has_no_checks_and_no_selection():
    summary = summarize(study={"state": "classified"}, evidence={}, plan={}, targets=[{"claim_text": "x"}], issues=[])
    assert summary["claims"][0]["checks"] == []
    assert summary["claims"][0]["selection"] is None
    assert summary["selection"] is None
    assert summary["experiments"] == []


def test_the_selected_claim_carries_its_consistency_and_its_reanalysis():
    claim = _summary()["claims"][1]
    assert claim["consistency"]["label"] == "Unresolved against the authors' results"
    assert [c["count"] for c in claim["consistency"]["candidates"]] == [257, 524]
    assert claim["result"]["tier"] == "Deposited data"
    assert claim["result"]["count"]["words"] == "250 against the claim's exactly 257"
    assert claim["predicate"] == "KO vs WT, P < 0.01, either direction, no fold-change requirement"


def test_a_reanalysis_attaches_only_to_the_claim_it_was_scored_for():
    assert _summary()["claims"][2]["result"] is None
