"""Seed the 10x `bamtofastq` converter as a runnable artifact.

A large share of pre-2019 10x Genomics data is deposited as an aligned CellRanger BAM rather than as
raw FASTQ. SRA's normalized view of such a deposit is a single cDNA read with **no cell barcode**: the
barcode and UMI survive only in the BAM's ``CR``/``CQ`` and ``UR``/``UQ`` tags. 10x's ``bamtofastq``
is the tool that rebuilds the original R1/R2/I1 reads from those tags, and without it that data
cannot be re-analysed at all.

Generic converters are not substitutes. ``samtools fastq`` and nf-core/bamtofastq reconstruct *reads*;
here the barcode was never a read, so they return the same barcode-less cDNA that SRA already serves.

Nothing new is built for this. bioAF already models "a container you can run" as a pipeline
Environment plus a custom pipeline, and the conda -> Dockerfile build template already installs the
cloud CLI that the entrypoint's output-sync trap needs. So this is one conda definition and one
pipeline definition, seeded idempotently the same way the default environments are.
"""

import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.custom_pipeline import CustomPipeline
from app.models.custom_pipeline_variable import CustomPipelineVariable
from app.models.custom_pipeline_version import CustomPipelineVersion
from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion

logger = logging.getLogger("bioaf.bootstrap_bamtofastq")

BAMTOFASTQ_ENVIRONMENT_NAME = "10x bamtofastq"
BAMTOFASTQ_PIPELINE_KEY = "10x-bamtofastq"

# Pinned rather than floating: a converter that silently changes version between two runs of the same
# archival dataset produces two different sets of "original" reads, which is the one property this
# tool exists to guarantee.
BAMTOFASTQ_CONDA_YML = """\
name: bioaf-bamtofastq
channels:
  - conda-forge
  - bioconda
dependencies:
  - 10x_bamtofastq=1.4.1
"""

# `bamtofastq` writes into a directory it creates, so the target must not already exist; /outputs does
# (it is the mounted results volume), hence the subdirectory. Its contents are synced to the results
# bucket by the entrypoint wrapper's EXIT trap, which is why the image needs the cloud CLI.
#
# The BAM is whatever was staged under /data by the input-staging init container. Finding it rather
# than naming it keeps the pipeline usable with any input file the scientist attaches. `sort` makes
# the pick deterministic when more than one is present, and the count is printed so a run against an
# unintended second BAM is visible in the log rather than silent.
BAMTOFASTQ_ENTRYPOINT = """\
BAM=$(find /data -type f -name '*.bam' | sort | head -n 1)
if [ -z "$BAM" ]; then
  echo "bamtofastq: no .bam file was staged under /data; attach the 10x BAM as an input file" >&2
  exit 1
fi
echo "bamtofastq: $(find /data -type f -name '*.bam' | wc -l) BAM file(s) staged; converting $BAM"
bamtofastq --version
bamtofastq $PARAM_BAMTOFASTQ_ARGS "$BAM" /outputs/fastq
echo "bamtofastq: wrote"
find /outputs -type f -name '*.fastq.gz' | sort
"""

_DESCRIPTION = (
    "Rebuild the original 10x FASTQ reads from an aligned CellRanger/Space Ranger/Long Ranger BAM. "
    "Use this when a dataset is deposited as a BAM rather than as raw reads, which is common for "
    "pre-2019 10x submissions: the cell barcode and UMI live in the BAM's tags, so a generic BAM to "
    "FASTQ converter returns cDNA reads with no barcode. For a BAM from CellRanger 1.0-1.1 (no "
    "'10x_bam_to_fastq' header comment) set bamtofastq_args to --cr11; for GemCode/Long Ranger 1.x "
    "use --gemcode. A modern BAM declares its own layout and needs no flag."
)


