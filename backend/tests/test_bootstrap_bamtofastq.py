"""The 10x bamtofastq converter, seeded as a runnable artifact.

A large share of pre-2019 10x data is deposited as an aligned CellRanger BAM rather than raw FASTQ.
SRA's normalized view of such a deposit is a single cDNA read with NO cell barcode: the barcode and
UMI survive only in the BAM's CR/CQ and UR/UQ tags. 10x's `bamtofastq` is the only tool that rebuilds
the original reads from those tags, so without it that data is unreachable.

This seeds it the way bioAF already models "a container you can run": a pipeline Environment whose
conda definition installs the bioconda package, plus a custom pipeline that invokes it. Generic
converters (`samtools fastq`, nf-core/bamtofastq) are NOT substitutes: they reconstruct reads, and
here the barcode was never a read.
"""

import pytest
from sqlalchemy import select

from app.models.custom_pipeline import CustomPipeline
from app.models.custom_pipeline_version import CustomPipelineVersion
from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.services.bootstrap_bamtofastq import (
    BAMTOFASTQ_ENVIRONMENT_NAME,
    BAMTOFASTQ_PIPELINE_KEY,
    ensure_bamtofastq_pipeline,
)


async def _env(session, org_id):
    return (
        await session.execute(
            select(Environment).where(
                Environment.organization_id == org_id,
                Environment.name == BAMTOFASTQ_ENVIRONMENT_NAME,
            )
        )
    ).scalar_one_or_none()


async def _pipeline(session, org_id):
    return (
        await session.execute(
            select(CustomPipeline).where(
                CustomPipeline.organization_id == org_id,
                CustomPipeline.pipeline_key == BAMTOFASTQ_PIPELINE_KEY,
            )
        )
    ).scalar_one_or_none()


@pytest.mark.asyncio
async def test_seeding_creates_a_pipeline_environment_that_installs_the_tool(session, admin_user):
    await ensure_bamtofastq_pipeline(session)

    env = await _env(session, admin_user.organization_id)
    assert env is not None
    # A pipeline environment, so it builds through the conda -> Dockerfile path whose template also
    # installs the cloud CLI. Without that CLI in the image the entrypoint's output-sync trap silently
    # no-ops and the run "succeeds" having produced nothing.
    assert env.environment_type == "pipeline"
    assert env.visibility == "organization"

    version = (
        (await session.execute(select(EnvironmentVersion).where(EnvironmentVersion.environment_id == env.id)))
        .scalars()
        .first()
    )
    assert version is not None
    assert version.definition_format == "conda"
    assert "bioconda" in version.definition_content
    assert "10x_bamtofastq" in version.definition_content
    # Draft, exactly like every other seeded environment: the image does not exist until someone
    # builds it, and claiming otherwise would let a launch fail with a missing image instead of a
    # clear "build this first".
    assert version.status == "draft"


@pytest.mark.asyncio
async def test_seeding_creates_a_launchable_custom_pipeline_on_that_environment(session, admin_user):
    await ensure_bamtofastq_pipeline(session)

    pipeline = await _pipeline(session, admin_user.organization_id)
    assert pipeline is not None

    version = (
        (
            await session.execute(
                select(CustomPipelineVersion).where(CustomPipelineVersion.custom_pipeline_id == pipeline.id)
            )
        )
        .scalars()
        .first()
    )
    assert version is not None
    assert version.status == "active"
    # `inline` means no code directory is materialized: the container already carries the binary, so
    # there is nothing to clone or write.
    assert version.code_source_type == "inline"

    env = await _env(session, admin_user.organization_id)
    env_version = (
        (await session.execute(select(EnvironmentVersion).where(EnvironmentVersion.environment_id == env.id)))
        .scalars()
        .first()
    )
    assert version.environment_version_id == env_version.id


@pytest.mark.asyncio
async def test_the_entrypoint_refuses_when_no_bam_was_staged(session, admin_user):
    """A converter that finds no input must say so and fail. Exiting 0 having written nothing would
    look like a successful conversion and hand the next step an empty directory."""
    await ensure_bamtofastq_pipeline(session)
    pipeline = await _pipeline(session, admin_user.organization_id)
    version = (
        (
            await session.execute(
                select(CustomPipelineVersion).where(CustomPipelineVersion.custom_pipeline_id == pipeline.id)
            )
        )
        .scalars()
        .first()
    )

    cmd = version.entrypoint_command
    assert ".bam" in cmd
    assert "exit 1" in cmd
    assert "/outputs" in cmd


