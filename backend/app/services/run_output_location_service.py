"""The one place a loaded pipeline run's outputs may be read from, registered from, or deleted.

The monitor, the registration CLI, the validation driver and the validation reaper each used to
re-derive this, and each trusted ``parameters_json.outdir`` when it was set. That value came from
the launch request, so it could name another bucket or another run's directory, and the data found
there was collected, registered under this run, and later deleted as this run's. Now the location
comes from the run's identity (see ``app.platform.run_output_layout``), and a stored ``outdir`` is
history to be checked: equal to the run's own location, or refused before any storage access.
"""

from __future__ import annotations


from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_app_logger
from app.adapters.models import StorageStore
from app.adapters.registry import get_storage_adapter
from app.exceptions import ValidationError
from app.platform.platform_config_service import PlatformConfigService
from app.platform.run_output_layout import directory_prefix, results_bucket_from_config, run_output_key

logger = get_app_logger(__name__)


class OutputLocationRefused(ValidationError):
    """A run's output location could not be authorized; nothing may be read, registered or deleted."""


class RunOutputLocationService:
    @staticmethod
    async def authorized_uri(session: AsyncSession, run, storage=None) -> str:
        """The run's output directory URI (no trailing separator), or ``OutputLocationRefused``.

        A catalog run's stored ``outdir``, when present, must name this same directory; a custom
        pipeline run's ``parameters_json`` holds its user variables, so it is never read here.
        """
        storage = storage or get_storage_adapter()
        try:
            key = run_output_key(run_id=run.id, experiment_id=run.experiment_id, project_id=run.project_id)
        except ValueError as e:
            raise OutputLocationRefused(str(e)) from e

        config = await PlatformConfigService.get_many(session, ["results_bucket_name", "raw_bucket_name"])
        bucket = results_bucket_from_config(config)
        try:
            expected = (
                storage.build_uri(bucket, key) if bucket else await storage.resolve_uri(StorageStore.RESULTS, key)
            )
        except ValidationError as e:
            raise OutputLocationRefused(f"Run {run.id} has no results store: {e}") from e

        if run.custom_pipeline_version_id is None:
            stored = (run.parameters_json or {}).get("outdir")
            if stored is not None and directory_prefix(str(stored)) != directory_prefix(expected):
                logger.warning("Run %s: stored outdir %r is not its output location %r", run.id, stored, expected)
                raise OutputLocationRefused(f"Run {run.id} records an output location other than its own")
        return expected
