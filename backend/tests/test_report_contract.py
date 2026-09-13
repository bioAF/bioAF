"""change_7.3 section 11: every vocabulary has a label on both stacks, held by one checked-in contract.

The frontend renders the report projection; its tests render the fixture this test writes. So a label
the backend adds, renames or drops fails here until the fixture the frontend tests read is
regenerated, and a frontend map that lacks a value in the fixture's ``enums`` fails there.

Regenerate after an intended change with ``BIOAF_WRITE_REPORT_CONTRACT=1``.
"""

import json
import os
import pathlib

import pytest

_FIXTURE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "components"
    / "validation"
    / "__fixtures__"
    / "reportContract.json"
)
_FROZEN = "2026-09-10T13:09:18+00:00"


async def _contract() -> dict:
    from app.services.validation_report_summary import enum_labels, summarize
    from tests.test_report_summary import _STUDY_34, _groff_failed_evidence

    evidence = await _groff_failed_evidence()
    # Times are the one thing that differs between runs; the contract is about shape and wording.
    for entry in evidence["retrieval_ledger"]:
        entry["at"] = _FROZEN
    groff = summarize(
        study={"state": "classified", "classification": "access_restricted"},
        evidence=evidence,
        plan=_STUDY_34["reproduction_plan"],
        targets=_STUDY_34["reproduction_plan"]["comparison_targets"],
        issues=[],
    )
    legacy = summarize(
        study={"state": "classified", "classification": _STUDY_34["study"]["classification"]},
        evidence=_STUDY_34["evidence"],
        plan=_STUDY_34["reproduction_plan"],
        targets=_STUDY_34["reproduction_plan"]["comparison_targets"],
        issues=[],
    )
    attempted = summarize(
        study={"state": "classified", "classification": "inconclusive", "analysis_run_id": 7},
        evidence={},
        plan={},
        targets=[],
        issues=[],
    )
    mapping_unresolved = summarize(
        study={"state": "classified", "classification": "inconclusive"},
        evidence=_mapping_unresolved_evidence(),
        plan={},
        targets=[],
        issues=[],
    )
    reference_unavailable = summarize(
        study={"state": "classified", "classification": "inconclusive"},
        evidence=_reference_unavailable_evidence(),
        plan={},
        targets=[],
        issues=[],
    )
    from tests.test_report_claim_checks import EVIDENCE as STAGE2_EVIDENCE
    from tests.test_report_claim_checks import PLAN as STAGE2_PLAN
    from tests.test_report_claim_checks import TARGETS as STAGE2_TARGETS

    # change_7.5 stage 2: experiments, resources, each claim's four checks and the selection.
    stage2 = summarize(
        study={"state": "plan_ready"},
        evidence=STAGE2_EVIDENCE,
        plan={**STAGE2_PLAN, "resources": _stage2_resources()},
        targets=STAGE2_TARGETS,
        issues=[],
    )
    # plan_8: the Validation Scorecard in each of its states, through the real projection.
    from tests.test_report_scorecard import (
        _groff_plan_and_targets,
        _samd1_like,
        long_example,
        qc_scenario,
        scored_example,
    )

    groff_plan, groff_targets = _groff_plan_and_targets(evidence)
    groff_scored = summarize(
        study={"state": "classified", "classification": "access_restricted"},
        evidence=evidence,
        plan=groff_plan,
        targets=groff_targets,
        issues=[],
    )
    samd1_plan, samd1_targets, samd1_evidence = _samd1_like()
    samd1 = summarize(
        study={"state": "classified", "classification": "inconclusive"},
        evidence=samd1_evidence,
        plan=samd1_plan,
        targets=samd1_targets,
        issues=[],
    )
    unresolved_plan = {
        **samd1_plan,
        "finding_inventory": {
            **samd1_plan["finding_inventory"],
            "status": "unresolved",
            "reason": "The importance of F1 is not established: its quote is not in the paper's text.",
        },
    }
    unresolved_plan["finding_inventory"]["findings"] = [
        {
            **f,
            "importance": {
                **f["importance"],
                "status": "unresolved",
                "problem": "its quote is not in the paper's text",
            },
        }
        for f in unresolved_plan["finding_inventory"]["findings"]
    ]
    not_established = summarize(
        study={"state": "classified", "classification": "inconclusive"},
        evidence=samd1_evidence,
        plan=unresolved_plan,
        targets=samd1_targets,
        issues=[],
    )
    not_applicable = qc_scenario(["agree"], ["technical"])
    in_progress = qc_scenario(["agree", "not_computed"], ["primary", "supporting"], state="comparing")
    # plan_8_1 section 2.3: an unassessed finding of unestablished importance (a provisional scope), and an
    # assessed one (the score withheld).
    provisional = qc_scenario(["agree", "not_computed"], ["primary", "supporting"], unquoted=(1,))
    score_pending = qc_scenario(["agree", "diverge"], ["primary", "supporting"], unquoted=(1,))
    # plan_8_1 sections 1.3 and 1.4: a study shaped like 42 (legacy) and a failed read on this build.
    from tests.test_read_failure_projection import _STUDY_42

    failed_read_legacy = summarize(
        study=_STUDY_42["study"],
        evidence=_STUDY_42["evidence"],
        plan=_STUDY_42["plan"],
        targets=[],
        issues=_STUDY_42["issues"],
    )
    blocker = (
        "bioAF could not read the paper: the answer was cut off at its token limit of 16,000 output tokens; the "
        "recovery attempt failed too: the answer was cut off at its token limit of 32,000 output tokens. This is a "
        "bioAF limitation, not a finding about the paper."
    )
    failed_read = summarize(
        study={"state": "error"},
        evidence={"extraction": {"status": "failed", "cause": blocker.split(": ", 1)[1].rsplit(". This", 1)[0]}},
        plan={"blockers": [blocker], "blocker_kinds": [{"text": blocker, "kind": "bioaf_limitation"}]},
        targets=[],
        issues=[],
    )
    return json.loads(
        json.dumps(
            {
                "enums": enum_labels(),
                "stage2_selection": stage2,
                "groff_failed": groff,
                "study_34_legacy": legacy,
                "attempted_no_verdict": attempted,
                "mapping_unresolved": mapping_unresolved,
                "reference_unavailable": reference_unavailable,
                "scorecard_scored": scored_example(),
                "scorecard_long": long_example(),
                "scorecard_groff": groff_scored,
                "scorecard_samd1": samd1,
                "scorecard_not_established": not_established,
                "scorecard_not_applicable": not_applicable,
                "scorecard_in_progress": in_progress,
                "scorecard_provisional": provisional,
                "scorecard_score_pending": score_pending,
                "failed_read_legacy": failed_read_legacy,
                "failed_read": failed_read,
            }
        )
    )


