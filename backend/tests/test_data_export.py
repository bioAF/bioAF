"""Tests for experiment and project data export endpoints."""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest
from sqlalchemy import text


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_project(session, org_id: int, owner_id: int, project_id: int = 1) -> None:
    await session.execute(
        text(
            "INSERT INTO projects (id, organization_id, name, description, status, owner_user_id, created_by_user_id) "
            "VALUES (:pid, :org, 'Test Project', 'A test project', 'active', :owner, :owner)"
        ),
        {"pid": project_id, "org": org_id, "owner": owner_id},
    )


async def _seed_experiment(
    session,
    org_id: int,
    owner_id: int,
    experiment_id: int = 1,
    project_id: int = 1,
    name: str = "Test Experiment",
) -> None:
    await session.execute(
        text(
            "INSERT INTO experiments (id, organization_id, project_id, name, status, owner_user_id) "
            "VALUES (:eid, :org, :pid, :name, 'sequencing', :owner)"
        ),
        {"eid": experiment_id, "org": org_id, "pid": project_id, "name": name, "owner": owner_id},
    )


async def _seed_sample(session, experiment_id: int, sample_id: int = 1) -> None:
    await session.execute(
        text(
            "INSERT INTO samples (id, experiment_id, sample_id_external, organism, tissue_type, "
            "qc_status, status) VALUES (:sid, :eid, :extid, 'Homo sapiens', 'PBMC', 'pass', 'registered')"
        ),
        {"sid": sample_id, "eid": experiment_id, "extid": f"S{sample_id:03d}"},
    )


async def _seed_file(
    session,
    org_id: int,
    owner_id: int,
    experiment_id: int,
    file_id: int = 1,
    file_type: str = "results",
    size_bytes: int = 1024,
) -> None:
    await session.execute(
        text(
            "INSERT INTO files (id, organization_id, filename, gcs_uri, file_type, size_bytes, "
            "md5_checksum, experiment_id, source_type, uploader_user_id) "
            "VALUES (:fid, :org, :fname, :uri, :ftype, :size, 'abc', :eid, 'upload', :owner)"
        ),
        {
            "fid": file_id,
            "org": org_id,
            "fname": f"file_{file_id}.txt",
            "uri": f"gs://bucket/file_{file_id}.txt",
            "ftype": file_type,
            "size": size_bytes,
            "eid": experiment_id,
            "owner": owner_id,
        },
    )


async def _seed_full_experiment(session, org_id: int, owner_id: int) -> dict:
    """Seed a project + experiment + sample + two files (results + fastq)."""
    await _seed_project(session, org_id, owner_id)
    await _seed_experiment(session, org_id, owner_id)
    await _seed_sample(session, experiment_id=1)
    await _seed_file(session, org_id, owner_id, experiment_id=1, file_id=1, file_type="results", size_bytes=500)
    await _seed_file(session, org_id, owner_id, experiment_id=1, file_id=2, file_type="fastq", size_bytes=2000)
    await session.execute(text("INSERT INTO sample_files (id, sample_id, file_id) VALUES (1, 1, 1)"))
    await session.commit()
    return {"project_id": 1, "experiment_id": 1}


@pytest.fixture
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def viewer_headers(viewer_token):
    return {"Authorization": f"Bearer {viewer_token}"}


# ---------------------------------------------------------------------------
# Estimate tests
# ---------------------------------------------------------------------------


