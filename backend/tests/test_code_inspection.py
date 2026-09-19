"""plan_8_4 section 6.2: the source a paper supplied, extracted from what bioAF already holds.

Discovery is not inspection. A repository that exists, a supplement that was retrieved and a role that
says "code" are three facts about a file, and none of them is source text a parser can read. This is
the step that turns held bytes into source, with the file boundaries and the provenance intact, and
refuses where the format does not support it: unsupported extraction stays undetermined, never a guess.
"""

from app.services.validation_code_inspection import inspect_code

_PY = b"import os\n\n\ndef main():\n    print(os.getcwd())\n\n\nif __name__ == '__main__':\n    main()\n"
_R = b"library(DESeq2)\nres <- results(dds)\n"
_REQS = b"numpy==1.26.4\npandas==2.2.1\n"


def _supplement(filename, role="code", **kw):
    return {"filename": filename, "role": role, "resolved": True, **kw}


class TestWhatBecomesSource:
    def test_a_python_file_becomes_source_with_its_provenance(self):
        found = inspect_code([_supplement("analysis.py")], bytes_for={"analysis.py": _PY})
        (source,) = found["sources"]
        assert source["path"] == "analysis.py"
        assert source["language"] == "python"
        assert source["text"].startswith("import os")
        assert source["provenance"]["from"] == "supplement"
        assert source["provenance"]["sha256"]

    def test_an_r_file_becomes_source_in_a_language_with_no_parser(self):
        """It is still source, and still recorded. What bioAF cannot do is PARSE it, which the code
        checks say for themselves rather than this step pretending the file does not exist."""
        (source,) = inspect_code([_supplement("AllRCode.R")], bytes_for={"AllRCode.R": _R})["sources"]
        assert source["language"] == "r"

    def test_a_manifest_is_kept_apart_from_the_source(self):
        found = inspect_code(
            [_supplement("analysis.py"), _supplement("requirements.txt", role="supporting_input")],
            bytes_for={"analysis.py": _PY, "requirements.txt": _REQS},
        )
        assert [s["path"] for s in found["sources"]] == ["analysis.py"]
        assert [m["path"] for m in found["manifests"]] == ["requirements.txt"]

    def test_each_file_keeps_its_own_boundary(self):
        found = inspect_code(
            [_supplement("a.py"), _supplement("b.py")],
            bytes_for={"a.py": b"A = 1\n", "b.py": b"B = 2\n"},
        )
        assert sorted(s["path"] for s in found["sources"]) == ["a.py", "b.py"]
        assert all(len(s["text"].splitlines()) == 1 for s in found["sources"])


class TestWhatDoesNot:
    def test_a_role_that_says_code_with_no_bytes_in_hand_yields_nothing(self):
        found = inspect_code([_supplement("analysis.py")], bytes_for={})
        assert found["sources"] == []
        assert found["unreadable"][0]["reason"]

    def test_a_document_bioaf_cannot_split_into_files_is_recorded_as_unreadable(self):
        """Groff's code is a .docx. Extracting compilable source from a word-processed document, with
        its file boundaries, is not something bioAF does, and guessing would make up a file that was
        never supplied."""
        found = inspect_code([_supplement("AllRCode_Review.docx")], bytes_for={"AllRCode_Review.docx": b"PK\x03\x04junk"})
        assert found["sources"] == []
        (row,) = found["unreadable"]
        assert row["path"] == "AllRCode_Review.docx"
        assert "boundaries" in row["reason"] or "document" in row["reason"]

    def test_a_results_table_is_not_code(self):
        found = inspect_code(
            [_supplement("siggenes.txt", role="results_table")], bytes_for={"siggenes.txt": b"gene\tlfc\nA\t1\n"}
        )
        assert found["sources"] == []

    def test_bytes_that_do_not_decode_are_recorded_rather_than_forced(self):
        found = inspect_code([_supplement("analysis.py")], bytes_for={"analysis.py": b"\xff\xfe\x00\x01\x02"})
        assert found["sources"] == []
        assert found["unreadable"]


class TestTheRecordIsWhatTheAdapterReads:
    def test_it_is_the_shape_the_code_checks_take(self):
        from app.services.validation_code_checks import assess_code
        from app.services.validation_rubric_v3 import VERIFIED

        found = inspect_code(
            [_supplement("analysis.py"), _supplement("requirements.txt", role="supporting_input")],
            bytes_for={"analysis.py": _PY, "requirements.txt": _REQS},
        )
        assessed = assess_code(sources=found["sources"], manifests=found["manifests"], defects=None)
        assert assessed["C1.A"]["outcome"] == VERIFIED


class TestItRunsWhereTheStudysBytesAreRead:
    """plan_8_4 section 6.2: on already held artifacts, at the point their bytes are in hand. The
    supplement bundle is downloaded once and its members are not addressable afterwards, so an
    inspection that ran later would have to fetch the bundle again to read a file bioAF already had."""

    def test_the_supplement_resolution_hands_back_the_bytes_of_code_and_manifests(self):
        import asyncio
        import io
        import zipfile

        from app.services.supplement_inventory import resolve_supplements

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("analysis.py", "import os\nprint(os.getcwd())\n")
            archive.writestr("requirements.txt", "numpy==1.26.4\n")
            archive.writestr("siggenes.txt", "gene\tlfc\nA\t1\n")

        async def _fetcher(_url):
            return buffer.getvalue()

        held: dict[str, bytes] = {}
        asyncio.run(
            resolve_supplements(
                "PMC1",
                [{"label": "analysis.py", "kind": "attachment"}],
                fetcher=_fetcher,
                code_bytes=held,
            )
        )
        assert "analysis.py" in held
        assert "requirements.txt" in held
        assert "siggenes.txt" not in held, "a results table is not code and its bytes are not kept"
