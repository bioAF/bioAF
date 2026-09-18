"""plan_8_2 section 4.2 (approved by the owner 2026-09-14): a compact scorecard and four report sections.

The projection every surface renders gains, for each of Findings, Data and code, Checks performed and Run
diagnostics, a short summary and counts, from the same facts the rest of the report states:

- findings are grouped by the experiment that reports them; a reason several findings share is stated once,
  naming the findings it affects; a valid discrepancy is opened by default;
- resources bioAF has no adapter for are one compact group with links, never hidden;
- the scorecard names its units (findings conclusive and inconclusive, checks by what they did), and a blank
  score says "No finding has a conclusive assessment yet" when that is the cause.

Paper-shaped fixtures; nothing here is a rule about any one paper.
"""

from app.services.validation_finding_inventory import inventory_from_proposal
from app.services.validation_report_summary import summarize
from tests.test_report_scorecard import scored_example

_P = [{"kind": "pvalue", "operator": "<", "value": 0.01}]
_ACCESS = {
    "status": "unavailable",
    "reason": "EGAS00001003667 holds the reads, and bioAF cannot acquire them (controlled access)",
    "requirement": "raw_reads",
}
_NO_MATRIX = {
    "status": "unavailable",
    "reason": "no processed matrix holding both arms is published",
    "requirement": "processed_matrix",
}


def _target(text, value, contrast):
    return {
        "claim_text": text,
        "claimed_value": value,
        "contrast_index": contrast,
        "cutoffs": _P,
        "output_type": "gene_set_size",
        "reported_experiment_id": "e1",
        "checks": {"processed_reanalysis": dict(_NO_MATRIX), "raw_reanalysis": dict(_ACCESS)},
    }


def _groff_like():
    """Four findings from one RNA-seq experiment, two claims each, all waiting on controlled access; one
    technical finding; a readable deposit, a controlled one, a structure and a sample record."""
    targets = []
    proposal = []
    for position, (category, words) in enumerate(
        [("primary", "aneuploid"), ("primary", "morphology"), ("primary", "morphokinetics"), ("supporting", "sex")]
    ):
        targets += [_target(f"{words} genes down", 5, position), _target(f"{words} genes up", 48, position)]
        proposal.append(
            {
                "description": f"Differentially expressed genes by {words}",
                "claim_indices": [2 * position, 2 * position + 1],
                "importance": category,
                "rationale": "A result the paper reports.",
                "quote": f"{words} genes down",
            }
        )
    targets.append({**_target("44.6 million reads per library", 44.6, None), "output_type": "count"})
    proposal.append(
        {
            "description": "Sequencing depth per library",
            "claim_indices": [8],
            "importance": "technical",
            "rationale": "Depth.",
            "quote": "44.6 million reads per library",
        }
    )
    text = " ".join(t["claim_text"] for t in targets)
    plan = {
        "differential_design": {"contrasts": [{"name": f"contrast {i}", "cutoffs": _P} for i in range(4)]},
        "reported_experiments": [{"id": "e1", "assay": "bulk RNA-seq", "workflow": "nf-core/rnaseq"}],
        "resources": [
            {"identifier": "EGAS00001003667", "type": "sequencing_data", "reported_experiment_ids": ["e1"]},
            {"identifier": "GSE000001", "type": "sequencing_data", "reported_experiment_ids": ["e1"]},
            {"identifier": "6LUI", "type": "structure", "archive": "pdb", "role": "a protein structure"},
            {"identifier": "GSM2454338", "type": "sequencing_data", "role": "a sample"},
        ],
        "finding_inventory": {
            **inventory_from_proposal(proposal, targets=targets, full_text=text, decided_by={"kind": "model"}),
            "rubric_version": 2,
        },
    }
    return plan, targets


def _summary(plan=None, targets=None, checks=None, issues=None):
    if plan is None:
        plan, targets = _groff_like()
    return summarize(
        study={"state": "classified", "classification": "access_restricted"},
        evidence={},
        plan=plan,
        targets=targets,
        issues=issues or [],
        checks=checks,
    )


