"""Tests for CodeService: project / experiment code generation and filename suggestion."""

import pytest

from app.services.code_service import CodeService


# --- Pure helpers: org prefix derivation ---


def test_org_prefix_bioaf_takes_first_four_lowercase():
    assert CodeService.derive_org_prefix("bioAF") == "bioa"


def test_org_prefix_strips_non_alphanumeric():
    assert CodeService.derive_org_prefix("Acme & Co") == "acme"


def test_org_prefix_keeps_digits():
    assert CodeService.derive_org_prefix("42 Bio") == "42bi"


def test_org_prefix_short_name_returns_shorter_prefix():
    assert CodeService.derive_org_prefix("X") == "x"


def test_org_prefix_empty_when_no_alphanumerics():
    assert CodeService.derive_org_prefix("!!!") == ""


# --- Pure helpers: code formatting ---


def test_format_project_code_zero_pads_to_four_digits():
    assert CodeService.format_project_code("bioa", 8) == "bioap-0008"


def test_format_experiment_code_uses_e_suffix():
    assert CodeService.format_experiment_code("bioa", 25) == "bioae-0025"


def test_format_project_code_handles_large_counter():
    assert CodeService.format_project_code("test", 9999) == "testp-9999"


# --- DB-integrated: counter is per-org, monotonic, kind-scoped ---


@pytest.mark.asyncio
async def test_project_gets_code_on_creation(client, admin_token):
    resp = await client.post(
        "/api/projects",
        json={"name": "CRISPR Screen"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == "testp-0001"


@pytest.mark.asyncio
async def test_project_code_increments_per_org(client, admin_token):
    await client.post(
        "/api/projects",
        json={"name": "Project A"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    resp2 = await client.post(
        "/api/projects",
        json={"name": "Project B"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp2.json()["code"] == "testp-0002"


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
    assert resp.json()["code"] == "teste-0001"


@pytest.mark.asyncio
async def test_experiment_code_increments_across_projects(client, admin_token):
    proj_a = (
        await client.post(
            "/api/projects",
            json={"name": "Project A"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    ).json()
    proj_b = (
        await client.post(
            "/api/projects",
            json={"name": "Project B"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    ).json()

    e1 = await client.post(
        "/api/experiments",
        json={"name": "X", "project_id": proj_a["id"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    e2 = await client.post(
        "/api/experiments",
        json={"name": "Y", "project_id": proj_b["id"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert e1.json()["code"] == "teste-0001"
    assert e2.json()["code"] == "teste-0002"


# --- suggest_filename (unchanged) ---


def test_suggest_filename_project_only():
    name = CodeService.suggest_filename(
        original="mydata.csv",
        project_code="testp-0001",
        experiment_code=None,
        sample_id=None,
        data_type="data",
        date_str="20260325",
    )
    assert name == "testp-0001_data_20260325.csv"


def test_suggest_filename_experiment():
    name = CodeService.suggest_filename(
        original="reads.fastq.gz",
        project_code="testp-0001",
        experiment_code="teste-0001",
        sample_id=None,
        data_type="FQ",
        date_str="20260325",
    )
    assert name == "testp-0001_teste-0001_FQ_20260325.fastq.gz"


def test_suggest_filename_all_levels():
    name = CodeService.suggest_filename(
        original="reads.fastq.gz",
        project_code="testp-0001",
        experiment_code="teste-0001",
        sample_id="SMP-001",
        data_type="R1",
        date_str="20260325",
    )
    assert name == "testp-0001_teste-0001_SMP-001_R1_20260325.fastq.gz"


def test_suggest_filename_no_association():
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
        project_code="testp-0001",
        experiment_code="teste-0002",
        sample_id=None,
        data_type="FQ",
        date_str="20260325",
    )
    assert name.endswith(".fastq.gz")


def test_suggest_filename_infers_data_type_from_fastq():
    name = CodeService.suggest_filename(
        original="sample.fastq.gz",
        project_code="testp-0001",
        experiment_code="teste-0002",
        sample_id=None,
        data_type=None,
        date_str="20260325",
    )
    assert "FQ" in name


def test_suggest_filename_infers_data_type_from_h5ad():
    name = CodeService.suggest_filename(
        original="counts.h5ad",
        project_code="testp-0001",
        experiment_code="teste-0002",
        sample_id=None,
        data_type=None,
        date_str="20260325",
    )
    assert "counts" in name