async def ensure_bamtofastq_pipeline(session: AsyncSession) -> None:
    """Create the 10x bamtofastq environment and pipeline if they do not exist yet. Idempotent."""
    org_row = (await session.execute(text("SELECT id FROM organizations LIMIT 1"))).fetchone()
    if not org_row:
        return
    org_id = org_row[0]

    admin_row = (
        await session.execute(
            text(
                "SELECT u.id FROM users u "
                "JOIN roles r ON u.role_id = r.id "
                "WHERE u.organization_id = :org_id AND r.name = 'admin' "
                "ORDER BY u.id LIMIT 1"
            ).bindparams(org_id=org_id)
        )
    ).fetchone()
    if not admin_row:
        return
    user_id = admin_row[0]

    env_version = await _ensure_environment(session, org_id, user_id)
    await _ensure_pipeline(session, org_id, user_id, env_version)


async def _ensure_environment(session: AsyncSession, org_id: int, user_id: int) -> EnvironmentVersion:
    env = (
        await session.execute(
            select(Environment).where(
                Environment.organization_id == org_id,
                Environment.name == BAMTOFASTQ_ENVIRONMENT_NAME,
            )
        )
    ).scalar_one_or_none()

    if env is None:
        env = Environment(
            name=BAMTOFASTQ_ENVIRONMENT_NAME,
            description="Container image carrying 10x Genomics bamtofastq, for rebuilding FASTQ from a 10x BAM.",
            organization_id=org_id,
            created_by_user_id=user_id,
            visibility="organization",
            environment_type="pipeline",
        )
        session.add(env)
        await session.flush()
        logger.info("Created the 10x bamtofastq environment (id=%d)", env.id)

    version = (
        (
            await session.execute(
                select(EnvironmentVersion)
                .where(EnvironmentVersion.environment_id == env.id)
                .order_by(EnvironmentVersion.version_number)
            )
        )
        .scalars()
        .first()
    )

    if version is None:
        # Draft, like every other seeded environment: the image does not exist until someone builds
        # it. Marking it ready would let a launch fail on a missing image instead of saying "build
        # this first".
        version = EnvironmentVersion(
            environment_id=env.id,
            version_number=1,
            build_number=1,
            status="draft",
            definition_format="conda",
            definition_content=BAMTOFASTQ_CONDA_YML,
            created_by_user_id=user_id,
        )
        session.add(version)
        await session.flush()

    return version


async def _ensure_pipeline(session: AsyncSession, org_id: int, user_id: int, env_version: EnvironmentVersion) -> None:
    pipeline = (
        await session.execute(
            select(CustomPipeline).where(
                CustomPipeline.organization_id == org_id,
                CustomPipeline.pipeline_key == BAMTOFASTQ_PIPELINE_KEY,
            )
        )
    ).scalar_one_or_none()

    if pipeline is None:
        pipeline = CustomPipeline(
            organization_id=org_id,
            name="10x bamtofastq",
            description=_DESCRIPTION,
            pipeline_key=BAMTOFASTQ_PIPELINE_KEY,
            created_by_user_id=user_id,
        )
        session.add(pipeline)
        await session.flush()
        logger.info("Created the 10x bamtofastq pipeline (id=%d)", pipeline.id)

    version = (
        (
            await session.execute(
                select(CustomPipelineVersion)
                .where(CustomPipelineVersion.custom_pipeline_id == pipeline.id)
                .order_by(CustomPipelineVersion.version_number)
            )
        )
        .scalars()
        .first()
    )
    if version is not None:
        return

    version = CustomPipelineVersion(
        custom_pipeline_id=pipeline.id,
        version_number=1,
        # No code to fetch: the binary is in the image.
        code_source_type="inline",
        entrypoint_command=BAMTOFASTQ_ENTRYPOINT,
        environment_version_id=env_version.id,
        # A 10x BAM is tens of GB and the conversion is I/O bound rather than parallel, so the
        # defaults elsewhere (2 CPU / 8Gi) are the wrong shape; give it room to stream.
        cpu_request="4",
        memory_request="16Gi",
        status="active",
        created_by_user_id=user_id,
    )
    session.add(version)
    await session.flush()

    session.add(
        CustomPipelineVariable(
            custom_pipeline_version_id=version.id,
            variable_name="bamtofastq_args",
            default_value="",
            variable_type="string",
            # Optional: a BAM written by a recent pipeline carries a `10x_bam_to_fastq` header comment
            # that declares its own read layout. The legacy flags exist for the ones that do not.
            is_required=False,
        )
    )
    await session.flush()
