"""change_7.1 section 5: the export says what the paper published and what bioAF could reach.

A report that is right on screen and lossy on export fails the same reader. The deposit rows and
the supplement inventory are the two places where "the authors published this" and "bioAF could not
obtain it" have to appear together, because a reader who sees only the second blames the first.
"""

from app.services.provenance.markdown_renderer import (
    _append_capability_checklist,
    _append_supplement_inventory,
)

_CAPS = {
    "deposit_exists": {"value": "yes", "evidence": "EGA dataset EGAD00001005044", "failure_reason": None},
    "deposits": [
        {
            "archive": "ega",
            "accession": "EGAS00001003667",
            "provenance": "extracted",
            "scoped": True,
            "exists": "yes",
            "access": "controlled",
            "supported": "no",
            "evidence": "EGA dataset EGAD00001005044 is controlled access and registers 54 sample(s)",
            "failure_reason": None,
        }
    ],
}

_SUPPLEMENTS = [
    {"label": "Supplemental File S1", "role": "sample_metadata", "resolved": True, "row_count": 54},
    {"label": "Supplemental File S2", "role": "code", "resolved": True, "size_bytes": 97992},
    {
        "label": "Supplemental File S3",
        "role": "results_table",
        "resolved": True,
        "row_count": 194,
        "threshold_splits": {"abs_log2fc>2": 88},
    },
    {
        "label": "Supplemental File S4",
        "role": "unknown",
        "resolved": False,
        "failure_reason": "bioAF could not find a file in the bundle matching this reference",
    },
]


class TestTheDepositReachesTheExport:
    def test_the_deposit_and_its_access_are_both_stated(self):
        parts: list[str] = []
        _append_capability_checklist(parts, _CAPS)
        rendered = "\n".join(parts)
        assert "EGAS00001003667" in rendered
        assert "controlled" in rendered.lower()

    def test_the_export_says_bioaf_cannot_acquire_it(self):
        parts: list[str] = []
        _append_capability_checklist(parts, _CAPS)
        assert "cannot" in "\n".join(parts).lower()


class TestTheSupplementInventoryReachesTheExport:
    def test_each_supplement_appears_with_its_role(self):
        parts: list[str] = []
        _append_supplement_inventory(parts, _SUPPLEMENTS)
        rendered = "\n".join(parts)
        for label in ("Supplemental File S1", "Supplemental File S2", "Supplemental File S3"):
            assert label in rendered
        assert "Sample metadata" in rendered
        assert "Analysis code" in rendered

    def test_an_unresolved_reference_is_not_reported_as_absent(self):
        parts: list[str] = []
        _append_supplement_inventory(parts, _SUPPLEMENTS)
        rendered = "\n".join(parts)
        assert "Not retrieved" in rendered
        assert "could not find a file" in rendered

    def test_the_results_table_row_counts_are_shown(self):
        parts: list[str] = []
        _append_supplement_inventory(parts, _SUPPLEMENTS)
        assert "194" in "\n".join(parts)

    def test_nothing_is_rendered_when_no_supplements_were_found(self):
        parts: list[str] = []
        _append_supplement_inventory(parts, [])
        assert parts == []


class TestTheReportDoesNotContradictItself:
    """The owner, 2026-09-21: "Its code-source listing says 'Not retrieved' despite the scored code
    inspection", and "the report says attachments were retrieved while still recommending retrying
    their download". A reader cannot act on a report that states a thing and its opposite."""

    _EVIDENCE = {
        "capabilities": {
            "code_sources": [
                {"kind": "github", "url": "https://github.com/lab/paper", "exists": "yes", "identified_in": ["methods"]}
            ]
        },
        "code_resolution": {
            "outcome": "resolved",
            "kind": "github",
            "url": "https://github.com/lab/paper",
            "commit_sha": "abc123",
            "is_publication_revision": True,
        },
        "code_inspection": {
            "sources": [
                {
                    "path": "a.py",
                    "text": "x = 1",
                    "provenance": {"from": "repository", "origin": "https://github.com/lab/paper"},
                }
            ],
            "manifests": [],
        },
    }

    def test_a_fetched_repository_is_not_listed_as_not_retrieved(self):
        from app.services.validation_report_summary import _code_sources

        row = _code_sources(self._EVIDENCE)[0]
        assert row["retrieval"]["status"] == "retrieved"
        assert "Not retrieved" not in row["retrieval"]["label"]

    def test_the_listing_says_it_was_inspected_when_its_source_was_read(self):
        from app.services.validation_report_summary import _code_sources

        row = _code_sources(self._EVIDENCE)[0]
        assert row["inspection"]["status"] != "not_inspected"

    def test_the_revision_that_was_fetched_is_on_the_row(self):
        from app.services.validation_report_summary import _code_sources

        row = _code_sources(self._EVIDENCE)[0]
        assert "abc123" in str(row["retrieval"].get("reason") or "")

    def test_a_repository_that_was_not_fetched_still_says_so(self):
        from app.services.validation_report_summary import _code_sources

        evidence = {
            "capabilities": self._EVIDENCE["capabilities"],
            "code_resolution": {"outcome": "code_unreachable", "url": "https://github.com/lab/paper", "reason": "404"},
            "code_inspection": {"sources": [], "manifests": []},
        }
        row = _code_sources(evidence)[0]
        assert row["retrieval"]["status"] != "retrieved"

    def test_a_stale_not_inspected_annotation_does_not_beat_the_inspection(self):
        """Found on the demo: the supplement path writes `inspection: not_inspected` for a source it
        never saw, and reading that first left a fetched, parsed repository listed as uninspected."""
        from app.services.validation_report_summary import _code_sources

        evidence = {
            **self._EVIDENCE,
            "capabilities": {
                "code_sources": [
                    {
                        "kind": "github",
                        "url": "https://github.com/lab/paper",
                        "retrieval": {"status": "not_attempted"},
                        "inspection": {"status": "not_inspected"},
                    }
                ]
            },
        }
        row = _code_sources(evidence)[0]
        assert row["retrieval"]["status"] == "retrieved"
        assert row["inspection"]["status"] == "inspected"

    def test_a_retrieved_attachment_set_must_not_also_be_recommended_for_retry(self):
        from app.services.validation_report_summary import report_contradictions

        breaches = report_contradictions(
            {
                "summary": ["bioAF could not download the paper's supplementary files in this attempt."],
                "facts": {"attachments": {"identified": 19, "retrieved": 19, "failed": 0}},
            }
        )
        assert breaches

    def test_a_code_listing_that_disagrees_with_the_inspection_is_a_breach(self):
        from app.services.validation_report_summary import report_contradictions

        breaches = report_contradictions(
            {
                "summary": [],
                "code_sources": [{"retrieval": {"status": "not_attempted"}, "inspection": {"status": "inspected"}}],
            }
        )
        assert breaches

    def test_a_consistent_report_breaches_nothing(self):
        from app.services.validation_report_summary import report_contradictions

        assert (
            report_contradictions(
                {
                    "summary": ["19 supplementary attachments were retrieved and inspected."],
                    "facts": {"attachments": {"identified": 19, "retrieved": 19, "failed": 0}},
                    "code_sources": [{"retrieval": {"status": "retrieved"}, "inspection": {"status": "inspected"}}],
                }
            )
            == []
        )
