import pathlib
import re

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio_runs@test.com",
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


@pytest_asyncio.fixture
async def experiment(session, admin_user):
    from app.models.experiment import Experiment

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Test Experiment",
        owner_user_id=admin_user.id,
        status="fastq_uploaded",
    )
    session.add(exp)
    await session.flush()
    await session.commit()
    return exp


@pytest_asyncio.fixture
async def samples(session, experiment):
    from app.models.file import File
    from app.models.sample import Sample, sample_files

    sample_list = []
    for i in range(3):
        s = Sample(
            experiment_id=experiment.id,
            external_id=f"SAMPLE_{i + 1}",
            organism="Homo sapiens",
            tissue_type="PBMC",
        )
        session.add(s)
        await session.flush()
        # nf-core/scrnaseq consumes per-sample FASTQ; give each sample its own
        # linked reads so the launch passes the file requirement.
        for read in ("R1", "R2"):
            f = File(
                organization_id=experiment.organization_id,
                experiment_id=experiment.id,
                gcs_uri=f"gs://bucket/SAMPLE_{i + 1}_{read}_001.fastq.gz",
                filename=f"SAMPLE_{i + 1}_{read}_001.fastq.gz",
                file_type="fastq",
            )
            session.add(f)
            await session.flush()
            await session.execute(sample_files.insert().values(sample_id=s.id, file_id=f.id))
        sample_list.append(s)
    await session.flush()
    await session.commit()
    return sample_list


@pytest_asyncio.fixture
async def initialized_catalog(client, admin_token):
    """Ensure pipeline catalog is initialized."""
    await client.get("/api/pipelines", headers={"Authorization": f"Bearer {admin_token}"})


