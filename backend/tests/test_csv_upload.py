import io
import pytest

from app.services.csv_service import parse_sample_csv


def test_parse_valid_csv():
    content = b"sample_id,organism,tissue_type\nS001,Human,Brain\nS002,Mouse,Liver\n"
    samples, errors, _ = parse_sample_csv(content, experiment_id=1)
    assert len(samples) == 2
    assert len(errors) == 0
    assert samples[0].external_id == "S001"
    assert samples[0].organism == "Human"
    assert samples[1].external_id == "S002"


def test_parse_tsv():
    content = b"sample_id\torganism\ttissue\nS001\tHuman\tBrain\n"
    samples, errors, _ = parse_sample_csv(content, experiment_id=1)
    assert len(samples) == 1
    assert samples[0].tissue_type == "Brain"


def test_parse_alternative_headers():
    content = b"external_id,tissue,donor,treatment,chemistry\nEX1,Brain,Donor1,Drug A,v3\n"
    samples, errors, _ = parse_sample_csv(content, experiment_id=1)
    assert len(samples) == 1
    assert samples[0].external_id == "EX1"
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


def test_parse_uses_aliases_for_unknown_headers():
    # 'library' isn't in COLUMN_MAP, but the experiment has aliased it to library_prep_method
    content = b"sample_id,Library\nS001,Chromium 3' v3\n"
    aliases = {"library": "library_prep_method"}
    samples, errors, _ = parse_sample_csv(content, experiment_id=1, aliases=aliases)
    assert errors == []
    assert len(samples) == 1
    assert samples[0].library_prep_method == "Chromium 3' v3"


def test_user_mappings_take_priority_over_aliases():
    # User mapping 'library' -> custom:Library wins over alias to library_prep_method
    content = b"sample_id,Library\nS001,LIB123\n"
    samples, errors, custom_rows = parse_sample_csv(
        content,
        experiment_id=1,
        column_mappings={"library": "custom:Library"},
        aliases={"library": "library_prep_method"},
    )
    assert errors == []
    assert samples[0].library_prep_method is None
    assert custom_rows[0] == {"Library": "LIB123"}


def test_alias_routes_to_custom_field():
    content = b"sample_id,centrifuge_number\nS001,42\n"
    samples, errors, custom_rows = parse_sample_csv(
        content,
        experiment_id=1,
        aliases={"centrifuge_number": "custom:centrifuge_number"},
    )
    assert errors == []
    assert custom_rows[0] == {"centrifuge_number": "42"}


@pytest.mark.asyncio
async def test_experiment_persists_column_aliases(client, admin_token):
    resp = await client.post(
        "/api/experiments",
        json={
            "name": "Aliased Experiment",
            "column_aliases": {"library": "library_prep_method", "centrifuge_number": "custom:centrifuge_number"},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    exp_id = resp.json()["id"]

    csv_content = b"sample_id,Library,centrifuge_number\nS001,Chromium 3' v3,42\n"
    response = await client.post(
        f"/api/experiments/{exp_id}/samples/upload",
        files={"file": ("samples.csv", io.BytesIO(csv_content), "text/csv")},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["created_count"] == 1

    # The sample should have library_prep_method populated and a centrifuge_number custom field
    samples_resp = await client.get(
        f"/api/experiments/{exp_id}/samples",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    [s] = samples_resp.json()
    assert s["library_prep_method"] == "Chromium 3' v3"
    by_name = {cf["field_name"]: cf["field_value"] for cf in s["custom_fields"]}
    assert by_name == {"centrifuge_number": "42"}
