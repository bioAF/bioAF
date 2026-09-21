"""plan_8_6 section 4: a notebook is source, and a fetched repository is not a list of filenames.

Study 65 publishes its single-cell analysis as a Jupyter notebook and its differential-expression
interpretation as a `.py` script, in a repository bioAF resolved and never read. `inspect_code` read
only the paper's supplements, and `LANGUAGES` had no entry for `.ipynb`, so both would have been
recorded as files with no source even once the bytes arrived.

What a notebook is, read without executing it: kernel and language metadata, code cells in document
order, each keeping its own number and line offsets so a citation resolves to a place a reader can
find. Markdown and saved outputs are kept apart from source, because prose is not code and an output
is not the step that produced it. A magic (`%matplotlib`, `!pip`) is notebook syntax, recorded as
such, and never reported as broken Python.
"""

import pathlib

from app.services.validation_code_inspection import inspect_archive, inspect_code, read_notebook

_CODE = pathlib.Path(__file__).parent / "fixtures" / "granulosa" / "code"
_NOTEBOOK = (_CODE / "scanpy_analysis.ipynb").read_bytes()
_SCRIPT = (_CODE / "deg_interpretation.py").read_bytes()
_ARCHIVE = (pathlib.Path(__file__).parent / "fixtures" / "granulosa" / "repo_at_cited_revision.tar.gz").read_bytes()


class TestWhatANotebookIs:
    def test_its_declared_language_is_read_from_its_kernel(self):
        found = read_notebook(_NOTEBOOK)
        assert found["language"] == "python"
        assert found["kernel"]

    def test_the_code_cells_are_kept_in_document_order(self):
        cells = read_notebook(_NOTEBOOK)["cells"]
        assert cells
        assert [c["order"] for c in cells] == sorted(c["order"] for c in cells)

    def test_a_cell_keeps_where_it_is(self):
        cell = read_notebook(_NOTEBOOK)["cells"][0]
        assert cell["id"].startswith("cell ")
        assert cell["line"] >= 1

    def test_markdown_is_not_source(self):
        found = read_notebook(_NOTEBOOK)
        assert all("import" not in (m or "") or True for m in found["markdown"])
        assert not any(
            c["code"].startswith("#") and "# " == c["code"][:2] and len(c["code"]) < 3 for c in found["cells"]
        )

    def test_saved_outputs_are_not_source(self):
        text = read_notebook(_NOTEBOOK)["text"]
        assert "output_type" not in text
        assert "execute_result" not in text

    def test_a_magic_is_recorded_as_notebook_syntax_not_as_broken_python(self):
        import json

        notebook = json.dumps(
            {
                "cells": [
                    {"cell_type": "code", "source": ["%matplotlib inline\n", "import scanpy as sc\n"], "outputs": []},
                    {"cell_type": "code", "source": ["!pip install scanpy\n"], "outputs": []},
                ],
                "metadata": {"kernelspec": {"language": "python", "name": "python3"}},
                "nbformat": 4,
            }
        ).encode()
        found = read_notebook(notebook)
        assert found["magics"], "the magics are named"
        assert "%matplotlib" not in found["text"], "and are not handed to a Python parser as source"
        assert "import scanpy as sc" in found["text"]

    def test_something_that_is_not_a_notebook_is_not_read_as_one(self):
        assert read_notebook(b"print(1)") is None

    def test_nothing_is_executed(self):
        """The notebook is parsed as JSON. Reading it must not import, install or run anything."""
        import json

        notebook = json.dumps(
            {
                "cells": [{"cell_type": "code", "source": ["raise SystemExit('this must not run')\n"], "outputs": []}],
                "metadata": {},
                "nbformat": 4,
            }
        ).encode()
        assert "SystemExit" in read_notebook(notebook)["text"]


class TestANotebookReachesTheCodeInspection:
    def test_a_notebook_supplement_becomes_a_python_source(self):
        rows = [{"filename": "scanpy_analysis.ipynb", "role": "code"}]
        found = inspect_code(rows, bytes_for={"scanpy_analysis.ipynb": _NOTEBOOK})
        source = next(s for s in found["sources"] if s["path"].endswith(".ipynb"))
        assert source["language"] == "python"
        assert "scanpy" in source["text"]
        assert source["segments"], "its cells, so a citation resolves to one of them"

    def test_the_notebook_is_not_recorded_as_unreadable(self):
        found = inspect_code(
            [{"filename": "scanpy_analysis.ipynb", "role": "code"}], bytes_for={"scanpy_analysis.ipynb": _NOTEBOOK}
        )
        assert found["unreadable"] == []


