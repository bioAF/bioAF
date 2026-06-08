"""Keep the legacy gcs_uri column and the neutral storage_uri column in sync.

BAL rework, Phase 4 (expand/contract). storage_uri is being introduced as the
backend-neutral physical column to eventually replace gcs_uri. During the
transition BOTH columns exist and must hold the same value, so live installs and
any external readers of gcs_uri keep working until a later migration drops it.

This registers ORM before-insert/before-update listeners that mirror the two
columns on every flush, so existing writers (which set gcs_uri) and new writers
(which may set storage_uri) both end up with both columns populated:

  - if only storage_uri was set (new-style insert), copy it into gcs_uri
    (gcs_uri is still NOT NULL during the transition);
  - otherwise gcs_uri is canonical and storage_uri mirrors it.

Raw-SQL writers (a handful of UPDATEs) bypass the ORM and set both columns
explicitly. When gcs_uri is dropped (the contract migration), delete this module
and its registrations.
"""

from __future__ import annotations

from sqlalchemy import event


def register_storage_uri_sync(model) -> None:
    """Mirror gcs_uri <-> storage_uri on insert/update for the given model."""

    def _sync(_mapper, _connection, target) -> None:
        gcs = getattr(target, "gcs_uri", None)
        storage = getattr(target, "storage_uri", None)
        if gcs is None and storage is not None:
            target.gcs_uri = storage
        else:
            target.storage_uri = gcs

    event.listen(model, "before_insert", _sync)
    event.listen(model, "before_update", _sync)
