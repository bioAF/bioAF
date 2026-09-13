"""plan_8_1 stage 4: version 2 read onto each finding from the study's evidence, and onto every surface.

A version 2 inventory lets author-result consistency govern a claim at consistency depth; a version 1
inventory keeps version 1's reading, and a record kept under version 1 keeps its score and label. Each
unassessed check keeps its own cause, so an access-blocked analysis and a consistency check whose table
could not be retrieved are never collapsed into one "Access required".
"""

import pytest

from app.services.validation_finding_outcomes import finding_outcomes
from app.services.validation_report_summary import _usable_record, scorecard_projection
from app.services.validation_scorecard import BLOCKED, DISCREPANCY, SUPPORTED

_P = [{"kind": "pvalue", "operator": "<", "value": 0.01}]


def _check(status, reason=None, requirement=None):
    return {"status": status, "reason": reason, "requirement": requirement}


_AVAILABLE = {
    "qc_metric": _check("unavailable", "no QC metric measures a differential result", "qc_binding"),
    "author_results": _check("available", "the authors published t.txt.gz"),
    "processed_reanalysis": _check("unresolved", "the mapping is established with the input", "sample_mapping"),
    "raw_reanalysis": _check("available"),
}
# Groff: the reads are controlled, and the results supplement is the only author table.
_GROFF = {
    "qc_metric": _check("unavailable", "no QC metric measures a differential result", "qc_binding"),
    "author_results": _check("available", "the authors published Supplementary Table S3"),
    "processed_reanalysis": _check(
        "unavailable", "no processed matrix holding both arms is published", "processed_matrix"
    ),
    "raw_reanalysis": _check(
        "unavailable", "EGAS1 holds the reads, and bioAF cannot acquire them (controlled access)", "raw_reads"
    ),
}


def _target(text, checks, experiment="e1"):
    return {
        "claim_text": text,
        "claimed_value": 100,
        "contrast_index": 0,
        "cutoffs": _P,
        "checks": checks,
        "reported_experiment_id": experiment,
    }


def _finding(fid, category, claims):
    return {
        "id": fid,
        "description": f"finding {fid}",
        "claim_indices": claims,
        "required": claims,
        "prerequisite_for": [],
        "importance": {
            "category": category,
            "weight": {"primary": 2, "supporting": 1, "technical": 0}[category],
            "status": "validated",
        },
        "criteria": {"rule": "all_required_subchecks", "words": "w"},
    }


def _inventory(*findings, version=2):
    return {"rubric_version": version, "revision": 1, "status": "established", "findings": list(findings)}


_PLAN = {
    "differential_design": {"contrasts": [{"name": "c0", "cutoffs": _P}]},
    "analysis_selection": {"current": {"revision": 2, "claim_index": 0, "check": "processed_reanalysis"}},
}
_AGREE = {"outcome": "agree", "label": "Consistent with the authors' deposited results", "table": "t.txt.gz"}
_DISAGREE = {"outcome": "disagree", "label": "Differs from the authors' results", "table": "t.txt.gz"}


def _level3(verdict):
    return {
        "level3": {"claim_index": 0, "source": "deposit", "contrast": "c0", "cutoffs": {"significance": _P[0]}},
        "level3_result": {
            "concordance": {"verdict": verdict, "paper_n": 100, "concordant": 70, "enrichment_p": 1e-9, "notes": []}
        },
        "classification_result": {"comparisons": [], "attribution": {"our_side": "cleared"}},
        "artifact_revisions": {"level3": 2, "level3_result": 2},
    }


def _outcomes(inventory, targets, evidence, consistency, study=None, plan=None):
    return finding_outcomes(
        inventory,
        targets=targets,
        plan=_PLAN if plan is None else plan,
        evidence=evidence,
        study=study or {"state": "classified"},
        consistency=consistency,
    )


