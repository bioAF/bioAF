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
                {
                    "label": "Supplemental Material (s1.txt)",
                    "filename": "s1.txt",
                    "role": SAMPLE_METADATA,
                    "resolved": True,
                    "row_count": 54,
                },
                {
                    "label": "Supplemental File S1",
                    "filename": "s1.txt",
                    "role": SAMPLE_METADATA,
                    "resolved": True,
                    "row_count": 54,
                },
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
                {
                    "label": "Supplemental File S3",
                    "filename": "s3.txt",
                    "resolved": True,
                    "row_count": 194,
                    "threshold_splits": {"abs_log2fc>2": 88},
                },
            ]
        )
        assert rows[0]["row_count"] == 194
        assert rows[0]["threshold_splits"] == {"abs_log2fc>2": 88}

    def test_an_established_role_beats_an_unknown_one(self):
        rows = merge_resource_identity(
            [
                {
                    "label": "Supplemental Material (s2.docx)",
                    "filename": "s2.docx",
                    "resolved": True,
                    "role": "unknown",
                },
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
            {
                "kind": "supplementary",
                "url": None,
                "identifier": "Supplemental File S2",
                "exists": "yes",
                "accessible": "not_attempted",
                "accessible_reason": None,
            }
        ]
        updated = apply_retrieval_to_code_sources(
            sources, [{"label": "Supplemental File S2", "filename": "s2.docx", "role": CODE, "resolved": True}]
        )
        assert updated[0]["accessible"] == "yes"

    def test_the_reason_names_what_was_retrieved(self):
        from app.services.supplement_inventory import apply_retrieval_to_code_sources

        sources = [
            {
                "kind": "supplementary",
                "url": None,
                "identifier": "Supplemental File S2",
                "exists": "yes",
                "accessible": "not_attempted",
                "accessible_reason": None,
            }
        ]
        updated = apply_retrieval_to_code_sources(
            sources, [{"label": "Supplemental File S2", "filename": "s2.docx", "role": CODE, "resolved": True}]
        )
        assert "s2.docx" in (updated[0]["accessible_reason"] or "")

    def test_a_reference_that_was_not_retrieved_stays_not_attempted(self):
        from app.services.supplement_inventory import apply_retrieval_to_code_sources

        sources = [
            {
                "kind": "supplementary",
                "url": None,
                "identifier": "Supplemental File S9",
                "exists": "yes",
                "accessible": "not_attempted",
                "accessible_reason": None,
            }
        ]
        updated = apply_retrieval_to_code_sources(
            sources, [{"label": "Supplemental File S9", "filename": None, "resolved": False}]
        )
        assert updated[0]["accessible"] == "not_attempted"

    def test_extraction_status_is_separate_from_retrieval(self):
        """Section 7: retrieval, code extraction and execution have separate statuses. A DOCX we
        downloaded but could not read is accessible and unextracted."""
        from app.services.supplement_inventory import apply_retrieval_to_code_sources

        sources = [
            {
                "kind": "supplementary",
                "url": None,
                "identifier": "Supplemental File S2",
                "exists": "yes",
                "accessible": "not_attempted",
                "accessible_reason": None,
            }
        ]
        updated = apply_retrieval_to_code_sources(
            sources,
            [{"label": "Supplemental File S2", "filename": "s2.docx", "role": "unknown", "resolved": True}],
        )
        assert updated[0]["accessible"] == "yes"
        assert updated[0]["code_extracted"] is False


@pytest.mark.parametrize("rows", [[], None])
def test_merging_nothing_yields_nothing(rows):
    assert merge_resource_identity(rows) == []


# ---- change_7.3 section 2: identity from the manifest, before and after retrieval -----------------
#
# Identity was a side effect of a successful download. When the bundle failed, "Supplemental File S2"
# and `..._Supplemental_File_2_AllRCode_Review.docx` were never compared, although the article itself
# links them, and study 34 listed eight "supplements" for four attachments and one index page.

import pathlib  # noqa: E402

from app.services.supplement_inventory import parse_jats_supplements  # noqa: E402

_GROFF_JATS = (pathlib.Path(__file__).parent / "fixtures" / "groff" / "fulltext_jats.xml").read_text()

_XLINK = 'xmlns:xlink="http://www.w3.org/1999/xlink"'


def _jats(*, media: list[tuple[str, str | None, str]], body: str) -> str:
    """A minimal JATS article: one supplementary-material block holding ``media`` as
    (href, xlink:role or None, mimetype/subtype), and ``body`` as the prose."""
    elements = []
    for href, role, mime in media:
        mimetype, _, subtype = mime.partition("/")
        role_attr = f' xlink:role="{role}"' if role else ""
        elements.append(
            f'<media {_XLINK}{role_attr} mimetype="{mimetype}" mime-subtype="{subtype}" xlink:href="{href}"/>'
        )
    return (
        "<article><body>"
        f"<p>{body}</p>"
        '<supplementary-material id="S"><caption><title>Supplemental Material</title></caption>'
        + "".join(elements)
        + "</supplementary-material></body></article>"
    )


def _by_kind(rows: list[dict], kind: str) -> list[dict]:
    return [r for r in rows if r.get("kind") == kind]


class TestTheManifestEstablishesIdentityWithoutBytes:
    def test_groff_is_four_attachments_and_one_index_page(self):
        rows = parse_jats_supplements(_GROFF_JATS)
        assert len(_by_kind(rows, "attachment")) == 4
        assert len(_by_kind(rows, "index")) == 1
        assert _by_kind(rows, "reference") == []

    def test_the_prose_references_are_aliases_of_the_attached_files(self):
        rows = parse_jats_supplements(_GROFF_JATS)
        by_filename = {r["filename"]: r for r in _by_kind(rows, "attachment")}
        file_2 = by_filename["supp_gr.252981.119_Supplemental_File_2_AllRCode_Review.docx"]
        assert "Supplemental File S2" in file_2["references"]
        assert file_2["label"] == "Supplemental File S2"
        for number in ("1", "3"):
            match = next(r for r in by_filename.values() if f"Supplemental_File_{number}_" in r["filename"])
            assert f"Supplemental File S{number}" in match["references"]

    def test_the_identity_holds_while_nothing_has_been_retrieved(self):
        rows = parse_jats_supplements(_GROFF_JATS)
        s2 = next(r for r in rows if "Supplemental File S2" in r.get("references", []))
        assert s2["resolved"] is False
        assert s2["identity"] == "supp_gr.252981.119_Supplemental_File_2_AllRCode_Review.docx"
        assert s2["retrieval"]["status"] == "not_attempted"

    def test_where_each_artifact_was_named_is_recorded(self):
        rows = parse_jats_supplements(_GROFF_JATS)
        s2 = next(r for r in rows if "Supplemental File S2" in r.get("references", []))
        assert set(s2["identified_in"]) == {"article_manifest", "prose"}
        materials = next(r for r in rows if r["filename"].endswith("Supplemental_Materials_.docx"))
        assert materials["identified_in"] == ["article_manifest"]

    def test_every_supplement_is_listed_once(self):
        rows = parse_jats_supplements(_GROFF_JATS)
        identities = [r["identity"] for r in rows]
        assert len(identities) == len(set(identities))


class TestAnIndexPageIsNotASupplement:
    def test_an_html_wrapper_beside_associated_files_is_an_index(self):
        rows = parse_jats_supplements(
            _jats(
                media=[
                    ("index.html", None, "text/html"),
                    ("table_s1.xlsx", "associated-file", "application/vnd.ms-excel"),
                ],
                body="See Supplementary Table S1.",
            )
        )
        index = next(r for r in rows if r["filename"] == "index.html")
        assert index["kind"] == "index"
        assert next(r for r in rows if r["filename"] == "table_s1.xlsx")["kind"] == "attachment"

    def test_a_data_file_with_no_role_is_still_an_attachment(self):
        """Most journals never set `xlink:role`. Its absence is not evidence of an index page."""
        rows = parse_jats_supplements(_jats(media=[("data.csv", None, "text/csv")], body="The data are attached."))
        assert rows[0]["kind"] == "attachment"


class TestProseKindsStayApart:
    def test_table_s1_and_file_s1_are_two_references(self):
        rows = parse_jats_supplements(
            "<article><body><p>See Supplementary Table S1 and Supplemental File S1.</p></body></article>"
        )
        labels = sorted(r["label"] for r in rows)
        assert len(labels) == 2
        assert any("Table S1" in label for label in labels)
        assert any("File S1" in label for label in labels)

    def test_each_resolves_to_its_own_attachment(self):
        rows = parse_jats_supplements(
            _jats(
                media=[
                    ("paper_Supplementary_Table_1.xlsx", None, "application/vnd.ms-excel"),
                    ("paper_Supplementary_File_1.pdf", None, "application/pdf"),
                ],
                body="See Supplementary Table S1 and Supplementary File S1.",
            )
        )
        table = next(r for r in rows if r["filename"] == "paper_Supplementary_Table_1.xlsx")
        file_ = next(r for r in rows if r["filename"] == "paper_Supplementary_File_1.pdf")
        assert any("Table S1" in ref for ref in table["references"])
        assert not any("File S1" in ref for ref in table["references"])
        assert any("File S1" in ref for ref in file_["references"])

    def test_one_reference_cited_two_ways_is_still_one_row(self):
        rows = parse_jats_supplements(
            "<article><body><p>Supplementary File S2 and later Supplemental file s2.</p></body></article>"
        )
        assert len(rows) == 1

    def test_an_enumerated_citation_names_every_file(self):
        rows = parse_jats_supplements("<article><body><p>(Supplemental Files S2, S3)</p></body></article>")
        assert {r["label"] for r in rows} == {"Supplemental File S2", "Supplemental File S3"}

    def test_an_additional_file_is_a_reference_too(self):
        rows = parse_jats_supplements(
            _jats(
                media=[("12864_2021_7001_MOESM2_ESM.xlsx", None, "application/vnd.ms-excel")],
                body="Differential results are in Additional file 2.",
            )
        )
        attachment = rows[0]
        assert attachment["kind"] == "attachment"
        assert any("Additional File 2" in ref for ref in attachment["references"])


class TestTheIdentifierComesFromTheCitation:
    def test_digits_in_a_doi_are_never_an_identifier(self):
        """`_IDENTIFIER_RE` took 252981 from "Supplemental Material (supp_gr.252981.119_...)", the
        DOI embedded in the publisher's filename."""
        rows = parse_jats_supplements(
            _jats(
                media=[("supp_gr.252981.119_Supplemental_Materials_.docx", "associated-file", "application/msword")],
                body="Nothing is cited by number here.",
            )
        )
        assert [r["references"] for r in rows] == [["Supplemental Material"]]

    @pytest.mark.asyncio
    async def test_an_attachment_missing_from_the_bundle_does_not_take_another_file(self):
        import io
        import zipfile

        from app.services.supplement_inventory import resolve_supplements

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("Table_252981.txt", "gene\tpadj\nA\t0.01\n")
        references = parse_jats_supplements(
            _jats(
                media=[("supp_gr.252981.119_Supplemental_Materials_.docx", "associated-file", "application/msword")],
                body="",
            )
        )

        async def _fetch(_url):
            return buffer.getvalue()

        rows = await resolve_supplements("PMC1", references, fetcher=_fetch)
        materials = next(r for r in rows if r["filename"] == "supp_gr.252981.119_Supplemental_Materials_.docx")
        assert materials["resolved"] is False
        assert materials["retrieval"]["status"] == "not_in_bundle"
        assert any(r["filename"] == "Table_252981.txt" and r["resolved"] for r in rows)


class TestAnUnmatchedReferenceIsADiscoveryLimitation:
    def test_it_stays_its_own_entry(self):
        rows = parse_jats_supplements(
            _jats(
                media=[("paper_Supplementary_Table_1.xlsx", None, "application/vnd.ms-excel")],
                body="See Supplementary Table S1 and Supplementary Table S4.",
            )
        )
        s4 = [r for r in rows if r["kind"] == "reference"]
        assert len(s4) == 1
        assert "S4" in s4[0]["label"]
        assert s4[0]["filename"] is None
        assert s4[0]["identified_in"] == ["prose"]

    def test_it_is_never_merged_into_an_attachment(self):
        rows = parse_jats_supplements(
            _jats(
                media=[("paper_Supplementary_Table_1.xlsx", None, "application/vnd.ms-excel")],
                body="See Supplementary Table S4.",
            )
        )
        table = next(r for r in rows if r["kind"] == "attachment")
        assert not any("S4" in ref for ref in table["references"])


# ---- change_7.3 section 3: identification, retrieval, inspection and execution, separately -------


def _code_source(identifier="Supplemental File S2", stated_in="methods"):
    return {
        "kind": "supplementary",
        "url": None,
        "identifier": identifier,
        "stated_in": stated_in,
        "exists": "yes",
        "accessible": "not_attempted",
        "accessible_reason": None,
    }


def _failed_s2():
    return {
        "label": "Supplemental File S2",
        "filename": "supp_Supplemental_File_2_code.docx",
        "references": ["Supplemental Material (supp_Supplemental_File_2_code.docx)", "Supplemental File S2"],
        "identified_in": ["article_manifest", "prose"],
        "kind": "attachment",
        "resolved": False,
        "role": "unknown",
        "retrieval": {"status": "failed", "ledger": "R3"},
    }


class TestAFailedAttemptIsNotNotAttempted:
    def test_accessibility_becomes_unknown_with_the_reason(self):
        from app.services.supplement_inventory import apply_retrieval_to_code_sources

        updated = apply_retrieval_to_code_sources([_code_source()], [_failed_s2()])
        assert updated[0]["accessible"] == "unknown"
        assert "could not" in (updated[0]["accessible_reason"] or "")

    def test_the_retrieval_points_at_the_ledger(self):
        from app.services.supplement_inventory import apply_retrieval_to_code_sources

        updated = apply_retrieval_to_code_sources([_code_source()], [_failed_s2()])
        assert updated[0]["retrieval"] == {"status": "failed", "ledger": "R3"}

    def test_code_extracted_does_not_claim_the_file_is_not_code(self):
        """`code_extracted: false` reads as "retrieved and not code". It was never retrieved."""
        from app.services.supplement_inventory import apply_retrieval_to_code_sources

        updated = apply_retrieval_to_code_sources([_code_source()], [_failed_s2()])
        assert updated[0]["code_extracted"] is None

    def test_a_source_never_attempted_does_not_claim_it_either(self):
        from app.services.supplement_inventory import apply_retrieval_to_code_sources

        updated = apply_retrieval_to_code_sources([_code_source("Supplemental File S9")], [])
        assert updated[0]["accessible"] == "not_attempted"
        assert updated[0]["code_extracted"] is None


class TestGroffsCodeReadsAsFourStatuses:
    """Identified in the methods and the article manifest; retrieval attempted and failed; not
    inspected; not executed."""

    def test_identification_names_both_places(self):
        from app.services.supplement_inventory import apply_retrieval_to_code_sources

        updated = apply_retrieval_to_code_sources([_code_source()], [_failed_s2()])
        assert set(updated[0]["identified_in"]) == {"methods", "article_manifest"}

    def test_inspection_is_not_performed(self):
        from app.services.supplement_inventory import apply_retrieval_to_code_sources

        updated = apply_retrieval_to_code_sources([_code_source()], [_failed_s2()])
        assert updated[0]["inspection"] == {"status": "not_inspected", "role": None}

    def test_a_retrieved_code_file_is_inspected_with_its_role(self):
        from app.services.supplement_inventory import apply_retrieval_to_code_sources

        retrieved = {
            **_failed_s2(),
            "resolved": True,
            "role": CODE,
            "retrieval": {"status": "retrieved", "ledger": "R4"},
        }
        updated = apply_retrieval_to_code_sources([_code_source()], [retrieved])
        assert updated[0]["inspection"] == {"status": "inspected", "role": CODE}
        assert updated[0]["code_extracted"] is True
