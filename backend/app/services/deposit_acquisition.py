"""plan_7 step 5: bring the deposited files in, and decode what they actually are.

The pipeline route's acquisition is an nf-core/fetchngs run against SRA: 36.7 GB and ~5h15m on
study 22, 49.4 GB and ~17h on study 26. This one is an HTTP download and a decode. That difference
is the entire argument for the deposit route, so this module stays deliberately small: no pipeline
run, no Kubernetes, no notebook.

**Formats are sniffed from magic bytes, never from the extension.** This is not defensive
programming, it is what the real deposits require. Measured 2026-09-05 across the four studies this
project has run:

- ``GSE274331_TPMs_H2AS40-KD.xlsx`` is a TPM table in Excel. It is the only file in that deposit
  worth reproducing from, and study 22 spent five hours re-running the pipeline instead.
- ``GSE213770_DMR_DMB_TET2Neu.xls.gz`` gunzips to ``d0 cf 11 e0 a1 b1 1a e1``, the OLE2 compound
  document magic, so it is genuine legacy BIFF and not a mislabelled TSV.

Reading either as text produces mojibake that parses to zero rows, and a zero-row result is
indistinguishable from a paper that deposited nothing. Refusing by name is the honest outcome.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("bioaf.deposit_acquisition")

# Default ceiling on one study's whole deposit download. A GEO supplementary directory can hold a
# `_RAW.tar` of every BAM in the study (835 MB on GSE274331, and that is a small one). `raw`-
# classified entries are never selectable, so this is the backstop rather than the first line.
DEFAULT_DOWNLOAD_CAP_BYTES = 2 * 1024**3

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy BIFF .xls (OLE2 compound document)
_ZIP_MAGIC = b"PK\x03\x04"  # .xlsx is a zip
_GZIP_MAGIC = b"\x1f\x8b"


class UnreadableDepositError(Exception):
    """A deposited file whose format we cannot turn into a table.

    Carries the FILENAME and the format, because this reaches a scientist at the C1 gate and
    "GSE213770_DMR.xls.gz is a legacy Excel file bioAF cannot read" is actionable where "could not
    parse" is not. ``decoded`` is the shared decoder's account of the failure, with its provenance.
    """

    def __init__(self, message: str, *, decoded=None):
        super().__init__(message)
        self.decoded = decoded


class DepositTooLargeError(Exception):
    """The selected files exceed the download cap."""


def sniff_format(data: bytes) -> str:
    """What this payload actually is: ``gzip``, ``xlsx``, ``xls``, ``text`` or ``binary``.

    From the magic bytes. Depositors mislabel in both directions (a BIFF file named `.xls.gz`, a TSV
    named `.xls`), so the extension is a hint and the bytes are the evidence. plan_8_2 section 1.2: a
    byte-order mark names the text's encoding, so UTF-16 and UTF-32 tables are text, never binary.
    """
    from app.services.table_decoding import _MARKS

    if not data:
        return "binary"
    if data.startswith(_GZIP_MAGIC):
        return "gzip"
    if data.startswith(_OLE2_MAGIC):
        return "xls"
    if data.startswith(_ZIP_MAGIC):
        return "xlsx"
    if any(data.startswith(mark) for mark, _encoding in _MARKS):
        return "text"
    # A table is text. Decode strictly rather than with errors="replace": the whole point is to
    # notice binary rather than turn it into plausible-looking nonsense.
    try:
        data[:4096].decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    return "text"


def _xls_to_tsv(data: bytes, filename: str) -> str:
    """First worksheet of a legacy BIFF .xls as TSV (``table_decoding.xls_to_tsv``)."""
    from app.services.table_decoding import WorkbookError, xls_to_tsv

    try:
        return xls_to_tsv(data, filename)
    except WorkbookError as exc:
        raise UnreadableDepositError(str(exc)) from exc


def _xlsx_to_tsv(data: bytes, filename: str) -> str:
    """First worksheet of an .xlsx as TSV (``table_decoding.xlsx_to_tsv``)."""
    from app.services.table_decoding import WorkbookError, xlsx_to_tsv

    try:
        return xlsx_to_tsv(data, filename)
    except WorkbookError as exc:
        raise UnreadableDepositError(str(exc)) from exc


def decode_deposit(filename: str, raw: bytes) -> tuple[str, str]:
    """Turn a downloaded deposit file into (text, format), or refuse with a reason.

    plan_8_2 section 1.2: the one decoder every consumer shares (``table_decoding.decode_table``).
    Compression first, because a `.gz` wrapper hides the real format underneath and that is exactly how
    a BIFF spreadsheet came to be read as text; then a byte-order mark; then strict UTF-8.
    """
    decoded = decode_deposit_table(filename, raw)
    return decoded.text, decoded.format


def decode_deposit_table(filename: str, raw: bytes):
    """The decoded table with its provenance. A payload past the decompression limit raises
    ``DepositTooLargeError``; any other failure ``UnreadableDepositError`` naming the file and why."""
    from app.services.table_decoding import TOO_LARGE, decode_table

    decoded = decode_table(raw, filename)
    if not decoded.ok:
        if decoded.reason_kind == TOO_LARGE:
            raise DepositTooLargeError(decoded.reason)
        raise UnreadableDepositError(decoded.reason, decoded=decoded)
    return decoded


def total_download_bytes(entries) -> int | None:
    """The summed size of a selection, or None when any size is unknown.

    None rather than a partial sum: a directory listing states no sizes, and treating an unknown as
    zero would let an unbounded file straight through the cap, which is the one thing the cap is
    for.
    """
    total = 0
    for e in entries or []:
        if e.size_bytes is None:
            return None
        total += int(e.size_bytes)
    return total


def check_download_cap(total: int | None, *, cap_bytes: int = DEFAULT_DOWNLOAD_CAP_BYTES) -> bool:
    """Whether the cap was checkable, raising when a known total exceeds it.

    Returns True when the total was known and fits, False when it could not be pre-checked. False is
    not a failure: most deposits list no sizes and are small. It is recorded so the gate can say the
    cap was not pre-checked rather than implying it passed.
    """
    if total is None:
        return False
    if total > cap_bytes:
        raise DepositTooLargeError(
            f"the selected deposit files total {total / 1024**3:.1f} GB, over the "
            f"{cap_bytes / 1024**3:.1f} GB limit for a deposit download"
        )
    return True
