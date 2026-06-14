"""Keep the canonical storage_uri column and the legacy gcs_uri mirror in sync.

BAL rework / AWS-prep. ``storage_uri`` is the backend-neutral, AUTHORITATIVE
object-store URI column; ``gcs_uri`` is a retained LEGACY MIRROR. Both columns
exist and hold the same value. App code reads and writes ``storage_uri``; this
shim backfills ``gcs_uri`` so the legacy column stays populated (it is NOT NULL)
and faithful, allowing an operator to confirm nothing depends on it and drop it
later. We do NOT drop ``gcs_uri`` here.

Direction (reversed from the original expand-phase shim): ``storage_uri`` is
canonical.

  - if only ``gcs_uri`` was set (a legacy-style write that did not touch
    ``storage_uri``), copy it INTO ``storage_uri`` so readers (which read
    ``storage_uri``) stay correct;
  - otherwise ``storage_uri`` is canonical and ``gcs_uri`` mirrors it.

Consequence: ``storage_uri`` wins on UPDATE. Writing ``gcs_uri`` alone on an
existing row whose ``storage_uri`` is already set is reverted to ``storage_uri``;
app writers must set ``storage_uri``.

This shim fires only on ORM flush (mapper before_insert / before_update). Writes
that bypass the ORM unit of work (Core ``update(...).values(...)`` and raw SQL)
must set BOTH columns explicitly to keep the mirror faithful.

When an operator finally drops ``gcs_uri``, delete this module and its
registrations, and drop the explicit ``gcs_uri`` writes from the Core/raw-SQL
writers.
"""

from __future__ import annotations

from sqlalchemy import event


def _mirror_storage_uri(target) -> None:
    """Mirror storage_uri (canonical) <-> gcs_uri (legacy) on a flushed row."""
    gcs = getattr(target, "gcs_uri", None)
    storage = getattr(target, "storage_uri", None)
    if storage is None and gcs is not None:
        target.storage_uri = gcs
    else:
        target.gcs_uri = storage


def register_storage_uri_sync(model) -> None:
    """Register the storage_uri <-> gcs_uri mirror for the given model."""

    def _sync(_mapper, _connection, target) -> None:
        _mirror_storage_uri(target)

    event.listen(model, "before_insert", _sync)
    event.listen(model, "before_update", _sync)
