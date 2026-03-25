"""Tests for the provenance report API endpoints."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO

import pytest
from sqlalchemy import text


async def _seed_experiment(session, org_id: int, owner_id: int) -> dict:
    """Seed minimal experiment data for API tests."""
    await session.execute(
        text(
            "INSERT INTO projects (id, organization_id, name, description, status, owner_user_id, created_by_user_id) "
            "VALUES (1, :org, 'Test Project', 'A test project', 'active', :owner, :owner)"
        ),
        {"org": org_id, "owner": owner_id},
    )
    await session.execute(
        text(
            "INSERT INTO experiments (id, organization_id, project_id, name, status, owner_user_id) "
            "VALUES (1, :org, 1, 'Test Experiment', 'sequencing', :owner)"
        ),
        {"org": org_id, "owner": owner_id},
    )
    await session.execute(
        text(
            "INSERT INTO samples (id, experiment_id, sample_id_external, organism, tissue_type, "
            "qc_status, status) VALUES (1, 1, 'S001', 'Homo sapiens', 'PBMC', 'pass', 'registered')"
        ),
    )
    await session.execute(
        text(
            "INSERT INTO files (id, organization_id, filename, gcs_uri, file_type, size_bytes, "
            "md5_checksum, experiment_id, source_type, uploader_user_id) "
            "VALUES (1, :org, 'reads.fastq.gz', 'gs://b/reads.fastq.gz', 'fastq', 1024, "
            "'abc', 1, 'upload', :owner)"
        ),
        {"org": org_id, "owner": owner_id},
    )
    await session.execute(text("INSERT INTO sample_files (id, sample_id, file_id) VALUES (1, 1, 1)"))
    await session.execute(
        text(
            "INSERT INTO pipeline_runs (id, organization_id, experiment_id, project_id, "
            "submitted_by_user_id, pipeline_name, pipeline_version, status, "
            "parameters_json, input_files_json, output_files_json, container_versions_json, "
            "retry_count, work_dir, k8s_namespace, k8s_pod_name) "
            "VALUES (1, :org, 1, 1, :owner, 'nf-core/rnaseq', '3.0', 'completed', "
            ":params, '[]', '[]', '{}', 0, '/work', 'ns', 'pod')"
        ),
        {"org": org_id, "owner": owner_id, "params": json.dumps({"aligner": "star"})},
    )
    await session.execute(text("INSERT INTO pipeline_run_samples (id, pipeline_run_id, sample_id) VALUES (1, 1, 1)"))
    await session.commit()
    return {"project_id": 1, "experiment_id": 1, "sample_id": 1, "file_id": 1, "pipeline_run_id": 1}


@pytest.fixture
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


class TestProvenanceReportAPI:
    """Test provenance report endpoints for each entity type and format."""

    @pytest.mark.asyncio
    async def test_experiment_json(self, client, session, admin_user, admin_token, auth_headers):
        ids = await _seed_experiment(session, admin_user.organization_id, admin_user.id)
        resp = await client.get(
            f"/api/experiments/{ids['experiment_id']}/provenance/report?format=json",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/json"
        assert "attachment" in resp.headers["content-disposition"]
        report = resp.json()
        assert report["schema_version"] == "1.0"
        assert report["report_type"] == "experiment"

    @pytest.mark.asyncio
    async def test_experiment_csv(self, client, session, admin_user, admin_token, auth_headers):
        ids = await _seed_experiment(session, admin_user.organization_id, admin_user.id)
        resp = await client.get(
            f"/api/experiments/{ids['experiment_id']}/provenance/report?format=csv",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        zf = zipfile.ZipFile(BytesIO(resp.content))
        names = zf.namelist()
        assert any(n.endswith(".csv") for n in names)

    @pytest.mark.asyncio
    async def test_experiment_pdf(self, client, session, admin_user, admin_token, auth_headers):
        ids = await _seed_experiment(session, admin_user.organization_id, admin_user.id)
        resp = await client.get(
            f"/api/experiments/{ids['experiment_id']}/provenance/report?format=pdf",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content[:5] == b"%PDF-"

    @pytest.mark.asyncio
    async def test_project_json(self, client, session, admin_user, admin_token, auth_headers):
        ids = await _seed_experiment(session, admin_user.organization_id, admin_user.id)
        resp = await client.get(
            f"/api/projects/{ids['project_id']}/provenance/report?format=json",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        report = resp.json()
        assert report["report_type"] == "project"

    @pytest.mark.asyncio
    async def test_sample_json(self, client, session, admin_user, admin_token, auth_headers):
        ids = await _seed_experiment(session, admin_user.organization_id, admin_user.id)
        resp = await client.get(
            f"/api/samples/{ids['sample_id']}/provenance/report?format=json",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        report = resp.json()
        assert report["report_type"] == "sample"

    @pytest.mark.asyncio
    async def test_pipeline_run_json(self, client, session, admin_user, admin_token, auth_headers):
        ids = await _seed_experiment(session, admin_user.organization_id, admin_user.id)
        resp = await client.get(
            f"/api/pipeline-runs/{ids['pipeline_run_id']}/provenance/report?format=json",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        report = resp.json()
        assert report["report_type"] == "pipeline_run"

    @pytest.mark.asyncio
    async def test_artifact_json(self, client, session, admin_user, admin_token, auth_headers):
        ids = await _seed_experiment(session, admin_user.organization_id, admin_user.id)
        resp = await client.get(
            f"/api/files/{ids['file_id']}/provenance/report?format=json",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        report = resp.json()
        assert report["report_type"] == "artifact"

    @pytest.mark.asyncio
    async def test_invalid_format_returns_422(self, client, session, admin_user, admin_token, auth_headers):
        ids = await _seed_experiment(session, admin_user.organization_id, admin_user.id)
        resp = await client.get(
            f"/api/experiments/{ids['experiment_id']}/provenance/report?format=yaml",
            headers=auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_viewer_can_access(self, client, session, admin_user, viewer_token):
        ids = await _seed_experiment(session, admin_user.organization_id, admin_user.id)
        resp = await client.get(
            f"/api/experiments/{ids['experiment_id']}/provenance/report?format=json",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, client, session, admin_user):
        ids = await _seed_experiment(session, admin_user.organization_id, admin_user.id)
        resp = await client.get(
            f"/api/experiments/{ids['experiment_id']}/provenance/report?format=json",
        )
        assert resp.status_code == 401
