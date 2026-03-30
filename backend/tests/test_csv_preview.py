import io
import csv

import pytest

from app.services.csv_service import (
    generate_sample_template,
    preview_sample_csv,
    parse_sample_csv,
    SAMPLE_FIELDS,
)


# ---------------------------------------------------------------------------
# Template generation
# ---------------------------------------------------------------------------


def test_generate_template_returns_csv_bytes():
    content = generate_sample_template()
    assert isinstance(content, bytes)


def test_generate_template_has_all_sample_fields():
    content = generate_sample_template()
    text = content.decode("utf-8")
    reader = csv.reader(io.StringIO(text))
    headers = next(reader)
    for field in SAMPLE_FIELDS:
        assert field in headers, f"Missing field '{field}' in template headers"


def test_generate_template_has_example_row():
    content = generate_sample_template()
    text = content.decode("utf-8")
    reader = csv.reader(io.StringIO(text))
    next(reader)  # headers
    example_row = next(reader)
    # Example row should have values for at least some columns
    assert any(val.strip() for val in example_row)


# ---------------------------------------------------------------------------
# Preview parsing
# ---------------------------------------------------------------------------


def test_preview_recognized_columns():
    content = b"sample_id,organism,tissue_type\nS001,Human,Brain\nS002,Mouse,Liver\n"
    result = preview_sample_csv(content)
    assert result["total_rows"] == 2
    assert len(result["errors"]) == 0
    # All columns should be recognized
    mapped_names = [c["mapped_to"] for c in result["recognized_columns"]]
    assert "sample_id_external" in mapped_names
    assert "organism" in mapped_names
    assert "tissue_type" in mapped_names
    assert len(result["unknown_columns"]) == 0


def test_preview_detects_unknown_columns():
    content = b"sample_id,batch_label,organism,mystery_field\nS001,B1,Human,foo\n"
    result = preview_sample_csv(content)
    unknown = result["unknown_columns"]
    assert "batch_label" in unknown
    assert "mystery_field" in unknown
    # Known columns should still be recognized
    mapped_names = [c["mapped_to"] for c in result["recognized_columns"]]
    assert "sample_id_external" in mapped_names
    assert "organism" in mapped_names


def test_preview_returns_preview_rows():
    rows = "\n".join([f"S{i:03d},Human" for i in range(1, 21)])
    content = f"sample_id,organism\n{rows}\n".encode()
    result = preview_sample_csv(content)
    assert result["total_rows"] == 20
    # Preview should return at most 5 rows
    assert len(result["preview_rows"]) == 5
    assert result["preview_rows"][0]["sample_id_external"] == "S001"


def test_preview_empty_file():
    result = preview_sample_csv(b"")
    assert result["total_rows"] == 0
    assert len(result["errors"]) >= 1


def test_preview_includes_parse_errors():
    content = b"sample_id,viability_pct\nS001,not_a_number\nS002,95.5\n"
    result = preview_sample_csv(content)
    assert len(result["errors"]) >= 1
    assert "Invalid numeric" in result["errors"][0]
    # Valid row should still be in preview
    assert result["total_rows"] == 2


def test_preview_alternative_headers():
    content = b"external_id,tissue,donor\nEX1,Brain,Donor1\n"
    result = preview_sample_csv(content)
    mapped = {c["csv_header"]: c["mapped_to"] for c in result["recognized_columns"]}
    assert mapped["external_id"] == "sample_id_external"
    assert mapped["tissue"] == "tissue_type"
    assert mapped["donor"] == "donor_source"


# ---------------------------------------------------------------------------
# Column mapping in parse_sample_csv
# ---------------------------------------------------------------------------


def test_parse_with_custom_column_mappings():
    content = b"sample_id,batch_label,organism\nS001,B1,Human\nS002,B2,Mouse\n"
    # User maps "batch_label" to prep_notes
    samples, errors, _ = parse_sample_csv(content, experiment_id=1, column_mappings={"batch_label": "prep_notes"})
    assert len(samples) == 2
    assert samples[0].prep_notes == "B1"
    assert samples[1].prep_notes == "B2"


def test_parse_with_custom_field_mapping():
    content = b"sample_id,cell_line,organism\nS001,HEK293,Human\n"
    # User says "cell_line" is a custom field
    samples, errors, _ = parse_sample_csv(content, experiment_id=1, column_mappings={"cell_line": "custom:cell_line"})
    assert len(samples) == 1
    assert samples[0].sample_id_external == "S001"
    # Custom fields should be collected separately
    assert len(errors) == 0


def test_parse_mapping_overrides_ignored_column():
    """If a column was unknown and user maps it, it should be used."""
    content = b"sample_id,batch_label\nS001,my notes\n"
    # Without mapping, batch_label is ignored
    samples_no_map, _, _ = parse_sample_csv(content, experiment_id=1)
    assert samples_no_map[0].prep_notes is None

    # With mapping, batch_label goes to prep_notes
    samples_mapped, _, _ = parse_sample_csv(content, experiment_id=1, column_mappings={"batch_label": "prep_notes"})
    assert samples_mapped[0].prep_notes == "my notes"