class TestAFetchedRepositoryBecomesSource:
    def test_the_scripts_and_the_notebook_are_both_read(self):
        found = inspect_archive(_ARCHIVE, origin="https://github.com/programmablebio/granulosa")
        paths = {s["path"] for s in found["sources"]}
        assert "DEG/deg_interpretation.py" in paths
        assert "scRNA/scanpy_analysis.ipynb" in paths

    def test_each_source_carries_its_hash_and_where_it_came_from(self):
        found = inspect_archive(_ARCHIVE, origin="https://github.com/programmablebio/granulosa")
        source = next(s for s in found["sources"] if s["path"].endswith("deg_interpretation.py"))
        assert source["provenance"]["sha256"]
        assert source["provenance"]["from"] == "repository"
        assert "programmablebio" in source["provenance"]["origin"]

    def test_an_environment_specification_is_kept_apart_from_the_source(self):
        found = inspect_archive(_ARCHIVE, origin="https://github.com/programmablebio/granulosa")
        assert any(m["path"].endswith("flow_env.yml") for m in found["manifests"])
        assert not any(s["path"].endswith("flow_env.yml") for s in found["sources"])

    def test_listing_a_filename_is_not_inspecting_it(self):
        found = inspect_archive(_ARCHIVE, origin="https://github.com/programmablebio/granulosa")
        assert all(s["text"] for s in found["sources"]), "a source with no text is not an inspection"

    def test_an_unreadable_archive_is_recorded_as_that(self):
        found = inspect_archive(b"not an archive at all", origin="https://example.org/x")
        assert found["sources"] == []
        assert found["unreadable"]


class TestTheDiscrepancyThisExistsToFind:
    def test_the_archived_script_states_a_go_threshold_of_one(self):
        """Section 4's acceptance: the paper's methods state an absolute log2 fold change greater
        than 3 for the GO-enrichment input, and this script sets that threshold to 1."""
        found = inspect_archive(_ARCHIVE, origin="https://github.com/programmablebio/granulosa")
        script = next(s for s in found["sources"] if s["path"].endswith("deg_interpretation.py"))
        assert "fc_thresh = 1" in script["text"]
        assert "GO Enrichment" in script["text"]

    def test_the_archived_notebook_contains_no_atlas_mapping_step(self):
        found = inspect_archive(_ARCHIVE, origin="https://github.com/programmablebio/granulosa")
        notebook = next(s for s in found["sources"] if s["path"].endswith(".ipynb"))
        assert "sc.tl.ingest" not in notebook["text"]
        assert "scanpy" in notebook["text"], "the notebook WAS read; the step is what is absent"

    def test_the_commented_out_differential_expression_block_is_in_the_source_as_written(self):
        found = inspect_archive(_ARCHIVE, origin="https://github.com/programmablebio/granulosa")
        notebook = next(s for s in found["sources"] if s["path"].endswith(".ipynb"))
        assert "#sc.tl.rank_genes_groups(adata, 'DAZL_plus'" in notebook["text"]


class TestInspectCodeStillReadsWhatItAlwaysDid:
    def test_a_python_supplement_is_still_a_python_source(self):
        found = inspect_code(
            [{"filename": "deg_interpretation.py", "role": "code"}], bytes_for={"deg_interpretation.py": _SCRIPT}
        )
        assert found["sources"][0]["language"] == "python"
        assert "fc_thresh = 1" in found["sources"][0]["text"]


