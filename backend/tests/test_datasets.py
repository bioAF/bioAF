import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def experiment_with_files(session, admin_user):
    from app.models.experiment import Experiment
    from app.models.file import File

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Dataset Test Experiment",
        owner_user_id=admin_user.id,
        status="fastq_uploaded",
    )
    session.add(exp)
    await session.flush()

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://test-bucket/sample.fastq.gz",
        filename="sample.fastq.gz",
        size_bytes=500_000_000,
        file_type="fastq",
        uploader_user_id=admin_user.id,
        experiment_id=exp.id,
    )
    session.add(f)
    await session.flush()
    await session.commit()
    return exp, f


@pytest.mark.asyncio
async def test_search_datasets_returns_200(client, admin_token, experiment_with_files):
    resp = await client.get(
        "/api/datasets",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_search_datasets_aggregates_file_count(client, admin_token, experiment_with_files):
    exp, f = experiment_with_files
    resp = await client.get(
        "/api/datasets",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    match = next((d for d in data["experiments"] if d["experiment_id"] == exp.id), None)
    assert match is not None
    assert match["file_count"] == 1
    assert match["total_size_bytes"] == 500_000_000


@pytest.mark.asyncio
async def test_search_datasets_empty_org(client, admin_token):
    resp = await client.get(
        "/api/datasets",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "experiments" in data
    assert "total" in data


@pytest_asyncio.fixture
async def experiment_with_samples_and_batches(session, admin_user):
    """Create two experiments with different molecule_type, instrument_model, and review verdicts."""
    from app.models.sample_batch import SampleBatch
    from app.models.experiment import Experiment
    from app.models.pipeline_run import PipelineRun
    from app.models.pipeline_run_review import PipelineRunReview
    from app.models.sample import Sample

    # Experiment 1: total RNA, NovaSeq 6000, approved
    exp1 = Experiment(
        organization_id=admin_user.organization_id,
        name="RNA-seq Experiment",
        owner_user_id=admin_user.id,
        status="reviewed",
    )
    session.add(exp1)
    await session.flush()

    batch1 = SampleBatch(experiment_id=exp1.id, name="Batch-1")
    session.add(batch1)
    await session.flush()

    sample1 = Sample(
        experiment_id=exp1.id,
        sample_batch_id=batch1.id,
        organism="Human",
        molecule_type="total RNA",
    )
    session.add(sample1)
    await session.flush()

    run1 = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp1.id,
        pipeline_name="nf-core/rnaseq",
        status="completed",
    )
    session.add(run1)
    await session.flush()

    review1 = PipelineRunReview(
        pipeline_run_id=run1.id,
        reviewer_user_id=admin_user.id,
        verdict="approved",
    )
    session.add(review1)
    await session.flush()

    # Experiment 2: mRNA, NextSeq 2000, rejected
    exp2 = Experiment(
        organization_id=admin_user.organization_id,
        name="mRNA Experiment",
        owner_user_id=admin_user.id,
        status="pipeline_complete",
    )
    session.add(exp2)
    await session.flush()

    batch2 = SampleBatch(experiment_id=exp2.id, name="Batch-2")
    session.add(batch2)
    await session.flush()

    sample2 = Sample(
        experiment_id=exp2.id,
        sample_batch_id=batch2.id,
        organism="Mouse",
        molecule_type="mRNA",
    )
    session.add(sample2)
    await session.flush()

    run2 = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp2.id,
        pipeline_name="nf-core/rnaseq",
        status="completed",
    )
    session.add(run2)
    await session.flush()

    review2 = PipelineRunReview(
        pipeline_run_id=run2.id,
        reviewer_user_id=admin_user.id,
        verdict="rejected",
    )
    session.add(review2)
    await session.flush()

    await session.commit()
    return exp1, exp2


@pytest.mark.asyncio
async def test_filter_by_molecule_type(client, admin_token, experiment_with_samples_and_batches):
    exp1, exp2 = experiment_with_samples_and_batches
    resp = await client.get(
        "/api/datasets?molecule_type=total+RNA",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    ids = [d["experiment_id"] for d in data["experiments"]]
    assert exp1.id in ids
    assert exp2.id not in ids


@pytest.mark.asyncio
async def test_filter_by_instrument_model(client, admin_token, experiment_with_samples_and_batches):
    """Instrument model filtering returns empty until POBatch is implemented."""
    exp1, exp2 = experiment_with_samples_and_batches
    resp = await client.get(
        "/api/datasets?instrument_model=NextSeq+2000",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    # instrument_model moved from SampleBatch to POBatch; filter yields empty for now
    assert data["experiments"] == []


@pytest.mark.asyncio
async def test_filter_by_review_status(client, admin_token, experiment_with_samples_and_batches):
    exp1, exp2 = experiment_with_samples_and_batches
    resp = await client.get(
        "/api/datasets?review_status=approved",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    ids = [d["experiment_id"] for d in data["experiments"]]
    assert exp1.id in ids
    assert exp2.id not in ids


@pytest.mark.asyncio
async def test_response_includes_filter_fields(client, admin_token, experiment_with_samples_and_batches):
    exp1, _exp2 = experiment_with_samples_and_batches
    resp = await client.get(
        "/api/datasets",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    match = next((d for d in data["experiments"] if d["experiment_id"] == exp1.id), None)
    assert match is not None
    assert match["molecule_type"] == "total RNA"
    # instrument_model moved from SampleBatch to POBatch; None until POBatch implemented
    assert match["instrument_model"] is None
    assert match["review_status"] == "approved"


@pytest.mark.asyncio
async def test_filter_options_returns_distinct_organisms(client, admin_token, experiment_with_samples_and_batches):
    resp = await client.get(
        "/api/datasets/filter-options",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert set(data["organisms"]) == {"Human", "Mouse"}


@pytest.mark.asyncio
async def test_filter_options_preserves_casing_and_sorts_insensitively(session, admin_user, client, admin_token):
    """Distinct values preserve original casing but sort case-insensitively so variants cluster."""
    from app.models.experiment import Experiment
    from app.models.sample import Sample

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Casing Test",
        owner_user_id=admin_user.id,
        status="registered",
    )
    session.add(exp)
    await session.flush()

    for org_name in ["Zebrafish", "homo sapiens", "HOMO SAPIENS", "Human"]:
        session.add(Sample(experiment_id=exp.id, organism=org_name))
    await session.flush()
    await session.commit()

    resp = await client.get(
        "/api/datasets/filter-options",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    orgs = data["organisms"]
    # All variants preserved
    assert "homo sapiens" in orgs
    assert "HOMO SAPIENS" in orgs
    assert "Human" in orgs
    # Case-insensitive sort: "homo sapiens" clusters with "HOMO SAPIENS"/"Human", all before "Zebrafish"
    # With case-sensitive ASCII sort, "homo sapiens" would sort AFTER "Zebrafish" (Z < h)
    homo_idx = orgs.index("homo sapiens")
    zebra_idx = orgs.index("Zebrafish")
    assert homo_idx < zebra_idx


@pytest.mark.asyncio
async def test_filter_options_empty_org(client, admin_token):
    resp = await client.get(
        "/api/datasets/filter-options",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["organisms"] == []
