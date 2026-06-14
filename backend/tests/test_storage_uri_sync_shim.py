"""Unit tests for the storage_uri <-> gcs_uri mirror direction.

storage_uri is canonical; gcs_uri is the retained legacy mirror. These test the
pure mirror function directly (no DB), so they run locally and pin the reversed
direction: app code writes storage_uri, the shim backfills gcs_uri.
"""

from types import SimpleNamespace

from app.models._storage_uri_sync import _mirror_storage_uri


def test_new_style_write_backfills_gcs_uri_mirror():
    """Writing storage_uri (the canonical column) mirrors into gcs_uri."""
    target = SimpleNamespace(storage_uri="gs://bucket/new.bam", gcs_uri=None)
    _mirror_storage_uri(target)
    assert target.gcs_uri == "gs://bucket/new.bam"
    assert target.storage_uri == "gs://bucket/new.bam"


def test_legacy_gcs_only_write_backfills_storage_uri():
    """A legacy write that sets only gcs_uri is mirrored INTO storage_uri so
    readers (which read storage_uri) stay correct."""
    target = SimpleNamespace(storage_uri=None, gcs_uri="gs://bucket/legacy.bam")
    _mirror_storage_uri(target)
    assert target.storage_uri == "gs://bucket/legacy.bam"
    assert target.gcs_uri == "gs://bucket/legacy.bam"


def test_storage_uri_is_canonical_on_update():
    """On an existing row, storage_uri wins: gcs_uri follows the new storage_uri
    (the reversed direction vs the original expand-phase shim)."""
    target = SimpleNamespace(storage_uri="gs://bucket/updated.bam", gcs_uri="gs://bucket/stale.bam")
    _mirror_storage_uri(target)
    assert target.gcs_uri == "gs://bucket/updated.bam"


def test_both_none_is_noop():
    target = SimpleNamespace(storage_uri=None, gcs_uri=None)
    _mirror_storage_uri(target)
    assert target.storage_uri is None
    assert target.gcs_uri is None
