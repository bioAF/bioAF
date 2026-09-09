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
