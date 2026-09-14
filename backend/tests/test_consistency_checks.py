"""plan_8_1 section 3.3: consistency for every claim with an identified table, each in its own record.

A deposit listing one result table per contrast had only the selected contrast's claims checked. Now every
claim whose experiment's deposit lists a result table that names its contrast gets a consistency record;
two claims on one contrast keep their own predicate and outcome while their table is downloaded once; a
failed fetch leaves only its own record unresolved; and each limit on checks run before approval stops
the check, saying so.
"""

import gzip

import pytest

from app.services import validation_check_queue as queue
from app.services import validation_consistency_checks as consistency
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_study_service import ValidationStudyService

_ACCESSION = "GSE555001"
_TABLE_A = "GSE555001_DESeq2_treated_vs_control.txt.gz"
_TABLE_B = "GSE555001_DESeq2_day7_treated_vs_control.txt.gz"
_BASE = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE555nnn/GSE555001/suppl/"

_P = [{"kind": "pvalue", "operator": "<", "value": 0.01}]
_CONTRASTS = [
    {
        "name": "treated vs control",
        "test_condition": "treated",
        "reference_condition": "control",
        "reported_experiment_id": "e1",
    },
    {
        "name": "treated vs control, day 7",
        "test_condition": "treated",
        "reference_condition": "control",
        "reported_experiment_id": "e1",
    },
]


def _table(up: int, down: int) -> bytes:
    rows = ["gene\tlog2FoldChange(treated/control)\tpvalue\tpadj"]
    rows += [f"u{i}\t2.0\t0.001\t0.01" for i in range(up)]
    rows += [f"d{i}\t-2.0\t0.001\t0.01" for i in range(down)]
    rows += [f"n{i}\t0.1\t0.5\t0.9" for i in range(5)]
    return gzip.compress("\n".join(rows).encode())


_CLAIMS = [
    {
        "claim_text": "3 genes up",
        "claimed_value": 3,
        "direction": "up",
        "output_type": "gene_set_size",
        "contrast_index": 0,
        "cutoffs": _P,
        "count_relation": "=",
    },
    {
        "claim_text": "2 genes down",
        "claimed_value": 2,
        "direction": "down",
        "output_type": "gene_set_size",
        "contrast_index": 0,
        "cutoffs": _P,
        "count_relation": "=",
    },
    {
        "claim_text": "4 genes up on day 7",
        "claimed_value": 9,
        "direction": "up",
        "output_type": "gene_set_size",
        "contrast_index": 1,
        "cutoffs": _P,
        "count_relation": "=",
    },
]


async def _seed(session, user, *, tables=(_TABLE_A, _TABLE_B), selected=None):
    study = await ValidationStudyService.create_study(session, user.organization_id, user.id)
    plan = await ReproductionPlanService.create_plan(
        session,
        study,
        user.id,
        accessions=[_ACCESSION],
        differential_design={"contrasts": [dict(c) for c in _CONTRASTS]},
        reported_experiments=[{"id": "e1", "assay": "bulk RNA-seq", "claim_indices": [0, 1, 2]}],
        resources=[{"identifier": _ACCESSION, "type": "sequencing_data", "reported_experiment_ids": ["e1"]}],
        analysis_selection={"current": {"contrast_index": selected}} if selected is not None else None,
    )
    targets = await ReproductionPlanService.add_comparison_targets(
        session, plan, [{**c, "metric_key": "", "reported_experiment_id": "e1"} for c in _CLAIMS]
    )
    study.evidence_json = {
        "capabilities": {"deposits": [{"accession": _ACCESSION, "archive": "geo", "result_tables": list(tables)}]}
    }
    await session.flush()
    return study, plan, targets


class _Fetcher:
    def __init__(self, blobs: dict):
        self.blobs = blobs
        self.urls: list[str] = []

    async def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        blob = self.blobs.get(url)
        if isinstance(blob, Exception):
            raise blob
        if blob is None:
            raise RuntimeError(f"404 {url}")
        return blob


def _outcomes(records):
    return {r.comparison_target_id: (r.outcome_json or {}).get("outcome") for r in records}