def _stage2_resources() -> list[dict]:
    """A deposit bioAF reads and a proteomics deposit it has no adapter for, as the inventory builds them."""
    from app.services.resource_inventory import build_resource_inventory

    return build_resource_inventory(
        scanned=[{"identifier": "GSE555001", "archive": "geo"}],
        model_resources=[{"identifier": "PXD099001", "type": "proteomics_data", "role": "the proteome"}],
        extracted_accessions=["GSE555001"],
        supplements=[],
        deposits=[
            {
                "accession": "GSE555001",
                "archive": "geo",
                "exists": "yes",
                "access": "public",
                "supported": "yes",
                "raw_data": "yes",
                "preprocessed_data": "yes",
            }
        ],
        experiments=[{"id": "e2", "assay": "bulk RNA-seq", "resources": ["GSE555001"]}],
    )


def _reference_unavailable_evidence() -> dict:
    """change_7.5 section 1.3: a raw-reads study whose paper states a reference bioAF cannot supply,
    concluded the way the driver concludes it, before any read is fetched."""
    from app.services.validation_completion import completion_for
    from app.services.validation_reference import paper_reference, reference_limitation

    reference = paper_reference("mm9", "nf-core/chipseq")
    evidence: dict = {}
    evidence["completion"] = completion_for(
        route="pipeline",
        capabilities={"deposits": [], "preprocessed_data": {"value": "yes"}, "raw_data": {"value": "yes"}},
        supplements=[],
        acquisition=evidence,
        extra_limitations=[reference_limitation(reference, operation="pipeline")],
    )
    return evidence


def _mapping_unresolved_evidence() -> dict:
    """change_7.4 sections 1.1 and 1.3: a deposit acquired and read whose columns could not be
    assigned to the comparison, concluded the way the driver now concludes it."""
    from app.services.validation_completion import completion_for

    evidence = {
        "deposit_inventory": {"accession": "GSE1"},
        "deposit": {"files": [{"filename": "GSE1_counts.tsv.gz", "artifact_type": "deposited_matrix"}]},
        "deposit_inspection": {"usable": True, "n_columns": 4, "value_type_observed": "counts"},
        "analysis_readiness": {
            "value": "no",
            "reason": "no column of the deposited matrix could be matched to KO",
            "cause": "sample_mapping_unresolved",
        },
    }
    detail = (
        "bioAF downloaded and read GSE1_counts.tsv.gz. Held before running: no column of the deposited matrix "
        "could be matched to KO. An arm with no samples is not a smaller experiment."
    )
    evidence["completion"] = completion_for(
        route="deposit",
        capabilities={"deposits": [], "preprocessed_data": {"value": "yes"}, "raw_data": {"value": "yes"}},
        supplements=[],
        acquisition=evidence,
        extra_limitations=[
            {
                "kind": "sample_mapping_unresolved",
                "resource": "GSE1_counts.tsv.gz",
                "operation": "deposit",
                "detail": detail,
                "technical_detail": {"cause": "sample_mapping_unresolved"},
            }
        ],
    )
    return evidence


@pytest.mark.skipif(not _FIXTURE.parent.parent.exists(), reason="the frontend is not checked out beside the backend")
@pytest.mark.asyncio
async def test_the_frontend_renders_the_projection_the_backend_produces():
    contract = await _contract()
    if os.environ.get("BIOAF_WRITE_REPORT_CONTRACT"):
        _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        _FIXTURE.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n")
    assert json.loads(_FIXTURE.read_text()) == contract, (
        "The report projection changed. Regenerate the frontend contract with BIOAF_WRITE_REPORT_CONTRACT=1 "
        "and make the frontend render it."
    )
