"""ADR-048: samples endpoints on the public integration API.

QC writes are not permitted; status writes are not permitted; create starts
the sample at the default status. Upsert by sample_id_external within
experiment_id.
"""

import pytest


async def _make_experiment(client, headers, name="EXP", external_id="EXP-A"):
    pr = await client.post(
        "/api/v1/integrations/projects",
        headers=headers,
        json={"name": "P", "external_id": "P-A"},
    )
    pid = pr.json()["id"]
    er = await client.post(
        "/api/v1/integrations/experiments",
        headers=headers,
        json={"name": name, "project_id": pid, "external_id": external_id},
    )
    return er.json()


@pytest.mark.asyncio
async def test_create_sample(client, integration_api_key):
    headers = integration_api_key["headers"]
    exp = await _make_experiment(client, headers)
    r = await client.post(
        "/api/v1/integrations/samples",
        headers=headers,
        json={
            "experiment_id": exp["id"],
            "sample_id_external": "SAMPLE-001",
            "organism": "Homo sapiens",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["sample_id_external"] == "SAMPLE-001"
    assert body["experiment_id"] == exp["id"]


@pytest.mark.asyncio
async def test_upsert_sample_by_external_within_experiment(client, integration_api_key):
    headers = integration_api_key["headers"]
    exp = await _make_experiment(client, headers)
    r1 = await client.post(
        "/api/v1/integrations/samples",
        headers=headers,
        json={
            "experiment_id": exp["id"],
            "sample_id_external": "S-1",
            "organism": "Human",
        },
    )
    sid = r1.json()["id"]
    r2 = await client.post(
        "/api/v1/integrations/samples",
        headers=headers,
        json={
            "experiment_id": exp["id"],
            "sample_id_external": "S-1",
            "organism": "Homo sapiens",
        },
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == sid
    assert r2.json()["organism"] == "Homo sapiens"


@pytest.mark.asyncio
async def test_create_sample_rejects_qc_status(client, integration_api_key):
    headers = integration_api_key["headers"]
    exp = await _make_experiment(client, headers)
    r = await client.post(
        "/api/v1/integrations/samples",
        headers=headers,
        json={
            "experiment_id": exp["id"],
            "qc_status": "pass",
        },
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_patch_sample_rejects_qc_status(client, integration_api_key):
    headers = integration_api_key["headers"]
    exp = await _make_experiment(client, headers)
    create = await client.post(
        "/api/v1/integrations/samples",
        headers=headers,
        json={"experiment_id": exp["id"]},
    )
    sid = create.json()["id"]
    r = await client.patch(
        f"/api/v1/integrations/samples/{sid}",
        headers=headers,
        json={"qc_status": "fail"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_get_sample_by_external_id_requires_experiment_id(client, integration_api_key):
    headers = integration_api_key["headers"]
    exp = await _make_experiment(client, headers)
    await client.post(
        "/api/v1/integrations/samples",
        headers=headers,
        json={"experiment_id": exp["id"], "sample_id_external": "S-1"},
    )
    r = await client.get(
        f"/api/v1/integrations/samples/by-external/S-1?experiment_id={exp['id']}",
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["sample_id_external"] == "S-1"
