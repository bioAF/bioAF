"""plan_8_2 section 1.3: one failure never rolls back a batch, and no study monopolizes the queue.

Study 44's consistency check could not store its result (a NUL in the decoded text) and the exception
rolled back the whole queue transaction every 30 seconds for over two hours, leaving three checks
pending forever. Each check now commits its own result or a safe failure; an interpretation failure is
terminal until its evidence or bioAF changes; a transport failure is retried a bounded number of times,
with the count and the next attempt held in the record so restarts keep them; and the queue orders
studies fairly, so failing or locked studies cannot hold every slot.
"""

import gzip
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import update

from app.models.validation_check_record import ValidationCheckRecord
from app.services import validation_check_queue as queue
from app.services import validation_consistency_checks as consistency
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_study_service import ValidationStudyService

_P = [{"kind": "pvalue", "operator": "<", "value": 0.01}]
_CONTRASTS = [
    {"name": "treated vs control", "test_condition": "treated", "reference_condition": "control"},
    {"name": "knockout vs wildtype", "test_condition": "knockout", "reference_condition": "wildtype"},
]
_TABLES = ["treated_vs_control.txt.gz", "knockout_vs_wildtype.txt.gz"]


def _accession(n: int) -> str:
    return f"GSE88800{n}"


def _url(n: int, table: str) -> str:
    return f"https://ftp.ncbi.nlm.nih.gov/geo/series/GSE888nnn/{_accession(n)}/suppl/{_accession(n)}_{table}"


def _table(test: str, reference: str, up: int) -> bytes:
    rows = [f"gene\tlog2FoldChange({test}/{reference})\tpvalue\tpadj"]
    rows += [f"u{i}\t2.0\t0.001\t0.01" for i in range(up)]
    rows += [f"n{i}\t0.1\t0.5\t0.9" for i in range(4)]
    return gzip.compress("\n".join(rows).encode())


def _claim(index: int, up: int) -> dict:
    return {
        "claim_text": f"{up} genes up",
        "claimed_value": up,
        "direction": "up",
        "output_type": "gene_set_size",
        "contrast_index": index,
        "cutoffs": _P,
        "count_relation": "=",
    }


async def _seed(session, user, n: int = 1, *, claims=None, state: str = "plan_ready"):
    study = await ValidationStudyService.create_study(session, user.organization_id, user.id)
    study.state = state
    accession = _accession(n)
    plan = await ReproductionPlanService.create_plan(
        session,
        study,
        user.id,
        accessions=[accession],
        differential_design={"contrasts": [{**c, "reported_experiment_id": "e1"} for c in _CONTRASTS]},
        reported_experiments=[{"id": "e1", "assay": "bulk RNA-seq", "claim_indices": [0, 1]}],
        resources=[{"identifier": accession, "type": "sequencing_data", "reported_experiment_ids": ["e1"]}],
    )
    targets = await ReproductionPlanService.add_comparison_targets(
        session,
        plan,
        [{**c, "metric_key": "", "reported_experiment_id": "e1"} for c in (claims or [_claim(0, 3), _claim(1, 2)])],
    )
    study.evidence_json = {
        "capabilities": {
            "deposits": [
                {"accession": accession, "archive": "geo", "result_tables": [f"{accession}_{t}" for t in _TABLES]}
            ]
        }
    }
    await session.flush()
    await consistency.enqueue(session, study, plan)
    await session.commit()
    return study, plan, targets


class _Fetcher:
    def __init__(self, blobs=None, *, error=None):
        self.blobs = blobs or {}
        self.error = error
        self.urls: list[str] = []

    async def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        blob = self.blobs.get(url)
        if blob is None:
            raise RuntimeError(f"404 {url}")
        return blob


def _healthy(n: int) -> dict:
    return {
        _url(n, _TABLES[0]): _table("treated", "control", 3),
        _url(n, _TABLES[1]): _table("knockout", "wildtype", 2),
    }


async def _records(session, study):
    """The study's consistency records as the database holds them now."""
    from sqlalchemy import select

    rows = await session.execute(
        select(ValidationCheckRecord)
        .where(
            ValidationCheckRecord.validation_study_id == study.id,
            ValidationCheckRecord.kind == queue.AUTHOR_RESULTS,
        )
        .order_by(ValidationCheckRecord.id)
        .execution_options(populate_existing=True)
    )
    return {r.comparison_target_id: r for r in rows.scalars().all()}


