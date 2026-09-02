"""Alembic async environment configuration for bioAF."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.database import Base

# Import all models so their metadata is registered with Base
from app.models import (  # noqa: F401
    AuditLog,
    SampleBatch,
    SequencingBatch,
    ManifestEntry,
    EntitySnapshot,
    CellxgenePublication,
    ComponentState,
    Document,
    Environment,
    EnvironmentVersion,
    Experiment,
    ExperimentCustomField,
    ExperimentTemplate,
    File,
    GitOpsRepo,
    NotebookSession,
    Organization,
    PipelineCatalogEntry,
    PipelineProcess,
    PipelineRun,
    PipelineRunSample,
    PlatformConfig,
    PlotArchiveEntry,
    Project,
    QCDashboard,
    Sample,
    SlurmJob,
    StorageStatsCache,
    TemplateNotebook,
    TerraformRun,
    User,
    UserQuota,
    VerificationCode,
    ControlledVocabulary,
    PipelineRunReview,
)

# Alembic Config object
config = context.config

# Set up logging from the config file.
#
# `disable_existing_loggers=False` is load-bearing, not a default worth inheriting. fileConfig
# defaults it to True, which DISABLES every logger that already exists, including all of bioaf's.
# `./bioaf migrate` runs alembic in its own process so production never notices, but the test suite
# imports this module in-process: after test_migrations_apply.py runs, every `bioaf.*` logger was
# dead for the rest of the session and any later test asserting on a log line silently saw nothing
# and blamed itself. Two tests in test_validation_extraction.py failed exactly this way, and
# test_pipeline_output_service.py carries a hand-rolled workaround for the same cause.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Set the SQLAlchemy URL from app settings
config.set_main_option("sqlalchemy.url", settings.database_url)

# Target metadata for autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with just a URL and not an Engine.
    Calls to context.execute() emit the given string to the script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations using the provided connection."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with an async engine."""
    connectable = create_async_engine(
        settings.database_url,
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


# A caller may hand us an already-open (sync) Connection through
# `config.attributes`. Alembic documents this for driving migrations from a
# test, which is the only user here: it lets the suite run `upgrade head`
# against a throwaway schema on a connection it controls, instead of letting
# env.py build its own engine from settings.database_url and reach for the
# real database. Nothing sets this attribute in production, so the deploy path
# below is unchanged.
_injected_connection = config.attributes.get("connection", None)

if _injected_connection is not None:
    do_run_migrations(_injected_connection)
elif context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
