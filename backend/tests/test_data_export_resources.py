"""Scratch reservation, free-space floor, the global slot and bounded scratch files (plan 24)."""

from __future__ import annotations

import os
import threading
from types import SimpleNamespace

import pytest

from app.exceptions import ExportBusyError, ExportCapacityError, ExportLimitError
from app.services.data_export import resources
from app.services.data_export.resources import GiB, MiB

G = 64 * MiB


# -- Reservation arithmetic -----------------------------------------------------------------------


def _h(s: int) -> int:
    return max(32 * MiB, -(-(s + G) // 20))


@pytest.mark.parametrize(
    ("sizes", "expected"),
    [
        ([], 0 + G + G + 32 * MiB),
        ([1024], 1024 + G + G + _h(1024)),
        ([0] * 20_000, G + G + 32 * MiB),
        ([2 * GiB], 2 * GiB + 2 * GiB + G + _h(2 * GiB)),
        ([64 * MiB] * 32, 2 * GiB + G + G + _h(2 * GiB)),
        ([10 * MiB, 100 * MiB], 110 * MiB + 100 * MiB + G + _h(110 * MiB)),
    ],
)
def test_reservation_is_sources_plus_largest_staging_plus_generated_plus_overhead(sizes, expected):
    assert resources.reservation_for(sizes) == expected


@pytest.mark.parametrize("sizes", [[None], [10, None], [-1]])
def test_an_unknown_or_invalid_size_reserves_the_whole_ceiling(sizes):
    assert resources.reservation_for(sizes) == resources.SCRATCH_CEILING


def test_a_reservation_above_the_ceiling_is_refused_not_clamped():
    with pytest.raises(ExportLimitError):
        resources.reservation_for([3 * GiB])


def test_the_recorded_blackbird_examples():
    """plan.md's admission table against 6.63 GiB free and a 3 GiB floor."""
    free = 7_116_333_056
    total = 31_024_283_648
    disk = SimpleNamespace(free=free, total=total)

    assert resources.admits(disk, resources.reservation_for([1024]))
    assert not resources.admits(disk, resources.reservation_for([2 * GiB]))
    assert resources.admits(disk, resources.reservation_for([64 * MiB] * 32))
    assert not resources.admits(disk, resources.reservation_for([None]))


@pytest.mark.parametrize(
    ("total", "floor"),
    [(10 * GiB, 3 * GiB), (30 * GiB, 3 * GiB), (40 * GiB, 4 * GiB), (100 * GiB, 10 * GiB)],
)
def test_the_floor_is_three_gib_or_ten_percent(total, floor):
    assert resources.floor_for(total) == floor


def test_admission_at_the_floor_boundary():
    r = 100 * MiB
    exact = SimpleNamespace(total=10 * GiB, free=3 * GiB + r)
    short = SimpleNamespace(total=10 * GiB, free=3 * GiB + r - 1)

    assert resources.admits(exact, r)
    assert not resources.admits(short, r)


# -- Source limits -------------------------------------------------------------------------------


def test_known_source_bytes_over_two_gib_are_refused_with_the_limit_stated():
    with pytest.raises(ExportLimitError) as exc:
        resources.check_source_limits([GiB, GiB, 1], fastq_omitted=False)

    assert "2 GiB" in str(exc.value)
    assert exc.value.status_code == 413


def test_the_source_refusal_suggests_omitting_fastq_only_when_it_was_included():
    with pytest.raises(ExportLimitError) as with_fastq:
        resources.check_source_limits([3 * GiB], fastq_omitted=False)
    with pytest.raises(ExportLimitError) as without:
        resources.check_source_limits([3 * GiB], fastq_omitted=True)

    assert "FASTQ" in str(with_fastq.value)
    assert "FASTQ" not in str(without.value)


def test_more_than_ten_thousand_files_are_refused():
    with pytest.raises(ExportLimitError):
        resources.check_source_limits([1] * 10_001, fastq_omitted=True)


def test_exactly_two_gib_and_ten_thousand_files_are_admitted():
    resources.check_source_limits([2 * GiB // 10_000] * 10_000, fastq_omitted=True)


# -- The global slot ------------------------------------------------------------------------------


def test_one_export_at_a_time_and_the_slot_returns_after_release():
    slot = resources.ExportSlot()
    first = slot.try_acquire()

    with pytest.raises(ExportBusyError) as busy:
        slot.try_acquire()
    assert busy.value.status_code == 429

    first.release()
    first.release()  # idempotent
    slot.try_acquire().release()


def test_the_slot_is_atomic_across_threads():
    slot = resources.ExportSlot()
    won: list[object] = []
    barrier = threading.Barrier(16)

    def race():
        barrier.wait()
        try:
            won.append(slot.try_acquire())
        except ExportBusyError:
            pass

    threads = [threading.Thread(target=race) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(won) == 1


# -- Scratch budget and bounded files ------------------------------------------------------------


def _budget(tmp_path, reservation: int, *, free: int = 100 * GiB, total: int = 200 * GiB):
    state = {"free": free}
    probe = lambda: SimpleNamespace(free=state["free"], total=total)  # noqa: E731
    return resources.ScratchBudget(reservation, probe=probe), state


def test_growth_is_charged_by_high_water_not_by_bytes_written(tmp_path):
    budget, _ = _budget(tmp_path, 1000)
    with resources.BoundedFile(tmp_path / "f", budget) as f:
        f.write(b"a" * 100)
        f.seek(0)
        f.write(b"b" * 100)  # a retried range: no new extent
        f.seek(50)
        f.write(b"c" * 100)  # 50 bytes past the old end

    assert budget.charged == 150
    assert (tmp_path / "f").read_bytes() == b"b" * 50 + b"c" * 100


def test_out_of_order_ranges_charge_the_hole_once(tmp_path):
    budget, _ = _budget(tmp_path, 1000)
    with resources.BoundedFile(tmp_path / "f", budget) as f:
        f.seek(200)
        f.write(b"z" * 100)
        f.seek(0)
        f.write(b"a" * 200)

    assert budget.charged == 300
    assert (tmp_path / "f").read_bytes() == b"a" * 200 + b"z" * 100


def test_growth_past_the_reservation_is_refused_before_it_reaches_disk(tmp_path):
    budget, _ = _budget(tmp_path, 100)
    path = tmp_path / "f"
    with resources.BoundedFile(path, budget) as f:
        f.write(b"a" * 60)
        with pytest.raises(ExportCapacityError):
            f.write(b"b" * 60)
        with pytest.raises(ExportCapacityError):
            f.seek(500)
            f.write(b"c")
        with pytest.raises(ExportCapacityError):
            f.truncate(500)

    assert os.path.getsize(path) == 60
    assert budget.charged == 60


def test_deleting_a_staging_file_returns_its_extent(tmp_path):
    budget, _ = _budget(tmp_path, 100)
    f = resources.BoundedFile(tmp_path / "f", budget)
    f.write(b"a" * 80)
    f.discard()

    assert budget.charged == 0
    assert not (tmp_path / "f").exists()


def test_free_space_falling_below_the_floor_stops_growth(tmp_path):
    budget, state = _budget(tmp_path, 64 * MiB, free=3 * GiB + 64 * MiB, total=10 * GiB)
    with resources.BoundedFile(tmp_path / "f", budget) as f:
        f.write(b"a" * MiB)
        state["free"] = 3 * GiB  # another writer took space
        with pytest.raises(ExportCapacityError):
            f.write(b"b" * (16 * MiB))


def test_our_own_writes_are_not_charged_twice_against_the_floor(tmp_path):
    """Free space shrinks as we write; reserved-but-unwritten bytes are what must still fit."""
    reservation = 64 * MiB
    budget, state = _budget(tmp_path, reservation, free=3 * GiB + reservation, total=10 * GiB)
    with resources.BoundedFile(tmp_path / "f", budget) as f:
        for _ in range(48):
            f.write(b"a" * MiB)
            state["free"] -= MiB

    assert budget.charged == 48 * MiB


def test_a_failing_free_space_probe_fails_closed(tmp_path):
    def probe():
        raise OSError("statvfs failed")

    with pytest.raises(ExportCapacityError):
        resources.ScratchBudget(10, probe=probe).check()


def test_a_cancelled_export_stops_at_its_next_write(tmp_path):
    budget, _ = _budget(tmp_path, 1000)
    with resources.BoundedFile(tmp_path / "f", budget) as f:
        f.write(b"a")
        budget.cancel()
        with pytest.raises(resources.ExportCancelled):
            f.write(b"b")


# -- The locked S3 transfer path writes through a bounded file -----------------------------------


class _FlakyBody:
    """A ranged GetObject body that drops the connection once, partway through."""

    def __init__(self, data: bytes, fail: dict):
        self._data = data
        self._pos = 0
        self._fail = fail

    def read(self, amt=None):
        from botocore.exceptions import ReadTimeoutError

        if self._fail.get("pending") and self._pos > 0:
            self._fail["pending"] = False
            raise ReadTimeoutError(endpoint_url="https://s3.test")
        amt = len(self._data) - self._pos if amt is None else amt
        chunk = self._data[self._pos : self._pos + amt]
        self._pos += len(chunk)
        return chunk

    def close(self):
        pass


def _fake_s3_client(data: bytes, fail: dict):
    import boto3

    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y")

    def head_object(**kwargs):
        return {"ContentLength": len(data), "ETag": '"e"'}

    def get_object(**kwargs):
        rng = kwargs.get("Range")
        start, end = (0, len(data) - 1)
        if rng:
            first, last = rng.removeprefix("bytes=").split("-")
            start, end = int(first), (int(last) if last else len(data) - 1)
        part = data[start : end + 1]
        return {"Body": _FlakyBody(part, fail), "ContentLength": len(part), "ETag": '"e"'}

    client.head_object = head_object
    client.get_object = get_object
    return client


@pytest.fixture
def real_s3(mode_adapter_factory):
    from app.adapters.storage.s3 import S3StorageProvider
    from app.settings_contract import ComputeMode

    return mode_adapter_factory(S3StorageProvider, mode=ComputeMode.KUBERNETES)


@pytest.mark.asyncio
async def test_a_multipart_s3_download_with_a_retried_range_is_byte_exact_and_charged_once(
    tmp_path, real_s3, monkeypatch
):
    data = os.urandom(20 * MiB + 123)  # three ranges at the default 8 MiB part size
    fail = {"pending": True}
    monkeypatch.setattr(real_s3, "_get_s3_client", lambda creds=None: _fake_s3_client(data, fail))
    budget, _ = _budget(tmp_path, len(data))

    with resources.BoundedFile(tmp_path / "f", budget) as f:
        await real_s3.download_to_file("s3://bucket/key", f)

    assert fail["pending"] is False  # the retry happened
    assert (tmp_path / "f").read_bytes() == data
    assert budget.charged == len(data)


@pytest.mark.asyncio
async def test_an_s3_object_larger_than_its_metadata_is_refused_before_disk_fills(tmp_path, real_s3, monkeypatch):
    data = os.urandom(20 * MiB)
    monkeypatch.setattr(real_s3, "_get_s3_client", lambda creds=None: _fake_s3_client(data, {}))
    budget, _ = _budget(tmp_path, 9 * MiB)

    with pytest.raises(Exception) as exc:
        with resources.BoundedFile(tmp_path / "f", budget) as f:
            await real_s3.download_to_file("s3://bucket/key", f)

    assert isinstance(exc.value, ExportCapacityError) or isinstance(exc.value.__cause__, ExportCapacityError)
    assert os.path.getsize(tmp_path / "f") <= 9 * MiB