class TestOneCheckCannotRollBackAnother:
    @pytest.mark.asyncio
    async def test_a_database_rejected_outcome_is_recorded_safely_and_the_other_claim_completes(
        self, session, admin_user, monkeypatch
    ):
        from app.services import validation_author_consistency

        study, plan, targets = await _seed(session, admin_user)
        real = validation_author_consistency.check_claim

        def poisoned(target, predicate, table, **kwargs):
            record = real(target, predicate, table, **kwargs)
            if "treated" in str(table.get("name")):
                record["columns"] = {"id": "ge\x00ne"}  # PostgreSQL rejects this in JSONB
            return record

        monkeypatch.setattr(validation_author_consistency, "check_claim", poisoned)
        await consistency.run_pending(session, study, plan, fetcher=_Fetcher(_healthy(1)))
        await session.commit()

        records = await _records(session, study)
        rejected, other = records[targets[0].id], records[targets[1].id]
        assert other.state == queue.DONE and other.outcome_json["outcome"] == "agree"
        assert rejected.state == queue.UNRESOLVED
        assert rejected.terminal_reason == queue.PERSISTENCE_FAILED
        assert rejected.outcome_json["reason"] == queue.PERSISTENCE_REASON
        assert rejected.outcome_json["source"]["name"].endswith(_TABLES[0])

    @pytest.mark.asyncio
    async def test_three_checks_on_one_uninterpretable_table_conclude_once_and_are_never_fetched_again(
        self, session, admin_user
    ):
        claims = [_claim(0, 3), _claim(0, 4), _claim(0, 5)]
        study, plan, targets = await _seed(session, admin_user, claims=claims)
        undecodable = gzip.compress("gene\tlog2FoldChange(treated/control)\n".encode("utf-16-le"))  # no mark
        fetch = _Fetcher({_url(1, _TABLES[0]): undecodable})

        await consistency.run_pending(session, study, plan, fetcher=fetch)
        await session.commit()
        records = await _records(session, study)
        assert {r.state for r in records.values()} == {queue.UNRESOLVED}
        assert {r.terminal_reason for r in records.values()} == {queue.INTERPRETATION}
        assert fetch.urls == [_url(1, _TABLES[0])]

        await consistency.run_pending(session, study, plan, fetcher=fetch)
        assert fetch.urls == [_url(1, _TABLES[0])]


class TestBoundedRetries:
    @pytest.mark.asyncio
    async def test_a_transport_failure_is_retried_later_within_a_bound_that_survives_restarts(
        self, session, admin_user
    ):
        study, plan, targets = await _seed(session, admin_user, claims=[_claim(0, 3)])
        request = httpx.Request("GET", _url(1, _TABLES[0]))
        fetch = _Fetcher(error=httpx.ConnectError("connection reset by peer", request=request))

        await consistency.run_pending(session, study, plan, fetcher=fetch)
        await session.commit()
        (record,) = (await _records(session, study)).values()
        assert record.state == queue.PENDING and record.retry_count == 1
        assert record.next_attempt_at > datetime.now(timezone.utc)
        assert record.terminal_reason is None

        # Not due yet: nothing is fetched.
        await consistency.run_pending(session, study, plan, fetcher=fetch)
        assert len(fetch.urls) == 1

        for _attempt in range(queue.MAX_TRANSPORT_ATTEMPTS):
            # A restart: a new read of the record, with its retry time passed.
            await session.execute(
                update(ValidationCheckRecord)
                .where(ValidationCheckRecord.id == record.id)
                .values(next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1))
            )
            await session.commit()
            await consistency.run_pending(session, study, plan, fetcher=fetch)
            await session.commit()

        (record,) = (await _records(session, study)).values()
        assert len(fetch.urls) == queue.MAX_TRANSPORT_ATTEMPTS
        assert record.state == queue.UNRESOLVED
        assert record.terminal_reason == queue.RETRIES_EXHAUSTED
        assert record.retry_count == queue.MAX_TRANSPORT_ATTEMPTS
        assert "could not be retrieved" in record.outcome_json["reason"]

    @pytest.mark.asyncio
    async def test_a_retrying_check_is_distinct_from_one_waiting_its_first_turn(self, session, admin_user):
        study, plan, targets = await _seed(session, admin_user)
        request = httpx.Request("GET", _url(1, _TABLES[0]))
        blobs = _healthy(1)

        class _OneFails(_Fetcher):
            async def __call__(self, url):
                if url == _url(1, _TABLES[0]):
                    self.urls.append(url)
                    raise httpx.ConnectError("timed out", request=request)
                return await super().__call__(url)

        await consistency.run_pending(session, study, plan, fetcher=_OneFails(blobs), max_checks=1)
        await session.commit()
        records = await _records(session, study)
        assert queue.activity_of(records[targets[0].id]) == queue.RETRYING
        assert queue.activity_of(records[targets[1].id]) == queue.PENDING


async def _hold(session, study_id: int) -> None:
    from app.services.validation_ownership import acquire

    await acquire(session, study_id, holder="driver", lease_seconds=600)


