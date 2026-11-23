"""An in-memory object store with the storage adapter's object semantics, for service-level tests.

Models what filing and organization rely on: object versions, custom metadata that travels with a
create-only copy (bioAF's reserved owner keys replaced by the caller's), create-only refusal of an
occupied destination, a pinned source version, and deletion of exactly one observed version. Mocks
that only record that ``move`` was called cannot show that a second upload is refused or that the
wrong object is never claimed; this can.

Fault injection: ``fail(op, times, exc)`` makes the next ``times`` calls of ``op`` raise; ``after``
hooks run after an operation succeeds (a crash after publication, a racing writer). Every call is
recorded in ``calls`` as ``(op, args)``.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from inspect import isawaitable
from unittest.mock import MagicMock
from urllib.parse import urlparse

from app.adapters.models import (
    DestinationExists,
    ObjectChanged,
    ObjectIdentity,
    ObjectMetadata,
    ObjectObservation,
    StorageObjectNotFound,
    StorageStore,
    owner_metadata,
)

_versions = itertools.count(1)


class StoredObject:
    def __init__(self, data: bytes, metadata: dict | None = None):
        self.data = data
        self.metadata = dict(metadata or {})
        self.version = f"mem:{next(_versions)}"


class MemoryObjectStore:
    def __init__(self, ingest_bucket: str = "bioaf-ingest-test"):
        self.objects: dict[str, StoredObject] = {}
        self.calls: list[tuple[str, tuple]] = []
        self._failures: dict[str, list[BaseException]] = {}
        self.after: dict[str, Callable] = {}
        self.ingest_bucket = ingest_bucket
        # For API tests whose File rows were seeded without bytes: a source read of a missing object
        # for which this returns True finds placeholder bytes (destinations are never assumed).
        self.assume_present: Callable[[str], bool] | None = None
        self.build_uri = MagicMock(side_effect=lambda bucket, key: f"gs://{bucket}/{key.lstrip('/')}")
        self.parse_uri = MagicMock(side_effect=lambda uri: (urlparse(uri).netloc, urlparse(uri).path.lstrip("/")))

    # -- test controls ----------------------------------------------------------------------

    def fail(self, op: str, times: int = 1, exc: BaseException | None = None) -> None:
        err = exc or RuntimeError("503 POST https://storage.googleapis.com/rewrite SECRET")
        self._failures.setdefault(op, []).extend([err] * times)

    def put(self, uri: str, data: bytes, metadata: dict | None = None) -> StoredObject:
        self.objects[uri] = StoredObject(data, metadata)
        return self.objects[uri]

    def count(self, op: str) -> int:
        return sum(1 for name, _ in self.calls if name == op)

    async def _enter(self, op: str, *args) -> None:
        self.calls.append((op, args))
        pending = self._failures.get(op)
        if pending:
            raise pending.pop(0)

    async def _leave(self, op: str, *args) -> None:
        hook = self.after.get(op)
        if hook is not None:
            result = hook(*args)
            if isawaitable(result):
                await result

    def _identity(self, uri: str) -> ObjectIdentity:
        obj = self.objects.get(uri)
        if obj is None:
            raise StorageObjectNotFound(uri)
        return ObjectIdentity(uri=uri, size_bytes=len(obj.data), version=obj.version)

    # -- adapter surface ------------------------------------------------------------------------

    async def resolve_uri(self, store: StorageStore, key: str) -> str:
        return f"gs://bioaf-{store.value}-test/{key.lstrip('/')}"

    async def upload_file(self, uri, file_obj, *, content_type=None, metadata=None) -> None:
        await self._enter("upload_file", uri)
        data = file_obj.read()
        if isawaitable(data):
            data = await data
        self.put(uri, data, metadata)
        await self._leave("upload_file", uri)

    async def read_bytes(self, uri: str) -> bytes:
        await self._enter("read_bytes", uri)
        if uri not in self.objects:
            raise StorageObjectNotFound(uri)
        return self.objects[uri].data

    async def read_prefix(self, uri: str, length: int) -> bytes:
        return (await self.read_bytes(uri))[:length]

    async def exists(self, uri: str) -> bool:
        await self._enter("exists", uri)
        return uri in self.objects

    async def object_identity(self, uri: str) -> ObjectIdentity:
        await self._enter("object_identity", uri)
        if uri not in self.objects and self.assume_present is not None and self.assume_present(uri):
            self.put(uri, b"stored-bytes")
        return self._identity(uri)

    async def inspect_object(self, uri: str) -> ObjectObservation:
        await self._enter("inspect_object", uri)
        identity = self._identity(uri)
        return ObjectObservation(identity=identity, metadata=dict(self.objects[uri].metadata))

    async def get_object_metadata(self, uri: str) -> ObjectMetadata:
        await self._enter("get_object_metadata", uri)
        identity = self._identity(uri)
        return ObjectMetadata(uri=uri, size_bytes=identity.size_bytes, metadata=dict(self.objects[uri].metadata))

    async def copy_create_only(self, source_uri, dest_uri, *, source_identity=None, metadata=None) -> ObjectIdentity:
        await self._enter("copy_create_only", source_uri, dest_uri)
        src = self.objects.get(source_uri)
        if src is None:
            raise (ObjectChanged(source_uri) if source_identity else StorageObjectNotFound(source_uri))
        if source_identity is not None and source_identity.version != src.version:
            raise ObjectChanged(source_uri)
        if dest_uri in self.objects:
            raise DestinationExists(dest_uri)
        self.objects[dest_uri] = StoredObject(src.data, owner_metadata(src.metadata, metadata))
        await self._leave("copy_create_only", source_uri, dest_uri)
        return self._identity(dest_uri)

    async def copy(self, source_uri, dest_uri, *, create_only=False, source_identity=None, metadata=None) -> str:
        if create_only:
            await self.copy_create_only(source_uri, dest_uri, source_identity=source_identity, metadata=metadata)
            return dest_uri
        await self._enter("copy", source_uri, dest_uri)
        if source_uri not in self.objects:
            raise StorageObjectNotFound(source_uri)
        self.objects[dest_uri] = StoredObject(self.objects[source_uri].data, self.objects[source_uri].metadata)
        return dest_uri

    async def move(self, source_uri, dest_uri) -> str:
        await self._enter("move", source_uri, dest_uri)
        if source_uri not in self.objects:
            raise StorageObjectNotFound(source_uri)
        self.objects[dest_uri] = self.objects.pop(source_uri)
        return dest_uri

    async def delete(self, uri, *, generation=None) -> None:
        await self._enter("delete", uri)
        self.objects.pop(uri, None)

    async def delete_if_unchanged(self, identity: ObjectIdentity) -> bool:
        await self._enter("delete_if_unchanged", identity.uri)
        obj = self.objects.get(identity.uri)
        if obj is None:
            return False
        if obj.version != identity.version:
            raise ObjectChanged(identity.uri)
        del self.objects[identity.uri]
        await self._leave("delete_if_unchanged", identity.uri)
        return True


_RECORDED = (
    "upload_file",
    "exists",
    "object_identity",
    "inspect_object",
    "copy_create_only",
    "copy",
    "move",
    "delete",
    "delete_if_unchanged",
    "read_bytes",
    "get_object_metadata",
    "resolve_uri",
)


def recording_store(session_url: str | None = None) -> MemoryObjectStore:
    """A MemoryObjectStore whose operations are AsyncMocks wrapping it (assertable, overridable),
    with a provider upload session that finishes: when completion asks the provider, the session's
    object is there (placeholder bytes unless the test put real ones)."""
    from unittest.mock import AsyncMock

    from app.adapters.models import UploadSessionState, UploadSessionStatus
    from tests.support.uploads import TEST_SESSION_URL, with_upload_session

    store = MemoryObjectStore()
    for name in _RECORDED:
        setattr(store, name, AsyncMock(side_effect=getattr(store, name)))
    for name in (
        "begin_upload_session",
        "inspect_upload_session",
        "cancel_upload_session",
        "sign_upload_parts",
        "generate_signed_url",
        "create_resumable_upload_url",
    ):
        setattr(store, name, AsyncMock())
    with_upload_session(store, session_url or TEST_SESSION_URL)

    async def finished(uri, handle):
        if uri not in store.objects:
            store.put(uri, b"uploaded-bytes")
        return UploadSessionStatus(state=UploadSessionState.COMPLETED)

    store.inspect_upload_session = AsyncMock(side_effect=finished)
    return store
