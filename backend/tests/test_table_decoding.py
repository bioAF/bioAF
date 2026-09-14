"""plan_8_2 section 1.2: one table decoder for acquisition, supplement inspection and consistency.

Acquisition called the SAMD1 undifferentiated author table binary, while the consistency worker decoded
the same bytes as UTF-8 and produced NUL characters that PostgreSQL rejects in JSONB. One decoder now
recognizes compression, then the enclosed format, then a byte-order mark before UTF-8 validation or a
binary refusal. Decoding is strict: an uncertain encoding is a typed unresolved outcome, and a NUL in
decoded text invalidates interpretation rather than being stripped.
"""

import bz2
import gzip
import io
from pathlib import Path

import pytest

from app.services import table_decoding as decoding

_FIXTURE = Path(__file__).parent / "fixtures" / "samd1" / "GSE144396_RNA-Seq_DeSeq2.txt.gz"
_TABLE = "gene\tlog2FoldChange\tpadj\nTP53\t1.5\t0.01\nGÉNE\t-2.0\t0.001\n"


def _xlsx(rows) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestTheSamd1Fixture:
    def test_the_gzip_utf16_author_table_decodes_to_its_seven_headers_and_every_row(self):
        decoded = decoding.decode_table(_FIXTURE.read_bytes(), _FIXTURE.name)
        assert decoded.ok
        assert (decoded.compression, decoded.encoding, decoded.format) == ("gzip", "utf-16-le", "text")
        lines = decoded.text.splitlines()
        assert lines[0].split("\t") == ["GeneID", "Base mean", "log2(FC)", "StdErr", "Wald-Stats", "P-value", "P-adj"]
        assert len(lines) - 1 == 31067
        assert "\x00" not in decoded.text

    def test_the_decoded_artifact_records_its_provenance(self):
        raw = _FIXTURE.read_bytes()
        decoded = decoding.decode_table(raw, _FIXTURE.name)
        provenance = decoded.provenance()
        assert provenance["source_checksum"] == "4692db0a730dd34427035a29df70b89e5c0ffd05b2d0b12df7611b7a1535a6d9"
        assert provenance["source_bytes"] == len(raw)
        assert provenance["compression"] == "gzip"
        assert provenance["encoding"] == "utf-16-le"
        assert provenance["format"] == "text"
        assert provenance["decoder_version"] == decoding.DECODER_VERSION
        assert provenance["status"] == "decoded"


class TestByteOrderMarks:
    @pytest.mark.parametrize(
        "codec, encoding",
        [
            ("utf-16-le", "utf-16-le"),
            ("utf-16-be", "utf-16-be"),
            ("utf-32-le", "utf-32-le"),
            ("utf-32-be", "utf-32-be"),
        ],
    )
    def test_each_endianness_decodes_to_the_same_text(self, codec, encoding):
        bom = {
            "utf-16-le": b"\xff\xfe",
            "utf-16-be": b"\xfe\xff",
            "utf-32-le": b"\xff\xfe\x00\x00",
            "utf-32-be": b"\x00\x00\xfe\xff",
        }[codec]
        decoded = decoding.decode_table(bom + _TABLE.encode(codec), "t.txt")
        assert decoded.ok and decoded.encoding == encoding
        assert decoded.text == _TABLE

    def test_a_utf8_byte_order_mark_is_not_part_of_the_first_header(self):
        decoded = decoding.decode_table(b"\xef\xbb\xbf" + _TABLE.encode("utf-8"), "t.txt")
        assert decoded.ok and decoded.encoding == "utf-8-sig"
        assert decoded.text.split("\t")[0] == "gene"

    def test_plain_utf8_keeps_its_characters(self):
        decoded = decoding.decode_table(_TABLE.encode("utf-8"), "t.txt")
        assert decoded.ok and decoded.encoding == "utf-8" and "GÉNE" in decoded.text


