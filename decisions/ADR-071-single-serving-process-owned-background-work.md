# ADR-071: Single serving process with owned background work

**Status:** Proposed
**Date:** 2026-11-14
**Deciders:** Brent (repository owner)
**Supersedes:** in [ADR-021](ADR-021-kubernetes-compute-backend.md), the assumption that "bioAF already runs
its control plane on GKE". The application control plane runs on the application host, not on the cluster.
ADR-021's decision to use Kubernetes as the compute backend for pipeline workloads stands.
**Related:** [ADR-010](ADR-010-notification-system.md) (in-process event bus and notification router),
[ADR-051](ADR-051-outbound-webhook-delivery.md) (persisted webhook delivery),
[ADR-046](ADR-046-pipeline-version-cascade.md) (cascade via the event bus)

## Context

The backend does far more than answer requests. In the same process it runs the event bus, 27 periodic
jobs (`app/tasks/jobs.py`), the Pub/Sub auto-ingest listener, the webhook delivery worker, event
subscribers, and detached jobs started by requests (literature searches, hosted reviews, glossary scans,
URL imports, Terraform runs, restore review timeouts). Several correctness guarantees are held only in
that process's memory: the one-at-a-time data export slot, the backup and restore admission state, the
Terraform lock, the restore review's database swap, and the Literature Source request pacing.

Without a stated topology and lifetime model, each of these reads as an accident, and adding a second
uvicorn worker or a replica looks like a configuration change. It is not: each would silently break
exclusion, run every periodic job twice, and leave restore and pacing state split across processes.

## Decision

**Supported topology.** One application host (a VM) runs Compose: nginx, the Next.js frontend, PostgreSQL,
and exactly one backend container. That container runs exactly one uvicorn process with no `--workers`
option (`docker/Dockerfile.backend` CMD, started through `backend/entrypoint.sh`). nginx terminates TLS
and proxies `/api/` to it. The Kubernetes cluster hosts user workloads only (Pipeline Runs as Kubernetes
Jobs, Notebook Sessions, cellxgene); the application itself never runs on the cluster. This replaces
ADR-021's premise that the control plane runs on GKE and that pipeline workloads share its cluster.

**Ownership of background work.** Every lifespan creates one `TaskOwner` (`app/runtime.py`) and stores it
on `app.state.runtime`. Background work starts through it:

- `runtime.spawn` and `TaskOwner.spawn` keep strong references, a stable role name, and the observed
  outcome. One task's failure never cancels its peers; nothing is replayed automatically.
- Ownership propagates through `runtime.bound` (a context variable set by `TaskOwnerMiddleware` for
  requests and copied into every owned task) and through `runtime.owned_sessions`, which places the owner
  in `session.info` for the synchronous commit hook.
- Request-started jobs reserve admission with `runtime.admission` before their row is committed and start
  only after the commit; a rollback releases the reservation and nothing runs.
- Cleanup that must survive cancellation uses `runtime.shielded`, never a bare `asyncio.shield`.
- Periodic jobs run under `app/tasks/periodic.run_periodic`: no overlapping iterations, the next delay
  starts after the previous one settles, and each job keeps its own sessions, commits and error messages.

**Startup.** `app/startup.py` runs named steps in order, each `fatal` or `optional`, acquiring cleanup on
one `AsyncExitStack` as each resource is installed. The schema compatibility gate precedes every step
that writes, subscribes or starts work; an incompatible database serves maintenance responses only, and
the maintenance watcher exits the process once the gate opens so Compose (`restart: unless-stopped`)
restarts it into normal startup. Steps never run in parallel.

**Shutdown.** Closing admission stops producers at once. Active work then gets `DRAIN_SECONDS` (10 s) to
settle and `CANCEL_SECONDS` (20 s) for cancellation, event and child cleanup on one shared deadline,
followed by `LOG_SINK_SECONDS` (2 s, direct log delivery only) and `RESOURCE_SECONDS` (5 s). This
lifespan's event subscriptions are removed after owned work settles; the engine is disposed last and is
skipped if shutdown reports uncooperative work. uvicorn's request grace (`--timeout-graceful-shutdown 10`)
runs before the lifespan's shutdown. The hard stop is the container's `stop_grace_period: 60s` in both
Compose files; cancellation cannot stop a thread, a blocked loop or an external job.

