"""plan_8_3 section 1.1: a downloaded archive is not proof that its contents were inspected.

Study 50's report stated that the authors published no result table, while its own completion facts
recorded three attachments that were never inspected. One of them is the paper's supplementary
archive, which the decoder correctly refused as "an archive holding N files" and nothing then opened.

An archive is expanded through the same bounded path everything else is acquired through: member
count, expanded size, member size, path safety and nesting are all limited, and every member carries
where it came from. Availability is derived from that state, and the states stay apart: not yet
inspected, retrieval failed, an unsupported format, nothing eligible in what WAS inspected, and a
table whose applicability is unresolved are five different facts with five different remedies.
"""

import io
import zipfile

import pytest

from app.services.supplement_archives import (
    ARCHIVE_VERSION,
    MAX_EXPANDED_BYTES,
    MAX_MEMBERS,
    expand_archive,
    is_archive,
)
from app.services.validation_author_table_state import (
    NOT_DEPOSITED,
    NOT_INSPECTED,
    NO_ELIGIBLE_TABLE,
    RETRIEVAL_FAILED,
    UNRESOLVED_APPLICABILITY,
    UNSUPPORTED_FORMAT,
    author_table_state,
)


def _zip(members: dict[str, bytes], *, compress=zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compress) as zf:
        for name, blob in members.items():
            zf.writestr(name, blob)
    return buffer.getvalue()


_TABLE = b"gene\tlog2FoldChange\tpvalue\tpadj\nTP53\t1.5\t0.001\t0.01\n"


class TestRecognisingAnArchive:
    def test_a_multi_member_zip_is_an_archive(self):
        assert is_archive(_zip({"a.tsv": _TABLE, "b.tsv": _TABLE}), "s001.zip") is True

    def test_a_plain_table_is_not(self):
        assert is_archive(_TABLE, "table.tsv") is False

    def test_a_workbook_is_not_an_archive_to_expand(self):
        """An xlsx is a zip on disk and one table to bioAF; the decoder already reads it."""
        assert is_archive(_zip({"xl/workbook.xml": b"<x/>", "[Content_Types].xml": b"<x/>"}), "t.xlsx") is False


class TestExpandingOne:
    def test_every_member_comes_back_with_its_name_and_bytes(self):
        found = expand_archive(_zip({"S1.tsv": _TABLE, "S2.tsv": _TABLE}), "s001.zip")
        assert found["status"] == "expanded"
        assert sorted(m["name"] for m in found["members"]) == ["S1.tsv", "S2.tsv"]
        assert found["members"][0]["bytes"] == _TABLE

    def test_each_member_records_the_archive_it_came_from_and_the_version_that_read_it(self):
        found = expand_archive(_zip({"S1.tsv": _TABLE}), "s001.zip")
        member = found["members"][0]
        assert member["contained_in"] == "s001.zip"
        assert member["archive_version"] == ARCHIVE_VERSION
        assert member["size_bytes"] == len(_TABLE)

    def test_directories_and_publisher_metadata_are_not_members(self):
        found = expand_archive(_zip({"__MACOSX/._S1.tsv": b"junk", "S1.tsv": _TABLE}), "s001.zip")
        assert [m["name"] for m in found["members"]] == ["S1.tsv"]

    def test_a_nested_archive_is_named_and_not_opened(self):
        inner = _zip({"deep.tsv": _TABLE})
        found = expand_archive(_zip({"inner.zip": inner, "S1.tsv": _TABLE}), "s001.zip")
        assert "S1.tsv" in [m["name"] for m in found["members"]]
        nested = next(m for m in found["members"] if m["name"] == "inner.zip")
        assert nested["nested_archive"] is True
        assert nested["bytes"] is None


