"""CLI script to register pipeline output files for completed runs.

Backfills File records for pipeline runs whose outputs exist in storage
but were never registered in the database. Each run is collected from its
own authorized output location only (RunOutputLocationService).

Usage:
    python -m app.cli.register_outputs              # all completed runs
    python -m app.cli.register_outputs --run-id 42  # specific run
    python -m app.cli.register_outputs --force      # also runs that already have files

Webhook events (01C.2). A newly committed File announces ``file.registered`` (and ``file.ready`` when
it can be verified immediately) exactly as on the server, even for a months-old run; the event times
are now, not the run's. This process has its own engine and event bus, so it installs the webhook
dispatcher bound to its own session factory before changing anything, commits each batch, waits for
the deferred events to be dispatched, and checks a receipt for every event the commit published:
delivery rows committed, or no matching subscription (both successes). A failed or missing receipt,
or a drain that does not finish in time, makes the command exit nonzero and names the affected files.
That is a notification failure on top of a registration that is already committed: rerunning the
command does not reconstruct it, and readiness is never reset to force another event. Receivers are
contacted later by the serving process, through its destination checks. Checksum work that needs a
full read is left durable for the server's verification worker, which produces ``file.ready``.

An output that matches more than one unreconciled legacy File is refused as
``source_identity_ambiguous`` (no File, work, or event); the command names it and exits nonzero.
"""

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import runtime
from app.adapters.registry import get_storage_adapter, initialize_adapters
from app.models.file import File
from app.models.file_verification import FileVerification
from app.models.pipeline_run import PipelineRun
from app.services import webhook_dispatcher
from app.services.pipeline_output_service import PipelineOutputService
from app.services.post_commit_events import pending_event_types, wait_for_deferred_events
from app.services.run_output_location_service import OutputLocationRefused, RunOutputLocationService

# How long the command waits, after a commit, for its deferred webhook events to be persisted.
DRAIN_SECONDS = 60.0


def _engine_options() -> dict:
    """Extra engine options (a test seam; production uses none)."""
    return {}


async def register_outputs_for_run(
    session: AsyncSession,
    run: PipelineRun,
) -> int:
    """Collect and register output files for a single pipeline run.

    Returns the number of newly registered files.
    """
    return len(await _register_run(session, run))


async def _register_run(
    session: AsyncSession,
    run: PipelineRun,
    ambiguous: set[str] | None = None,
    resumed: list[File] | None = None,
) -> list[File]:
    """Collect and register one run's outputs; the newly registered files.

    URIs refused as ``source_identity_ambiguous`` are added to ``ambiguous``."""
    storage_adapter = get_storage_adapter()
    try:
        outdir = await RunOutputLocationService.authorized_uri(session, run, storage_adapter)
    except OutputLocationRefused as e:
        print(f"  Run {run.id}: not collected -- {e}")
        return []

    try:
        collected = await storage_adapter.collect_outputs(
            outdir,
            {"id": run.id, "experiment_id": run.experiment_id},
        )
    except Exception as e:
        print(f"  Run {run.id}: could not collect outputs from {outdir} -- {e}")
        return []
    if not collected:
        return []

    return await PipelineOutputService.register_outputs(
        session,
        run,
        [
            {
                "filename": f.filename,
                "gcs_uri": f.storage_uri,
                "size_bytes": f.size_bytes,
                "md5_hash": f.md5_hash,
            }
            for f in collected
        ],
        ambiguous=ambiguous,
        resumed=resumed,
    )


@dataclass
class _Report:
    registered: list[int] = field(default_factory=list)
    # Already-registered outputs this run started on their way to readiness.
    existing: list[File] = field(default_factory=list)
    deliveries: int = 0
    no_subscribers: int = 0
    failures: list[str] = field(default_factory=list)
    incomplete: bool = False
    # Outputs refused because they match more than one unreconciled legacy File.
    ambiguous: set[str] = field(default_factory=set)


_MAPPED = frozenset(webhook_dispatcher.INTERNAL_EVENTS)


async def _commit_and_drain(session: AsyncSession, binding, owner: runtime.TaskOwner, report: _Report) -> None:
    """Commit, wait for this commit's events to be dispatched, and account for each one."""
    expected = [t for t in pending_event_types(session) if t in _MAPPED]
    before = len(binding.receipts)
    await session.commit()
    finished = await owner.drain_events(DRAIN_SECONDS)
    if finished:
        try:
            await asyncio.wait_for(wait_for_deferred_events(), timeout=DRAIN_SECONDS)
        except TimeoutError:
            finished = False
    receipts = binding.receipts[before:]
    for receipt in receipts:
        if receipt.outcome == "persisted":
            report.deliveries += receipt.deliveries
        elif receipt.outcome == "no_matching_subscriptions":
            report.no_subscribers += 1
        else:
            subject = f"{receipt.subject[0]} {receipt.subject[1]}" if receipt.subject else "unknown subject"
            report.failures.append(f"{receipt.public_event} for {subject}: {receipt.code}")
    if not finished:
        report.incomplete = True
        report.failures.append("event dispatch did not finish before the deadline")
    elif len(receipts) < len(expected):
        report.failures.append(f"{len(expected) - len(receipts)} committed event(s) produced no dispatch receipt")


