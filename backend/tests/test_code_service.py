"""Tests for CodeService: project code and experiment code generation."""

import pytest

from app.services.code_service import CodeService


# --- Unit tests for generate_project_prefix (pure, no DB) ---


def test_project_prefix_consonants_only():
    assert CodeService.derive_project_prefix("CRISPR Screen") == "CS"


def test_project_prefix_skips_leading_vowels():
    # "Alpha" → first consonant is 'L'; "Beta" → 'B'
    assert CodeService.derive_project_prefix("Alpha Beta") == "LB"


def test_project_prefix_all_caps_acronym():
    # "RNA" → R; "seq" → S; "Study" → S
    assert CodeService.derive_project_prefix("RNA seq Study") == "RSS"


def test_project_prefix_hyphen_treated_as_single_word():
    # "Non-Human" is one space-delimited token; first consonant = N
    assert CodeService.derive_project_prefix("Non-Human Primate") == "NP"


def test_project_prefix_skips_all_vowel_words():
    # "AI" → no consonants → skip; "Research" → R
    assert CodeService.derive_project_prefix("AI Research") == "R"


def test_project_prefix_empty_fallback():
    # All vowels → use fallback "PRJ"
    assert CodeService.derive_project_prefix("A E I O U") == "PRJ"


def test_project_prefix_single_word():
    assert CodeService.derive_project_prefix("Genomics") == "G"


# --- generate_project_code with counter logic ---


def test_project_code_first_in_year():
    code = CodeService.generate_project_code("CRISPR Screen", 2026, [])
    assert code == "CS26-1"


def test_project_code_increments_counter():
    existing = ["CS26-1", "CS26-2"]
    code = CodeService.generate_project_code("CRISPR Screen", 2026, existing)
    assert code == "CS26-3"


def test_project_code_different_year_resets():
    # Existing code from 2025 does NOT count toward 2026 counter
    existing = ["CS25-1", "CS25-2"]
    code = CodeService.generate_project_code("CRISPR Screen", 2026, existing)
    assert code == "CS26-1"


def test_project_code_year_suffix_2digit():
    code = CodeService.generate_project_code("RNA Study", 2026, [])
    assert "26" in code


def test_project_code_mixed_existing_years():
    existing = ["CS26-1", "CS25-1"]
    code = CodeService.generate_project_code("CRISPR Screen", 2026, existing)
    assert code == "CS26-2"


# --- generate_experiment_code ---


def test_experiment_code_first():
    assert CodeService.generate_experiment_code(0) == "E001"


def test_experiment_code_increments():
    assert CodeService.generate_experiment_code(1) == "E002"
    assert CodeService.generate_experiment_code(9) == "E010"
    assert CodeService.generate_experiment_code(99) == "E100"


def test_experiment_code_zero_padded_3_digits():
    code = CodeService.generate_experiment_code(0)
    assert len(code) == 4  # 'E' + 3 digits
    assert code.startswith("E")


# --- suggest_filename ---


def test_suggest_filename_project_only():
    name = CodeService.suggest_filename(
        original="mydata.csv",
        project_code="CS26-1",
        experiment_code=None,
        sample_id=None,
        data_type="data",
        date_str="20260325",
    )
    assert name == "CS26-1_data_20260325.csv"


def test_suggest_filename_experiment():
    name = CodeService.suggest_filename(
        original="reads.fastq.gz",
        project_code="CS26-1",
        experiment_code="E001",
        sample_id=None,
        data_type="FQ",
        date_str="20260325",
    )
    assert name == "CS26-1_E001_FQ_20260325.fastq.gz"


def test_suggest_filename_all_levels():
    name = CodeService.suggest_filename(
        original="reads.fastq.gz",
        project_code="CS26-1",
        experiment_code="E001",
        sample_id="SMP-001",
        data_type="R1",
        date_str="20260325",
    )
    assert name == "CS26-1_E001_SMP-001_R1_20260325.fastq.gz"


def test_suggest_filename_no_association():
    # No codes provided → return original unchanged
    name = CodeService.suggest_filename(
        original="myfile.txt",
        project_code=None,
        experiment_code=None,
        sample_id=None,
        data_type=None,
        date_str="20260325",
    )
    assert name == "myfile.txt"


def test_suggest_filename_preserves_double_extension():
    name = CodeService.suggest_filename(
        original="sample.fastq.gz",
        project_code="NP26-1",
        experiment_code="E002",
        sample_id=None,
        data_type="FQ",
        date_str="20260325",
    )
    assert name.endswith(".fastq.gz")


def test_suggest_filename_infers_data_type_from_fastq():
    name = CodeService.suggest_filename(
        original="sample.fastq.gz",
        project_code="NP26-1",
        experiment_code="E002",
        sample_id=None,
        data_type=None,
        date_str="20260325",
    )
    assert "FQ" in name


def test_suggest_filename_infers_data_type_from_h5ad():
    name = CodeService.suggest_filename(
        original="counts.h5ad",
        project_code="NP26-1",
        experiment_code="E002",
        sample_id=None,
        data_type=None,
        date_str="20260325",
    )
    assert "counts" in name


# --- DB-integrated tests ---


@pytest.mark.asyncio
async def test_project_gets_code_on_creation(client, admin_token):
    resp = await client.post(
        "/api/projects",
        json={"name": "CRISPR Screen"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] is not None
    assert data["code"].startswith("CS26")


@pytest.mark.asyncio
async def test_project_code_increments_per_org(client, admin_token):
    await client.post(
        "/api/projects",
        json={"name": "CRISPR Screen"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    resp2 = await client.post(
        "/api/projects",
        json={"name": "CRISPR Screen"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    data2 = resp2.json()
    assert data2["code"] == "CS26-2"


@pytest.mark.asyncio
async def test_experiment_gets_code_on_creation(client, admin_token):
    proj = await client.post(
        "/api/projects",
        json={"name": "RNA Study"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    proj_id = proj.json()["id"]

    resp = await client.post(
        "/api/experiments",
        json={"name": "Batch A", "project_id": proj_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == "E001"


@pytest.mark.asyncio
async def test_experiment_code_increments_within_project(client, admin_token):
    proj = await client.post(
        "/api/projects",
        json={"name": "RNA Study"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    proj_id = proj.json()["id"]

    await client.post(
        "/api/experiments",
        json={"name": "Batch A", "project_id": proj_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    resp2 = await client.post(
        "/api/experiments",
        json={"name": "Batch B", "project_id": proj_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp2.json()["code"] == "E002"
