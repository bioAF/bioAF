"""plan_8_2 section 1.4: a scorecard follows the evidence it was built from.

A concluded study's scorecard record was reused while its inventory and selection revisions held, so a
check that completed after classification changed nothing on screen. A record now carries its full
provenance: the plan and inventory, the rubric, the binding and decoder versions, the evidence it read and
the revision of every contributing check's outcome. Any change invalidates it; it is rebuilt from
committed evidence, the record it replaces kept in history. The report, the list and the export read the
one projection, and the checks still under way are shown for what they are.
"""

import gzip
from datetime import datetime, timedelta, timezone

import pytest

from app.services import validation_check_queue as queue
from app.services import validation_consistency_checks as consistency
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_finding_inventory import inventory_from_proposal
from app.services.validation_report_summary import compact_scorecards_for, report_summary_for
from app.services.validation_study_service import ValidationStudyService

_ACCESSION = "GSE777201"
_TABLE = "GSE777201_results.txt.gz"
_URL = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE777nnn/GSE777201/suppl/" + _TABLE
_P = [{"kind": "pvalue", "operator": "<", "value": 0.01}]
_CONTRAST = {
    "name": "treated vs control",
    "test_condition": "treated",
    "reference_condition": "control",
    "reported_experiment_id": "e1",
}


def _table(up: int) -> bytes:
    rows = ["gene\tlog2FoldChange(treated/control)\tpvalue\tpadj"]
    rows += [f"u{i}\t2.0\t0.001\t0.01" for i in range(up)] + ["n\t0.1\t0.5\t0.9"]
    return gzip.compress("\n".join(rows).encode())


async def _fetch(url):
    return _table(3)


async def _classified(session, user):
    """A concluded study whose one primary finding rests on one count its deposit's table can check."""
    study = await ValidationStudyService.create_study(session, user.organization_id, user.id)
    plan = await ReproductionPlanService.create_plan(
        session,
        study,
        user.id,
        accessions=[_ACCESSION],
        differential_design={"contrasts": [dict(_CONTRAST)]},
        reported_experiments=[{"id": "e1", "assay": "bulk RNA-seq", "claim_indices": [0]}],
        resources=[{"identifier": _ACCESSION, "type": "sequencing_data", "reported_experiment_ids": ["e1"]}],
        analysis_selection={"current": {"revision": 1, "claim_index": 0, "contrast_index": 0}},
    )
    claim = {
        "metric_key": "",
        "claim_text": "3 genes were up",
        "claimed_value": 3,
        "output_type": "gene_set_size",
        "direction": "up",
        "contrast_index": 0,
        "cutoffs": _P,
        "count_relation": "=",
        "reported_experiment_id": "e1",
    }
    await ReproductionPlanService.add_comparison_targets(session, plan, [claim])
    plan.finding_inventory_json = inventory_from_proposal(
        [
            {
                "description": "Treatment induces genes",
                "claim_indices": [0],
                "importance": "primary",
                "rationale": "The main conclusion.",
                "quote": "3 genes were up",
            }
        ],
        targets=[claim],
        full_text="3 genes were up",
        decided_by={"kind": "model"},
    )
    study.evidence_json = {
        "capabilities": {"deposits": [{"accession": _ACCESSION, "archive": "geo", "result_tables": [_TABLE]}]}
    }
    study.state = "comparing"
    await session.flush()
    await consistency.enqueue(session, study, plan)
    await ValidationStudyService.transition(
        session, study.id, user.organization_id, user.id, "classified", classification="inconclusive"
    )
    await session.commit()
    return study, plan


async def _surfaces(session, study, org_id):
    summary = await report_summary_for(session, study, org_id)
    listed = (await compact_scorecards_for(session, [study]))[study.id]
    return summary, listed


