"""change_7.1 section 7: one file is one resource, however many ways the paper names it.

The deployed rerun (study 32) listed Supplemental Files S1, S2 and S3 TWICE each: once as the JATS
media element that carries the filename, once as the prose reference that carries the identifier a
reader would recognise. Same bytes, same checksum, two rows, and a reader counting the paper's
attachments gets the wrong number.

And retrieval has to propagate. Study 32 recorded S2 as `exists: yes, accessible: not_attempted`
while the same run had downloaded it, read the R Markdown inside it and classified it as code. An
artifact cannot be retrieved and not-attempted at once.
"""

import pytest

from app.services.supplement_inventory import CODE, SAMPLE_METADATA, merge_resource_identity


class TestOneFileIsOneResource:
    def test_a_named_reference_and_a_manifest_entry_merge(self):
        rows = merge_resource_identity(
            [
                {"label": "Supplemental Material (s1.txt)", "filename": "s1.txt", "role": SAMPLE_METADATA,
                 "resolved": True, "row_count": 54},
                {"label": "Supplemental File S1", "filename": "s1.txt", "role": SAMPLE_METADATA,
                 "resolved": True, "row_count": 54},
            ]
        )
        assert len(rows) == 1

    def test_the_reader_facing_identifier_wins_the_label(self):
        """'Supplemental File S1' is what the paper's prose cites and what a reader looks for.
        'Supplemental Material (s1.txt)' is the publisher's packaging."""
        rows = merge_resource_identity(
            [
                {"label": "Supplemental Material (s1.txt)", "filename": "s1.txt", "resolved": True},
                {"label": "Supplemental File S1", "filename": "s1.txt", "resolved": True},
            ]
        )
        assert rows[0]["label"] == "Supplemental File S1"

    def test_every_way_the_paper_referred_to_it_is_kept(self):
        rows = merge_resource_identity(
            [
                {"label": "Supplemental Material (s1.txt)", "filename": "s1.txt", "resolved": True},
                {"label": "Supplemental File S1", "filename": "s1.txt", "resolved": True},
            ]
        )
        assert set(rows[0]["references"]) == {"Supplemental Material (s1.txt)", "Supplemental File S1"}

    def test_measurements_survive_the_merge(self):
        rows = merge_resource_identity(
            [
                {"label": "Supplemental Material (s3.txt)", "filename": "s3.txt", "resolved": True},
                {"label": "Supplemental File S3", "filename": "s3.txt", "resolved": True,
                 "row_count": 194, "threshold_splits": {"abs_log2fc>2": 88}},
            ]
        )
        assert rows[0]["row_count"] == 194
        assert rows[0]["threshold_splits"] == {"abs_log2fc>2": 88}

    def test_an_established_role_beats_an_unknown_one(self):
        rows = merge_resource_identity(
            [
                {"label": "Supplemental Material (s2.docx)", "filename": "s2.docx", "resolved": True, "role": "unknown"},
                {"label": "Supplemental File S2", "filename": "s2.docx", "resolved": True, "role": CODE},
            ]
        )
        assert rows[0]["role"] == CODE

    def test_unresolved_references_stay_separate(self):
        """Two references bioAF could not resolve are not known to be the same file, and merging
        them would invent a fact."""
        rows = merge_resource_identity(
            [
                {"label": "Supplemental File S4", "filename": None, "resolved": False},
                {"label": "Supplemental File S5", "filename": None, "resolved": False},
            ]
        )
        assert len(rows) == 2

    def test_different_files_stay_separate(self):
        rows = merge_resource_identity(
            [
                {"label": "Supplemental File S1", "filename": "s1.txt", "resolved": True},
                {"label": "Supplemental File S3", "filename": "s3.txt", "resolved": True},
            ]
        )
        assert len(rows) == 2


class TestRetrievalPropagatesToTheCodeRows:
    def test_a_retrieved_code_supplement_is_accessible(self):
        """Study 32 said `exists: yes, accessible: not_attempted` for a file it had downloaded and
        read. Retrieval IS the accessibility answer."""
        from app.services.supplement_inventory import apply_retrieval_to_code_sources

        sources = [
            {"kind": "supplementary", "url": None, "identifier": "Supplemental File S2",
             "exists": "yes", "accessible": "not_attempted", "accessible_reason": None}
        ]
        updated = apply_retrieval_to_code_sources(
            sources, [{"label": "Supplemental File S2", "filename": "s2.docx", "role": CODE, "resolved": True}]
        )
        assert updated[0]["accessible"] == "yes"

    def test_the_reason_names_what_was_retrieved(self):
        from app.services.supplement_inventory import apply_retrieval_to_code_sources

        sources = [{"kind": "supplementary", "url": None, "identifier": "Supplemental File S2",
                    "exists": "yes", "accessible": "not_attempted", "accessible_reason": None}]
        updated = apply_retrieval_to_code_sources(
            sources, [{"label": "Supplemental File S2", "filename": "s2.docx", "role": CODE, "resolved": True}]
        )
        assert "s2.docx" in (updated[0]["accessible_reason"] or "")

    def test_a_reference_that_was_not_retrieved_stays_not_attempted(self):
        from app.services.supplement_inventory import apply_retrieval_to_code_sources

        sources = [{"kind": "supplementary", "url": None, "identifier": "Supplemental File S9",
                    "exists": "yes", "accessible": "not_attempted", "accessible_reason": None}]
        updated = apply_retrieval_to_code_sources(
            sources, [{"label": "Supplemental File S9", "filename": None, "resolved": False}]
        )
        assert updated[0]["accessible"] == "not_attempted"

    def test_extraction_status_is_separate_from_retrieval(self):
        """Section 7: retrieval, code extraction and execution have separate statuses. A DOCX we
        downloaded but could not read is accessible and unextracted."""
        from app.services.supplement_inventory import apply_retrieval_to_code_sources

        sources = [{"kind": "supplementary", "url": None, "identifier": "Supplemental File S2",
                    "exists": "yes", "accessible": "not_attempted", "accessible_reason": None}]
        updated = apply_retrieval_to_code_sources(
            sources,
            [{"label": "Supplemental File S2", "filename": "s2.docx", "role": "unknown", "resolved": True}],
        )
        assert updated[0]["accessible"] == "yes"
        assert updated[0]["code_extracted"] is False


@pytest.mark.parametrize("rows", [[], None])
def test_merging_nothing_yields_nothing(rows):
    assert merge_resource_identity(rows) == []
