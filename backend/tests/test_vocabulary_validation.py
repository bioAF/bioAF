import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def seeded_vocab(client, admin_token):
    """Seed vocabulary values needed for validation tests."""
    values = [
        {"field_name": "molecule_type", "allowed_value": "total RNA", "is_default": True},
        {"field_name": "molecule_type", "allowed_value": "polyA RNA"},
        {"field_name": "library_layout", "allowed_value": "paired"},
        {"field_name": "library_layout", "allowed_value": "single"},
        {"field_name": "instrument_model", "allowed_value": "Illumina NovaSeq 6000", "is_default": True},
        {"field_name": "instrument_model", "allowed_value": "PacBio Sequel II"},
        {"field_name": "quality_score_encoding", "allowed_value": "Phred+33"},
        {"field_name": "reference_genome", "allowed_value": "GRCh38"},
        {"field_name": "reference_genome", "allowed_value": "GRCm39"},
        {"field_name": "alignment_algorithm", "allowed_value": "STARsolo"},
    ]
    for val in values:
        await client.post(
            "/api/vocabularies",
            json=val,
            headers={"Authorization": f"Bearer {admin_token}"},
        )


@pytest_asyncio.fixture
async def experiment_id(client, admin_token):
    resp = await client.post(
        "/api/experiments",
        json={"name": "Vocab Validation Experiment"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_create_sample_with_valid_minseqe_fields(client, admin_token, experiment_id, seeded_vocab):
    response = await client.post(
        f"/api/experiments/{experiment_id}/samples",
        json={
            "sample_id_unique": "S001",
            "organism": "Human",
            "molecule_type": "total RNA",
            "library_prep_method": "10x Chromium 3' v3.1",
            "library_layout": "paired",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["molecule_type"] == "total RNA"
    assert data["library_layout"] == "paired"


@pytest.mark.asyncio
async def test_create_sample_with_invalid_molecule_type(client, admin_token, experiment_id, seeded_vocab):
    response = await client.post(
        f"/api/experiments/{experiment_id}/samples",
        json={
            "sample_id_unique": "S002",
            "molecule_type": "invalid_type",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_sample_with_null_molecule_type(client, admin_token, experiment_id, seeded_vocab):
    response = await client.post(
        f"/api/experiments/{experiment_id}/samples",
        json={
            "sample_id_unique": "S003",
            "molecule_type": None,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_create_sample_batch_with_instrument_fields(client, admin_token, experiment_id, seeded_vocab):
    """SampleBatch retains instrument fields for prep equipment tracking."""
    response = await client.post(
        f"/api/experiments/{experiment_id}/sample-batches",
        json={"name": "Simple Batch", "instrument_model": "Illumina NovaSeq 6000"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["instrument_model"] == "Illumina NovaSeq 6000"
