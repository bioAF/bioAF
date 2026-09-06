"""plan_7 step 5: bring the deposited files in and decode them.

The pipeline route's acquisition is a fetchngs run against SRA, measured at 36.7 GB / ~5h15m on
study 22. This one is an HTTP download and a decode, which is why the route exists.

The decoding half is the part real deposits made necessary. Measured 2026-09-05 on the four studies
this project has run: `GSE274331_TPMs_H2AS40-KD.xlsx` is a TPM table in Excel, and
`GSE213770_DMR_DMB_TET2Neu.xls.gz` gunzips to OLE2 magic, so it is genuine legacy BIFF rather than a
mislabelled TSV. Reading either as text yields mojibake that parses to zero rows and reads exactly
like a deposit with no findings in it.
"""

import gzip
import io

import pytest

from app.services.deposit_acquisition import (
    DepositTooLargeError,
    UnreadableDepositError,
    decode_deposit,
    sniff_format,
    total_download_bytes,
)
from app.services.literature.deposit_inventory_service import DepositEntry


def _xlsx_bytes(rows: list[list]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


# ---- format sniffing, from magic bytes rather than the extension ----


def test_sniffs_a_real_xlsx():
    assert sniff_format(_xlsx_bytes([["gene", "s1"], ["TP53", 5]])) == "xlsx"


def test_sniffs_legacy_biff_xls():
    """The exact magic GSE213770 gunzips to."""
    assert sniff_format(_OLE2_MAGIC + b"\x00" * 64) == "xls"


def test_sniffs_plain_text():
    assert sniff_format(b"gene\ts1\ts2\nTP53\t5\t6\n") == "text"


def test_sniffs_gzip():
    assert sniff_format(gzip.compress(b"gene\ts1\n")) == "gzip"


def test_the_extension_never_decides():
    """Depositors mislabel in both directions. GSE213770 names a BIFF file `.xls.gz`, and plenty of
    deposits name a TSV `.xls`. The bytes are the evidence."""
    assert sniff_format(b"gene\ts1\nTP53\t5\n") == "text"
    assert sniff_format(_xlsx_bytes([["a", 1]])) == "xlsx"


# ---- decoding ----


def test_decodes_plain_text_unchanged():
    text, fmt = decode_deposit("x.tsv", b"gene\ts1\nTP53\t5\n")
    assert fmt == "text"
    assert text == "gene\ts1\nTP53\t5\n"


def test_gunzips_before_deciding():
    """A `.gz` wrapper hides the real format, which is how the BIFF file got read as text."""
    text, fmt = decode_deposit("x.tsv.gz", gzip.compress(b"gene\ts1\nTP53\t5\n"))
    assert fmt == "text"
    assert text.startswith("gene\ts1")


def test_decodes_xlsx_to_tsv():
    """openpyxl is already a dependency (requirements.txt), so .xlsx needs nothing new."""
    text, fmt = decode_deposit("GSE274331_TPMs.xlsx", _xlsx_bytes([["gene", "s1", "s2"], ["TP53", 5, 6]]))
    assert fmt == "xlsx"
    assert text.splitlines()[0] == "gene\ts1\ts2"
    assert text.splitlines()[1] == "TP53\t5\t6"


def test_a_gzipped_xlsx_is_still_decoded():
    text, fmt = decode_deposit("x.xlsx.gz", gzip.compress(_xlsx_bytes([["gene", "s1"], ["TP53", 5]])))
    assert fmt == "xlsx"
    assert "TP53" in text


def test_legacy_xls_is_refused_by_name_rather_than_parsed_into_mojibake():
    """`xlrd` is NOT a dependency, so a BIFF file cannot be read today. Refusing with a reason that
    names the format is the honest outcome: decoding it as text produces a table that parses to zero
    rows, which is indistinguishable from a paper that deposited nothing.

    Adding xlrd is a one-line requirements change and an owner decision, not a silent one.
    """
    with pytest.raises(UnreadableDepositError) as exc:
        decode_deposit("GSE213770_DMR.xls.gz", gzip.compress(_OLE2_MAGIC + b"\x00" * 64))
    assert "xls" in str(exc.value).lower()
    assert "GSE213770_DMR.xls.gz" in str(exc.value)


def test_an_empty_xlsx_is_refused_rather_than_returned_as_an_empty_table():
    with pytest.raises(UnreadableDepositError):
        decode_deposit("empty.xlsx", _xlsx_bytes([]))


def test_a_binary_blob_of_no_known_format_is_refused():
    with pytest.raises(UnreadableDepositError):
        decode_deposit("mystery.dat", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


def test_utf8_is_preserved():
    text, _ = decode_deposit("x.tsv", "gene\tsample\nGÉNE\t5\n".encode("utf-8"))
    assert "GÉNE" in text


# ---- the download cap ----


def _entry(name, cls, size):
    return DepositEntry(filename=name, url=f"https://x/{name}", classification=cls, level="series", size_bytes=size)


def test_total_download_bytes_sums_the_selection():
    assert total_download_bytes([_entry("a.tsv", "matrix_counts", 100), _entry("b.tsv", "metadata", 50)]) == 150


def test_an_unknown_size_does_not_silently_count_as_zero():
    """A directory listing states no sizes. Treating None as 0 would let an unbounded file through
    the cap, which is the one thing the cap exists to stop."""
    assert total_download_bytes([_entry("a.tsv", "matrix_counts", None)]) is None


def test_the_cap_refuses_before_downloading():
    from app.services.deposit_acquisition import check_download_cap

    with pytest.raises(DepositTooLargeError) as exc:
        check_download_cap(3_000_000_000, cap_bytes=2_000_000_000)
    assert "2" in str(exc.value)


def test_the_cap_allows_an_unknown_total_but_says_so():
    """An unknown total is not a refusal: most deposits list no sizes and are small. The per-file
    guard during download is what bounds it, and this is recorded so the gate can say the cap was
    not pre-checked."""
    from app.services.deposit_acquisition import check_download_cap

    assert check_download_cap(None, cap_bytes=2_000_000_000) is False
    assert check_download_cap(1_000, cap_bytes=2_000_000_000) is True


# ---- the driver handler ----

import gzip as _gzip  # noqa: E402

import pytest_asyncio  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.models.file import File  # noqa: E402
from app.models.validation_study import ValidationStudy  # noqa: E402
from app.services.validation_driver_service import ValidationDriverService  # noqa: E402


class _FakeStorage:
    """Records what was written, so a test asserts the bytes landed without a bucket."""

    def __init__(self):
        self.written: dict[str, bytes] = {}

    async def write_bytes(self, uri, data, *, content_type="application/octet-stream"):
        self.written[uri] = data

    async def write_text(self, uri, text, *, content_type="text/plain"):
        self.written[uri] = text.encode()


def _bytes_fetcher(pages: dict):
    async def fetch(url: str) -> bytes:
        if url not in pages:
            raise RuntimeError(f"404 {url}")
        return pages[url]

    return fetch


_MATRIX_TSV = b"gene\tWT_1\tWT_2\tKO_1\tKO_2\nTP53\t10\t12\t50\t55\nGAPDH\t100\t110\t105\t99\n"


@pytest_asyncio.fixture
async def deposit_study(session, admin_user):
    """A study approved onto the deposit route, sitting at acquiring_processed."""
    study = ValidationStudy(
        organization_id=admin_user.organization_id,
        requested_by_user_id=admin_user.id,
        source_accession="GSE1",
        state="acquiring_processed",
        evidence_json={
            "route": "deposit",
            "deposit_selection": {
                "primary_matrix": "GSE1_counts.tsv",
                "matrix_files": ["GSE1_counts.tsv"],
                "metadata_file": "GSE1_meta.tsv",
                "value_type": "counts",
            },
        },
    )
    session.add(study)
    await session.flush()
    return study


@pytest.mark.asyncio
async def test_the_deposit_lands_as_files_on_a_new_experiment(session, deposit_study, monkeypatch):
    """No pipeline run, no experiment set-up from a samplesheet: an HTTP download and a File row.
    The experiment still exists, so a deposit-route study looks like any other in the UI."""
    storage = _FakeStorage()
    base = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSEnnn/GSE1/suppl/"
    pages = {base + "GSE1_counts.tsv": _MATRIX_TSV, base + "GSE1_meta.tsv": b"sample\tcondition\nWT_1\tWT\n"}

    await ValidationDriverService._handle_acquiring_processed(
        session, deposit_study, fetcher=_bytes_fetcher(pages), storage_adapter=storage
    )

    assert deposit_study.experiment_id is not None
    files = (
        (await session.execute(select(File).where(File.experiment_id == deposit_study.experiment_id))).scalars().all()
    )
    assert {f.filename for f in files} == {"GSE1_counts.tsv", "GSE1_meta.tsv"}
    assert all(f.source_type == "external_deposit" for f in files)
    assert {f.artifact_type for f in files} == {"deposited_matrix", "deposited_metadata"}


@pytest.mark.asyncio
async def test_the_study_advances_to_inspection(session, deposit_study):
    storage = _FakeStorage()
    base = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSEnnn/GSE1/suppl/"
    pages = {base + "GSE1_counts.tsv": _MATRIX_TSV, base + "GSE1_meta.tsv": b"sample\tcondition\n"}
    await ValidationDriverService._handle_acquiring_processed(
        session, deposit_study, fetcher=_bytes_fetcher(pages), storage_adapter=storage
    )
    assert deposit_study.state == "inspecting_deposit"


@pytest.mark.asyncio
async def test_the_md5_of_what_was_downloaded_is_recorded(session, deposit_study):
    """GEO supplementary files can be revised in place, so the checksum is the only thing that makes
    a deposit-route verdict reproducible later."""
    import hashlib

    storage = _FakeStorage()
    base = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSEnnn/GSE1/suppl/"
    await ValidationDriverService._handle_acquiring_processed(
        session,
        deposit_study,
        fetcher=_bytes_fetcher({base + "GSE1_counts.tsv": _MATRIX_TSV, base + "GSE1_meta.tsv": b"a\tb\n"}),
        storage_adapter=storage,
    )
    deposit = deposit_study.evidence_json["deposit"]
    assert deposit["files"][0]["md5"] == hashlib.md5(_MATRIX_TSV).hexdigest()
    assert deposit["files"][0]["url"].endswith("GSE1_counts.tsv")


@pytest.mark.asyncio
async def test_a_gzipped_deposit_is_stored_decoded(session, deposit_study):
    """Stored decoded so step 8's notebook reads a table rather than re-deriving the format."""
    storage = _FakeStorage()
    base = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSEnnn/GSE1/suppl/"
    await ValidationDriverService._handle_acquiring_processed(
        session,
        deposit_study,
        fetcher=_bytes_fetcher(
            {base + "GSE1_counts.tsv": _gzip.compress(_MATRIX_TSV), base + "GSE1_meta.tsv": b"a\tb\n"}
        ),
        storage_adapter=storage,
    )
    stored = [v for k, v in storage.written.items() if "counts" in k][0]
    assert b"TP53" in stored


@pytest.mark.asyncio
async def test_an_unreadable_deposit_holds_for_a_human_rather_than_erroring(session, deposit_study):
    """A legacy .xls is a deposit problem, not an infrastructure failure. The study stays on the
    deposit route with a reason a scientist can act on, and the gate can escalate to raw reads."""
    storage = _FakeStorage()
    base = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSEnnn/GSE1/suppl/"
    await ValidationDriverService._handle_acquiring_processed(
        session,
        deposit_study,
        fetcher=_bytes_fetcher({base + "GSE1_counts.tsv": _OLE2_MAGIC + b"\x00" * 64, base + "GSE1_meta.tsv": b"a\n"}),
        storage_adapter=storage,
    )
    assert deposit_study.state == "acquiring_processed"
    assert "xls" in deposit_study.evidence_json["deposit_failed"]["reason"].lower()


@pytest.mark.asyncio
async def test_a_missing_file_holds_rather_than_downloading_a_partial_deposit(session, deposit_study):
    storage = _FakeStorage()
    base = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSEnnn/GSE1/suppl/"
    await ValidationDriverService._handle_acquiring_processed(
        session, deposit_study, fetcher=_bytes_fetcher({base + "GSE1_meta.tsv": b"a\n"}), storage_adapter=storage
    )
    assert deposit_study.state == "acquiring_processed"
    assert deposit_study.evidence_json.get("deposit") is None


@pytest.mark.asyncio
async def test_reentry_does_not_download_twice(session, deposit_study):
    """The driver ticks repeatedly. Re-downloading on every tick would hammer NCBI and duplicate the
    File rows."""
    storage = _FakeStorage()
    calls: list[str] = []

    async def counting(url):
        calls.append(url)
        return _MATRIX_TSV if "counts" in url else b"a\tb\n"

    await ValidationDriverService._handle_acquiring_processed(
        session, deposit_study, fetcher=counting, storage_adapter=storage
    )
    n = len(calls)
    deposit_study.state = "acquiring_processed"  # pretend the tick came round again
    await ValidationDriverService._handle_acquiring_processed(
        session, deposit_study, fetcher=counting, storage_adapter=storage
    )
    assert len(calls) == n


@pytest.mark.asyncio
async def test_a_study_with_no_selection_holds_for_the_gate(session, admin_user):
    """Assisted mode reaches acquiring_processed with nothing chosen yet. That is a wait, not a
    failure."""
    study = ValidationStudy(
        organization_id=admin_user.organization_id,
        requested_by_user_id=admin_user.id,
        source_accession="GSE1",
        state="acquiring_processed",
        evidence_json={"route": "deposit"},
    )
    session.add(study)
    await session.flush()
    await ValidationDriverService._handle_acquiring_processed(
        session, study, fetcher=_bytes_fetcher({}), storage_adapter=_FakeStorage()
    )
    assert study.state == "acquiring_processed"