class TestConsistencyGovernsUnderVersionTwoOnly:
    def test_an_agreeing_table_supports_the_finding_at_consistency_depth(self):
        outcome = _outcomes(_inventory(_finding("F1", "primary", [0])), [_target("up", _AVAILABLE)], {}, {0: _AGREE})[
            "F1"
        ]
        assert (outcome["status"], outcome["depth"]) == (SUPPORTED, "consistency")
        assert outcome["governing"]["method"] == "author_results"
        assert outcome["subchecks"][0]["depth"] == "consistency"

    def test_version_one_keeps_consistency_out_of_the_numbers(self):
        inventory = _inventory(_finding("F1", "primary", [0]), version=1)
        outcome = _outcomes(inventory, [_target("up", _AVAILABLE)], {}, {0: _AGREE})["F1"]
        assert outcome["status"] not in (SUPPORTED, DISCREPANCY)
        assert "depth" not in outcome

    def test_an_independent_discrepancy_governs_and_the_agreeing_table_is_a_concern(self):
        outcome = _outcomes(
            _inventory(_finding("F1", "primary", [0])), [_target("up", _AVAILABLE)], _level3("diverge"), {0: _AGREE}
        )["F1"]
        assert (outcome["status"], outcome["depth"]) == (DISCREPANCY, "independent")
        assert any("the authors' table agrees with the paper" in c["text"] for c in outcome["concerns"])

    def test_an_independent_success_with_a_disagreeing_table_stays_supported(self):
        outcome = _outcomes(
            _inventory(_finding("F1", "primary", [0])), [_target("up", _AVAILABLE)], _level3("agree"), {0: _DISAGREE}
        )["F1"]
        assert (outcome["status"], outcome["depth"]) == (SUPPORTED, "independent")
        assert [c["text"] for c in outcome["concerns"]] == ["the paper's text disagrees with its own table"]

    def test_the_outcome_names_the_experiments_its_claims_serve(self):
        outcome = _outcomes(_inventory(_finding("F1", "primary", [0])), [_target("up", _AVAILABLE)], {}, {0: _AGREE})[
            "F1"
        ]
        assert outcome["experiment_ids"] == ["e1"]


class TestEachCheckKeepsItsOwnCause:
    def test_groff_access_blocked_analysis_and_unretrieved_supplement_each_keep_their_cause(self):
        unretrieved = {
            "outcome": "unresolved",
            "label": "Unresolved against the authors' results",
            "reason": "the results supplement could not be retrieved",
            "table": None,
        }
        outcome = _outcomes(
            _inventory(_finding("F1", "primary", [0])),
            [_target("up", _GROFF)],
            {},
            {0: unretrieved},
            plan={},
        )["F1"]
        assert (outcome["status"], outcome["cause"]) == (BLOCKED, "access")
        causes = {c["check"]: c for c in outcome["check_causes"]}
        assert causes["raw_reanalysis"]["cause_label"] == "Access required"
        assert causes["author_results"]["cause_label"] == "Not resolved by bioAF"
        assert "could not be retrieved" in causes["author_results"]["reason"]
        assert "could not be retrieved" in outcome["reason"]


def _plan_projection(version):
    return {
        **_PLAN,
        "finding_inventory": _inventory(_finding("F1", "primary", [0]), version=version),
        "reported_experiments": [{"id": "e1", "assay": "bulk RNA-seq", "resources": ["GSE1"]}],
    }


_DEPOSITS = {
    "capabilities": {"deposits": [{"accession": "GSE1", "archive": "geo", "exists": "yes", "access": "public"}]}
}


