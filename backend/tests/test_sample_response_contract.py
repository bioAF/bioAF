"""The sample wire shape every sample route returns.

Experiment sample listing, sample detail, sample create and the three sample
update routes all return ``SampleResponse``. They share one mapping, so these
tests pin the fields, the nested relationship summaries, decimal conversion,
custom fields, inherited defaults, and the one deliberate difference: only the
experiment listing computes ``file_count``; the others report zero.
"""

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import event, insert

from app.models.experiment import Experiment
from app.models.experiment_field_default import ExperimentFieldDefault
from app.models.file import File
from app.models.sample import Sample, sample_files
from app.models.sample_batch import SampleBatch
from app.models.sample_custom_field import SampleCustomField
from app.models.sequencing_batch import SequencingBatch

SAMPLE_KEYS = {
    "id",
    "external_id",
    "relabeled_from",
    "organism",
    "tissue_type",
    "donor_source",
    "treatment_condition",
    "chemistry_version",
    "sample_batch",
    "sequencing_batch",
    "sequencing_batch_position",
    "viability_pct",
    "cell_count",
    "prep_notes",
    "molecule_type",
    "library_prep_method",
    "library_layout",
    "assay",
    "qc_status",
    "qc_notes",
    "parent_sample_id",
    "collection_timestamp",
    "collection_method",
    "file_count",
    "status",
    "created_at",
    "updated_at",
    "custom_fields",
}


@pytest.fixture
def headers(admin_token) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest_asyncio.fixture
async def records(session, admin_user):
    org_id = admin_user.organization_id
    experiment = Experiment(organization_id=org_id, name="Shape")
    session.add(experiment)
    await session.flush()
    batch = SampleBatch(experiment_id=experiment.id, name="prep-1")
    seq = SequencingBatch(organization_id=org_id, name="run", code="RUN1", status="pending")
    session.add_all([batch, seq])
    await session.flush()
    full = Sample(
        experiment_id=experiment.id,
        external_id="FULL",
        organism="Homo sapiens",
        viability_pct=Decimal("92.50"),
        cell_count=1000,
        sample_batch_id=batch.id,
        sequencing_batch_id=seq.id,
        sequencing_batch_position=3,
        status="registered",
    )
    bare = Sample(experiment_id=experiment.id, external_id="BARE", status="registered")
    session.add_all([full, bare])
    await session.flush()
    session.add(SampleCustomField(sample_id=full.id, field_name="cell_line", field_value="HEK293"))
    for name in ("a.fastq.gz", "b.fastq.gz"):
        f = File(
            organization_id=org_id,
            gcs_uri=f"gs://b/{name}",
            storage_uri=f"gs://b/{name}",
            filename=name,
            file_type="fastq",
        )
        session.add(f)
        await session.flush()
        await session.execute(insert(sample_files).values(file_id=f.id, sample_id=full.id))
    await session.commit()
    return experiment, full, bare, batch, seq


def _by_external(rows: list[dict]) -> dict[str, dict]:
    return {r["external_id"]: r for r in rows}


@pytest.mark.asyncio
async def test_experiment_listing_shape_and_computed_file_count(client, headers, records):
    experiment, full, bare, batch, seq = records
    response = await client.get(f"/api/experiments/{experiment.id}/samples", headers=headers)
    assert response.status_code == 200
    rows = _by_external(response.json())
    assert set(rows["FULL"]) == SAMPLE_KEYS
    assert rows["FULL"]["file_count"] == 2
    assert rows["FULL"]["viability_pct"] == 92.5
    assert rows["FULL"]["sample_batch"] == {"id": batch.id, "name": "prep-1"}
    assert rows["FULL"]["sequencing_batch"] == {"id": seq.id, "code": "RUN1"}
    assert rows["FULL"]["sequencing_batch_position"] == 3
    assert [(c["field_name"], c["field_value"]) for c in rows["FULL"]["custom_fields"]] == [("cell_line", "HEK293")]
    assert rows["BARE"]["file_count"] == 0
    assert rows["BARE"]["sample_batch"] is None
    assert rows["BARE"]["sequencing_batch"] is None
    assert rows["BARE"]["viability_pct"] is None
    assert rows["BARE"]["custom_fields"] == []
    assert rows["BARE"]["relabeled_from"] is None


@pytest.mark.asyncio
async def test_detail_matches_the_listing_except_file_count(client, headers, records):
    experiment, full, *_ = records
    listed = _by_external((await client.get(f"/api/experiments/{experiment.id}/samples", headers=headers)).json())
    detail = await client.get(f"/api/samples/{full.id}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert set(body) == SAMPLE_KEYS
    assert body["file_count"] == 0  # detail does not compute it; only the listing does
    assert {k: v for k, v in body.items() if k != "file_count"} == {
        k: v for k, v in listed["FULL"].items() if k != "file_count"
    }


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("patch", "", {"prep_notes": "edited"}),
        ("patch", "/qc", {"qc_status": "pass", "qc_notes": "ok"}),
        ("patch", "/status", {"status": "library_prepped"}),
    ],
)
@pytest.mark.asyncio
async def test_update_routes_return_the_detail_shape(client, headers, records, method, suffix, body):
    _, full, _, batch, seq = records
    response = await getattr(client, method)(f"/api/samples/{full.id}{suffix}", headers=headers, json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data) == SAMPLE_KEYS
    assert data["file_count"] == 0
    assert data["sample_batch"] == {"id": batch.id, "name": "prep-1"}
    assert data["sequencing_batch"] == {"id": seq.id, "code": "RUN1"}
    assert data["viability_pct"] == 92.5
    assert len(data["custom_fields"]) == 1


@pytest.mark.asyncio
async def test_create_returns_inherited_defaults_and_relationships(client, headers, session, records):
    experiment, *_ = records
    session.add(
        ExperimentFieldDefault(experiment_id=experiment.id, field_name="organism", default_value="Mus musculus")
    )
    await session.commit()
    response = await client.post(
        f"/api/experiments/{experiment.id}/samples",
        headers=headers,
        json={
            "external_id": "NEW",
            "viability_pct": 81.25,
            "sample_batch_code": "prep-1",
            "custom_fields": [{"field_name": "donor_age", "field_value": "41"}],
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data) == SAMPLE_KEYS
    assert data["organism"] == "Mus musculus"  # inherited from the experiment default
    assert data["viability_pct"] == 81.25
    assert data["sample_batch"]["name"] == "prep-1"
    assert data["sequencing_batch"] is None
    assert data["file_count"] == 0
    assert data["status"] == "registered"
    assert [(c["field_name"], c["field_value"]) for c in data["custom_fields"]] == [("donor_age", "41")]


@pytest.mark.asyncio
async def test_the_shared_loader_lets_the_builder_run_without_lazy_sql(session, records):
    """Every attribute the builder reads is loaded by the shared loader: no SQL after the load."""
    from app.api.sample_responses import sample_response
    from app.services.sample_service import SampleService

    _, full, *_ = records
    session.expunge_all()
    loaded = await SampleService.get_sample(session, full.id)
    statements: list[str] = []

    def _count(conn, cursor, statement, *args):
        statements.append(statement)

    engine = session.bind.sync_engine if hasattr(session.bind, "sync_engine") else session.bind
    event.listen(engine, "before_cursor_execute", _count)
    try:
        response = sample_response(loaded)
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    assert statements == []
    assert response.sample_batch is not None and response.sequencing_batch is not None
    assert len(response.custom_fields) == 1