class TestWhichTable:
    def test_a_table_names_its_contrast_among_the_others(self):
        assert consistency.names_contrast(_TABLE_A, _CONTRASTS[0], [_CONTRASTS[1]])
        assert not consistency.names_contrast(_TABLE_B, _CONTRASTS[0], [_CONTRASTS[1]])
        assert consistency.names_contrast(_TABLE_B, _CONTRASTS[1], [_CONTRASTS[0]])
        assert not consistency.names_contrast(_TABLE_A, _CONTRASTS[1], [_CONTRASTS[0]])

    def test_several_candidates_none_naming_the_contrast_is_unresolved(self):
        candidates = [{"name": "results_1.txt"}, {"name": "results_2.txt"}]
        table, reason, by = consistency.identify_table(_CONTRASTS[0], [_CONTRASTS[1]], candidates)
        assert table is None and by == "none"
        assert "which table reports this claim's contrast is not established" in reason

    def test_the_one_listed_table_is_the_table(self):
        table, _, by = consistency.identify_table(_CONTRASTS[0], [], [{"name": "only.txt"}])
        assert table["name"] == "only.txt" and by == "only_table"


class TestEveryClaimWithAnIdentifiedTable:
    @pytest.mark.asyncio
    async def test_every_claim_gets_a_record_not_only_the_selected_contrast(self, session, admin_user):
        study, plan, targets = await _seed(session, admin_user, selected=0)
        records = await consistency.enqueue(session, study, plan)
        assert sorted(r.comparison_target_id for r in records) == sorted(t.id for t in targets)
        tables = {r.comparison_target_id: r.dependencies_json["table"]["name"] for r in records}
        assert tables == {targets[0].id: _TABLE_A, targets[1].id: _TABLE_A, targets[2].id: _TABLE_B}

    @pytest.mark.asyncio
    async def test_two_predicates_on_one_contrast_and_one_on_another_keep_three_outcomes_and_share_the_download(
        self, session, admin_user
    ):
        study, plan, targets = await _seed(session, admin_user)
        await consistency.enqueue(session, study, plan)
        fetch = _Fetcher({_BASE + _TABLE_A: _table(3, 2), _BASE + _TABLE_B: _table(4, 1)})
        await consistency.run_pending(session, study, plan, fetcher=fetch)
        records = await queue.records_for(session, study.id, kind=queue.AUTHOR_RESULTS)
        assert _outcomes(records) == {targets[0].id: "agree", targets[1].id: "agree", targets[2].id: "disagree"}
        assert sorted(fetch.urls) == sorted([_BASE + _TABLE_A, _BASE + _TABLE_B])
        up, down = (next(r for r in records if r.comparison_target_id == t.id) for t in targets[:2])
        assert (up.outcome_json["rows_passing"], down.outcome_json["rows_passing"]) == (3, 2)

    @pytest.mark.asyncio
    async def test_a_failed_fetch_leaves_that_record_unresolved_and_the_others_intact(self, session, admin_user):
        """plan_8_2 section 1.3 (flagged change): a failed fetch is retried within the acquisition policy's
        bound before the record concludes unresolved; the other records stand throughout."""
        from datetime import datetime, timedelta, timezone

        study, plan, targets = await _seed(session, admin_user)
        await consistency.enqueue(session, study, plan)
        fetch = _Fetcher({_BASE + _TABLE_A: _table(3, 2)})
        await consistency.run_pending(session, study, plan, fetcher=fetch)
        records = await queue.records_for(session, study.id, kind=queue.AUTHOR_RESULTS)
        failed = next(r for r in records if r.comparison_target_id == targets[2].id)
        assert _outcomes(records) == {targets[0].id: "agree", targets[1].id: "agree", targets[2].id: None}
        assert queue.activity_of(failed) == queue.RETRYING
        for _attempt in range(queue.MAX_TRANSPORT_ATTEMPTS - 1):
            failed.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await consistency.run_pending(session, study, plan, fetcher=fetch)
        records = await queue.records_for(session, study.id, kind=queue.AUTHOR_RESULTS)
        assert _outcomes(records) == {targets[0].id: "agree", targets[1].id: "agree", targets[2].id: "unresolved"}
        assert "could not be retrieved" in failed.outcome_json["reason"]

    @pytest.mark.asyncio
    async def test_an_unidentified_table_is_an_unresolved_record(self, session, admin_user):
        study, plan, targets = await _seed(session, admin_user, tables=("results_1.txt.gz", "results_2.txt.gz"))
        await consistency.enqueue(session, study, plan)
        await consistency.run_pending(session, study, plan, fetcher=_Fetcher({}))
        records = await queue.records_for(session, study.id, kind=queue.AUTHOR_RESULTS)
        assert set(_outcomes(records).values()) == {"unresolved"}