class TestTheProjection:
    def test_version_two_shows_the_resource_statements_beneath_the_findings_they_serve(self):
        card = scorecard_projection(
            study={"state": "classified"},
            evidence=_DEPOSITS,
            plan=_plan_projection(2),
            targets=[_target("up", _AVAILABLE)],
        )
        assert [s["identifier"] for s in card["resource_statements"]] == ["GSE1"]
        items = card["assessed_items"] + card["unassessed_items"]
        assert items[0]["resource_statements"] == ["GSE1"]

    def test_version_one_shows_none(self):
        card = scorecard_projection(
            study={"state": "classified"},
            evidence=_DEPOSITS,
            plan=_plan_projection(1),
            targets=[_target("up", _AVAILABLE)],
        )
        assert card["resource_statements"] == []
        assert card["rubric_label"] == "weighted rubric version 1"

    def test_a_record_kept_under_version_one_stays_usable_for_its_inventory(self):
        plan = _plan_projection(1)
        record = {"rubric_version": 1, "inventory_revision": 1, "analysis_selection_revision": 2, "outcomes": {}}
        assert _usable_record({"state": "classified"}, {"scorecard_record": record}, plan) is record

    def test_a_record_kept_under_another_version_than_its_inventory_is_not_used(self):
        plan = _plan_projection(2)
        record = {"rubric_version": 1, "inventory_revision": 1, "analysis_selection_revision": 2, "outcomes": {}}
        assert _usable_record({"state": "classified"}, {"scorecard_record": record}, plan) is None


class TestANewInventoryIsVersionTwo:
    def test_the_pending_inventory_and_an_established_one_carry_version_two_and_the_authority(self):
        from app.services.validation_finding_inventory import inventory_from_proposal
        from app.services.validation_inventory_stage import pending_inventory
        from app.services.validation_rubric_v2 import AUTHORITY

        assert pending_inventory()["rubric_version"] == 2
        inventory = inventory_from_proposal({"findings": []}, targets=[], full_text=None, decided_by={"by": "test"})
        assert inventory["rubric_version"] == 2
        assert inventory["authority"] == {"rubric_version": 2, **AUTHORITY}


@pytest.mark.asyncio
async def test_the_concluded_record_keeps_its_inventorys_version(monkeypatch):
    from app.services import validation_report_summary as summary

    class _Plan:
        id = 1
        finding_inventory_json = _inventory(_finding("F1", "primary", [0]), version=1)

    class _Study:
        id = 1
        state = "classified"
        classification = None
        analysis_run_id = None
        data_run_id = None
        evidence_json: dict = {}

    async def _active_plan(session, study):
        return _Plan()

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class _Session:
        async def execute(self, *_a, **_k):
            return _Result()

    monkeypatch.setattr("app.services.validation_assessment.active_plan", _active_plan)
    monkeypatch.setattr(summary, "plan_projection", lambda plan: {"finding_inventory": plan.finding_inventory_json})
    study = _Study()
    await summary.record_scorecard(_Session(), study)
    assert study.evidence_json["scorecard_record"]["rubric_version"] == 1


class TestTheMarkdown:
    def test_it_shows_the_depth_beside_the_scope_and_on_every_item_with_the_statements(self):
        from app.services.provenance.markdown_renderer import _append_scorecard
        from app.services.validation_rubric_v2 import resource_statements
        from app.services.validation_scorecard import build_scorecard

        outcomes = {
            "F1": {
                "status": SUPPORTED,
                "depth": "consistency",
                "governing": {"method": "author_results", "evidence": ["author_results (t.txt.gz)"]},
                "concerns": [{"kind": "text_disagrees", "text": "the paper's text disagrees with its own table"}],
                "experiment_ids": ["e1"],
            }
        }
        statements = resource_statements(_plan_projection(2), _DEPOSITS)
        card = build_scorecard(_inventory(_finding("F1", "primary", [0])), outcomes, resource_statements=statements)
        parts: list[str] = []
        _append_scorecard(parts, card)
        text = "\n".join(parts)
        assert "1 / 1 assessed (1 consistency only; 0 independently assessed)" in text
        assert "Supported, Consistency only" in text
        assert "Governing evidence: author_results (t.txt.gz)" in text
        assert "Concern: the paper's text disagrees with its own table" in text
        assert "Resource statement GSE1: Verified" in text
        assert "### Resource statements" in text