class TestExperimentEstimate:
    @pytest.mark.asyncio
    async def test_estimate_returns_size_breakdown(self, client, session, admin_user, admin_token, auth_headers):
        await _seed_full_experiment(session, admin_user.organization_id, admin_user.id)

        resp = await client.get("/api/experiments/1/export/estimate", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "total_bytes" in data
        assert "breakdown" in data
        # Without fastq: only results file (500 bytes)
        assert data["total_bytes"] == 500

    @pytest.mark.asyncio
    async def test_estimate_includes_fastq_when_requested(self, client, session, admin_user, admin_token, auth_headers):
        await _seed_full_experiment(session, admin_user.organization_id, admin_user.id)

        resp = await client.get("/api/experiments/1/export/estimate?include_fastq=true", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        # results (500) + fastq (2000)
        assert data["total_bytes"] == 2500

    @pytest.mark.asyncio
    async def test_estimate_requires_permission(
        self, client, session, admin_user, admin_token, viewer_user, viewer_headers
    ):
        await _seed_full_experiment(session, admin_user.organization_id, admin_user.id)

        resp = await client.get("/api/experiments/1/export/estimate", headers=viewer_headers)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_estimate_cross_org_isolation(self, client, session, admin_user, admin_token, auth_headers):
        """Non-existent experiment returns 404."""
        resp = await client.get("/api/experiments/9999/export/estimate", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_estimate_no_files_returns_zero(self, client, session, admin_user, admin_token, auth_headers):
        await _seed_project(session, admin_user.organization_id, admin_user.id)
        await _seed_experiment(session, admin_user.organization_id, admin_user.id)
        await session.commit()

        resp = await client.get("/api/experiments/1/export/estimate", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_bytes"] == 0


class TestProjectEstimate:
    @pytest.mark.asyncio
    async def test_project_estimate_aggregates_experiments(
        self, client, session, admin_user, admin_token, auth_headers
    ):
        await _seed_project(session, admin_user.organization_id, admin_user.id)
        await _seed_experiment(session, admin_user.organization_id, admin_user.id, experiment_id=1, name="Exp 1")
        await _seed_experiment(session, admin_user.organization_id, admin_user.id, experiment_id=2, name="Exp 2")
        await _seed_file(
            session,
            admin_user.organization_id,
            admin_user.id,
            experiment_id=1,
            file_id=1,
            file_type="results",
            size_bytes=100,
        )
        await _seed_file(
            session,
            admin_user.organization_id,
            admin_user.id,
            experiment_id=2,
            file_id=2,
            file_type="results",
            size_bytes=200,
        )
        await session.commit()

        resp = await client.get("/api/projects/1/export/estimate", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_bytes"] == 300
        assert "experiments" in data

    @pytest.mark.asyncio
    async def test_project_estimate_requires_permission(
        self, client, session, admin_user, admin_token, viewer_user, viewer_headers
    ):
        await _seed_project(session, admin_user.organization_id, admin_user.id)
        await session.commit()

        resp = await client.get("/api/projects/1/export/estimate", headers=viewer_headers)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Direct export tests
# ---------------------------------------------------------------------------


class TestExperimentDirectExport:
    @pytest.mark.asyncio
    async def test_direct_export_returns_zip(self, client, session, admin_user, admin_token, auth_headers):
        await _seed_full_experiment(session, admin_user.organization_id, admin_user.id)

        resp = await client.post(
            "/api/experiments/1/export/data",
            json={"delivery_method": "direct", "include_fastq": False, "include_provenance": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "attachment" in resp.headers["content-disposition"]

    @pytest.mark.asyncio
    async def test_direct_export_zip_structure(self, client, session, admin_user, admin_token, auth_headers):
        await _seed_full_experiment(session, admin_user.organization_id, admin_user.id)

        resp = await client.post(
            "/api/experiments/1/export/data",
            json={"delivery_method": "direct", "include_fastq": False, "include_provenance": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        zf = zipfile.ZipFile(BytesIO(resp.content))
        names = zf.namelist()
        # Must contain README and sample_manifest at minimum
        assert any("README.txt" in n for n in names)
        assert any("sample_manifest.csv" in n for n in names)

    @pytest.mark.asyncio
    async def test_direct_export_creates_audit_log(self, client, session, admin_user, admin_token, auth_headers):
        await _seed_full_experiment(session, admin_user.organization_id, admin_user.id)

        await client.post(
            "/api/experiments/1/export/data",
            json={"delivery_method": "direct", "include_fastq": False, "include_provenance": False},
            headers=auth_headers,
        )

        result = await session.execute(
            text(
                "SELECT action FROM audit_log WHERE entity_type = 'experiment' "
                "AND entity_id = 1 AND action = 'data_exported'"
            )
        )
        row = result.fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_export_requires_permission(
        self, client, session, admin_user, admin_token, viewer_user, viewer_headers
    ):
        await _seed_full_experiment(session, admin_user.organization_id, admin_user.id)

        resp = await client.post(
            "/api/experiments/1/export/data",
            json={"delivery_method": "direct", "include_fastq": False, "include_provenance": False},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_export_cross_org_isolation(self, client, session, admin_user, admin_token, auth_headers):
        """Non-existent experiment returns 404."""
        resp = await client.post(
            "/api/experiments/9999/export/data",
            json={"delivery_method": "direct", "include_fastq": False, "include_provenance": False},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_export_no_files_returns_minimal_zip(self, client, session, admin_user, admin_token, auth_headers):
        """Experiment with no files returns a ZIP containing README + sample_manifest only."""
        await _seed_project(session, admin_user.organization_id, admin_user.id)
        await _seed_experiment(session, admin_user.organization_id, admin_user.id)
        await session.commit()

        resp = await client.post(
            "/api/experiments/1/export/data",
            json={"delivery_method": "direct", "include_fastq": False, "include_provenance": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        zf = zipfile.ZipFile(BytesIO(resp.content))
        names = zf.namelist()
        assert any("README.txt" in n for n in names)


class TestExperimentGcsExport:
    @pytest.mark.asyncio
    async def test_gcs_export_returns_signed_url_response(
        self, client, session, admin_user, admin_token, auth_headers, monkeypatch
    ):
        await _seed_full_experiment(session, admin_user.organization_id, admin_user.id)

        # Patch GCS upload + signed URL generation
        from app.services import export_service as svc

        async def _fake_upload(zip_bytes, org_id, experiment_name, session):
            return "https://storage.googleapis.com/signed-url-token"

        monkeypatch.setattr(svc, "_upload_zip_to_gcs", _fake_upload)

        resp = await client.post(
            "/api/experiments/1/export/data",
            json={"delivery_method": "gcs", "include_fastq": False, "include_provenance": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "signed_url" in data
        assert data["signed_url"] == "https://storage.googleapis.com/signed-url-token"
        assert "expires_in_hours" in data


class TestProjectDirectExport:
    @pytest.mark.asyncio
    async def test_project_export_zip_has_experiment_subfolders(
        self, client, session, admin_user, admin_token, auth_headers
    ):
        await _seed_project(session, admin_user.organization_id, admin_user.id)
        await _seed_experiment(session, admin_user.organization_id, admin_user.id, experiment_id=1, name="Alpha")
        await _seed_experiment(session, admin_user.organization_id, admin_user.id, experiment_id=2, name="Beta")
        await session.commit()

        resp = await client.post(
            "/api/projects/1/export/data",
            json={"delivery_method": "direct", "include_fastq": False, "include_provenance": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        zf = zipfile.ZipFile(BytesIO(resp.content))
        names = zf.namelist()
        # Each experiment should have its own sub-folder in the ZIP
        has_alpha = any("Alpha" in n or "alpha" in n.lower() for n in names)
        has_beta = any("Beta" in n or "beta" in n.lower() for n in names)
        assert has_alpha
        assert has_beta

    @pytest.mark.asyncio
    async def test_project_export_requires_permission(
        self, client, session, admin_user, admin_token, viewer_user, viewer_headers
    ):
        await _seed_project(session, admin_user.organization_id, admin_user.id)
        await session.commit()

        resp = await client.post(
            "/api/projects/1/export/data",
            json={"delivery_method": "direct", "include_fastq": False, "include_provenance": False},
            headers=viewer_headers,
        )
        assert resp.status_code == 403