class TestTheScorecardNamesItsUnits:
    def test_a_blank_score_says_no_finding_is_conclusive_yet(self):
        card = _summary()["scorecard"]
        assert card["status"] == "not_assessed"
        assert card["score_note"] == "No finding has a conclusive assessment yet"
        assert card["score_status_label"] is None  # the blank stays a blank; the note explains it

    def test_findings_and_checks_are_counted_in_their_own_units(self):
        # plan_8_4 defect 4: the finished record carries the outcome a finished record always
        # carries. Every caller that writes `done` writes one, and what the check concluded is now
        # read from it rather than assumed from the state.
        checks = [
            {"kind": "author_results", "state": "unresolved", "retry_count": 0},
            {"kind": "author_results", "state": "done", "retry_count": 0, "outcome": {"outcome": "agree"}},
            {"kind": "author_results", "state": "pending", "retry_count": 1},
        ]
        units = {u["key"]: (u["count"], u["label"]) for u in _summary(checks=checks)["scorecard"]["units"]}
        assert units["findings_conclusive"] == (0, "findings conclusive")
        assert units["findings_inconclusive"] == (4, "findings inconclusive")
        assert units["checks_concluded"] == (1, "check concluded")
        assert units["checks_without_conclusion"] == (1, "check completed without a conclusion")
        assert units["checks_under_way"] == (1, "check under way")

    def test_a_finished_check_that_concluded_nothing_is_not_counted_as_concluded(self):
        """plan_8_4 defect 4: the queue's STATE says the check finished; the OUTCOME says whether it
        settled anything. A comparison that ran, read its table and could not resolve the claim is
        recorded ``done`` with an outcome of ``unresolved``, and counting that as a check concluded
        tells a reader the paper was checked when nothing about it was established.
        """
        checks = [
            {"kind": "author_results", "state": "done", "retry_count": 0, "outcome": {"outcome": "agree"}},
            {
                "kind": "author_results",
                "state": "done",
                "retry_count": 0,
                "outcome": {"outcome": "unresolved", "reason": "the refinement's cutoff reading is not established"},
            },
            {
                "kind": "author_results",
                "state": "done",
                "retry_count": 0,
                "outcome": {"outcome": "not_checkable", "reason": "the claim states no number to compare"},
            },
        ]
        card = _summary(checks=checks)["scorecard"]
        units = {u["key"]: (u["count"], u["label"]) for u in card["units"]}
        assert units["checks_concluded"] == (1, "check concluded")
        assert units["checks_without_conclusion"] == (2, "checks completed without a conclusion")
        assert "checks_under_way" not in units
        counts = card["activity"]["counts"]
        assert counts["done"] == 3, "all three finished; the state is what the queue did"
        assert counts["concluded"] == 1, "only one settled its question"
        assert counts["finished_without_conclusion"] == 2
        assert card["activity"]["completed"] == 3
        assert "2 checks could not conclude" in card["activity"]["label"]


class TestFindings:
    def test_findings_are_grouped_by_the_experiment_that_reports_them(self):
        findings = _summary()["sections"]["findings"]
        (group, excluded) = findings["groups"]
        assert group["label"] == "Experiment e1: bulk RNA-seq (nf-core/rnaseq)"
        assert len(group["finding_ids"]) == 4
        assert excluded["key"] == "excluded" and len(excluded["finding_ids"]) == 1

    def test_a_reason_several_findings_share_is_stated_once_and_names_them(self):
        summary = _summary()
        (shared,) = summary["sections"]["findings"]["shared_reasons"]
        assert shared["id"] == "R1"
        assert len(shared["finding_ids"]) == 4
        assert "controlled access" in shared["text"]
        items = {i["finding_id"]: i for i in summary["scorecard"]["unassessed_items"]}
        assert all(items[f]["shared_reason"] == "R1" for f in shared["finding_ids"])

    def test_a_reason_never_repeats_a_clause(self):
        item = _summary()["scorecard"]["unassessed_items"][0]
        assert item["reason"].count("no processed matrix holding both arms is published") == 1

    def test_the_summary_and_counts_say_what_the_section_holds(self):
        findings = _summary()["sections"]["findings"]
        labels = [c["label"] for c in findings["counts"]]
        assert "3 primary" in labels and "1 supporting" in labels and "1 technical, not scored" in labels
        assert findings["summary"].startswith("4 findings scored, 0 assessed.")
        assert "stated once" in findings["summary"]

    def test_a_valid_discrepancy_opens_by_default(self):
        findings = scored_example()["sections"]["findings"]
        assert findings["open"] == ["F5"]


class TestDataAndCode:
    def test_resources_bioaf_has_no_adapter_for_are_one_compact_group_with_links(self):
        data = _summary()["sections"]["data"]
        unsupported = data["unsupported"]
        assert [r["identifier"] for r in unsupported["resources"]] == ["6LUI"]
        assert unsupported["resources"][0]["link"]
        assert [r["identifier"] for r in data["sample_records"]] == ["GSM2454338"]
        labels = [c["label"] for c in data["counts"]]
        assert "1 without an adapter" in labels and "1 sample record" in labels


class TestChecksPerformed:
    def test_each_check_says_how_many_claims_it_applies_to_and_what_it_found(self):
        checks = {r["key"]: r for r in _summary()["sections"]["checks"]["rows"]}
        assert checks["raw_reanalysis"]["unavailable"] == 9
        assert checks["raw_reanalysis"]["label"] == "Reanalysis from raw reads"


class TestRunDiagnostics:
    def test_the_check_records_retries_and_issues_are_listed(self):
        checks = [
            {
                "check_id": "plan:1:claim:1:author_results",
                "kind": "author_results",
                "state": "pending",
                "revision": 2,
                "retry_count": 1,
                "terminal_reason": None,
            }
        ]
        diagnostics = _summary(checks=checks, issues=[{"message": "x"}])["sections"]["diagnostics"]
        (row,) = diagnostics["checks"]
        assert (row["check_id"], row["revision"], row["retry_count"]) == ("plan:1:claim:1:author_results", 2, 1)
        labels = [c["label"] for c in diagnostics["counts"]]
        assert "1 check record" in labels and "1 retrying" in labels and "1 issue" in labels


def test_a_paper_outside_bioafs_methods_names_its_experiments_instead():
    plan = {
        "reported_experiments": [
            {"id": "e1", "assay": "qRT-PCR", "workflow": None},
            {"id": "e2", "assay": "western blot", "workflow": None},
        ],
        "finding_inventory": {"status": "not_applicable", "findings": [], "reason": "no computational finding"},
    }
    summary = _summary(plan=plan, targets=[])
    assert summary["sections"]["findings"]["summary"].startswith("bioAF has no validation method for qRT-PCR")
    units = {u["key"]: u["count"] for u in summary["scorecard"]["units"]}
    assert units == {"experiments_outside_methods": 2}
