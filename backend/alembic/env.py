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
    Batch,
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

# Set up logging from the config file
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

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


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
