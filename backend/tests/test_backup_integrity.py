"""Backup integrity mechanics shared by the upgrade recovery backup and Postgres backups (app/services/backup_integrity.py).

- One block-streaming SHA-256 and one TOC parser, used by both backup flows.
- A Postgres backup made by this code carries a versioned marker (format, its own name, its SHA-256). A marker
  that is missing a part, names another backup, or has an unknown format is invalid, never "legacy".
- Only backups named in the cutover inventory, written once before the first marked backup, may be treated as
  unverified legacy backups; any other unmarked object is invalid.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app import schema_upgrade
from app.services import backup_integrity as bi

NAME = "pgdump-20261114-120000.dump"
DIGEST = "a" * 64


def test_the_file_hash_is_sha256_of_every_byte(tmp_path):
    path = tmp_path / "f"
    data = b"x" * (3 * 1024 * 1024 + 7)
    path.write_bytes(data)
    assert bi.file_sha256(path) == hashlib.sha256(data).hexdigest()


def test_the_upgrade_backup_uses_the_same_mechanics():
    assert schema_upgrade.file_sha256 is bi.file_sha256
    assert schema_upgrade.tables_with_data is bi.tables_with_data


def test_tables_with_data_are_read_from_a_toc_listing():
    listing = "\n".join(
        [
            ";",
            "; Archive created at 2026-11-14",
            "215; 1259 16385 TABLE public organizations bioaf_app",
            "3401; 0 16385 TABLE DATA public organizations bioaf_app",
            "3402; 0 16390 TABLE DATA public samples bioaf_app",
            "; 3403; 0 16391 TABLE DATA public commented bioaf_app",
        ]
    )
    assert bi.tables_with_data(listing) == {"organizations", "samples"}


def test_a_marker_round_trips():
    marker = bi.marker(NAME, DIGEST)
    assert bi.classify(NAME, marker, legacy=frozenset()) == ("checksummed", DIGEST)


@pytest.mark.parametrize(
    "metadata",
    [
        {bi.KEY_FORMAT: "1", bi.KEY_ID: NAME},  # no digest
        {bi.KEY_FORMAT: "1", bi.KEY_SHA256: DIGEST},  # no identity
        {bi.KEY_FORMAT: "2", bi.KEY_ID: NAME, bi.KEY_SHA256: DIGEST},  # unknown format
        {bi.KEY_FORMAT: "1", bi.KEY_ID: "pgdump-20200101-000000.dump", bi.KEY_SHA256: DIGEST},  # another backup's
        {bi.KEY_FORMAT: "1", bi.KEY_ID: NAME, bi.KEY_SHA256: "not-a-digest"},
        {bi.KEY_FORMAT: "1", bi.KEY_ID: NAME, bi.KEY_SHA256: "A" * 64},  # not the canonical lowercase hex
    ],
)
def test_a_damaged_marker_is_invalid_even_for_a_legacy_name(metadata):
    assert bi.classify(NAME, metadata, legacy=frozenset({NAME})) == ("invalid", None)


def test_an_unmarked_backup_is_legacy_only_when_the_inventory_names_it():
    assert bi.classify(NAME, {}, legacy=frozenset({NAME})) == ("unverified", None)
    assert bi.classify(NAME, {}, legacy=frozenset()) == ("invalid", None)
    assert bi.classify(NAME, {"unrelated": "x"}, legacy=frozenset({NAME})) == ("unverified", None)


def test_the_cutover_inventory_round_trips_and_rejects_damage():
    doc = bi.cutover_document(["b.dump", "a.dump"], created_at="2026-11-14T00:00:00+00:00")
    assert bi.parse_cutover(doc) == frozenset({"a.dump", "b.dump"})
    for bad in (b"", b"{", json.dumps({"format": 2, "legacy": []}).encode(), json.dumps({"format": 1}).encode()):
        with pytest.raises(bi.IntegrityError):
            bi.parse_cutover(bad)


@pytest.mark.asyncio
async def test_concurrent_first_callers_record_the_cutover_once():
    """Two first callers (a listing and a backup) must not each write an inventory: the second would include a
    newer backup and qualify it for the unverified opt-in (review finding 5)."""
    import asyncio

    from app.adapters.models import StorageObjectNotFound, StoredObject
    from app.services import backup_service

    class Store:
        def __init__(self):
            self.objects = ["pgdump-20261101-000000.dump"]
            self.writes: list[bytes] = []
            self.data: bytes | None = None
            self.listing = asyncio.Event()

        def build_uri(self, bucket, key):
            return f"fake://{bucket}/{key}"

        async def read_bytes(self, uri):
            if self.data is None:
                raise StorageObjectNotFound(uri)
            return self.data

        async def list_objects(self, prefix):
            names = list(self.objects)
            await self.listing.wait()
            return [StoredObject(filename=n, storage_uri=f"fake://b/postgres/{n}") for n in names]

        async def write_bytes(self, uri, data, **kwargs):
            self.writes.append(data)
            self.data = data

    store = Store()
    first = asyncio.create_task(backup_service._legacy_inventory(store, "b"))
    await asyncio.sleep(0)
    store.objects.append("pgdump-20261114-000000.dump")  # a new backup lands while the first is listing
    second = asyncio.create_task(backup_service._legacy_inventory(store, "b"))
    await asyncio.sleep(0)
    store.listing.set()
    a, b = await asyncio.gather(first, second)
    assert len(store.writes) == 1
    assert a == b == frozenset({"pgdump-20261101-000000.dump"})
