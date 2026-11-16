"""01C.1: sample.created / sample.updated / sample.qc_changed from every reachable owner.

One event per committed sample (bulk and imports included), one event per entity and category that
actually changed, nothing for no-ops, rolled-back rows, or Integration API idempotent replays.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.models.experiment import Experiment
from app.models.organization import Organization
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.project import Project
from app.models.sample import Sample
from app.models.sample_batch import SampleBatch
from app.schemas.sample import SampleCreate
from app.services.sample_service import SampleService
from tests.support.webhook_capture import WebhookCapture

pytestmark = pytest.mark.asyncio

CREATED, UPDATED, QC = "sample.created", "sample.updated", "sample.qc_changed"


@pytest_asyncio.fixture
async def capture(session, admin_user):
    c = await WebhookCapture.start(session, admin_user)
    yield c
    c.close()


@pytest_asyncio.fixture
async def experiment(session, admin_user):
    project = Project(organization_id=admin_user.organization_id, name="P", external_id="PRJ")
    session.add(project)
    await session.flush()
    exp = Experiment(
        organization_id=admin_user.organization_id,
        project_id=project.id,
        name="E",
        external_id="LIMS-EXP-042",
        status="registered",
    )
    session.add(exp)
    await session.commit()
    return exp


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _ref(sample_id, external_id, exp):
    return {
        "sample_id": sample_id,
        "external_id": external_id,
        "experiment_id": exp.id,
        "experiment_external_id": "LIMS-EXP-042",
    }


async def _sample(session, exp, external_id="S-1", **kw):
    s = Sample(experiment_id=exp.id, external_id=external_id, status="registered", **kw)
    session.add(s)
    await session.commit()
    return s


# --- creation ---------------------------------------------------------------------------------


async def test_ui_create_announces_once_including_initial_qc(client, admin_token, experiment, capture):
    r = await client.post(
        f"/api/experiments/{experiment.id}/samples",
        headers=_auth(admin_token),
        json={"external_id": "LIMS-SMP-1001", "qc_status": "pass", "qc_notes": "fine"},
    )
    assert r.status_code == 200, r.text
    assert await capture.deliveries() == [(CREATED, _ref(r.json()["id"], "LIMS-SMP-1001", experiment))]


async def test_bulk_create_announces_one_event_per_sample(client, admin_token, experiment, capture):
    r = await client.post(
        f"/api/experiments/{experiment.id}/samples/bulk",
        headers=_auth(admin_token),
        json={"samples": [{"external_id": f"S-{i}"} for i in range(3)]},
    )
    assert r.status_code in (200, 201), r.text
    created = await capture.data(CREATED)
    assert sorted(d["external_id"] for d in created) == ["S-0", "S-1", "S-2"]
    assert len({d["sample_id"] for d in created}) == 3


async def test_csv_import_announces_only_surviving_rows(session, admin_user, experiment, capture):
    from app.services.sample_import_service import import_samples

    await _sample(session, experiment, "DUP")
    content = b"external_id,organism\nOK-1,human\nDUP,human\nOK-2,mouse\n"
    result = await import_samples(
        session, experiment_id=experiment.id, org_id=admin_user.organization_id, user_id=admin_user.id, content=content
    )
    await session.commit()
    assert result.created_count == 2
    assert sorted(d["external_id"] for d in await capture.data(CREATED)) == ["OK-1", "OK-2"]


async def test_an_import_rolled_back_by_its_caller_announces_nothing(session, admin_user, experiment, capture):
    from app.services.sample_import_service import import_samples

    exp_id, org, user = experiment.id, admin_user.organization_id, admin_user.id
    await import_samples(session, experiment_id=exp_id, org_id=org, user_id=user, content=b"external_id\nA\nB\n")
    await session.rollback()
    assert await capture.deliveries() == []


async def test_fetchngs_ingest_announces_its_samples(session, admin_user, experiment, capture, monkeypatch):
    from app.services.fetchngs_ingest_service import FetchngsIngestService

    class Storage:
        async def read_text(self, uri):
            return "sample,run_accession\nSRX1,SRR1\nSRX2,SRR2\n"

    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=experiment.id,
        pipeline_name="nf-core/fetchngs",
        status="completed",
        submitted_by_user_id=admin_user.id,
    )
    session.add(run)
    await session.flush()
    await FetchngsIngestService.ingest_for_run(session, run, outdir="s3://b/out", storage_adapter=Storage())
    await session.commit()
    assert sorted(d["external_id"] for d in await capture.data(CREATED)) == ["SRX1", "SRX2"]


# --- updates ----------------------------------------------------------------------------------


async def test_updates_announce_actual_changes_only(client, admin_token, session, experiment, capture):
    s = await _sample(session, experiment, organism="human")
    url = f"/api/samples/{s.id}"

    await client.patch(url, headers=_auth(admin_token), json={"organism": "human"})
    assert await capture.data(UPDATED) == []

    await client.patch(
        url,
        headers=_auth(admin_token),
        json={
            "organism": "mouse",
            "sample_batch_code": "B1",
            "custom_fields": [{"field_name": "x", "field_value": "1"}],
        },
    )
    [data] = await capture.data(UPDATED)
    assert data == {**_ref(s.id, "S-1", experiment), "changed_fields": ["custom_fields", "organism", "sample_batch_id"]}

    await client.patch(
        url, headers=_auth(admin_token), json={"custom_fields": [{"field_name": "x", "field_value": "1"}]}
    )
    await client.patch(url, headers=_auth(admin_token), json={"sample_batch_code": "B1"})
    assert len(await capture.data(UPDATED)) == 1


async def test_sequencing_batch_changes_name_the_persisted_fields(client, admin_token, session, experiment, capture):
    s = await _sample(session, experiment)
    await client.patch(f"/api/samples/{s.id}", headers=_auth(admin_token), json={"sequencing_batch_code": "RUN-7"})
    [data] = await capture.data(UPDATED)
    assert data["changed_fields"] == ["sequencing_batch_id", "sequencing_batch_position"]


async def test_bulk_update_announces_per_changed_sample(client, admin_token, session, experiment, capture):
    a = await _sample(session, experiment, "A", organism="human")
    b = await _sample(session, experiment, "B", organism="mouse")
    r = await client.patch(
        "/api/samples/bulk/update",
        headers=_auth(admin_token),
        json={"sample_ids": [a.id, b.id], "update": {"organism": "mouse"}},
    )
    assert r.status_code == 200, r.text
    assert [d["sample_id"] for d in await capture.data(UPDATED)] == [a.id]


async def test_lifecycle_status_is_an_update_of_status(client, admin_token, session, experiment, capture):
    s = await _sample(session, experiment)
    r = await client.patch(
        f"/api/samples/{s.id}/status", headers=_auth(admin_token), json={"status": "library_prepped"}
    )
    assert r.status_code == 200, r.text
    [data] = await capture.data(UPDATED)
    assert data["changed_fields"] == ["status"]


async def test_qc_changes_and_notes_only_edits(client, admin_token, session, experiment, capture):
    s = await _sample(session, experiment)
    url = f"/api/samples/{s.id}/qc"
    await client.patch(url, headers=_auth(admin_token), json={"qc_status": "pass", "qc_notes": "ok"})
    await client.patch(url, headers=_auth(admin_token), json={"qc_status": "pass", "qc_notes": "ok"})
    await client.patch(url, headers=_auth(admin_token), json={"qc_status": "pass", "qc_notes": "secret note"})

    assert await capture.data(QC) == [{**_ref(s.id, "S-1", experiment), "old_qc_status": None, "new_qc_status": "pass"}]
    updates = await capture.data(UPDATED)
    assert updates == [{**_ref(s.id, "S-1", experiment), "changed_fields": ["qc_notes"]}]
    assert "secret note" not in str(await capture.deliveries())


async def test_review_verdicts_announce_qc_changes(session, admin_user, experiment, capture):
    from app.services.pipeline_review_service import PipelineReviewService

    s = await _sample(session, experiment, qc_status="warning")
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=experiment.id,
        pipeline_name="rnaseq",
        status="completed",
    )
    session.add(run)
    await session.flush()
    session.add(PipelineRunSample(pipeline_run_id=run.id, sample_id=s.id))
    await session.commit()
    await PipelineReviewService.create_review(
        session,
        run.id,
        admin_user.organization_id,
        admin_user.id,
        "rejected",
        None,
        {str(s.id): {"verdict": "fail"}},
        None,
    )
    await session.commit()
    [data] = await capture.data(QC)
    assert (data["old_qc_status"], data["new_qc_status"]) == ("warning", "fail")


async def test_batch_assignment_announces_changed_samples(client, admin_token, session, experiment, capture):
    batch = SampleBatch(experiment_id=experiment.id, name="B")
    session.add(batch)
    await session.flush()
    a = await _sample(session, experiment, "A")
    b = await _sample(session, experiment, "B", sample_batch_id=batch.id)
    r = await client.post(
        f"/api/sample-batches/{batch.id}/assign-samples", headers=_auth(admin_token), json={"sample_ids": [a.id, b.id]}
    )
    assert r.status_code == 200, r.text
    assert [(d["sample_id"], d["changed_fields"]) for d in await capture.data(UPDATED)] == [(a.id, ["sample_batch_id"])]


async def test_sheet_writeback_announces_actual_changes(session, admin_user, experiment, capture):
    s = await _sample(session, experiment, organism="human")
    await SampleService.apply_field_updates(session, {s.id: s}, {s.id: {"organism": "human", "assay": "bulk_rna"}})
    await session.commit()
    [data] = await capture.data(UPDATED)
    assert data["changed_fields"] == ["assay"]


# --- Integration API --------------------------------------------------------------------------


async def test_integration_create_patch_and_replay(client, integration_api_key, session, experiment, capture):
    headers = {**integration_api_key["headers"], "Idempotency-Key": "s-1"}
    body = {"experiment_id": experiment.id, "external_id": "007", "organism": "human"}
    first = await client.post("/api/v1/integrations/samples", headers=headers, json=body)
    assert first.status_code == 201, first.text
    await client.post("/api/v1/integrations/samples", headers=headers, json=body)
    sid = first.json()["id"]

    plain = integration_api_key["headers"]
    await client.patch(f"/api/v1/integrations/samples/{sid}", headers=plain, json={"organism": "human"})
    await client.patch(f"/api/v1/integrations/samples/{sid}", headers=plain, json={"tissue_type": "liver"})
    refused = await client.patch(f"/api/v1/integrations/samples/{sid}", headers=plain, json={"qc_status": "pass"})
    assert refused.status_code == 400

    assert await capture.deliveries() == [
        (CREATED, _ref(sid, "007", experiment)),
        (UPDATED, {**_ref(sid, "007", experiment), "changed_fields": ["tissue_type"]}),
    ]


# --- claims -----------------------------------------------------------------------------------


async def test_claiming_a_sample_announces_its_changes(client, admin_token, session, experiment, capture):
    s = await _sample(session, experiment, "auto-1", is_unclaimed=True)
    r = await client.post(f"/api/samples/{s.id}/claim", headers=_auth(admin_token), json={"name": "LIMS-1"})
    assert r.status_code == 200, r.text
    [data] = await capture.data(UPDATED)
    assert data["external_id"] == "LIMS-1" and data["changed_fields"] == ["external_id", "is_unclaimed"]


async def test_another_organizations_sample_cannot_be_claimed(client, admin_token, session, capture):
    other = Organization(name="Other")
    session.add(other)
    await session.flush()
    exp = Experiment(organization_id=other.id, name="E", status="registered")
    session.add(exp)
    await session.flush()
    s = Sample(experiment_id=exp.id, external_id="theirs", is_unclaimed=True, status="registered")
    session.add(s)
    await session.commit()
    r = await client.post(f"/api/samples/{s.id}/claim", headers=_auth(admin_token), json={"name": "mine"})
    assert r.status_code == 404
    await session.refresh(s)
    assert s.is_unclaimed is True and s.external_id == "theirs"


async def test_sample_events_stay_in_their_organization(session, admin_user, capture):
    other = Organization(name="Other")
    session.add(other)
    await session.flush()
    exp = Experiment(organization_id=other.id, name="E", status="registered")
    session.add(exp)
    await session.commit()
    await SampleService.create_sample(session, exp.id, admin_user.id, SampleCreate(external_id="x"))
    await session.commit()
    assert await capture.deliveries() == []