**Events after commit, best effort.** A service announcing a state change calls
`post_commit_events.defer_event` on the session it wrote with. The event waits in `session.info`, is
emitted only after the outermost transaction commits, follows savepoint release and rollback, and is
dropped on rollback or a failed commit. Emission runs as owned event work (`spawn_event` drops it once
the owner stops admitting events). Delivery is in process and best effort: a process that exits between
commit and emission loses the event. There is no outbox and no replay. Consumers that need durability
persist their own state; webhook delivery is the model (ADR-051).

## Rationale

The single process makes simple, correct mechanisms sufficient. An in-process lock is a real exclusion,
`TaskOwner` sees all application work, a restore review can pause every automatic writer before swapping
the database, and request pacing covers every request that shares a credential. At the scale bioAF
targets (one lab, or a small number of organizations, per installation), one event loop handles the
interactive load; heavy compute already runs off-host on Kubernetes or work nodes.

Alternatives considered:

- **Multiple uvicorn workers.** Rejected. Each worker runs its own lifespan, so every periodic job, the
  Pub/Sub listener and every startup seed would run once per worker. The export slot, backup and restore
  admission, Terraform lock and request pacing would become per-worker, permitting concurrent exports,
  overlapping restores, and pacing violations. A restore swap would rebind only the worker that served it.
- **Replicas behind nginx.** Rejected for the same reasons, plus cross-host failure modes: a second
  backend has its own owner and no knowledge of the first's admitted work, and a shutdown on one host
  cannot drain the other.
- **External queue and separate worker process** (for example Redis with a task runner). Rejected for
  now. It adds a service to install, back up and secure on every customer VM, and still requires
  claiming, leadership for periodic jobs, and orphan recovery to be designed. It does not by itself
  provide fairness between organizations.
- **Transactional outbox for events.** Deferred. It would make post-commit delivery survive a crash, but
  needs a table, a relay, consumer idempotency and retention for every event type. Today only webhook
  delivery requires durability, and it already persists `webhook_deliveries` and drains them with
  `FOR UPDATE SKIP LOCKED`.

## Consequences

**Positive:** one owner sees and bounds all application work; startup and shutdown order is explicit and
tested; in-process locks and slots are real exclusions; events never announce an uncommitted outcome.

**Negative / honest limits:**

- Adding workers or replicas is an architectural change, not a setting. It requires, at minimum:
  cross-process leadership or claiming for periodic jobs and the listener; database-backed (or otherwise
  shared) replacements for the export slot, backup and restore admission, the Terraform lock and request
  pacing; a restore design that pauses and rebinds every process; and orphan recovery for detached jobs.
  The webhook worker's row locking is the only piece already safe to run more than once; ADR-051's
  "every backend pod" note does not make the rest replica-safe.
- Events are lost if the process exits between commit and emission; notifications and cascades
  (ADR-010, ADR-046) inherit that limit.
- uvicorn run outside Compose has no supervisor deadline, so bounded shutdown is claimed only under
  Compose (including the development file's reloader, whose hard stop is the container's).
- A restart after an unclean stop does not reconcile every external job; families with external effects
  rely on their own reconcilers or operator action.

**Neutral:** the process is not a single-organization contract. Organization boundaries still apply to
every lookup, and shared process-wide resources (the export slot, pacing buckets) are contended across
organizations by design; fairness between organizations is a separate decision.

## References

- `backend/app/runtime.py`, `backend/app/startup.py`, `backend/app/main.py` (lifespan)
- `backend/app/tasks/periodic.py`, `backend/app/tasks/jobs.py`
- `backend/app/services/post_commit_events.py`, `backend/app/services/event_bus.py`
- `backend/app/services/data_export/resources.py` (`ExportSlot`), `backend/app/services/backup_service.py`,
  `backend/app/services/request_pacing.py`, `backend/app/services/terraform_executor.py`
- `docker/Dockerfile.backend`, `backend/entrypoint.sh`, `docker/docker-compose.yml`, `docker/docker-compose.dev.yml`
