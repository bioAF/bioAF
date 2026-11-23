"""A small in-memory Cloud Storage that honours generations and preconditions.

Enough of the google-cloud-storage surface for the conditional storage paths (``Blob.rewrite`` with
``if_generation_match`` / ``if_source_generation_match``, generation-pinned reads and deletes,
``reload``/``exists``) to behave like the service: a precondition that does not hold raises
``PreconditionFailed`` and changes nothing. Mocks that only assert a keyword were passed cannot show
that a second writer is refused; this can.
"""

from __future__ import annotations

import base64
import hashlib
import itertools
import threading
from dataclasses import dataclass, field

from google.api_core.exceptions import NotFound, PreconditionFailed

_generations = itertools.count(1_000)


@dataclass
class StoredBlob:
    data: bytes
    generation: int
    metadata: dict | None = None
    content_type: str | None = None


@dataclass
class FakeGcs:
    objects: dict[tuple[str, str], StoredBlob] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    rewrite_calls: list[dict] = field(default_factory=list)
    delete_calls: list[dict] = field(default_factory=list)
    # Rewrite answers in this many steps (continuation tokens) before finishing.
    rewrite_steps: int = 2
    # Called inside rewrite before the destination precondition is checked (a racing writer).
    before_publish: object = None

    def put(self, bucket: str, key: str, data: bytes, *, metadata=None, content_type=None) -> StoredBlob:
        stored = StoredBlob(data, next(_generations), dict(metadata) if metadata else None, content_type)
        self.objects[(bucket, key)] = stored
        return stored

    def get(self, bucket: str, key: str) -> StoredBlob | None:
        return self.objects.get((bucket, key))

    def bucket(self, name: str) -> "FakeBucket":
        return FakeBucket(self, name)


class FakeBucket:
    def __init__(self, gcs: FakeGcs, name: str):
        self.gcs, self.name = gcs, name

    def blob(self, key: str, generation: int | None = None) -> "FakeBlob":
        return FakeBlob(self.gcs, self.name, key, generation)


class FakeBlob:
    def __init__(self, gcs: FakeGcs, bucket: str, key: str, generation: int | None):
        self.gcs, self.bucket_name, self.name, self._pinned = gcs, bucket, key, generation
        self.metadata: dict | None = None
        self.content_type: str | None = None
        self.generation: int | None = generation
        self.size: int | None = None
        self.md5_hash: str | None = None
        self.storage_class = "STANDARD"
        self.updated = None
        self._rewrite_progress = 0

    def _current(self) -> StoredBlob | None:
        stored = self.gcs.get(self.bucket_name, self.name)
        if stored is None or (self._pinned is not None and stored.generation != self._pinned):
            return None
        return stored

    def exists(self) -> bool:
        return self._current() is not None

    def reload(self) -> None:
        stored = self._current()
        if stored is None:
            raise NotFound("no such object")
        self.generation = stored.generation
        self.size = len(stored.data)
        self.md5_hash = base64.b64encode(hashlib.md5(stored.data).digest()).decode()
        self.metadata = dict(stored.metadata) if stored.metadata else None
        self.content_type = stored.content_type

    def download_as_bytes(self, start=None, end=None, **_kw) -> bytes:
        stored = self._current()
        if stored is None:
            raise NotFound("no such object")
        return stored.data[start : None if end is None else end + 1] if start is not None else stored.data

    def delete(self, if_generation_match: int | None = None) -> None:
        with self.gcs.lock:
            self.gcs.delete_calls.append({"key": self.name, "if_generation_match": if_generation_match})
            stored = self._current()
            if stored is None:
                raise NotFound("no such object")
            if if_generation_match is not None and stored.generation != if_generation_match:
                raise PreconditionFailed("generation does not match")
            del self.gcs.objects[(self.bucket_name, self.name)]

    def rewrite(self, source: "FakeBlob", token=None, if_generation_match=None, if_source_generation_match=None, **kw):
        self.gcs.rewrite_calls.append(
            {
                "source": (source.bucket_name, source.name),
                "token": token,
                "if_generation_match": if_generation_match,
                "if_source_generation_match": if_source_generation_match,
                "metadata": self.metadata,
                "content_type": self.content_type,
            }
        )
        self._rewrite_progress += 1
        src = self.gcs.get(source.bucket_name, source.name)
        if src is None:
            raise NotFound("no source object")
        if if_source_generation_match is not None and src.generation != if_source_generation_match:
            raise PreconditionFailed("source generation does not match")
        if self._rewrite_progress < self.gcs.rewrite_steps:
            return f"token-{self._rewrite_progress}", len(src.data) // 2, len(src.data)
        if callable(self.gcs.before_publish):
            self.gcs.before_publish()
        with self.gcs.lock:
            existing = self.gcs.get(self.bucket_name, self.name)
            if if_generation_match == 0 and existing is not None:
                raise PreconditionFailed("destination exists")
            if if_generation_match not in (None, 0) and (
                existing is None or existing.generation != if_generation_match
            ):
                raise PreconditionFailed("destination generation does not match")
            stored = self.gcs.put(
                self.bucket_name,
                self.name,
                src.data,
                metadata=self.metadata if self.metadata is not None else src.metadata,
                content_type=self.content_type if self.content_type is not None else src.content_type,
            )
        self.generation, self.size = stored.generation, len(stored.data)
        return None, len(src.data), len(src.data)


class FakeClient:
    def __init__(self, gcs: FakeGcs):
        self.gcs = gcs

    def bucket(self, name: str) -> FakeBucket:
        return self.gcs.bucket(name)
