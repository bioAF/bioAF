"""Tests for the full search results page backend (SearchService.full_search).

Covers the 7 searchable types, broader-content matching, match-quality + recency
ranking, the entity_types filter, the 300-result cap with pagination, per-type
counts, per-type destination urls, and the file disambiguation context line.
"""

from datetime import datetime, timezone

import pytest

from app.models.custom_pipeline import CustomPipeline
from app.models.experiment import Experiment
from app.models.file import File
from app.models.literature import PROVENANCE_USER_UPLOAD, LiteraturePaper
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.pipeline_run import PipelineRun
from app.models.project import Project
from app.models.sample import Sample, sample_files
from app.services.search_service import SearchService

ALL_TYPES = {
    "experiment",
    "sample",
    "pipeline_run",
    "file",
    "project",
    "pipeline_definition",
    "literature_paper",
}


@pytest.mark.asyncio
async def test_full_search_matches_name_across_all_types(session, admin_user):
    org_id = admin_user.organization_id
    exp = Experiment(organization_id=org_id, name="Alpha Study")
    session.add(exp)
    await session.flush()
    session.add_all(
        [
            Sample(experiment_id=exp.id, external_id="ALPHA-1"),
            PipelineRun(organization_id=org_id, experiment_id=exp.id, pipeline_name="alpha-rnaseq"),
            File(
                organization_id=org_id,
                experiment_id=exp.id,
                gcs_uri="gs://b/alpha.csv",
                filename="alpha.csv",
                file_type="csv",
            ),
            Project(organization_id=org_id, name="Alpha Project"),
            PipelineCatalogEntry(
                organization_id=org_id,
                pipeline_key="alpha/key",
                name="Alpha Pipeline",
                source_type="nf-core",
                is_builtin=True,
            ),
            LiteraturePaper(
                organization_id=org_id,
                title="Alpha findings in cells",
                title_normalized="alpha findings in cells",
                provenance=PROVENANCE_USER_UPLOAD,
            ),
        ]
    )
    await session.commit()

    hits, total, counts = await SearchService.full_search(session, org_id, "alpha")

    assert {h["entity_type"] for h in hits} == ALL_TYPES
    assert total == len(hits)
    for t in ALL_TYPES:
        assert counts.get(t, 0) >= 1


@pytest.mark.asyncio
async def test_full_search_hit_urls_per_type(session, admin_user):
    org_id = admin_user.organization_id
    exp = Experiment(organization_id=org_id, name="Zeta Study")
    session.add(exp)
    await session.flush()
    sample = Sample(experiment_id=exp.id, external_id="ZETA-1")
    run = PipelineRun(organization_id=org_id, experiment_id=exp.id, pipeline_name="zeta-run")
    f = File(
        organization_id=org_id,
        experiment_id=exp.id,
        gcs_uri="gs://b/zeta.txt",
        filename="zeta.txt",
        file_type="txt",
    )
    proj = Project(organization_id=org_id, name="Zeta Project")
    builtin = PipelineCatalogEntry(
        organization_id=org_id,
        pipeline_key="nf-core/zeta",
        name="Zeta builtin",
        source_type="nf-core",
        is_builtin=True,
    )
    cp = CustomPipeline(
        organization_id=org_id,
        name="Zeta custom pipeline",
        pipeline_key="custom-zeta",
        created_by_user_id=admin_user.id,
    )
    session.add(cp)
    await session.flush()
    custom = PipelineCatalogEntry(
        organization_id=org_id,
        pipeline_key="custom-zeta",
        name="Zeta custom",
        source_type="custom",
        is_builtin=False,
        custom_pipeline_id=cp.id,
    )
    paper = LiteraturePaper(
        organization_id=org_id,
        title="Zeta paper",
        title_normalized="zeta paper",
        provenance=PROVENANCE_USER_UPLOAD,
    )
    session.add_all([sample, run, f, proj, builtin, custom, paper])
    await session.commit()

    hits, _, _ = await SearchService.full_search(session, org_id, "zeta")
    single = {h["entity_type"]: h["url"] for h in hits if h["entity_type"] != "pipeline_definition"}

    assert single["experiment"] == f"/experiments/{exp.id}"
    assert single["sample"] == f"/experiments/{exp.id}?tab=samples"
    assert single["pipeline_run"] == f"/pipelines/runs/{run.id}"
    assert single["file"] == f"/data/files?file={f.id}"
    assert single["project"] == f"/projects/{proj.id}"
    assert single["literature_paper"] == f"/data/literature/papers/{paper.id}"

    defs = {h["title"]: h["url"] for h in hits if h["entity_type"] == "pipeline_definition"}
    # The pipeline_key slash must be URL-encoded so it stays one route segment.
    assert defs["Zeta builtin"] == "/pipelines/launch/nf-core%2Fzeta"
    assert defs["Zeta custom"] == f"/pipelines/custom/{cp.id}"


@pytest.mark.asyncio
async def test_full_search_ranks_name_match_before_content_match(session, admin_user):
    org_id = admin_user.organization_id
    content_only = Experiment(organization_id=org_id, name="Background notes", hypothesis="study of kidney tissue")
    exact = Experiment(organization_id=org_id, name="kidney")
    session.add_all([content_only, exact])
    await session.commit()

    hits, _, _ = await SearchService.full_search(session, org_id, "kidney", entity_types=["experiment"])
    names = [h["title"] for h in hits]
    assert "kidney" in names and "Background notes" in names
    assert names.index("kidney") < names.index("Background notes")