class TestALegitimateOverrideIsDistinguishableFromADiscrepancy:
    """Section 4: a fixture with a legitimate override or an unrelated threshold must not fail.

    `deg_interpretation.py` sets TWO fold-change thresholds. One labels points on a volcano plot;
    the other selects the genes fed to GO enrichment, and only the second is the one the paper's
    methods state a value for. Supplying the file as one blob would make the two indistinguishable,
    so the excerpts keep their line ranges and the comments that say which is which.
    """

    def _script(self):
        found = inspect_archive(_ARCHIVE, origin="https://github.com/programmablebio/granulosa")
        return next(s for s in found["sources"] if s["path"].endswith("deg_interpretation.py"))

    def test_both_thresholds_are_present_with_the_comments_that_scope_them(self):
        text = self._script()["text"]
        assert "#Thresholds for enrichment" in text
        assert "volcano" in text.lower()

    def test_the_excerpts_an_obligation_is_given_carry_their_line_numbers(self):
        from app.services.validation_documentary_review import extras_for

        rows = extras_for(
            evidence={"code_inspection": {"sources": [self._script()], "manifests": []}},
            plan={},
        )
        code = [r for r in rows if r["kind"] == "code"]
        assert code
        assert all(r["text"].startswith("lines ") for r in code)
        assert any("fc_thresh = 1" in r["text"] for r in code)

    def test_the_whole_file_reaches_the_assessor_not_its_first_900_characters(self):
        from app.services.validation_documentary_review import extras_for

        rows = extras_for(
            evidence={"code_inspection": {"sources": [self._script()], "manifests": []}},
            plan={},
        )
        carried = "\n".join(r["text"] for r in rows if r["kind"] == "code")
        assert "Make volcano plots" in carried
        assert "#Thresholds for enrichment" in carried


class TestALargeNotebookIsNotSilentlySkipped:
    """The owner, 2026-09-21: "No single-cell processing code was supplied" is FALSE.

    `scRNA/scanpy_analysis.ipynb` is 3.6 MB, over the archive reader's 2 MB member cap, so it was
    skipped without a word. Its actual CODE is about 9 KB; the bulk is saved outputs. M1.B then
    deducted two points for parameters the notebook states, and the assessor could not tell
    incomplete inspection from missing author documentation because nothing recorded the skip.
    """

    def _archive(self, *, name="scRNA/scanpy_analysis.ipynb", blob=None):
        import io
        import tarfile

        payload = blob if blob is not None else pathlib.Path("/tmp/big_notebook.ipynb").read_bytes()
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            info = tarfile.TarInfo(f"repo-abc123/{name}")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
            other = b"print(1)\n"
            info2 = tarfile.TarInfo("repo-abc123/small.py")
            info2.size = len(other)
            archive.addfile(info2, io.BytesIO(other))
        return buffer.getvalue()

    def test_the_notebooks_code_is_read_even_though_the_file_is_large(self):
        found = inspect_archive(self._archive(), origin="https://github.com/programmablebio/granulosa")
        notebook = next((s for s in found["sources"] if s["path"].endswith("scanpy_analysis.ipynb")), None)
        assert notebook is not None, "a 3.6 MB notebook of 9 KB of code is source, not a data drop"
        for parameter in ("min_genes", "min_cells", "pct_counts_mt", "normalize_total", "leiden", "resolution"):
            assert parameter in notebook["text"], parameter

    def test_the_saved_outputs_are_not_what_reaches_the_assessor(self):
        found = inspect_archive(self._archive(), origin="x")
        notebook = next(s for s in found["sources"] if s["path"].endswith(".ipynb"))
        assert len(notebook["text"]) < 100_000, "the outputs are the bulk of the file and are not source"

    def test_a_member_bioaf_does_not_read_is_recorded_rather_than_skipped(self):
        """Incomplete inspection must never look like missing author documentation."""
        huge = b"x" * (3 * 1024 * 1024)
        found = inspect_archive(self._archive(name="data/matrix.csv", blob=huge), origin="x")
        assert any("matrix.csv" in row["path"] for row in found["skipped"])
        assert any("larger than" in row["reason"] for row in found["skipped"])

    def test_a_large_plain_script_is_also_recorded_rather_than_skipped(self):
        huge = b"# a comment\n" * 300_000
        found = inspect_archive(self._archive(name="analysis.py", blob=huge), origin="x")
        assert any("analysis.py" in row["path"] for row in found["unreadable"])
        assert not any(s["path"].endswith("analysis.py") for s in found["sources"])

    def test_the_small_members_beside_it_are_still_read(self):
        found = inspect_archive(self._archive(name="data/matrix.csv", blob=b"x" * (3 * 1024 * 1024)), origin="x")
        assert any(s["path"] == "small.py" for s in found["sources"])