class TestTheLimitsOnChecksBeforeApproval:
    @pytest.mark.asyncio
    async def test_total_downloaded_bytes(self, session, admin_user):
        study, plan, targets = await _seed(session, admin_user)
        await consistency.enqueue(session, study, plan)
        blob = _table(3, 2)
        fetch = _Fetcher({_BASE + _TABLE_A: blob, _BASE + _TABLE_B: blob})
        await consistency.run_pending(session, study, plan, fetcher=fetch, limits={"total_bytes": len(blob) + 1})
        records = await queue.records_for(session, study.id, kind=queue.AUTHOR_RESULTS)
        over = next(r for r in records if r.comparison_target_id == targets[2].id)
        assert over.outcome_json["reason"] == "exceeds bioAF's limit for checks run before approval"

    @pytest.mark.asyncio
    async def test_decompressed_size(self, session, admin_user):
        study, plan, targets = await _seed(session, admin_user)
        await consistency.enqueue(session, study, plan)
        fetch = _Fetcher({_BASE + _TABLE_A: _table(3, 2), _BASE + _TABLE_B: _table(4, 1)})
        await consistency.run_pending(session, study, plan, fetcher=fetch, limits={"decompressed_bytes": 10})
        records = await queue.records_for(session, study.id, kind=queue.AUTHOR_RESULTS)
        assert {r.outcome_json["reason"] for r in records} == {"exceeds bioAF's limit for checks run before approval"}

    @pytest.mark.asyncio
    async def test_execution_time(self, session, admin_user):
        study, plan, targets = await _seed(session, admin_user)
        await consistency.enqueue(session, study, plan)
        fetch = _Fetcher({_BASE + _TABLE_A: _table(3, 2), _BASE + _TABLE_B: _table(4, 1)})
        await consistency.run_pending(session, study, plan, fetcher=fetch, limits={"seconds": 0.0})
        records = await queue.records_for(session, study.id, kind=queue.AUTHOR_RESULTS)
        assert {r.outcome_json["reason"] for r in records} == {"exceeds bioAF's limit for checks run before approval"}
        assert fetch.urls == []

    def test_model_spend(self):
        """A consistency check asks no model; the limit on model spend before approval is zero calls."""
        assert consistency.PRELIMINARY_LIMITS["model_calls"] == 0


class TestTheQueueRunsFromTheDriver:
    @pytest.mark.asyncio
    async def test_the_driver_tick_runs_pending_consistency_records_and_leaves_the_study_alone(
        self, session, admin_user, monkeypatch
    ):
        from app.services.validation_driver_service import ValidationDriverService

        study, plan, targets = await _seed(session, admin_user)
        study.state = "plan_ready"  # the C1 gate: nothing is approved, and nothing is launched
        await consistency.enqueue(session, study, plan)
        await session.commit()
        fetch = _Fetcher({_BASE + _TABLE_A: _table(3, 2), _BASE + _TABLE_B: _table(4, 1)})
        ran = await ValidationDriverService.advance_check_queue(session, fetcher=fetch)
        assert ran == 3
        records = await queue.records_for(session, study.id, kind=queue.AUTHOR_RESULTS)
        assert {r.state for r in records} == {"done"}
        await session.refresh(study)
        assert study.state == "plan_ready"

    @pytest.mark.asyncio
    async def test_the_read_enqueues_consistency_for_its_claims(self, session, admin_user, monkeypatch):
        from app.services.validation_driver_service import ValidationDriverService

        study, plan, targets = await _seed(session, admin_user)
        await session.commit()
        await ValidationDriverService._enqueue_checks(session, study, plan)
        records = await queue.records_for(session, study.id, kind=queue.AUTHOR_RESULTS)
        assert len(records) == 3 and {r.state for r in records} == {"pending"}


