import io
import pytest

from app.services.csv_service import parse_sample_csv


def test_parse_valid_csv():
    content = b"sample_id,organism,tissue_type\nS001,Human,Brain\nS002,Mouse,Liver\n"
    samples, errors, _ = parse_sample_csv(content, experiment_id=1)
    assert len(samples) == 2
    assert len(errors) == 0
    assert samples[0].sample_id_unique == "S001"
    assert samples[0].organism == "Human"
    assert samples[1].sample_id_unique == "S002"


def test_parse_tsv():
    content = b"sample_id\torganism\ttissue\nS001\tHuman\tBrain\n"
    samples, errors, _ = parse_sample_csv(content, experiment_id=1)
    assert len(samples) == 1
    assert samples[0].tissue_type == "Brain"


def test_parse_alternative_headers():
    content = b"external_id,tissue,donor,treatment,chemistry\nEX1,Brain,Donor1,Drug A,v3\n"
    samples, errors, _ = parse_sample_csv(content, experiment_id=1)
    assert len(samples) == 1
    assert samples[0].sample_id_unique == "EX1"
    assert samples[0].tissue_type == "Brain"
    assert samples[0].donor_source == "Donor1"
    assert samples[0].treatment_condition == "Drug A"
    assert samples[0].chemistry_version == "v3"


def test_parse_with_numeric_fields():
    content = b"sample_id,viability_pct,cell_count\nS001,95.5,10000\n"
    samples, errors, _ = parse_sample_csv(content, experiment_id=1)
    assert len(samples) == 1
    assert samples[0].viability_pct == 95.5
    assert samples[0].cell_count == 10000


def test_parse_invalid_numeric():
    content = b"sample_id,viability_pct\nS001,not_a_number\n"
    samples, errors, _ = parse_sample_csv(content, experiment_id=1)
    assert len(errors) >= 1
    assert "Invalid numeric" in errors[0]


def test_parse_empty_rows_skipped():
    content = b"sample_id,organism\nS001,Human\n\n\nS002,Mouse\n"
    samples, errors, _ = parse_sample_csv(content, experiment_id=1)
    assert len(samples) == 2


def test_parse_empty_file():
    content = b""
    samples, errors, _ = parse_sample_csv(content, experiment_id=1)
    assert len(samples) == 0
    assert len(errors) >= 1


def test_parse_latin1_encoding():
    content = "sample_id,organism\nS001,Mus musculus\n".encode("latin-1")
    samples, errors, _ = parse_sample_csv(content, experiment_id=1)
    assert len(samples) == 1


def test_parse_unknown_columns():
    content = b"sample_id,unknown_col,organism\nS001,mystery,Human\n"
    samples, errors, _ = parse_sample_csv(content, experiment_id=1)
    assert len(samples) == 1
    assert samples[0].organism == "Human"


@pytest.mark.asyncio
async def test_csv_upload_endpoint(client, admin_token):
    # Create experiment
    resp = await client.post(
        "/api/experiments",
        json={"name": "CSV Upload Test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    csv_content = b"sample_id,organism,tissue_type\nCSV001,Human,Brain\nCSV002,Mouse,Liver\n"

    response = await client.post(
        f"/api/experiments/{exp_id}/samples/upload",
        files={"file": ("samples.csv", io.BytesIO(csv_content), "text/csv")},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["created_count"] == 2
    assert data["error_count"] == 0