@pytest.mark.asyncio
async def test_full_search_recency_breaks_ties_within_a_tier(session, admin_user):
    org_id = admin_user.organization_id
    older = Experiment(
        organization_id=org_id, name="Liver scan one", created_at=datetime(2020, 1, 1, tzinfo=timezone.utc)
    )
    newer = Experiment(
        organization_id=org_id, name="Liver scan two", created_at=datetime(2024, 1, 1, tzinfo=timezone.utc)
    )
    session.add_all([older, newer])
    await session.commit()

    hits, _, _ = await SearchService.full_search(session, org_id, "liver", entity_types=["experiment"])
    names = [h["title"] for h in hits]
    assert names.index("Liver scan two") < names.index("Liver scan one")


@pytest.mark.asyncio
async def test_full_search_entity_types_filter_restricts_results(session, admin_user):
    org_id = admin_user.organization_id
    exp = Experiment(organization_id=org_id, name="Gamma thing")
    session.add(exp)
    await session.flush()
    session.add(
        File(
            organization_id=org_id,
            experiment_id=exp.id,
            gcs_uri="gs://b/gamma.txt",
            filename="gamma.txt",
            file_type="txt",
        )
    )
    await session.commit()

    hits, _, counts = await SearchService.full_search(session, org_id, "gamma", entity_types=["file"])
    assert {h["entity_type"] for h in hits} == {"file"}
    assert "experiment" not in counts


@pytest.mark.asyncio
async def test_full_search_caps_at_300_and_paginates(session, admin_user):
    org_id = admin_user.organization_id
    session.add_all(
        [
            File(
                organization_id=org_id,
                gcs_uri=f"gs://b/cap{i}.txt",
                filename=f"capfile{i}.txt",
                file_type="txt",
            )
            for i in range(305)
        ]
    )
    await session.commit()

    page1, total, counts = await SearchService.full_search(
        session, org_id, "capfile", entity_types=["file"], page=1, page_size=25
    )
    assert total == 300
    assert len(page1) == 25
    assert counts["file"] == 305  # counts are accurate even though results cap at 300

    page2, _, _ = await SearchService.full_search(
        session, org_id, "capfile", entity_types=["file"], page=2, page_size=25
    )
    assert len(page2) == 25
    assert {h["entity_id"] for h in page1}.isdisjoint({h["entity_id"] for h in page2})


@pytest.mark.asyncio
async def test_full_search_file_snippet_carries_run_and_sample_context(session, admin_user):
    org_id = admin_user.organization_id
    exp = Experiment(organization_id=org_id, name="Ctx Experiment")
    session.add(exp)
    await session.flush()
    run = PipelineRun(organization_id=org_id, experiment_id=exp.id, pipeline_name="salmon")
    session.add(run)
    await session.flush()
    sample = Sample(experiment_id=exp.id, external_id="CTX-9")
    session.add(sample)
    await session.flush()
    f = File(
        organization_id=org_id,
        experiment_id=exp.id,
        gcs_uri="gs://b/results.csv",
        filename="results.csv",
        file_type="csv",
        source_type="nextflow",
        source_pipeline_run_id=run.id,
    )
    session.add(f)
    await session.flush()
    await session.execute(sample_files.insert().values(sample_id=sample.id, file_id=f.id))
    await session.commit()

    hits, _, _ = await SearchService.full_search(session, org_id, "results.csv", entity_types=["file"])
    snippet = hits[0]["snippet"] or ""
    assert "salmon" in snippet  # generating run
    assert "CTX-9" in snippet  # associated sample
    assert "Ctx Experiment" in snippet  # parent experiment


@pytest.mark.asyncio
async def test_full_search_matches_literature_content_fields(session, admin_user):
    org_id = admin_user.organization_id
    paper = LiteraturePaper(
        organization_id=org_id,
        title="Unrelated title",
        title_normalized="unrelated title",
        abstract="a deep dive into spectroscopy methods",
        provenance=PROVENANCE_USER_UPLOAD,
    )
    session.add(paper)
    await session.commit()

    hits, _, _ = await SearchService.full_search(session, org_id, "spectroscopy", entity_types=["literature_paper"])
    assert any(h["entity_id"] == paper.id for h in hits)


@pytest.mark.asyncio
async def test_full_search_empty_query_returns_nothing(session, admin_user):
    org_id = admin_user.organization_id
    hits, total, counts = await SearchService.full_search(session, org_id, "   ")
    assert hits == []
    assert total == 0
    assert counts == {}


@pytest.mark.asyncio
async def test_full_search_is_org_scoped(session, admin_user):
    """A hit from another org must never leak in."""
    from app.models.organization import Organization

    org_id = admin_user.organization_id
    other = Organization(name="Other Org", setup_complete=True)
    session.add(other)
    await session.flush()
    session.add(Experiment(organization_id=other.id, name="Omega secret"))
    session.add(Experiment(organization_id=org_id, name="Omega visible"))
    await session.commit()

    hits, _, _ = await SearchService.full_search(session, org_id, "omega", entity_types=["experiment"])
    titles = {h["title"] for h in hits}
    assert "Omega visible" in titles
    assert "Omega secret" not in titles