@pytest_asyncio.fixture
async def pipeline_run(session, admin_user, experiment, samples, initialized_catalog, client, admin_token):
    """Create a pipeline run via API."""
    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="12345",
        ),
        patch(
            "app.services.experiment_service.ExperimentService.update_status",
            new_callable=AsyncMock,
        ),
    ):
        response = await client.post(
            "/api/pipeline-runs",
            json={
                "pipeline_key": "nf-core/scrnaseq",
                "experiment_id": experiment.id,
                "sample_ids": [s.id for s in samples],
                "parameters": {"aligner": "cellranger"},
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        return response.json()


@pytest.mark.asyncio
async def test_launch_run(client, admin_token, experiment, samples, initialized_catalog):
    """Launch a pipeline run creates record, links samples, updates experiment."""
    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="12345",
        ),
        patch(
            "app.services.experiment_service.ExperimentService.update_status",
            new_callable=AsyncMock,
        ) as mock_status,
    ):
        response = await client.post(
            "/api/pipeline-runs",
            json={
                "pipeline_key": "nf-core/scrnaseq",
                "experiment_id": experiment.id,
                "sample_ids": [s.id for s in samples],
                "parameters": {"aligner": "cellranger", "genome": "GRCh38"},
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["pipeline_name"] == "nf-core/scrnaseq"
        assert data["status"] in ("running", "pending", "failed")
        assert data["parameters"]["aligner"] == "cellranger"
        # Experiment status should have been updated
        mock_status.assert_called_once()


@pytest.mark.asyncio
async def test_launch_run_translates_reference_genome_to_genome_param(
    client, admin_token, experiment, samples, initialized_catalog
):
    """reference_genome must become the nf-core `--genome` param so pipelines that don't hardcode a
    genome default (registry-installed chipseq/atacseq) still receive it. Without this they launch with
    no genome and nf-core fails validation ("Missing required parameter: --fasta"). scrnaseq has no
    genome default, so it exercises the translation cleanly."""
    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="12345",
        ),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        response = await client.post(
            "/api/pipeline-runs",
            json={
                "pipeline_key": "nf-core/scrnaseq",
                "experiment_id": experiment.id,
                "sample_ids": [s.id for s in samples],
                "parameters": {},
                "reference_genome": "GRCh38",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 200
    assert response.json()["parameters"]["genome"] == "GRCh38"


@pytest.mark.asyncio
async def test_launch_run_explicit_genome_param_wins_over_reference_genome(
    client, admin_token, experiment, samples, initialized_catalog
):
    """An explicit `genome` param (from defaults or the caller) is not overridden by reference_genome."""
    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="12345",
        ),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        response = await client.post(
            "/api/pipeline-runs",
            json={
                "pipeline_key": "nf-core/scrnaseq",
                "experiment_id": experiment.id,
                "sample_ids": [s.id for s in samples],
                "parameters": {"genome": "GRCm39"},
                "reference_genome": "GRCh38",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 200
    assert response.json()["parameters"]["genome"] == "GRCm39"


@pytest.mark.asyncio
async def test_launch_run_chipseq_derives_macs_gsize_from_genome(
    client, admin_token, session, admin_user, experiment, samples, initialized_catalog
):
    """A peak-calling pipeline (chipseq/atacseq) must receive macs_gsize derived from the genome, or
    nf-core fails ("specify --read_length or --macs_gsize"). Found by the live ChIP-seq smoke."""
    from app.models.pipeline_catalog_entry import PipelineCatalogEntry

    session.add(
        PipelineCatalogEntry(
            organization_id=admin_user.organization_id,
            pipeline_key="nf-core/chipseq",
            name="nf-core/chipseq",
            source_type="nf-core",
            version="2.1.0",
            qc_template="chipseq",
            is_builtin=False,
            enabled=True,
        )
    )
    await session.commit()

    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="12345",
        ),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        response = await client.post(
            "/api/pipeline-runs",
            json={
                "pipeline_key": "nf-core/chipseq",
                "experiment_id": experiment.id,
                "sample_ids": [s.id for s in samples],
                "parameters": {},
                "reference_genome": "GRCh38",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 200
    params = response.json()["parameters"]
    assert params["genome"] == "GRCh38"
    assert params["macs_gsize"] == 2.7e9


@pytest.mark.asyncio
async def test_launch_run_validates_experiment(client, admin_token, initialized_catalog):
    """Launch fails if experiment doesn't exist."""
    response = await client.post(
        "/api/pipeline-runs",
        json={
            "pipeline_key": "nf-core/scrnaseq",
            "experiment_id": 99999,
            "parameters": {},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_launch_run_validates_pipeline(client, admin_token, experiment, initialized_catalog):
    """Launch fails if pipeline doesn't exist."""
    response = await client.post(
        "/api/pipeline-runs",
        json={
            "pipeline_key": "nonexistent/pipeline",
            "experiment_id": experiment.id,
            "parameters": {},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_launch_run_validates_samples(client, admin_token, experiment, samples, initialized_catalog):
    """Launch fails if sample IDs don't belong to the experiment."""
    with patch(
        "app.services.slurm_service.SlurmService._run_ssh_command",
        new_callable=AsyncMock,
        return_value="12345",
    ):
        response = await client.post(
            "/api/pipeline-runs",
            json={
                "pipeline_key": "nf-core/scrnaseq",
                "experiment_id": experiment.id,
                "sample_ids": [99999],
                "parameters": {},
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_launch_run_creates_audit_entry(client, admin_token, experiment, samples, initialized_catalog, session):
    """Launch creates an audit log entry."""
    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="12345",
        ),
        patch(
            "app.services.experiment_service.ExperimentService.update_status",
            new_callable=AsyncMock,
        ),
    ):
        response = await client.post(
            "/api/pipeline-runs",
            json={
                "pipeline_key": "nf-core/scrnaseq",
                "experiment_id": experiment.id,
                "parameters": {},
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200

    from sqlalchemy import select
    from app.models.audit_log import AuditLog

    result = await session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "pipeline_run",
            AuditLog.action == "launch",
        )
    )
    entries = list(result.scalars().all())
    assert len(entries) >= 1


@pytest.mark.asyncio
async def test_cancel_run(client, admin_token, pipeline_run):
    """Cancel a running pipeline."""
    with patch(
        "app.services.slurm_service.SlurmService._run_ssh_command",
        new_callable=AsyncMock,
        return_value="",
    ):
        response = await client.post(
            f"/api/pipeline-runs/{pipeline_run['id']}/cancel",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_run_creates_audit_entry(client, admin_token, pipeline_run, session):
    """Cancel writes an audit log entry."""
    with patch(
        "app.services.slurm_service.SlurmService._run_ssh_command",
        new_callable=AsyncMock,
        return_value="",
    ):
        response = await client.post(
            f"/api/pipeline-runs/{pipeline_run['id']}/cancel",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200

    from sqlalchemy import select
    from app.models.audit_log import AuditLog

    result = await session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "pipeline_run",
            AuditLog.action == "cancel",
        )
    )
    entries = list(result.scalars().all())
    assert len(entries) >= 1


@pytest.mark.asyncio
async def test_list_runs(client, admin_token, pipeline_run):
    """List pipeline runs returns runs."""
    response = await client.get(
        "/api/pipeline-runs",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert len(data["runs"]) >= 1


@pytest.mark.asyncio
async def test_list_runs_filter_by_experiment(client, admin_token, pipeline_run, experiment):
    """List runs with experiment filter."""
    response = await client.get(
        f"/api/pipeline-runs?experiment_id={experiment.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert all(r["experiment"]["id"] == experiment.id for r in data["runs"])


@pytest.mark.asyncio
async def test_get_run_detail(client, admin_token, pipeline_run):
    """Get run detail with processes and samples."""
    response = await client.get(
        f"/api/pipeline-runs/{pipeline_run['id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == pipeline_run["id"]
    assert "processes" in data
    assert "samples" in data


@pytest.mark.asyncio
async def test_reproduce_run(client, admin_token, pipeline_run):
    """Reproduce creates a new run with same params."""
    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="67890",
        ),
        patch(
            "app.services.experiment_service.ExperimentService.update_status",
            new_callable=AsyncMock,
        ),
    ):
        response = await client.post(
            f"/api/pipeline-runs/{pipeline_run['id']}/reproduce",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] != pipeline_run["id"]
        assert data["resume_from_run_id"] == pipeline_run["id"]


@pytest.mark.asyncio
async def test_provenance_export(client, admin_token, pipeline_run):
    """Provenance export returns expected structure."""
    response = await client.get(
        f"/api/pipeline-runs/{pipeline_run['id']}/provenance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "pipeline_name" in data
    assert "parameters" in data
    assert "samples" in data
    assert "experiment" in data

    # Input files must be human-readable records, not bare ids (issue #3).
    input_files = data["input_files"]
    assert input_files, "run should have resolved input files"
    first = input_files[0]
    assert isinstance(first, dict), "input files must be enriched objects, not ids"
    assert first["filename"].endswith(".fastq.gz")
    assert first["experiment"]["name"] == "Test Experiment"
    assert first["samples"] and first["samples"][0]["external_id"].startswith("SAMPLE_")


@pytest.mark.asyncio
async def test_compare_runs(client, admin_token, experiment, samples, initialized_catalog):
    """Compare runs shows parameter diffs."""
    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="12345",
        ),
        patch(
            "app.services.experiment_service.ExperimentService.update_status",
            new_callable=AsyncMock,
        ),
    ):
        # Create two runs with different params
        r1 = await client.post(
            "/api/pipeline-runs",
            json={
                "pipeline_key": "nf-core/scrnaseq",
                "experiment_id": experiment.id,
                "parameters": {"aligner": "star"},
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        r2 = await client.post(
            "/api/pipeline-runs",
            json={
                "pipeline_key": "nf-core/scrnaseq",
                "experiment_id": experiment.id,
                "parameters": {"aligner": "cellranger"},
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    response = await client.post(
        "/api/pipeline-runs/compare",
        json={"run_ids": [r1.json()["id"], r2.json()["id"]]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["runs"]) == 2
    assert "aligner" in data["parameter_diffs"]


@pytest.mark.asyncio
async def test_viewer_cannot_access_runs(client, viewer_token):
    """Viewer users cannot access pipeline run endpoints."""
    response = await client.get(
        "/api/pipeline-runs",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


async def _install_smrnaseq(session, admin_user):
    from app.models.pipeline_catalog_entry import PipelineCatalogEntry

    session.add(
        PipelineCatalogEntry(
            organization_id=admin_user.organization_id,
            pipeline_key="nf-core/smrnaseq",
            name="nf-core/smrnaseq",
            source_type="nf-core",
            version="2.4.1",
            is_builtin=False,
            enabled=True,
        )
    )
    await session.commit()


async def _launch_smrnaseq(client, admin_token, experiment, samples, parameters=None, genome="GRCh38"):
    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="12345",
        ),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        return await client.post(
            "/api/pipeline-runs",
            json={
                "pipeline_key": "nf-core/smrnaseq",
                "experiment_id": experiment.id,
                "sample_ids": [s.id for s in samples],
                "parameters": parameters or {},
                "reference_genome": genome,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )


@pytest.mark.asyncio
async def test_launch_run_smrnaseq_quantifies_against_real_mirbase(
    client, admin_token, session, admin_user, experiment, samples, initialized_catalog
):
    """nf-core/smrnaseq 2.4.1 defaults `mature` and `hairpin` to the nf-core CI **test dataset**, a
    handful of sequences used to make the pipeline's own tests fast (nextflow.config lines 20-21).

    Its own usage docs claim the defaults are real miRBase. They are not, and this is the third time
    in this project that an nf-core doc disagreed with its config. A run left on those defaults does
    not fail: it quantifies against a toy reference and emits a `mirna.tsv` that looks exactly like a
    real one, which is the worst possible outcome for a tool whose job is checking other people's
    numbers."""
    await _install_smrnaseq(session, admin_user)
    r = await _launch_smrnaseq(client, admin_token, experiment, samples)
    assert r.status_code == 200, r.text
    params = r.json()["parameters"]
    assert "test-datasets" not in str(params.get("mature")), params.get("mature")
    assert "test-datasets" not in str(params.get("hairpin")), params.get("hairpin")
    assert "mirbase.org" in str(params.get("mature"))
    assert "mirbase.org" in str(params.get("hairpin"))


@pytest.mark.asyncio
async def test_launch_run_smrnaseq_derives_the_mirbase_species_from_the_genome(
    client, admin_token, session, admin_user, experiment, samples, initialized_catalog
):
    """`mirtrace_species` is null by default and takes a 3-letter miRBase code. It also decides which
    per-species GFF3 the pipeline downloads, so without it there is no miRNA annotation at all. The
    genome the study already declares answers it."""
    await _install_smrnaseq(session, admin_user)
    r = await _launch_smrnaseq(client, admin_token, experiment, samples, genome="GRCh38")
    assert r.json()["parameters"]["mirtrace_species"] == "hsa"

    r = await _launch_smrnaseq(client, admin_token, experiment, samples, genome="GRCm39")
    assert r.json()["parameters"]["mirtrace_species"] == "mmu"


@pytest.mark.asyncio
async def test_launch_run_smrnaseq_sets_an_adapter_so_the_pipeline_starts_at_all(
    client, admin_token, session, admin_user, experiment, samples, initialized_catalog
):
    """From the pipeline's own usage docs: "If you do not choose a profile that sets the
    `three_prime_adapter`, `clip_r1` and `three_prime_clip_r1` options, the pipeline won't run."
    bioAF launches with no profile, so without this every smrnaseq run stops before it starts.
    auto-detect is what the docs name for the case where the kit is unknown, which it always is when
    the input is somebody else's deposited data."""
    await _install_smrnaseq(session, admin_user)
    r = await _launch_smrnaseq(client, admin_token, experiment, samples)
    assert r.json()["parameters"]["three_prime_adapter"] == "auto-detect"


@pytest.mark.asyncio
async def test_launch_run_smrnaseq_never_overrides_what_the_scientist_stated(
    client, admin_token, session, admin_user, experiment, samples, initialized_catalog
):
    """Every one of these is a default, not a policy. A lab that knows its kit and its reference
    must keep them."""
    await _install_smrnaseq(session, admin_user)
    r = await _launch_smrnaseq(
        client,
        admin_token,
        experiment,
        samples,
        parameters={
            "mature": "gs://lab/our_mature.fa",
            "hairpin": "gs://lab/our_hairpin.fa",
            "mirtrace_species": "rno",
            "three_prime_adapter": "TGGAATTCTCGGGTGCCAAGG",
        },
    )
    params = r.json()["parameters"]
    assert params["mature"] == "gs://lab/our_mature.fa"
    assert params["hairpin"] == "gs://lab/our_hairpin.fa"
    assert params["mirtrace_species"] == "rno"
    assert params["three_prime_adapter"] == "TGGAATTCTCGGGTGCCAAGG"


@pytest.mark.asyncio
async def test_launch_run_rnaseq_gains_no_small_rna_parameters(
    client, admin_token, session, admin_user, experiment, samples, initialized_catalog
):
    """Regression: `smrnaseq` contains `rnaseq`, the same substring trap the Level-3 wiring hit."""
    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="12345",
        ),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        r = await client.post(
            "/api/pipeline-runs",
            json={
                "pipeline_key": "nf-core/rnaseq",
                "experiment_id": experiment.id,
                "sample_ids": [s.id for s in samples],
                "parameters": {},
                "reference_genome": "GRCh38",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    params = r.json()["parameters"]
    for key in ("mature", "hairpin", "mirtrace_species", "three_prime_adapter"):
        assert key not in params, key


@pytest.mark.asyncio
async def test_launch_run_smrnaseq_reads_the_species_off_the_samples_when_no_genome_is_known(
    client, admin_token, session, admin_user, experiment, samples, initialized_catalog
):
    """miRBase is organised by SPECIES, not by genome build, and a paper states its organism far
    more reliably than its reference build.

    Found on the demo carrying a real study to a verdict: the heatstroke paper
    (10.3389/fphar.2026.1718110, GSE327014) names no genome anywhere, so the extractor recorded
    reference_genome=None, and deriving the species from the genome alone left mirtrace_species
    unset. Unset, smrnaseq downloads no per-species miRBase GFF3 and there is no miRNA annotation
    at all. The organism was known the whole time."""
    from sqlalchemy import update

    from app.models.sample import Sample

    await _install_smrnaseq(session, admin_user)
    await session.execute(update(Sample).where(Sample.id.in_([s.id for s in samples])).values(organism="Mus musculus"))
    await session.commit()

    r = await _launch_smrnaseq(client, admin_token, experiment, samples, genome=None)
    assert r.status_code == 200, r.text
    assert r.json()["parameters"]["mirtrace_species"] == "mmu"


@pytest.mark.asyncio
async def test_launch_run_smrnaseq_prefers_the_genome_over_the_sample_organism(
    client, admin_token, session, admin_user, experiment, samples, initialized_catalog
):
    """The genome is the more specific statement and the one the run is actually aligned against,
    so it wins when both are present."""
    from sqlalchemy import update

    from app.models.sample import Sample

    await _install_smrnaseq(session, admin_user)
    await session.execute(update(Sample).where(Sample.id.in_([s.id for s in samples])).values(organism="Mus musculus"))
    await session.commit()

    r = await _launch_smrnaseq(client, admin_token, experiment, samples, genome="GRCh38")
    assert r.json()["parameters"]["mirtrace_species"] == "hsa"


@pytest.mark.asyncio
async def test_launch_run_smrnaseq_says_nothing_about_a_species_it_cannot_name(
    client, admin_token, session, admin_user, experiment, samples, initialized_catalog
):
    """An organism bioAF has no miRBase code for must leave the parameter unset rather than guess.
    A wrong species quantifies against the wrong miRBase and still produces a plausible table."""
    from sqlalchemy import update

    from app.models.sample import Sample

    await _install_smrnaseq(session, admin_user)
    await session.execute(
        update(Sample).where(Sample.id.in_([s.id for s in samples])).values(organism="Chlorocebus sabaeus")
    )
    await session.commit()

    r = await _launch_smrnaseq(client, admin_token, experiment, samples, genome=None)
    assert "mirtrace_species" not in r.json()["parameters"]


@pytest.mark.asyncio
async def test_launch_run_smrnaseq_refuses_to_pick_between_two_organisms(
    client, admin_token, session, admin_user, experiment, samples, initialized_catalog
):
    """A mixed-organism experiment is not something smrnaseq can be pointed at, and choosing one of
    the two would be a guess that still produces a table."""
    from sqlalchemy import update

    from app.models.sample import Sample

    await _install_smrnaseq(session, admin_user)
    await session.execute(update(Sample).where(Sample.id == samples[0].id).values(organism="Mus musculus"))
    await session.execute(update(Sample).where(Sample.id == samples[1].id).values(organism="Homo sapiens"))
    await session.commit()

    r = await _launch_smrnaseq(client, admin_token, experiment, samples, genome=None)
    assert "mirtrace_species" not in r.json()["parameters"]


# --- the reference a run actually aligns against (plan_2) ---------------------------------------
#
# Verified on the demo before any of these existed: `pipeline_catalog` stores
#   nf-core/rnaseq    {"genome": "GRCh38", ...}
#   nf-core/scrnaseq  {"fasta": "...homo_sapiens...", "gtf": "...Homo_sapiens...", ...}
# and lit_validation writes `parameters_json = {}` on every plan by design. So the paper's own
# reference_genome was captured, shown in the UI, and never reached the pipeline: `launch_run` filled
# `genome` only when it was not already a key, and nf-core/scrnaseq's main.nf says outright that a
# manually provided `--fasta` is not overwritten by the genome attributes.
#
# Study 7 on the demo is a MOUSE scRNA-seq paper sitting at plan_ready. Approving it aligned mouse
# reads against the human primary assembly, completed, and produced a near-empty matrix for the
# verdict machinery to blame on the science.


async def _launch(client, admin_token, experiment, samples, key, parameters=None, genome="GRCh38"):
    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="12345",
        ),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        return await client.post(
            "/api/pipeline-runs",
            json={
                "pipeline_key": key,
                "experiment_id": experiment.id,
                "sample_ids": [s.id for s in samples],
                "parameters": parameters or {},
                "reference_genome": genome,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )


@pytest.mark.asyncio
async def test_scrnaseq_aligns_a_mouse_study_against_the_mouse_genome(
    client, admin_token, experiment, samples, initialized_catalog
):
    """The seeded scrnaseq defaults pin literal human Ensembl URLs, and inside nf-core an explicit
    --fasta beats --genome, so a mouse study aligned against GRCh38 and completed. Deriving the pair
    from the assembly the study declares is the same move `mirtrace_species` already makes."""
    r = await _launch(client, admin_token, experiment, samples, "nf-core/scrnaseq", genome="GRCm39")
    assert r.status_code == 200, r.text
    params = r.json()["parameters"]
    assert "mus_musculus" in params["fasta"], params["fasta"]
    assert "GRCm39" in params["fasta"]
    assert "Mus_musculus" in params["gtf"], params["gtf"]
    assert "homo_sapiens" not in params["fasta"].lower()
    assert "homo_sapiens" not in params["gtf"].lower()


@pytest.mark.asyncio
async def test_scrnaseq_still_aligns_a_human_study_against_the_human_genome(
    client, admin_token, experiment, samples, initialized_catalog
):
    """The regression guard on the case that already worked."""
    r = await _launch(client, admin_token, experiment, samples, "nf-core/scrnaseq", genome="GRCh38")
    assert r.status_code == 200, r.text
    params = r.json()["parameters"]
    assert "homo_sapiens" in params["fasta"]
    assert "GRCh38" in params["fasta"]


@pytest.mark.asyncio
async def test_an_explicitly_supplied_reference_always_wins(
    client, admin_token, experiment, samples, initialized_catalog
):
    """A derived reference is a default, never a policy. A lab that states its own reference keeps
    it, exactly as the smrnaseq parameters do."""
    mine = "gs://my-bucket/custom.fa"
    r = await _launch(
        client,
        admin_token,
        experiment,
        samples,
        "nf-core/scrnaseq",
        parameters={"fasta": mine, "gtf": "gs://my-bucket/custom.gtf"},
        genome="GRCm39",
    )
    assert r.status_code == 200, r.text
    assert r.json()["parameters"]["fasta"] == mine


@pytest.mark.asyncio
async def test_rnaseq_uses_the_studys_genome_rather_than_the_seeded_default(
    client, admin_token, experiment, samples, initialized_catalog
):
    """`genome: GRCh38` in the seeded defaults meant `"genome" not in merged_params` was never true,
    so the branch that fills it from the study never fired. A mouse bulk RNA-seq paper ran against
    GRCh38 while the run row and the UI both said GRCm39."""
    r = await _launch(client, admin_token, experiment, samples, "nf-core/rnaseq", genome="GRCm39")
    assert r.status_code == 200, r.text
    assert r.json()["parameters"]["genome"] == "GRCm39"


@pytest.mark.asyncio
async def test_a_reference_we_cannot_serve_refuses_instead_of_running_against_the_wrong_one(
    client, admin_token, experiment, samples, initialized_catalog
):
    """T2T-CHM13 is a genome the extractor normalizes and the reference table has no entry for. The
    old behaviour was to launch anyway against whatever the defaults pinned. Refusing is the only
    honest answer, and it has to name BOTH assemblies or it cannot be acted on."""
    r = await _launch(client, admin_token, experiment, samples, "nf-core/scrnaseq", genome="T2T-CHM13")
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "T2T-CHM13" in detail
    assert "GRCh38" in detail


@pytest.mark.asyncio
async def test_a_genome_alias_resolves_to_the_assembly_bioaf_carries(
    client, admin_token, experiment, samples, initialized_catalog
):
    """Papers name the same build several ways. `hg38` is GRCh38, so it must be served, not refused:
    the refusal exists for an assembly bioAF genuinely has no reference for, never for a spelling."""
    r = await _launch(client, admin_token, experiment, samples, "nf-core/scrnaseq", genome="hg38")
    assert r.status_code == 200, r.text
    params = r.json()["parameters"]
    assert "homo_sapiens" in params["fasta"]
    assert params["genome"] == "GRCh38"


@pytest.mark.asyncio
async def test_an_organizations_own_named_reference_is_left_alone(
    client, admin_token, experiment, samples, initialized_catalog
):
    """`reference_genome` is not only an assembly field: a lab can name its own uploaded reference
    dataset there. That is not a build bioAF should second-guess, and refusing it would break the
    custom-reference path entirely, so anything outside the assembly vocabulary passes through
    exactly as it did before."""
    r = await _launch(client, admin_token, experiment, samples, "nf-core/scrnaseq", genome="custom-genome-v1")
    assert r.status_code == 200, r.text
    params = r.json()["parameters"]
    assert params["genome"] == "custom-genome-v1"
    # The seeded pair is untouched: bioAF has no opinion about somebody else's reference.
    assert "homo_sapiens" in params["fasta"]


# --- the 10x chemistry a run is parsed with (plan_2 step 3.5) ------------------------------------
#
# `protocol` decides how STARsolo splits the barcode read: 10XV2 is a 16bp barcode + a 10bp UMI,
# 10XV3 is 16 + 12. Reading a v2 library as v3 runs the UMI two bases past the end of the read, and
# STARsolo does not refuse it. nf-core/scrnaseq's own default is 'auto', but its workflow errors with
# "Only cellranger supports `protocol = 'auto'`" for any other aligner, and bioAF runs STARsolo, so
# an explicit protocol has to come from somewhere. It came from a seeded constant: 10XV3, always.


async def _set_chemistry(session, samples, values):
    # Commit, not flush: the API client runs on its own session, so an uncommitted change is
    # invisible to the launch path (which is exactly the annotation the launch path must read).
    for sample, value in zip(samples, values):
        sample.chemistry_version = value
    await session.commit()


@pytest.mark.asyncio
async def test_scrnaseq_uses_the_chemistry_the_samples_declare(
    client, admin_token, session, experiment, samples, initialized_catalog
):
    """The frontend has derived this from `chemistry_version` since the launch page was built
    (`protocolDetection.ts`). The launch path never did, so an experiment annotated v2 was still
    parsed as v3 -- and the validation driver launches with no form at all, so it ALWAYS was."""
    await _set_chemistry(session, samples, ["v2", "v2", "v2"])
    r = await _launch(client, admin_token, experiment, samples, "nf-core/scrnaseq")
    assert r.status_code == 200, r.text
    assert r.json()["parameters"]["protocol"] == "10XV2"


@pytest.mark.asyncio
async def test_scrnaseq_reads_the_long_form_chemistry_spelling_too(
    client, admin_token, session, experiment, samples, initialized_catalog
):
    """Samples are annotated by hand, and the same chemistry is written several ways. Spelled with
    v2 deliberately: asserting v3 would pass on the seeded default alone and prove nothing."""
    await _set_chemistry(session, samples, ["10x Chromium 3' v2", "V2", "10x chromium 5' v2"])
    r = await _launch(client, admin_token, experiment, samples, "nf-core/scrnaseq")
    assert r.status_code == 200, r.text
    assert r.json()["parameters"]["protocol"] == "10XV2"


@pytest.mark.asyncio
async def test_scrnaseq_refuses_a_run_whose_samples_disagree_about_chemistry(
    client, admin_token, session, experiment, samples, initialized_catalog
):
    """STARsolo takes ONE protocol for the whole run, so a v2 sample and a v3 sample cannot both be
    parsed correctly by it. The frontend already answers this with null ("no single right answer");
    the launch path answered it by silently parsing one of them wrongly."""
    await _set_chemistry(session, samples, ["v2", "v3", "v3"])
    r = await _launch(client, admin_token, experiment, samples, "nf-core/scrnaseq")
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "10XV2" in detail and "10XV3" in detail


@pytest.mark.asyncio
async def test_scrnaseq_keeps_the_seeded_protocol_when_nothing_declares_a_chemistry(
    client, admin_token, experiment, samples, initialized_catalog
):
    """Fetched SRA samples carry no chemistry, and refusing there would switch lit_validation's
    scRNA route off entirely. So the seeded default still stands and this is NOT yet solved; the
    honest derivation is the R1 read length (26 vs 28), which plan_2 step 3.5 carries. Pinned here so
    the remaining gap is visible rather than assumed closed."""
    r = await _launch(client, admin_token, experiment, samples, "nf-core/scrnaseq")
    assert r.status_code == 200, r.text
    assert r.json()["parameters"]["protocol"] == "10XV3"


@pytest.mark.asyncio
async def test_an_explicitly_supplied_protocol_always_wins(
    client, admin_token, session, experiment, samples, initialized_catalog
):
    """A derived protocol is a default, never a policy, exactly like the reference and the smrnaseq
    parameters."""
    await _set_chemistry(session, samples, ["v2", "v2", "v2"])
    r = await _launch(client, admin_token, experiment, samples, "nf-core/scrnaseq", parameters={"protocol": "10XV4"})
    assert r.status_code == 200, r.text
    assert r.json()["parameters"]["protocol"] == "10XV4"


_FRONTEND_PROTOCOL_MAP = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "components"
    / "pipelines"
    / "protocolDetection.ts"
)


@pytest.mark.skipif(not _FRONTEND_PROTOCOL_MAP.exists(), reason="frontend tree not present (deployed backend image)")
def test_the_two_chemistry_maps_do_not_drift():
    """The chemistry -> protocol mapping exists twice on purpose: once in the browser bundle for the
    interactive launch page, once in the launch path that decides what a run actually submits. They
    are not shared, so nothing but this stops one from gaining a spelling the other does not have --
    and a spelling that resolves in the form but not at launch is a run parsed with the wrong barcode
    length, with no error anywhere."""
    from app.services.pipeline_run_service import _CHEMISTRY_TO_PROTOCOL

    source = _FRONTEND_PROTOCOL_MAP.read_text()
    body = source.split("CHEMISTRY_TO_PROTOCOL", 1)[1].split("};", 1)[0]
    frontend = dict(re.findall(r'"([^"]+)":\s*"([^"]+)"', body))

    assert frontend, "could not parse the frontend map; this test is now lying"
    assert frontend == _CHEMISTRY_TO_PROTOCOL, (
        "the frontend and launch-path chemistry maps have drifted: "
        f"only in frontend={set(frontend) - set(_CHEMISTRY_TO_PROTOCOL)}, "
        f"only in backend={set(_CHEMISTRY_TO_PROTOCOL) - set(frontend)}"
    )