class TestUncertainOrUnsafeInput:
    def test_malformed_utf16_after_its_mark_is_unresolved_never_replaced(self):
        decoded = decoding.decode_table(b"\xff\xfe" + "gene\t1\n".encode("utf-16-le") + b"\x00\xd8", "t.txt")
        assert not decoded.ok and decoded.reason_kind == decoding.ENCODING
        assert decoded.text is None

    def test_invalid_utf8_without_a_mark_is_an_uncertain_encoding(self):
        decoded = decoding.decode_table("gène\t1\n".encode("latin-1"), "t.txt")
        assert not decoded.ok and decoded.reason_kind == decoding.ENCODING

    def test_utf16_without_a_mark_is_not_guessed(self):
        decoded = decoding.decode_table(_TABLE.encode("utf-16-le"), "t.txt")
        assert not decoded.ok and decoded.reason_kind in (decoding.ENCODING, decoding.NUL)
        assert decoded.text is None

    def test_a_nul_in_decoded_text_invalidates_the_table_and_is_never_stripped(self):
        decoded = decoding.decode_table(b"gene\tvalue\nTP\x0053\t1\n", "t.txt")
        assert not decoded.ok and decoded.reason_kind == decoding.NUL
        assert decoded.text is None
        assert "\x00" not in decoded.reason

    def test_an_actual_binary_file_is_refused_as_binary(self):
        decoded = decoding.decode_table(b"\x89PNG\r\n\x1a\n" + bytes(range(256)), "figure.png")
        assert not decoded.ok and decoded.reason_kind == decoding.BINARY

    def test_an_empty_payload_is_unresolved(self):
        decoded = decoding.decode_table(b"", "t.txt")
        assert not decoded.ok and decoded.reason_kind == decoding.EMPTY


class TestCompressionThenFormat:
    def test_gzip_text_is_recognized_before_the_format(self):
        decoded = decoding.decode_table(gzip.compress(_TABLE.encode()), "t.txt.gz")
        assert decoded.ok and decoded.compression == "gzip" and decoded.text == _TABLE

    def test_bzip2_text(self):
        decoded = decoding.decode_table(bz2.compress(_TABLE.encode()), "t.txt.bz2")
        assert decoded.ok and decoded.compression == "bzip2" and decoded.text == _TABLE

    def test_a_corrupt_gzip_stream_is_unresolved(self):
        decoded = decoding.decode_table(gzip.compress(_TABLE.encode())[:20], "t.txt.gz")
        assert not decoded.ok and decoded.reason_kind == decoding.CORRUPT

    def test_the_decompression_limit_stops_a_large_payload(self):
        decoded = decoding.decode_table(gzip.compress(b"a\tb\n" * 10000), "t.gz", max_decompressed_bytes=100)
        assert not decoded.ok and decoded.reason_kind == decoding.TOO_LARGE

    def test_an_xlsx_is_read_as_its_first_sheet(self):
        decoded = decoding.decode_table(_xlsx([["gene", "padj"], ["TP53", 0.01]]), "t.xlsx")
        assert decoded.ok and decoded.format == "xlsx"
        assert decoded.text.splitlines()[0] == "gene\tpadj"

    def test_a_gzipped_xlsx(self):
        decoded = decoding.decode_table(gzip.compress(_xlsx([["gene", "padj"], ["TP53", 0.01]])), "t.xlsx.gz")
        assert decoded.ok and (decoded.compression, decoded.format) == ("gzip", "xlsx")

    def test_an_unreadable_workbook_is_unresolved_with_its_name(self):
        decoded = decoding.decode_table(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64, "t.xls")
        assert not decoded.ok and decoded.reason_kind == decoding.UNREADABLE_WORKBOOK
        assert "t.xls" in decoded.reason


class TestTheWordsAReaderIsGiven:
    def test_a_decoding_failure_says_the_file_arrived(self):
        decoded = decoding.decode_table("gène\t1\n".encode("latin-1"), "t.txt")
        assert decoded.reason.startswith("t.txt arrived but could not be interpreted")

    def test_a_diagnostic_excerpt_escapes_control_characters(self):
        assert decoding.safe_excerpt("a\x00b\x07c\td\n") == "a\\x00b\\x07c\\td\\n"

    def test_a_json_payload_is_made_safe_for_storage_without_touching_ordinary_text(self):
        payload = {"reason": "bad \x00 byte", "rows": ["ok", {"x": "\x00"}], "n": 3}
        assert decoding.json_safe(payload) == {"reason": "bad \\x00 byte", "rows": ["ok", {"x": "\\x00"}], "n": 3}