class TestTheLimits:
    def test_an_archive_with_too_many_members_is_refused_with_its_count(self):
        found = expand_archive(_zip({f"f{i}.tsv": b"x" for i in range(MAX_MEMBERS + 1)}), "s001.zip")
        assert found["status"] == "refused"
        assert found["reason_kind"] == "member_count"
        assert str(MAX_MEMBERS) in found["reason"]

    def test_an_archive_that_would_expand_past_the_limit_is_refused_before_it_is_read(self):
        """A zip bomb declares its expanded size in its own directory; nothing is decompressed."""
        big = _zip({"big.tsv": b"0" * 4096})
        found = expand_archive(big, "s001.zip", max_expanded_bytes=1024)
        assert found["status"] == "refused"
        assert found["reason_kind"] == "expanded_size"

    def test_the_default_expansion_limit_is_bounded(self):
        assert 0 < MAX_EXPANDED_BYTES <= 400 * 1024 * 1024

    @pytest.mark.parametrize("path", ["../escape.tsv", "/etc/passwd", "a/../../b.tsv"])
    def test_a_member_whose_path_escapes_the_archive_is_refused(self, path):
        found = expand_archive(_zip({path: _TABLE}), "s001.zip")
        assert found["status"] == "refused"
        assert found["reason_kind"] == "unsafe_path"

    def test_a_corrupt_archive_is_refused_and_says_so(self):
        found = expand_archive(b"PK\x03\x04 and then nonsense", "s001.zip")
        assert found["status"] == "refused"
        assert found["reason_kind"] == "corrupt"

    def test_a_member_over_the_per_member_limit_is_named_and_left_unread(self):
        found = expand_archive(_zip({"big.tsv": b"0" * 5000, "S1.tsv": _TABLE}), "s001.zip", max_member_bytes=1000)
        big = next(m for m in found["members"] if m["name"] == "big.tsv")
        assert big["bytes"] is None
        assert big["over_limit"] is True
        assert found["status"] == "expanded"


class TestWhatAvailabilityActuallyIs:
    def test_an_uninspected_attachment_is_never_a_claim_that_nothing_was_published(self):
        state = author_table_state(
            supplements=[{"identity": "s001.zip", "resolved": True, "role": "unknown", "inspected": False}]
        )
        assert state["status"] == NOT_INSPECTED
        assert "not" in state["reason"] and "inspected" in state["reason"]
        assert state["status"] != NOT_DEPOSITED

    def test_a_failed_retrieval_is_its_own_state(self):
        state = author_table_state(
            supplements=[{"identity": "S1", "resolved": False, "retrieval": {"status": "failed"}}]
        )
        assert state["status"] == RETRIEVAL_FAILED

    def test_a_format_bioaf_cannot_read_is_its_own_state(self):
        state = author_table_state(
            supplements=[
                {"identity": "S1.pdf", "resolved": True, "inspected": True, "role": "unknown", "format": "pdf"}
            ]
        )
        assert state["status"] == UNSUPPORTED_FORMAT

    def test_inspected_sources_holding_no_result_table_says_exactly_that(self):
        state = author_table_state(
            supplements=[
                {"identity": "S1.tsv", "resolved": True, "inspected": True, "role": "sample_metadata"},
                {"identity": "S2.docx", "resolved": True, "inspected": True, "role": "code"},
            ]
        )
        assert state["status"] == NO_ELIGIBLE_TABLE
        assert "inspected" in state["reason"]
        assert "no result table" in state["reason"]

    def test_a_retrieved_table_whose_contrast_is_not_established_is_unresolved_not_absent(self):
        state = author_table_state(
            supplements=[
                {
                    "identity": "S1.tsv",
                    "resolved": True,
                    "inspected": True,
                    "role": "results_table",
                    "binding": {"status": "candidate"},
                }
            ]
        )
        assert state["status"] == UNRESOLVED_APPLICABILITY

    def test_a_bound_table_is_available(self):
        state = author_table_state(
            supplements=[
                {
                    "identity": "S1.tsv",
                    "resolved": True,
                    "inspected": True,
                    "role": "results_table",
                    "binding": {"status": "established"},
                }
            ]
        )
        assert state["status"] == "available"

    def test_a_paper_that_names_no_supplement_at_all_is_the_only_not_deposited(self):
        state = author_table_state(supplements=[])
        assert state["status"] == NOT_DEPOSITED

    def test_one_uninspected_attachment_holds_the_whole_answer_open(self):
        """Two inspected files with no table plus one nobody opened is not "no table published"."""
        state = author_table_state(
            supplements=[
                {"identity": "S1.tsv", "resolved": True, "inspected": True, "role": "sample_metadata"},
                {"identity": "s001.zip", "resolved": True, "inspected": False, "role": "unknown"},
            ]
        )
        assert state["status"] == NOT_INSPECTED
        assert "s001.zip" in state["reason"]