# ---------------------------------------------------------------------------
# New columns in COLUMN_MAP
# ---------------------------------------------------------------------------


def test_molecule_type_column():
    content = b"sample_id,molecule_type\nS001,mRNA\n"
    samples, errors, _ = parse_sample_csv(content, experiment_id=1)
    assert len(samples) == 1
    assert samples[0].molecule_type == "mRNA"


def test_library_prep_method_column():
    content = b"sample_id,library_prep_method\nS001,10x Chromium\n"
    samples, errors, _ = parse_sample_csv(content, experiment_id=1)
    assert len(samples) == 1
    assert samples[0].library_prep_method == "10x Chromium"


def test_library_layout_column():
    content = b"sample_id,library_layout\nS001,paired\n"
    samples, errors, _ = parse_sample_csv(content, experiment_id=1)
    assert len(samples) == 1
    assert samples[0].library_layout == "paired"


def test_collection_method_column():
    content = b"sample_id,collection_method\nS001,biopsy\n"
    samples, errors, _ = parse_sample_csv(content, experiment_id=1)
    assert len(samples) == 1
    assert samples[0].collection_method == "biopsy"


def test_collection_timestamp_column():
    content = b"sample_id,collection_timestamp\nS001,2024-01-15T10:30:00\n"
    samples, errors, _ = parse_sample_csv(content, experiment_id=1)
    assert len(samples) == 1
    assert samples[0].collection_timestamp is not None
    assert samples[0].collection_timestamp.year == 2024


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_template_download_endpoint(client, admin_token):
    response = await client.get(
        "/api/experiments/1/samples/csv-template",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    # Template doesn't require an existing experiment
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    # Should be parseable CSV
    reader = csv.reader(io.StringIO(response.text))
    headers = next(reader)
    assert "sample_id_external" in headers
    assert "organism" in headers


@pytest.mark.asyncio
async def test_preview_endpoint_all_recognized(client, admin_token):
    resp = await client.post(
        "/api/experiments",
        json={"name": "Preview Test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    csv_content = b"sample_id,organism,tissue_type\nS001,Human,Brain\nS002,Mouse,Liver\n"
    response = await client.post(
        f"/api/experiments/{exp_id}/samples/upload/preview",
        files={"file": ("samples.csv", io.BytesIO(csv_content), "text/csv")},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_rows"] == 2
    assert len(data["unknown_columns"]) == 0
    assert len(data["recognized_columns"]) == 3


@pytest.mark.asyncio
async def test_preview_endpoint_with_unknown_columns(client, admin_token):
    resp = await client.post(
        "/api/experiments",
        json={"name": "Preview Unknown Test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    csv_content = b"sample_id,organism,cell_line,passage_number\nS001,Human,HEK293,5\n"
    response = await client.post(
        f"/api/experiments/{exp_id}/samples/upload/preview",
        files={"file": ("samples.csv", io.BytesIO(csv_content), "text/csv")},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "cell_line" in data["unknown_columns"]
    assert "passage_number" in data["unknown_columns"]
    assert data["total_rows"] == 1


@pytest.mark.asyncio
async def test_confirm_endpoint_creates_samples(client, admin_token):
    resp = await client.post(
        "/api/experiments",
        json={"name": "Confirm Test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    csv_content = b"sample_id,organism,tissue_type\nS001,Human,Brain\nS002,Mouse,Liver\n"
    response = await client.post(
        f"/api/experiments/{exp_id}/samples/upload/confirm",
        files={"file": ("samples.csv", io.BytesIO(csv_content), "text/csv")},
        data={"column_mappings": "{}"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["created_count"] == 2
    assert data["error_count"] == 0


@pytest.mark.asyncio
async def test_confirm_endpoint_with_column_remapping(client, admin_token):
    resp = await client.post(
        "/api/experiments",
        json={"name": "Remap Test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    csv_content = b"sample_id,batch_label,organism\nS001,my notes,Human\n"
    import json

    response = await client.post(
        f"/api/experiments/{exp_id}/samples/upload/confirm",
        files={"file": ("samples.csv", io.BytesIO(csv_content), "text/csv")},
        data={"column_mappings": json.dumps({"batch_label": "prep_notes"})},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["created_count"] == 1

    # Verify the mapping took effect
    samples_resp = await client.get(
        f"/api/experiments/{exp_id}/samples",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    samples = samples_resp.json()
    assert samples[0]["prep_notes"] == "my notes"


@pytest.mark.asyncio
async def test_confirm_endpoint_with_custom_fields(client, admin_token):
    resp = await client.post(
        "/api/experiments",
        json={"name": "Custom Field Test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]

    csv_content = b"sample_id,organism,cell_line\nS001,Human,HEK293\n"
    import json

    response = await client.post(
        f"/api/experiments/{exp_id}/samples/upload/confirm",
        files={"file": ("samples.csv", io.BytesIO(csv_content), "text/csv")},
        data={"column_mappings": json.dumps({"cell_line": "custom:cell_line"})},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["created_count"] == 1
    # Custom fields should be reported in response
    assert "custom_fields_created" in data
    assert data["custom_fields_created"] == ["cell_line"]
