"""Contract tests for the expanded StorageProvider object-store interface (Phase 3).

Phase 3 grows StorageProvider from the pipeline-centric staging methods into a
full object-store interface (read/write/upload/download/delete/exists/list/
copy/move/metadata/signed-url) plus a logical-store resolver. These tests pin
the *contract*: the methods exist, the base default is "unimplemented" so a new
backend must override, and the NFS stub honestly raises NotImplementedError
until Phase 7 builds it. GCS behavior is tested separately.
"""

from __future__ import annotations

import inspect

import pytest

from app.adapters.base import StorageProvider
from app.adapters.models import ObjectMetadata, StorageStore
from app.adapters.storage.nfs import NfsStorageProvider

# The object-store methods Phase 3 adds to the interface, with the minimal
# positional args each needs (beyond self) to be invoked for the default-raise
# and NFS-stub assertions.
OBJECT_STORE_METHODS = {
    "read_text": ("gs://b/k",),
    "read_bytes": ("gs://b/k",),
    "write_text": ("gs://b/k", "hello"),
    "write_bytes": ("gs://b/k", b"hello"),
    "upload_file": ("gs://b/k", object()),
    "upload_filename": ("gs://b/k", "/tmp/x"),
    "download_to_file": ("gs://b/k", object()),
    "download_to_filename": ("gs://b/k", "/tmp/x"),
    "delete": ("gs://b/k",),
    "exists": ("gs://b/k",),
    "list_objects": ("gs://b/prefix/",),
    "copy": ("gs://b/k", "gs://b/k2"),
    "move": ("gs://b/k", "gs://b/k2"),
    "get_object_metadata": ("gs://b/k",),
    "generate_signed_url": ("gs://b/k",),
    "resolve_uri": (StorageStore.INGEST, "some/key"),
}


def test_storage_provider_declares_object_store_methods():
    for name in OBJECT_STORE_METHODS:
        assert hasattr(StorageProvider, name), f"StorageProvider missing {name}"
        assert inspect.iscoroutinefunction(getattr(StorageProvider, name)), (
            f"{name} must be async"
        )


def test_storage_store_enum_has_logical_stores():
    names = {s.name for s in StorageStore}
    assert {
        "INGEST",
        "RAW",
        "WORKING",
        "RESULTS",
        "REFERENCES",
        "LITERATURE",
        "CONFIG_BACKUPS",
        "BACKUPS",
    } <= names


def test_object_metadata_model_shape():
    md = ObjectMetadata(uri="gs://b/k", size_bytes=10, md5_hash="abc")
    assert md.uri == "gs://b/k"
    assert md.size_bytes == 10
    assert md.md5_hash == "abc"
    # content_type / storage_class / updated are optional extras
    assert md.content_type is None


class _BareStorage(StorageProvider):
    """Concrete subclass that overrides only the pre-Phase-3 abstract methods,
    to prove the new object-store methods default to NotImplementedError."""

    async def resolve_input_path(self, file_record):
        return ""

    async def resolve_output_path(self, pipeline_run, filename):
        return ""

    async def stage_inputs(self, file_records, working_dir):
        return []

    async def collect_outputs(self, working_dir, pipeline_run):
        return []

    async def get_storage_metrics(self):
        from app.adapters.models import StorageMetrics

        return StorageMetrics()


@pytest.mark.asyncio
@pytest.mark.parametrize("name,args", list(OBJECT_STORE_METHODS.items()))
async def test_base_default_is_unimplemented(name, args):
    provider = _BareStorage()
    with pytest.raises(NotImplementedError):
        await getattr(provider, name)(*args)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,args", list(OBJECT_STORE_METHODS.items()))
async def test_nfs_stub_raises_not_implemented(name, args):
    provider = NfsStorageProvider()
    with pytest.raises(NotImplementedError):
        await getattr(provider, name)(*args)
