"""CLI script to register pipeline output files for completed runs.

Backfills File records for pipeline runs whose outputs exist in GCS
but were never registered in the database.

Usage:
    python -m app.cli.register_outputs              # all completed runs
    python -m app.cli.register_outputs --run-id 42  # specific run
"""

import argparse
import asyncio
import os
import sys

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.adapters.registry import get_storage_adapter, initialize_adapters
from app.models.file import File
from app.models.pipeline_run import PipelineRun
from app.services.pipeline_output_service import PipelineOutputService


async def _resolve_outdir(session: AsyncSession, run: PipelineRun) -> str:
    """Resolve the GCS output directory for a pipeline run."""
    # Prefer the outdir stored in parameters_json (set by K8s adapter at launch)
    outdir = (run.parameters_json or {}).get("outdir", "")
    if outdir:
        return outdir

    # Fall back: build the GCS URI from results_bucket_name in platform_config
    result = await session.execute(text("SELECT value FROM platform_config WHERE key = 'results_bucket_name'"))
    row = result.first()
    if row:
        return f"gs://{row[0]}/experiments/{run.experiment_id}/pipeline-runs/{run.id}"

    # Last resort: local-style path (GCS adapter will use its default bucket)
    return f"/data/results/experiments/{run.experiment_id}/pipeline-runs/{run.id}"


async def register_outputs_for_run(
    session: AsyncSession,
    run: PipelineRun,
) -> int:
    """Collect and register output files for a single pipeline run.

    Returns the number of newly registered files.
    """
    storage_adapter = get_storage_adapter()
    outdir = await _resolve_outdir(session, run)

    try:
        collected = await storage_adapter.collect_outputs(
            outdir,
            {"id": run.id, "experiment_id": run.experiment_id},
        )
    except Exception as e:
        print(f"  Run {run.id}: could not collect outputs from {outdir} -- {e}")
        return 0
    if not collected:
        return 0

    files = await PipelineOutputService.register_outputs(session, run, collected)
    return len(files)


async def _main(args: argparse.Namespace) -> None:
    database_url = os.environ.get("BIOAF_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: Set BIOAF_DATABASE_URL or DATABASE_URL environment variable.")
        sys.exit(1)

    engine = create_async_engine(database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        await initialize_adapters(session, session_factory=factory)

        if args.run_id:
            # Register outputs for a specific run
            result = await session.execute(select(PipelineRun).where(PipelineRun.id == args.run_id))
            run = result.scalar_one_or_none()
            if not run:
                print(f"ERROR: Pipeline run {args.run_id} not found.")
                sys.exit(1)
            if run.status not in ("completed", "failed"):
                print(f"WARNING: Run {run.id} has status '{run.status}', skipping.")
                sys.exit(0)

            count = await register_outputs_for_run(session, run)
            await session.commit()
            print(f"Run {run.id}: registered {count} output files.")
        else:
            # Find all completed runs missing output File records
            result = await session.execute(
                select(PipelineRun).where(
                    PipelineRun.status.in_(["completed", "failed"]),
                )
            )
            runs = list(result.scalars().all())

            if not runs:
                print("No completed pipeline runs found.")
                await engine.dispose()
                return

            total_registered = 0
            for run in runs:
                # Check if this run already has registered output files
                existing = await session.execute(
                    select(File.id)
                    .where(
                        File.source_pipeline_run_id == run.id,
                        File.source_type == "pipeline_output",
                    )
                    .limit(1)
                )
                if existing.first():
                    if not args.force:
                        continue

                count = await register_outputs_for_run(session, run)
                if count > 0:
                    print(f"  Run {run.id} ({run.pipeline_name}): registered {count} files")
                    total_registered += count

            await session.commit()
            print(f"\nDone. Registered {total_registered} files across {len(runs)} runs.")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Register pipeline output files from GCS into the database")
    parser.add_argument(
        "--run-id",
        type=int,
        default=None,
        help="Register outputs for a specific pipeline run ID",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-register outputs even for runs that already have File records",
    )
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