async def _verification_counts(factory, file_ids: list[int]) -> tuple[int, int]:
    if not file_ids:
        return 0, 0
    async with factory() as s:
        ready = len((await s.execute(select(File.id).where(File.id.in_(file_ids), File.ready_at.is_not(None)))).all())
        pending = len(
            (
                await s.execute(
                    select(FileVerification.id).where(
                        FileVerification.file_id.in_(file_ids),
                        FileVerification.status.in_(["waiting_expectation", "queued", "preparing", "verifying"]),
                    )
                )
            ).all()
        )
    return ready, pending


async def _main(args: argparse.Namespace) -> int:
    database_url = os.environ.get("BIOAF_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: Set BIOAF_DATABASE_URL or DATABASE_URL environment variable.")
        return 1

    engine = create_async_engine(database_url, echo=False, **_engine_options())
    base_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    owner = runtime.TaskOwner(name="register-outputs")
    factory = runtime.owned_sessions(base_factory, owner)
    # The dispatcher writes delivery rows through this command's own database, never the
    # application's global factory (which this process does not configure).
    binding = webhook_dispatcher.subscribe_all(session_factory=base_factory, collect_receipts=True)
    report = _Report()
    status = 0
    try:
        with runtime.bound(owner):
            async with factory() as session:
                await initialize_adapters(session, session_factory=factory)
                if args.run_id:
                    run = (
                        await session.execute(select(PipelineRun).where(PipelineRun.id == args.run_id))
                    ).scalar_one_or_none()
                    if not run:
                        print(f"ERROR: Pipeline run {args.run_id} not found.")
                        return 1
                    if run.status not in ("completed", "failed"):
                        print(f"WARNING: Run {run.id} has status '{run.status}', skipping.")
                        return 0
                    try:
                        files = await _register_run(session, run, report.ambiguous, report.existing)
                        report.registered += [f.id for f in files]
                        await _commit_and_drain(session, binding, owner, report)
                    except Exception as exc:
                        await session.rollback()
                        print(
                            f"ERROR: Run {args.run_id}: registration failed and nothing was committed "
                            f"({type(exc).__name__})."
                        )
                        return 1
                    print(f"Run {args.run_id}: registered {len(report.registered)} output files.")
                else:
                    runs = list(
                        (
                            await session.execute(
                                select(PipelineRun).where(PipelineRun.status.in_(["completed", "failed"]))
                            )
                        )
                        .scalars()
                        .all()
                    )
                    if not runs:
                        print("No completed pipeline runs found.")
                        return 0
                    for run in runs:
                        existing = await session.execute(
                            select(File.id)
                            .where(File.source_pipeline_run_id == run.id, File.source_type == "pipeline_output")
                            .limit(1)
                        )
                        if existing.first() and not args.force:
                            continue
                        files = await _register_run(session, run, report.ambiguous, report.existing)
                        if files:
                            print(f"  Run {run.id} ({run.pipeline_name}): registered {len(files)} files")
                            report.registered += [f.id for f in files]
                    try:
                        await _commit_and_drain(session, binding, owner, report)
                    except Exception as exc:
                        await session.rollback()
                        print(f"ERROR: registration failed and nothing was committed ({type(exc).__name__}).")
                        return 1
                    print(f"\nDone. Registered {len(report.registered)} files across {len(runs)} runs.")

        ready, pending = await _verification_counts(base_factory, report.registered)
        existing_ready, existing_pending = await _verification_counts(base_factory, [f.id for f in report.existing])
        print(
            f"New files registered: {len(report.registered)} (ready: {ready}, "
            f"waiting for checksum verification: {pending})."
        )
        print(
            f"Existing files made ready: {existing_ready}"
            + (f" (waiting for checksum verification: {existing_pending})" if existing_pending else "")
            + "."
        )
        print(
            f"Webhook deliveries recorded: {report.deliveries}"
            + (
                f"; events with no matching webhook subscription: {report.no_subscribers}"
                if report.no_subscribers
                else ""
            )
            + "."
        )
        if pending or existing_pending:
            print("Checksum verification of the waiting files continues in the bioAF server's background worker.")
        if report.ambiguous:
            status = 1
            print(
                "ERROR: these outputs were not registered (source_identity_ambiguous): each matches more than "
                "one existing file record. Reconcile the duplicate records, then rerun this command:"
            )
            for uri in sorted(report.ambiguous):
                print(f"  - {uri}")
        if report.failures:
            status = 1
            print(
                "ERROR: the file registrations above are already committed, but their webhook notifications are "
                "incomplete:"
            )
            for failure in report.failures:
                print(f"  - {failure}")
            print(
                f"  Affected file IDs: {', '.join(str(i) for i in report.registered) or 'none'}. Rerunning this "
                "command or replaying deliveries does not recreate a notification that was never persisted; "
                "reconcile these files with your LIMS."
            )
        return status
    finally:
        await owner.drain_events(DRAIN_SECONDS)
        binding.close()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Register pipeline output files from storage into the database")
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
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
