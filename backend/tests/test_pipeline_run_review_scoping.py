"""Pipeline run reviews stay inside the caller's Organization.

A review reads and writes through the run, so the run must belong to the caller's
Organization; a foreign run answers the same 404 as a missing one. A review's sample
verdicts change sample QC, so every referenced sample must be both a member of the run
(``pipeline_run_samples``) and owned by the caller's Organization through its experiment.
Any bad reference rejects the whole request with a generic 422 before any side effect.
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select

from app.exceptions import NotFoundError, UnprocessableError
from app.models.audit_log import AuditLog
from app.models.experiment import Experiment
from app.models.organization import Organization
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.pipeline_run_review import PipelineRunReview
from app.models.sample import Sample
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.bootstrap_roles import seed_builtin_roles
from app.services.pipeline_review_service import PipelineReviewService


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _experiment(session, org_id: int, name: str) -> Experiment:
    experiment = Experiment(organization_id=org_id, name=name, status="pipeline_complete")
    session.add(experiment)
    await session.flush()
    return experiment


async def _sample(session, experiment: Experiment, external_id: str) -> Sample:
    sample = Sample(experiment_id=experiment.id, sample_id_external=external_id, qc_status="pass")
    session.add(sample)
    await session.flush()
    return sample


async def _run(session, org_id: int, experiment: Experiment, members: list[Sample]) -> PipelineRun:
    run = PipelineRun(
        organization_id=org_id, experiment_id=experiment.id, pipeline_name="nf-core/rnaseq", status="completed"
    )
    session.add(run)
    await session.flush()
    for sample in members:
        session.add(PipelineRunSample(pipeline_run_id=run.id, sample_id=sample.id))
    await session.flush()
    return run


@pytest_asyncio.fixture
async def world(session, admin_user):
    """Two Organizations, each with a reviewed-ready run, plus the odd associations."""
    org_id = admin_user.organization_id
    other_org = Organization(name="Other Org", setup_complete=True)
    session.add(other_org)
    await session.flush()
    other_roles = await seed_builtin_roles(session, other_org.id)
    outsider = User(
        email="outsider@other.test",
        password_hash=AuthService.hash_password("outsiderpass1"),
        role_id=other_roles["admin"],
        organization_id=other_org.id,
        status="active",
    )
    session.add(outsider)

    mine = await _experiment(session, org_id, "Mine")
    member = await _sample(session, mine, "M1")
    non_member = await _sample(session, mine, "M2")
    run = await _run(session, org_id, mine, [member])
    unreviewed = await _run(session, org_id, mine, [member])

    theirs = await _experiment(session, other_org.id, "Theirs")
    foreign_sample = await _sample(session, theirs, "F1")
    foreign_run = await _run(session, other_org.id, theirs, [foreign_sample])

    # A corrupt association: a run of mine that lists a sample owned by the other Organization.
    corrupt_run = await _run(session, org_id, mine, [member, foreign_sample])
    await session.commit()

    foreign_review = PipelineRunReview(
        pipeline_run_id=foreign_run.id,
        reviewer_user_id=outsider.id,
        verdict="approved",
        notes="foreign secret notes",
        sample_verdicts_json={str(foreign_sample.id): {"verdict": "pass", "notes": None}},
        reviewed_at=foreign_run.created_at,
    )
    session.add(foreign_review)
    await session.commit()

    outsider_token = AuthService.create_token(
        outsider.id, outsider.email, outsider.role_id, other_org.id, role_name="admin"
    )
    return SimpleNamespace(
        org_id=org_id,
        other_org_id=other_org.id,
        experiment=mine.id,
        foreign_experiment=theirs.id,
        member=member.id,
        non_member=non_member.id,
        foreign_sample=foreign_sample.id,
        run=run.id,
        unreviewed=unreviewed.id,
        foreign_run=foreign_run.id,
        corrupt_run=corrupt_run.id,
        foreign_review=foreign_review.id,
        outsider=outsider.id,
        outsider_token=outsider_token,
        reviewer=admin_user.id,
    )


@pytest.fixture
def spawned(monkeypatch):
    """Record review events instead of scheduling them."""
    calls: list = []

    def _record(coro, **kwargs):
        calls.append(kwargs)
        coro.close()

    monkeypatch.setattr("app.services.pipeline_review_service.spawn_event", _record)
    return calls


async def _snapshot(session, world) -> dict:
    """Everything a review request could change."""
    session.expire_all()
    reviews = (
        await session.execute(
            select(PipelineRunReview.id, PipelineRunReview.superseded_by_id).order_by(PipelineRunReview.id)
        )
    ).all()
    samples = (await session.execute(select(Sample.id, Sample.qc_status, Sample.qc_notes).order_by(Sample.id))).all()
    experiments = (await session.execute(select(Experiment.id, Experiment.status).order_by(Experiment.id))).all()
    audits = (await session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    return {
        "reviews": [tuple(r) for r in reviews],
        "samples": [tuple(s) for s in samples],
        "experiments": [tuple(e) for e in experiments],
        "audits": audits,
    }


# --- runs in another Organization ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_foreign_run_answers_the_same_404_as_a_missing_one_on_every_review_route(client, comp_bio_token, world):
    for run_id in (world.foreign_run, 999_999):
        listed = await client.get(f"/api/pipeline-runs/{run_id}/reviews", headers=_bearer(comp_bio_token))
        active = await client.get(f"/api/pipeline-runs/{run_id}/review", headers=_bearer(comp_bio_token))
        assert listed.status_code == 404, listed.text
        assert active.status_code == 404, active.text
        assert "foreign secret notes" not in listed.text + active.text
    missing = await client.get("/api/pipeline-runs/999999/review", headers=_bearer(comp_bio_token))
    foreign = await client.get(f"/api/pipeline-runs/{world.foreign_run}/review", headers=_bearer(comp_bio_token))
    assert foreign.json() == missing.json()


@pytest.mark.asyncio
async def test_reviewing_a_foreign_run_is_a_404_and_changes_nothing(client, session, comp_bio_token, world, spawned):
    before = await _snapshot(session, world)
    response = await client.post(
        f"/api/pipeline-runs/{world.foreign_run}/reviews",
        json={"verdict": "rejected", "notes": "should not land"},
        headers=_bearer(comp_bio_token),
    )
    assert response.status_code == 404, response.text
    assert await _snapshot(session, world) == before
    assert spawned == []


@pytest.mark.asyncio
async def test_the_other_organization_still_reads_its_own_review(client, world):
    response = await client.get(f"/api/pipeline-runs/{world.foreign_run}/review", headers=_bearer(world.outsider_token))
    assert response.status_code == 200
    assert response.json()["id"] == world.foreign_review


@pytest.mark.asyncio
async def test_an_owned_run_with_no_review_is_still_null_and_an_empty_list(client, comp_bio_token, world):
    active = await client.get(f"/api/pipeline-runs/{world.unreviewed}/review", headers=_bearer(comp_bio_token))
    listed = await client.get(f"/api/pipeline-runs/{world.unreviewed}/reviews", headers=_bearer(comp_bio_token))
    assert (active.status_code, active.json()) == (200, None)
    assert (listed.status_code, listed.json()) == (200, {"reviews": []})


# --- sample references ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_member_sample_of_an_owned_run_is_reviewed_and_updated(client, session, comp_bio_token, world, spawned):
    response = await client.post(
        f"/api/pipeline-runs/{world.run}/reviews",
        json={
            "verdict": "approved_with_caveats",
            "notes": "borderline",
            "sample_verdicts": {str(world.member): {"verdict": "warning", "notes": "low viability"}},
        },
        headers=_bearer(comp_bio_token),
    )
    assert response.status_code == 201, response.text
    session.expire_all()
    member = await session.get(Sample, world.member)
    non_member = await session.get(Sample, world.non_member)
    experiment = await session.get(Experiment, world.experiment)
    assert (member.qc_status, member.qc_notes) == ("warning", "low viability")
    assert non_member.qc_status == "pass"
    assert experiment.status == "reviewed"
    assert len(spawned) == 1


@pytest.mark.parametrize(
    "case",
    ["same_org_non_member", "foreign_sample", "corrupt_cross_org_member", "missing_sample", "malformed", "mixed"],
)
@pytest.mark.asyncio
async def test_a_bad_sample_reference_is_a_generic_422_with_no_side_effects(
    client, session, comp_bio_token, world, spawned, case
):
    run = world.corrupt_run if case == "corrupt_cross_org_member" else world.run
    verdicts = {
        "same_org_non_member": {str(world.non_member): {"verdict": "fail"}},
        "foreign_sample": {str(world.foreign_sample): {"verdict": "fail"}},
        "corrupt_cross_org_member": {str(world.foreign_sample): {"verdict": "fail"}},
        "missing_sample": {"999999": {"verdict": "fail"}},
        "malformed": {"not-a-sample": {"verdict": "fail"}},
        "mixed": {str(world.member): {"verdict": "fail"}, str(world.non_member): {"verdict": "fail"}},
    }[case]
    # A prior active review must not be superseded by a rejected request.
    first = await client.post(
        f"/api/pipeline-runs/{run}/reviews",
        json={"verdict": "revision_requested", "notes": "first"},
        headers=_bearer(comp_bio_token),
    )
    assert first.status_code == 201, first.text
    spawned.clear()
    before = await _snapshot(session, world)

    response = await client.post(
        f"/api/pipeline-runs/{run}/reviews",
        json={"verdict": "approved", "notes": "second", "sample_verdicts": verdicts},
        headers=_bearer(comp_bio_token),
    )
    assert response.status_code == 422, response.text
    body = response.text
    for sample_id in (world.non_member, world.foreign_sample, world.member):
        assert str(sample_id) not in body
    assert "not-a-sample" not in body
    assert await _snapshot(session, world) == before
    assert spawned == []


@pytest.mark.parametrize("key", ["99999999999", "-1", "0", "1.0", "", " 1", "01"])
@pytest.mark.asyncio
async def test_an_out_of_range_or_non_canonical_sample_key_is_the_same_generic_422(
    client, session, comp_bio_token, world, spawned, key
):
    before = await _snapshot(session, world)
    response = await client.post(
        f"/api/pipeline-runs/{world.run}/reviews",
        json={"verdict": "approved", "sample_verdicts": {key: {"verdict": "fail"}}},
        headers=_bearer(comp_bio_token),
    )
    assert response.status_code == 422, response.text
    assert await _snapshot(session, world) == before
    assert spawned == []


@pytest.mark.asyncio
async def test_a_payload_larger_than_one_query_can_carry_is_the_generic_422(
    client, session, comp_bio_token, world, spawned
):
    # 32,768 ids is one more than asyncpg's bind-parameter limit for a single IN list.
    verdicts = {str(world.member)} | {str(i) for i in range(1_000_000, 1_032_767)}
    assert len(verdicts) == 32_768
    before = await _snapshot(session, world)
    response = await client.post(
        f"/api/pipeline-runs/{world.run}/reviews",
        json={"verdict": "approved", "sample_verdicts": {k: {"verdict": "fail"} for k in verdicts}},
        headers=_bearer(comp_bio_token),
    )
    assert response.status_code == 422, response.text[:300]
    assert await _snapshot(session, world) == before
    assert spawned == []


@pytest.mark.asyncio
async def test_members_are_resolved_across_bounded_queries(
    client, session, db_engine, comp_bio_token, world, spawned, monkeypatch
):
    """With one id per query, every member still resolves and a bad id in a later query still rejects all."""
    monkeypatch.setattr("app.services.pipeline_review_service._ID_QUERY_CHUNK", 1)
    resolutions: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT SAMPLES.") and "JOIN pipeline_run_samples" in statement:
            resolutions.append(statement)

    event.listen(db_engine.sync_engine, "before_cursor_execute", _count)
    two = await _run(
        session,
        world.org_id,
        await session.get(Experiment, world.experiment),
        [await session.get(Sample, world.member), await session.get(Sample, world.non_member)],
    )
    await session.commit()
    two_id = two.id
    before = await _snapshot(session, world)
    # Two ids for a two-member run pass the count guard; ids resolve in ascending order, so the
    # missing id (larger than any real one) is the second query's, after the member resolved.
    missing = 2_000_000_000
    assert world.member < missing
    rejected = await client.post(
        f"/api/pipeline-runs/{two_id}/reviews",
        json={
            "verdict": "approved",
            "sample_verdicts": {str(world.member): {"verdict": "fail"}, str(missing): {"verdict": "fail"}},
        },
        headers=_bearer(comp_bio_token),
    )
    assert rejected.status_code == 422, rejected.text
    assert len(resolutions) == 2
    assert await _snapshot(session, world) == before
    resolutions.clear()

    accepted = await client.post(
        f"/api/pipeline-runs/{two_id}/reviews",
        json={
            "verdict": "approved",
            "sample_verdicts": {str(world.member): {"verdict": "fail"}, str(world.non_member): {"verdict": "warning"}},
        },
        headers=_bearer(comp_bio_token),
    )
    assert accepted.status_code == 201, accepted.text
    assert len(resolutions) == 2
    event.remove(db_engine.sync_engine, "before_cursor_execute", _count)
    session.expire_all()
    assert (await session.get(Sample, world.member)).qc_status == "fail"
    assert (await session.get(Sample, world.non_member)).qc_status == "warning"


@pytest.mark.asyncio
async def test_missing_and_foreign_sample_references_answer_identically(client, comp_bio_token, world):
    bodies = []
    for sample_id in (world.foreign_sample, 999_999):
        response = await client.post(
            f"/api/pipeline-runs/{world.run}/reviews",
            json={"verdict": "approved", "sample_verdicts": {str(sample_id): {"verdict": "fail"}}},
            headers=_bearer(comp_bio_token),
        )
        bodies.append((response.status_code, response.json()))
    assert bodies[0] == bodies[1]


@pytest.mark.asyncio
async def test_recommended_exclusions_do_not_widen_which_samples_get_qc_updates(client, session, comp_bio_token, world):
    response = await client.post(
        f"/api/pipeline-runs/{world.run}/reviews",
        json={"verdict": "approved", "recommended_exclusions": [world.non_member, world.foreign_sample]},
        headers=_bearer(comp_bio_token),
    )
    assert response.status_code == 201, response.text
    assert response.json()["recommended_exclusions"] == [world.non_member, world.foreign_sample]
    session.expire_all()
    for sample_id in (world.member, world.non_member, world.foreign_sample):
        assert (await session.get(Sample, sample_id)).qc_status == "pass"


@pytest.mark.asyncio
async def test_a_permission_denied_caller_still_gets_403_and_changes_nothing(
    client, session, viewer_token, world, spawned
):
    before = await _snapshot(session, world)
    response = await client.post(
        f"/api/pipeline-runs/{world.run}/reviews",
        json={"verdict": "approved", "sample_verdicts": {str(world.member): {"verdict": "fail"}}},
        headers=_bearer(viewer_token),
    )
    assert response.status_code == 403
    assert await _snapshot(session, world) == before
    assert spawned == []


# --- the service boundary ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_service_refuses_a_foreign_run_on_create_list_and_active_read(session, admin_user, world, spawned):
    with pytest.raises(NotFoundError):
        await PipelineReviewService.list_reviews(session, world.foreign_run, org_id=world.org_id)
    with pytest.raises(NotFoundError):
        await PipelineReviewService.get_active_review(session, world.foreign_run, org_id=world.org_id)
    before = await _snapshot(session, world)
    with pytest.raises(NotFoundError):
        await PipelineReviewService.create_review(
            session,
            pipeline_run_id=world.foreign_run,
            org_id=world.org_id,
            reviewer_user_id=world.reviewer,
            verdict="rejected",
            notes=None,
            sample_verdicts=None,
            recommended_exclusions=None,
        )
    await session.rollback()
    assert await _snapshot(session, world) == before
    assert spawned == []


@pytest.mark.asyncio
async def test_the_service_refuses_a_non_member_sample_before_any_write(session, admin_user, world, spawned):
    before = await _snapshot(session, world)
    with pytest.raises(UnprocessableError):
        await PipelineReviewService.create_review(
            session,
            pipeline_run_id=world.corrupt_run,
            org_id=world.org_id,
            reviewer_user_id=world.reviewer,
            verdict="approved",
            notes=None,
            sample_verdicts={str(world.member): {"verdict": "fail"}, str(world.foreign_sample): {"verdict": "fail"}},
            recommended_exclusions=None,
        )
    # Nothing was flushed, so even without a rollback the session holds no pending change.
    assert not session.new and not session.dirty
    await session.rollback()
    assert await _snapshot(session, world) == before
    assert spawned == []


@pytest.mark.asyncio
async def test_a_run_whose_experiment_is_foreign_does_not_change_that_experiments_status(
    session, admin_user, world, spawned
):
    run = PipelineRun(
        organization_id=world.org_id,
        experiment_id=world.foreign_experiment,
        pipeline_name="nf-core/rnaseq",
        status="completed",
    )
    session.add(run)
    await session.commit()
    await PipelineReviewService.create_review(
        session,
        pipeline_run_id=run.id,
        org_id=world.org_id,
        reviewer_user_id=world.reviewer,
        verdict="approved",
        notes=None,
        sample_verdicts=None,
        recommended_exclusions=None,
    )
    await session.commit()
    session.expire_all()
    assert (await session.get(Experiment, world.foreign_experiment)).status == "pipeline_complete"