class TestFairScheduling:
    @pytest.mark.asyncio
    async def test_studies_held_by_another_worker_never_take_the_slots_from_a_healthy_one(self, session, admin_user):
        from app.services.validation_driver_service import ValidationDriverService

        held = [await _seed(session, admin_user, n) for n in range(1, 6)]
        healthy, _plan, _targets = await _seed(session, admin_user, 6)
        for study, _p, _t in held:
            await _hold(session, study.id)

        ran = await ValidationDriverService.advance_check_queue(session, fetcher=_Fetcher(_healthy(6)), limit=5)
        assert ran == 2
        assert {r.state for r in (await _records(session, healthy)).values()} == {queue.DONE}

    @pytest.mark.asyncio
    async def test_studies_whose_checks_keep_failing_rotate_behind_studies_with_due_work(
        self, session, admin_user, monkeypatch
    ):
        from app.services import validation_consistency_checks
        from app.services.validation_driver_service import ValidationDriverService

        failing = [await _seed(session, admin_user, n) for n in range(1, 6)]
        healthy = [await _seed(session, admin_user, n) for n in (6, 7)]
        failing_ids = {study.id for study, _p, _t in failing}
        real = validation_consistency_checks.run_pending

        async def crashes(session, study, plan, **kwargs):
            if study.id in failing_ids:
                raise RuntimeError("an unexpected failure outside any one check")
            return await real(session, study, plan, **kwargs)

        monkeypatch.setattr(validation_consistency_checks, "run_pending", crashes)
        blobs = {**_healthy(6), **_healthy(7)}
        await ValidationDriverService.advance_check_queue(session, fetcher=_Fetcher(blobs), limit=5)
        await ValidationDriverService.advance_check_queue(session, fetcher=_Fetcher(blobs), limit=5)

        for study, _p, _t in healthy:
            assert {r.state for r in (await _records(session, study)).values()} == {queue.DONE}

    @pytest.mark.asyncio
    async def test_one_study_does_at_most_its_share_of_work_in_a_tick(self, session, admin_user):
        from app.services.validation_driver_service import ValidationDriverService

        claims = [_claim(0, 3), _claim(0, 3), _claim(0, 3), _claim(1, 2), _claim(1, 2), _claim(1, 2)]
        study, _plan, _targets = await _seed(session, admin_user, claims=claims)
        ran = await ValidationDriverService.advance_check_queue(
            session, fetcher=_Fetcher(_healthy(1)), limit=5, per_study=4
        )
        assert ran == 4
        states = [r.state for r in (await _records(session, study)).values()]
        assert states.count(queue.DONE) == 4 and states.count(queue.PENDING) == 2

    @pytest.mark.asyncio
    async def test_a_blocked_raw_read_route_does_not_stop_an_author_result_check(self, session, admin_user):
        from app.services.validation_driver_service import ValidationDriverService

        study, _plan, _targets = await _seed(session, admin_user, claims=[_claim(0, 3)], state="classified")
        study.classification = "access_restricted"
        study.evidence_json = {
            **study.evidence_json,
            "route_blocked": {"route": "pipeline", "reason": "controlled access"},
        }
        await session.commit()
        ran = await ValidationDriverService.advance_check_queue(session, fetcher=_Fetcher(_healthy(1)))
        assert ran == 1


class TestLaunchIdentity:
    @pytest.mark.asyncio
    async def test_a_launch_interrupted_after_dispatch_is_reconciled_rather_than_launched_again(
        self, session, admin_user
    ):
        study, plan, targets = await _seed(session, admin_user, claims=[_claim(0, 3)])
        record = await queue.ensure_record(
            session, study, plan, targets[0], queue.PROCESSED_REANALYSIS, {"analysis": 1}, analysis_key="a1"
        )
        await queue.approve(session, study.id, [record.check_id], approval_id="ap1")
        await session.commit()

        key = queue.launch_key("a1", "ap1")
        provider: dict[str, str] = {}
        launched: list[str] = []

        async def crashing_launch():
            provider[key] = "run:41"  # the provider accepted it under the durable identity
            launched.append("first")
            raise ConnectionError("the worker stopped before the run's identifier came back")

        with pytest.raises(ConnectionError):
            await queue.execute(session, record, launch=crashing_launch, lookup=provider.get)
        await session.commit()
        dispatching = (await session.get(ValidationCheckRecord, record.id, populate_existing=True)).attempts_json[-1]
        assert dispatching["launch_key"] == key and dispatching["execution_ref"] is None

        async def launch():
            launched.append("second")
            return "run:42"

        ref = await queue.execute(session, record, launch=launch, lookup=provider.get)
        assert ref == "run:41"
        assert launched == ["first"]

    @pytest.mark.asyncio
    async def test_a_dispatch_the_provider_never_received_is_launched_once_under_the_same_identity(
        self, session, admin_user
    ):
        study, plan, targets = await _seed(session, admin_user, claims=[_claim(0, 3)])
        record = await queue.ensure_record(
            session, study, plan, targets[0], queue.PROCESSED_REANALYSIS, {"analysis": 1}, analysis_key="a1"
        )
        await queue.approve(session, study.id, [record.check_id], approval_id="ap1")
        await session.commit()
        calls: list[str] = []

        async def never_arrives():
            calls.append("refused")
            raise ConnectionError("refused before the provider saw it")

        with pytest.raises(ConnectionError):
            await queue.execute(session, record, launch=never_arrives, lookup=lambda key: None)

        async def launch():
            calls.append("launched")
            return "run:7"

        assert await queue.execute(session, record, launch=launch, lookup=lambda key: None) == "run:7"
        # Once launched, the identity answers every later call without another launch.
        assert await queue.execute(session, record, launch=launch, lookup=lambda key: None) == "run:7"
        assert calls == ["refused", "launched"]
        keys = {a.get("launch_key") for a in record.attempts_json}
        assert keys == {queue.launch_key("a1", "ap1")}