class TestALateCheckMovesTheScore:
    @pytest.mark.asyncio
    async def test_a_check_completing_after_classification_updates_the_report_list_and_export(
        self, session, admin_user
    ):
        from app.services.provenance.markdown_renderer import _append_scorecard
        from app.services.validation_driver_service import ValidationDriverService

        study, plan = await _classified(session, admin_user)
        before = study.evidence_json["scorecard_record"]
        assert before["compact"]["score"] is None
        summary, listed = await _surfaces(session, study, admin_user.organization_id)
        assert summary["scorecard"]["score"] is None and listed["score"] is None

        await ValidationDriverService.advance_check_queue(session, fetcher=_fetch)
        await session.refresh(study)

        summary, listed = await _surfaces(session, study, admin_user.organization_id)
        card = summary["scorecard"]
        assert (
            card["score_label"] == "100 / 100" and card["depth_label"] == "1 consistency only; 0 independently assessed"
        )
        assert listed["score_label"] == card["score_label"] and listed["scope_label"] == card["scope_label"]
        parts: list[str] = []
        _append_scorecard(parts, card)
        assert "100 / 100" in "\n".join(parts)
        # Nothing was read again and the selection did not move.
        assert card["analysis_selection_revision"] == 1
        record = study.evidence_json["scorecard_record"]
        assert record["compact"]["score_label"] == "100 / 100"
        assert any(entry["compact"]["score"] is None for entry in study.evidence_json["scorecard_history"])
        (check,) = record["provenance"]["checks"]
        assert check[0].endswith(":author_results") and check[2] >= 1

    @pytest.mark.asyncio
    async def test_an_invalidated_check_withdraws_its_credit_and_the_prior_scorecard_is_history(
        self, session, admin_user
    ):
        from app.services.validation_driver_service import ValidationDriverService
        from app.services.validation_report_summary import record_scorecard

        study, plan = await _classified(session, admin_user)
        await ValidationDriverService.advance_check_queue(session, fetcher=_fetch)
        await session.refresh(study)
        assert study.evidence_json["scorecard_record"]["compact"]["score_label"] == "100 / 100"

        await queue.invalidate(session, study.id, "predicate", "a changed statistical definition")
        await record_scorecard(session, study, reason="a contributing check was invalidated")
        await session.commit()

        summary, listed = await _surfaces(session, study, admin_user.organization_id)
        assert summary["scorecard"]["score"] is None and listed["score"] is None
        history = study.evidence_json["scorecard_history"]
        assert history[-1]["compact"]["score_label"] == "100 / 100"
        assert history[-1]["superseded_because"] == "a contributing check was invalidated"


class TestConcurrentCompletions:
    @pytest.mark.asyncio
    async def test_an_older_projection_never_replaces_a_newer_one(self, session, admin_user):
        from app.services.validation_report_summary import compute_scorecard_record, publish_scorecard_record

        study, plan = await _classified(session, admin_user)
        older = await compute_scorecard_record(session, study)
        # plan_8_5 section 3.1: settling a check publishes, so the newer projection is already stored.
        await consistency.run_pending(session, study, plan, fetcher=_fetch)
        newer = await compute_scorecard_record(session, study)
        assert study.evidence_json["scorecard_record"]["provenance_fingerprint"] == newer["provenance_fingerprint"]
        assert not await publish_scorecard_record(session, study, older, reason="a check completed")
        assert study.evidence_json["scorecard_record"]["compact"]["score_label"] == "100 / 100"


class TestTheWorkUnderWay:
    @pytest.mark.asyncio
    async def test_pending_retrying_and_terminal_unresolved_checks_are_told_apart(self, session, admin_user):
        study, plan = await _classified(session, admin_user)
        summary, listed = await _surfaces(session, study, admin_user.organization_id)
        assert summary["scorecard"]["activity"]["counts"]["pending"] == 1
        assert summary["scorecard"]["activity"]["label"] == "1 check pending"
        assert listed["activity"] == summary["scorecard"]["activity"]

        (record,) = await queue.records_for(session, study.id)
        await queue.retry_later(session, record, error="timed out", exhausted_reason="gone")
        summary, _ = await _surfaces(session, study, admin_user.organization_id)
        assert summary["scorecard"]["activity"]["counts"]["retrying"] == 1
        assert summary["scorecard"]["activity"]["label"] == "1 check retrying"

        record.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        for _attempt in range(queue.MAX_TRANSPORT_ATTEMPTS - 1):
            await queue.retry_later(session, record, error="timed out", exhausted_reason="gone")
        summary, _ = await _surfaces(session, study, admin_user.organization_id)
        activity = summary["scorecard"]["activity"]
        assert activity["counts"]["unresolved"] == 1 and activity["counts"]["pending"] == 0
        assert activity["label"] == "1 check could not conclude"
        # Completed attempts are counted in checks, never as assessed findings.
        assert activity["completed"] == 1 and summary["scorecard"]["assessed_count"] == 0


@pytest.mark.asyncio
async def test_the_export_states_the_checks_under_way(session, admin_user):
    from app.services.provenance.markdown_renderer import _append_scorecard

    study, plan = await _classified(session, admin_user)
    summary = await report_summary_for(session, study, admin_user.organization_id)
    parts: list[str] = []
    _append_scorecard(parts, summary["scorecard"])
    assert "Checks: 1 check pending" in "\n".join(parts)