@pytest.mark.asyncio
async def test_the_pipeline_exposes_the_legacy_cellranger_flag(session, admin_user):
    """A BAM from CellRanger 1.0-1.1 carries no `10x_bam_to_fastq` @CO declaration, so bamtofastq
    cannot infer the read layout and needs `--cr11` (or `--gemcode` for GemCode/Longranger). Without
    a way to pass it, exactly the archival deposits this exists for stay unreachable."""
    await ensure_bamtofastq_pipeline(session)
    pipeline = await _pipeline(session, admin_user.organization_id)
    version = (
        (
            await session.execute(
                select(CustomPipelineVersion).where(CustomPipelineVersion.custom_pipeline_id == pipeline.id)
            )
        )
        .scalars()
        .first()
    )
    await session.refresh(version, ["variables"])

    names = {v.variable_name for v in version.variables}
    assert "bamtofastq_args" in names
    args = next(v for v in version.variables if v.variable_name == "bamtofastq_args")
    assert args.is_required is False  # a modern BAM declares its own layout and needs no flag
    # The entrypoint must actually pass the variable through, or setting it does nothing.
    assert "PARAM_BAMTOFASTQ_ARGS" in version.entrypoint_command


@pytest.mark.asyncio
async def test_seeding_twice_is_idempotent(session, admin_user):
    await ensure_bamtofastq_pipeline(session)
    await ensure_bamtofastq_pipeline(session)

    envs = (
        (
            await session.execute(
                select(Environment).where(
                    Environment.organization_id == admin_user.organization_id,
                    Environment.name == BAMTOFASTQ_ENVIRONMENT_NAME,
                )
            )
        )
        .scalars()
        .all()
    )
    pipelines = (
        (
            await session.execute(
                select(CustomPipeline).where(
                    CustomPipeline.organization_id == admin_user.organization_id,
                    CustomPipeline.pipeline_key == BAMTOFASTQ_PIPELINE_KEY,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(envs) == 1
    assert len(pipelines) == 1


@pytest.mark.asyncio
async def test_the_request_fits_the_pool_the_pod_is_pinned_to(session, admin_user):
    """Every pipeline job pod is pinned to `bioaf.io/pool=pipeline-head` (kubernetes.py sets that
    nodeSelector and the matching toleration), and that pool is e2-standard-2: 1930m CPU and ~6.1Gi
    ALLOCATABLE once the kubelet takes its cut. A request above either number is not "slow to
    schedule", it is unschedulable forever, and the pod sits Pending while the run reports `running`.

    bamtofastq streams a BAM and is I/O bound rather than parallel, so it does not need the headroom.
    """
    await ensure_bamtofastq_pipeline(session)
    pipeline = await _pipeline(session, admin_user.organization_id)
    version = (
        (
            await session.execute(
                select(CustomPipelineVersion).where(CustomPipelineVersion.custom_pipeline_id == pipeline.id)
            )
        )
        .scalars()
        .first()
    )

    assert version.cpu_request.endswith("m") is False  # plain cores, as the column's other rows use
    assert float(version.cpu_request) <= 1.5
    assert version.memory_request.endswith("Gi")
    assert int(version.memory_request.removesuffix("Gi")) <= 5


@pytest.mark.asyncio
async def test_the_entrypoint_only_invokes_the_converter_to_convert(session, admin_user):
    """bamtofastq 1.4.1 takes `<bam> <output-path>` and rejects `--version`; it prints its version
    banner on every invocation regardless. A diagnostic call to it exits non-zero, and the wrapper
    runs under `set -e`, so the conversion never happens and the run fails with an empty /outputs."""
    await ensure_bamtofastq_pipeline(session)
    pipeline = await _pipeline(session, admin_user.organization_id)
    version = (
        (
            await session.execute(
                select(CustomPipelineVersion).where(CustomPipelineVersion.custom_pipeline_id == pipeline.id)
            )
        )
        .scalars()
        .first()
    )

    calls = [ln.strip() for ln in version.entrypoint_command.splitlines() if ln.strip().startswith("bamtofastq")]
    assert len(calls) == 1, f"expected exactly one bamtofastq invocation, got {calls}"
    assert "--version" not in version.entrypoint_command
    assert "/outputs/fastq" in calls[0]
