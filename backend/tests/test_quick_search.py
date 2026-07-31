import pytest

from app.models.experiment import Experiment
from app.models.file import File
from app.models.literature import LiteraturePaper
from app.models.pipeline_run import PipelineRun
from app.models.sample import Sample
from app.services.search_service import SearchService


@pytest.mark.asyncio
async def test_quick_search_matches_names_across_entity_types(session, admin_user):
    org_id = admin_user.organization_id

    exp = Experiment(organization_id=org_id, name="Alpha Liver Study")
    other_exp = Experiment(organization_id=org_id, name="Beta Kidney Study")
    session.add_all([exp, other_exp])
    await session.flush()

    session.add_all(
        [
            Sample(experiment_id=exp.id, external_id="ALPHA-001"),
            PipelineRun(organization_id=org_id, experiment_id=exp.id, pipeline_name="alpha-scrnaseq"),
            File(
                organization_id=org_id,
                experiment_id=exp.id,
                gcs_uri="gs://bucket/alpha_R1.fastq.gz",
                filename="alpha_R1.fastq.gz",
                file_type="fastq",
            ),
        ]
    )
    await session.commit()

    hits = await SearchService.quick_search(session, org_id, "alpha")
    by_type = {h["entity_type"]: h for h in hits}

    assert by_type["experiment"]["entity_id"] == exp.id
    assert by_type["experiment"]["name"] == "Alpha Liver Study"
    assert by_type["sample"]["name"] == "ALPHA-001"
    assert by_type["sample"]["experiment_id"] == exp.id
    assert "alpha-scrnaseq" in by_type["pipeline_run"]["name"]
    assert by_type["file"]["name"] == "alpha_R1.fastq.gz"

    # The non-matching experiment must not appear.
    assert all(h["name"] != "Beta Kidney Study" for h in hits)


@pytest.mark.asyncio
async def test_quick_search_matches_library_papers_by_title(session, admin_user):
    """Papers in the library are reachable from the header jump-to search by title;
    a paper no longer in the library does not surface."""
    org_id = admin_user.organization_id
    paper = LiteraturePaper(
        organization_id=org_id,
        title="Alpha CRISPR screen",
        title_normalized="alpha crispr screen",
        provenance="user_upload",
    )
    excluded = LiteraturePaper(
        organization_id=org_id,
        title="Alpha excluded paper",
        title_normalized="alpha excluded paper",
        provenance="user_upload",
        in_library=False,
    )
    session.add_all([paper, excluded])
    await session.commit()

    hits = await SearchService.quick_search(session, org_id, "alpha")
    papers = [h for h in hits if h["entity_type"] == "literature_paper"]

    assert [h["entity_id"] for h in papers] == [paper.id]
    assert papers[0]["name"] == "Alpha CRISPR screen"


@pytest.mark.asyncio
async def test_quick_search_is_case_insensitive(session, admin_user):
    org_id = admin_user.organization_id
    exp = Experiment(organization_id=org_id, name="Proteomics Run")
    session.add(exp)
    await session.commit()

    hits = await SearchService.quick_search(session, org_id, "PROTEOMICS")
    assert any(h["entity_id"] == exp.id and h["entity_type"] == "experiment" for h in hits)


@pytest.mark.asyncio
async def test_quick_search_matches_names_only_not_other_fields(session, admin_user):
    """Names only: a sample whose organism (not its id) contains the term must not match."""
    org_id = admin_user.organization_id
    exp = Experiment(organization_id=org_id, name="Host Study")
    session.add(exp)
    await session.flush()
    session.add(Sample(experiment_id=exp.id, external_id="S-100", organism="Zebrafish"))
    await session.commit()

    hits = await SearchService.quick_search(session, org_id, "zebrafish")
    assert all(h["entity_type"] != "sample" for h in hits)


@pytest.mark.asyncio
async def test_quick_search_empty_query_returns_nothing(session, admin_user):
    org_id = admin_user.organization_id
    assert await SearchService.quick_search(session, org_id, "   ") == []


@pytest.mark.asyncio
async def test_quick_search_endpoint_returns_hits(client, admin_token):
    await client.post(
        "/api/experiments",
        json={"name": "Zeta Endpoint Exp"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    resp = await client.get(
        "/api/search/quick?q=Zeta",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert any(h["entity_type"] == "experiment" and "Zeta" in h["name"] for h in results)


@pytest.mark.asyncio
async def test_quick_search_endpoint_empty_query(client, admin_token):
    resp = await client.get(
        "/api/search/quick?q=",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["results"] == []
