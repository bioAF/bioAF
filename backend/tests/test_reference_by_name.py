"""TDD: GET /api/references/by-name — returns all versions of a reference (spec §4)."""

import pytest
import pytest_asyncio

from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio_byname@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["comp_bio"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def comp_bio_token(comp_bio_user) -> str:
    return AuthService.create_token(
        comp_bio_user.id,
        comp_bio_user.email,
        comp_bio_user.role_id,
        comp_bio_user.organization_id,
        role_name="comp_bio",
    )


REF_BASE = {
    "name": "GRCh38 GENCODE",
    "category": "genome",
    "scope": "public",
    "gcs_prefix": "genome/grch38/gencode/",
    "files": [
        {
            "filename": "genome.fa",
            "gcs_uri": "gs://bucket/genome/grch38/gencode/v44/genome.fa",
            "size_bytes": 1000,
            "md5_checksum": "a" * 32,
            "file_type": "fasta",
        }
    ],
}


@pytest.mark.asyncio
async def test_by_name_returns_all_versions_descending(client, comp_bio_token):
    # Create three versions
    for v in ["v43", "v44", "v45"]:
        await client.post(
            "/api/references",
            json={**REF_BASE, "version": v, "gcs_prefix": f"genome/grch38/gencode/{v}/"},
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )

    response = await client.get(
        "/api/references/by-name?name=GRCh38%20GENCODE&category=genome",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    versions = [r["version"] for r in body["references"]]
    assert versions == ["v45", "v44", "v43"]  # newest first


@pytest.mark.asyncio
async def test_by_name_filters_to_matching_category(client, comp_bio_token):
    """Same name in two categories should only return rows for the requested category."""
    await client.post(
        "/api/references",
        json={**REF_BASE, "version": "v1"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    await client.post(
        "/api/references",
        json={
            **REF_BASE,
            "category": "annotation",
            "version": "v1-anno",
            "gcs_prefix": "annotation/grch38/gencode/v1/",
            "name": "GRCh38 GENCODE",
        },
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )

    response = await client.get(
        "/api/references/by-name?name=GRCh38%20GENCODE&category=annotation",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["references"][0]["category"] == "annotation"


@pytest.mark.asyncio
async def test_by_name_empty_when_no_match(client, comp_bio_token):
    response = await client.get(
        "/api/references/by-name?name=nonexistent&category=genome",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    assert response.json() == {"references": [], "total": 0}