class TestTheReportReadsTheRecords:
    @pytest.mark.asyncio
    async def test_each_claim_shows_its_own_record_and_a_study_before_the_queue_renders_as_before(
        self, session, admin_user
    ):
        from app.services.validation_report_summary import report_summary_for

        study, plan, targets = await _seed(session, admin_user)
        await consistency.enqueue(session, study, plan)
        fetch = _Fetcher({_BASE + _TABLE_A: _table(3, 2), _BASE + _TABLE_B: _table(4, 1)})
        await consistency.run_pending(session, study, plan, fetcher=fetch)
        summary = await report_summary_for(session, study, admin_user.organization_id)
        rows = [c["consistency"] for c in summary["claims"]]
        assert [r["outcome"] for r in rows] == ["agree", "agree", "disagree"]
        assert [r["table"] for r in rows] == [_TABLE_A, _TABLE_A, _TABLE_B]
        assert {r["check_state"] for r in rows} == {"done"}

    @pytest.mark.asyncio
    async def test_a_study_made_before_the_queue_renders_from_its_single_slot_artifact(self, session, admin_user):
        from app.services.validation_report_summary import report_summary_for

        study, plan, targets = await _seed(session, admin_user)
        study.evidence_json = {
            **study.evidence_json,
            "author_consistency": {"records": [{"claim_index": 0, "outcome": "agree", "table": "legacy.txt"}]},
        }
        await session.flush()
        summary = await report_summary_for(session, study, admin_user.organization_id)
        assert summary["claims"][0]["consistency"]["table"] == "legacy.txt"
        assert "check_state" not in summary["claims"][0]["consistency"]


class TestAResultsSupplement:
    @pytest.mark.asyncio
    async def test_a_retrieved_results_supplement_is_checked_from_what_the_assessment_read(self, session, admin_user):
        """plan_8_1 section 3.5: a supplement that holds a result table is checked (3.3). The assessment
        reads every claim against it while its bytes are in hand; that reading is the record's outcome."""
        study, plan, targets = await _seed(session, admin_user, tables=())
        held = {"claim_index": 0, "outcome": "agree", "table": "Supplemental_File_3.txt", "rows_passing": 3}
        study.evidence_json = {
            **study.evidence_json,
            "supplements": [
                {
                    "label": "Supplemental File S3",
                    "filename": "Supplemental_File_3.txt",
                    "role": "results_table",
                    "resolved": True,
                    "consistency": [held],
                }
            ],
        }
        await session.flush()
        records = await consistency.enqueue(session, study, plan)
        assert {r.dependencies_json["table"]["source"] for r in records} == {"supplement"}
        fetch = _Fetcher({})
        await consistency.run_pending(session, study, plan, fetcher=fetch)
        first = next(r for r in await queue.records_for(session, study.id) if r.comparison_target_id == targets[0].id)
        assert (first.state, first.outcome_json["outcome"]) == ("done", "agree")
        assert fetch.urls == []

    @pytest.mark.asyncio
    async def test_a_driver_tick_queues_what_the_assessment_found(self, session, admin_user):
        from app.services.validation_driver_service import ValidationDriverService

        study, plan, targets = await _seed(session, admin_user, tables=())
        study.state = "classified"
        study.classification = "access_restricted"
        await session.flush()
        assert await queue.records_for(session, study.id) == []
        study.evidence_json = {
            **study.evidence_json,
            "supplements": [{"filename": "S3.txt", "role": "results_table", "resolved": True, "consistency": []}],
        }
        await session.flush()
        await ValidationDriverService._sync_revisions(session, study)
        assert len(await queue.records_for(session, study.id, kind=queue.AUTHOR_RESULTS)) == 3