class TestTheResolutionOpensThem:
    """The whole path: a bundle holding an archive holding the authors' result table."""

    @staticmethod
    async def _resolve(bundle: bytes, references):
        from app.services.supplement_inventory import resolve_supplements

        async def _fetch(url):
            return bundle

        return await resolve_supplements("PMC1", references, fetcher=_fetch)

    @pytest.mark.asyncio
    async def test_a_result_table_inside_an_archive_is_found_and_classified(self):
        inner = _zip({"Table_S1.tsv": _TABLE, "notes.txt": b"some notes\n"})
        rows = await self._resolve(_zip({"s001.zip": inner}), [{"label": "Supplementary Materials"}])
        table = next(r for r in rows if r.get("identity") == "Table_S1.tsv")
        assert table["role"] == "results_table"
        assert table["contained_in"] == "s001.zip"
        assert table["resolved"] is True

    @pytest.mark.asyncio
    async def test_the_archive_itself_stays_on_the_record_as_inspected(self):
        inner = _zip({"Table_S1.tsv": _TABLE})
        rows = await self._resolve(_zip({"s001.zip": inner}), [])
        archive = next(r for r in rows if r.get("identity") == "s001.zip")
        assert archive["inspected"] is True
        assert archive["archive"]["status"] == "expanded"
        assert archive["archive"]["members"] == ["Table_S1.tsv"]

    @pytest.mark.asyncio
    async def test_an_archive_bioaf_refused_is_recorded_as_not_inspected_with_the_reason(self):
        rows = await self._resolve(_zip({"s001.zip": b"PK\x03\x04 nonsense"}), [])
        archive = next(r for r in rows if r.get("identity") == "s001.zip")
        assert archive["inspected"] is False
        assert archive["archive"]["reason_kind"] == "corrupt"

    @pytest.mark.asyncio
    async def test_the_availability_state_follows_what_was_actually_opened(self):
        inner = _zip({"Table_S1.tsv": _TABLE})
        rows = await self._resolve(_zip({"s001.zip": inner}), [])
        state = author_table_state(supplements=rows)
        assert state["status"] in (UNRESOLVED_APPLICABILITY, "available")
        assert state["status"] != NOT_DEPOSITED


class TestItReadsRealEvidenceWithoutRaising:
    """Found by the deployed smoke check: a study's `deposit_inventory` is a dict, not a list."""

    @pytest.mark.parametrize("deposits", [None, {}, {"GSE1": {"files": []}}, "nonsense", [{"identity": "S1"}]])
    def test_a_deposits_value_of_any_shape_is_read_or_ignored(self, deposits):
        state = author_table_state(supplements=[], deposits=deposits)
        assert state["status"] in {
            NOT_DEPOSITED,
            NOT_INSPECTED,
            RETRIEVAL_FAILED,
            UNSUPPORTED_FORMAT,
            NO_ELIGIBLE_TABLE,
            UNRESOLVED_APPLICABILITY,
            "available",
        }

    @pytest.mark.parametrize("supplements", [None, {}, "nonsense", [None, 3]])
    def test_a_supplements_value_of_any_shape_is_read_or_ignored(self, supplements):
        assert author_table_state(supplements=supplements)["status"] == NOT_DEPOSITED
