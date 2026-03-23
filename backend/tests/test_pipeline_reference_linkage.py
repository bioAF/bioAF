"""Tests for pipeline launcher reference linkage (Step 8)."""

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio@test.com",
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
async def reference_and_run(session, admin_user):
    """Create a reference dataset and pipeline run for linkage tests."""
    from app.models.reference_dataset import ReferenceDataset

    ref = ReferenceDataset(
        organization_id=admin_user.organization_id,
        name="GRCh38 GENCODE v43",
        category="genome",
        scope="public",
        version="v43",
        gcs_prefix="genomes/GRCh38/v43/",
        status="active",
    )
    session.add(ref)

    deprecated_ref = ReferenceDataset(
        organization_id=admin_user.organization_id,
        name="GRCh38 GENCODE v42",
        category="genome",
        scope="public",
        version="v42",
        gcs_prefix="genomes/GRCh38/v42/",
        status="deprecated",
        deprecation_note="Replaced by v43",
    )
    session.add(deprecated_ref)
    await session.flush()
    await session.commit()

    return {"ref_id": ref.id, "deprecated_ref_id": deprecated_ref.id, "org_id": admin_user.organization_id}


@pytest.mark.asyncio
async def test_link_references_from_params_resolves(session, reference_and_run):
    """Parameters with reference paths create linkage records."""
    from app.models.pipeline_run import PipelineRun
    from app.services.pipeline_run_service import PipelineRunService

    org_id = reference_and_run["org_id"]

    run = PipelineRun(
        organization_id=org_id,
        pipeline_name="nf-core/scrnaseq",
        status="pending",
    )
    session.add(run)
    await session.flush()

    params = {
        "genome": "/data/references/genomes/GRCh38/v43/genome.fa",
        "gtf": "/data/references/genomes/GRCh38/v43/genes.gtf",
        "other_param": "not_a_reference",
    }

    linked = await PipelineRunService._link_references_from_params(session, run.id, org_id, params)
    await session.commit()

    # Should link to one reference (both paths resolve to same dataset)
    assert len(linked) == 1
    assert linked[0] == reference_and_run["ref_id"]

    # Verify DB record
    result = await session.execute(
        text(f"SELECT reference_dataset_id FROM pipeline_run_references WHERE pipeline_run_id = {run.id}")
    )
    rows = result.fetchall()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_link_references_unresolvable_succeeds(session, reference_and_run):
    """Unresolvable paths log warning but don't fail."""
    from app.models.pipeline_run import PipelineRun
    from app.services.pipeline_run_service import PipelineRunService

    org_id = reference_and_run["org_id"]

    run = PipelineRun(
        organization_id=org_id,
        pipeline_name="nf-core/scrnaseq",
        status="pending",
    )
    session.add(run)
    await session.flush()

    params = {
        "genome": "/data/references/genomes/unknown_species/v1/genome.fa",
    }

    linked = await PipelineRunService._link_references_from_params(session, run.id, org_id, params)
    await session.commit()

    # No linkage created, but no error
    assert len(linked) == 0


@pytest.mark.asyncio
async def test_link_references_deprecated_warns(session, reference_and_run):
    """Deprecated references generate warning but don't block."""
    from app.models.pipeline_run import PipelineRun
    from app.services.pipeline_run_service import PipelineRunService

    org_id = reference_and_run["org_id"]

    run = PipelineRun(
        organization_id=org_id,
        pipeline_name="nf-core/scrnaseq",
        status="pending",
    )
    session.add(run)
    await session.flush()

    params = {
        "genome": "/data/references/genomes/GRCh38/v42/genome.fa",
    }

    linked = await PipelineRunService._link_references_from_params(session, run.id, org_id, params)
    await session.commit()

    # Should still link, just with a warning logged
    assert len(linked) == 1
    assert linked[0] == reference_and_run["deprecated_ref_id"]


@pytest.mark.asyncio
async def test_link_references_nested_params(session, reference_and_run):
    """Reference paths inside nested parameter structures are extracted."""
    from app.models.pipeline_run import PipelineRun
    from app.services.pipeline_run_service import PipelineRunService

    org_id = reference_and_run["org_id"]

    run = PipelineRun(
        organization_id=org_id,
        pipeline_name="nf-core/scrnaseq",
        status="pending",
    )
    session.add(run)
    await session.flush()

    params = {
        "references": {
            "primary": "/data/references/genomes/GRCh38/v43/genome.fa",
            "indices": ["/data/references/genomes/GRCh38/v43/star_index/"],
        },
        "unrelated": 42,
    }

    linked = await PipelineRunService._link_references_from_params(session, run.id, org_id, params)
    await session.commit()

    assert len(linked) == 1


@pytest.mark.asyncio
async def test_link_references_no_reference_paths(session, reference_and_run):
    """Parameters with no reference paths produce no linkage."""
    from app.models.pipeline_run import PipelineRun
    from app.services.pipeline_run_service import PipelineRunService

    org_id = reference_and_run["org_id"]

    run = PipelineRun(
        organization_id=org_id,
        pipeline_name="nf-core/scrnaseq",
        status="pending",
    )
    session.add(run)
    await session.flush()

    params = {"cell_count": 5000, "min_genes": 200}

    linked = await PipelineRunService._link_references_from_params(session, run.id, org_id, params)
    assert len(linked) == 0
