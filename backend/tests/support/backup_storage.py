"""A fake backups bucket for restore tests: one stored dump, its metadata, and the cutover record.

By default the dump carries a valid integrity marker for whatever name is asked for (as a backup made by this code
would). ``metadata`` replaces that marker (``{}`` makes an unmarked object); ``legacy`` seeds the cutover inventory.
A download can be held open (``release``), made to fail (``fail``), and its target is recorded.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from app.adapters.models import ObjectMetadata, StorageObjectNotFound, StoredObject
from app.services import backup_integrity


class FakeBackupStorage:
    def __init__(
        self,
        source: str | None = None,
        *,
        content: bytes = b"PGDMP fake dump",
        metadata: dict[str, str] | None = None,
        legacy: list[str] | None = None,
    ):
        self.source = source
        self.content = content
        self.metadata = metadata
        self.downloads: list[str] = []
        self.paths: list[Path] = []
        self.started = asyncio.Event()
        self.release: asyncio.Event | None = None
        self.fail: Exception | None = None
        self.replace_after_metadata: bytes | None = None
        self.writes: dict[str, bytes] = {}
        if legacy is not None:
            self.writes["fake://backups/" + backup_integrity.CUTOVER_KEY] = backup_integrity.cutover_document(
                legacy, created_at="2026-11-01T00:00:00+00:00"
            )

    def build_uri(self, bucket: str, key: str) -> str:
        return f"fake://{bucket}/{key}"

    def body(self) -> bytes:
        if self.source:
            return Path(self.source).read_bytes()
        return self.content

    async def get_object_metadata(self, uri: str) -> ObjectMetadata:
        name = uri.rsplit("/", 1)[-1]
        body = self.body()
        if self.metadata is None:
            metadata = backup_integrity.marker(name, hashlib.sha256(body).hexdigest())
        else:
            metadata = self.metadata
        if self.replace_after_metadata is not None:
            self.content, self.source = self.replace_after_metadata, None  # swapped between the read and the download
        return ObjectMetadata(uri=uri, size_bytes=len(body), metadata=metadata)

    async def read_bytes(self, uri: str) -> bytes:
        if uri not in self.writes:
            raise StorageObjectNotFound(uri)
        return self.writes[uri]

    async def write_bytes(self, uri: str, data: bytes, **kwargs) -> None:
        self.writes[uri] = data

    async def list_objects(self, uri_prefix: str, **kwargs) -> list[StoredObject]:
        return []

    async def download_to_file(self, uri: str, file_obj) -> None:
        self.downloads.append(uri)
        self.paths.append(Path(file_obj.name))
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.fail is not None:
            raise self.fail
        file_obj.write(self.body())
