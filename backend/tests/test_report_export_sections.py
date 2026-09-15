"""plan_8_2 section 4.2 (approved 2026-09-14): the markdown export keeps the report's hierarchy and summaries.

Collapsing on screen never omits anything from the export. It opens on the scorecard (with its units and
why a blank score is blank) and the verdict, then the four sections, each leading with the same summary and
counts the page shows, with the earlier sections nested beneath the one they belong to. A reason several
findings share is stated once.
"""

from app.services.provenance.markdown_renderer import MarkdownRenderer
from tests.test_report_sections import _summary


def _export(summary):
    report = {
        "entity": {
            "id": 45,
            "state": "classified",
            "classification": "access_restricted",
            "report_summary": summary,
            "reproduction_plan": {"pipeline_key": "nf-core/rnaseq", "accessions": ["EGAS00001003667"]},
            "evidence": {},
            "issues": [],
        },
        "generated_at": "2026-09-14T12:00:00+00:00",
        "generated_by": "reviewer@example.org",
        "schema_version": "1.0",
        "audit_trail": [],
    }
    return MarkdownRenderer.render("validation_study", report)


def _section(text, heading):
    return text.split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]


def test_the_export_opens_on_the_scorecard_and_verdict_then_the_four_sections_in_order():
    text = _export(_summary())
    order = ["## Validation Scorecard", "## Verdict", "## Findings", "## Data and code", "## Checks performed"]
    positions = [text.index(f"\n{h}\n") for h in [*order, "## Run diagnostics"]]
    assert positions == sorted(positions)


def test_each_section_leads_with_the_summary_and_counts_the_page_shows():
    summary = _summary()
    text = _export(summary)
    for heading, key in (
        ("Findings", "findings"),
        ("Data and code", "data"),
        ("Checks performed", "checks"),
        ("Run diagnostics", "diagnostics"),
    ):
        section = _section(text, heading)
        assert summary["sections"][key]["summary"] in section
        for count in summary["sections"][key]["counts"]:
            assert count["label"] in section


def test_the_earlier_sections_are_nested_under_the_one_they_belong_to():
    text = _export(_summary())
    assert "### Reproduction Plan" in _section(text, "Checks performed")
    assert "### Resources the paper names" in _section(text, "Data and code")
    assert "### Provenance Chain" in _section(text, "Run diagnostics")


def test_the_scorecard_names_its_units_and_why_the_score_is_blank():
    section = _section(_export(_summary()), "Validation Scorecard")
    assert "No finding has a conclusive assessment yet" in section
    assert "0 findings conclusive; 4 findings inconclusive" in section


def test_a_shared_reason_is_stated_once_and_each_finding_points_to_it():
    summary = _summary()
    (shared,) = summary["sections"]["findings"]["shared_reasons"]
    text = _export(summary)
    assert text.count(shared["text"]) == 1
    assert f"**{shared['id']}**" in _section(text, "Findings")
    assert "see R1 under Findings" in _section(text, "Validation Scorecard")


def test_a_check_that_named_its_candidate_tables_renders_on_the_page_and_in_the_export():
    """Demo, study 45 after its recovery: an unresolved check whose outcome names its candidate tables (the
    queue's "which table reports this contrast is not established" record) crashed the export."""
    from app.services.provenance.markdown_renderer import _append_each_claim
    from app.services.validation_report_summary import _record_consistency

    record = {
        "kind": "author_results",
        "state": "unresolved",
        "dependencies": {"binding_version": 1, "candidates": ["S3_siggenes.txt"]},
        "outcome": {
            "outcome": "unresolved",
            "reason": "which table reports this claim's contrast is not established: no listed table reports it",
            "candidates": ["S3_siggenes.txt"],
            "bindings": [{"name": "S3_siggenes.txt", "status": "rejected", "reason": "another contrast"}],
        },
    }
    row = _record_consistency(record)
    assert row["candidates"] == [{"interpretation": "S3_siggenes.txt", "count": None}]
    parts: list[str] = []
    _append_each_claim(parts, {"claims": [{"description": "53 genes", "checks": [], "consistency": row}]})
    assert "  - if S3_siggenes.txt: None" in "\n".join(parts)
