"""plan_8_4 section 7: every surface reads the same stored projection.

The report, the studies list, the JSON export and the markdown export must all show V, F and U and
the same score. A number that differs between two surfaces is a number nobody can act on.
"""

import pytest

from app.services.validation_report_summary import summarize
from tests.replay import with_assessment

_PLAN = {
    "reported_experiments": [
        {
            "id": "e1",
            "assay": "bulk RNA-seq",
            "organism": "Homo sapiens",
            "workflow": "nf-core/rnaseq",
            "reference": {"assembly": {"stated": "hg19", "resolved": "GRCh37"}, "annotation": {"stated": "GENCODE v19", "resolved": "GENCODE v19"}},
        }
    ],
    "differential_design": {
        "contrasts": [
            {
                "name": "XX vs XY",
                "reported_experiment_id": "e1",
                "test_condition": "XX",
                "reference_condition": "XY",
                "cutoffs": [{"kind": "padj", "operator": "<", "value": 0.05}],
            }
        ]
    },
    "sample_sheet": {"organism": "Homo sapiens", "sample_count": 54},
}


def _summary():
    # plan_8_5 section 3.2: a surface shows the assessment the study PUBLISHED, so the production
    # publisher runs before anything is projected.
    return summarize(
        study={"state": "classified", "classification": "access_restricted"},
        evidence=with_assessment({}, plan=_PLAN),
        plan=_PLAN,
        targets=[],
        issues=[],
        checks=None,
    )


class TestTheListCellSaysWhatTheReportSays:
    def test_the_compact_card_carries_the_three_quantities(self):
        from app.services.validation_rubric_v3 import compact_evidence_score

        card = _summary()["evidence_score"]
        compact = compact_evidence_score(card)
        assert compact["headline"] == card["headline"]
        assert compact["parts"] == card["parts"]
        assert compact["counts_label"] == card["counts_label"]
        assert compact["rubric_version"] == 3

    def test_a_lone_number_is_never_the_whole_cell(self):
        """Section 7: a lone 35 cannot tell 65 unknown points from 65 failed ones, so the compact cell
        keeps V, F and U even where the unweighted scope has to move to the detail."""
        from app.services.validation_rubric_v3 import compact_evidence_score

        compact = compact_evidence_score(_summary()["evidence_score"])
        assert {p["key"] for p in compact["parts"]} == {"verified", "untested", "negative"}
        assert "scope" not in compact


class TestTheExportsCarryIt:
    def _markdown(self):
        from app.services.provenance.markdown_renderer import MarkdownRenderer

        return MarkdownRenderer.render(
            "validation_study",
            {
                "generated_at": "2026-09-18T00:00:00+00:00",
                "generated_by": "a@b.c",
                "schema_version": 1,
                "entity": {"id": 1, "title": "A paper", "report_summary": _summary()},
            },
        )

    def test_the_markdown_export_states_the_score_and_its_three_parts(self):
        text = self._markdown()
        assert "## Validation Scorecard" in text
        assert _summary()["evidence_score"]["headline"] in text
        assert "positive points" in text and "untested points" in text and "negative points" in text

    def test_the_markdown_export_names_the_reproduction_status_separately(self):
        assert "Independent reproduction:" in self._markdown()

    def test_the_markdown_export_lists_the_capability_limits(self):
        assert "no implemented check" in self._markdown()


class TestTheListQueryProducesTheSameNumbers:
    @pytest.mark.asyncio
    async def test_a_listed_study_shows_the_score_its_report_shows(self, session, admin_user):
        from app.services.validation_report_summary import compact_scorecards_for, report_summary_for
        from tests.replay import replay_publish, restore

        restored = await restore(session, 55, organization_id=admin_user.organization_id, user_id=admin_user.id)
        await replay_publish(session, restored)
        listed = await compact_scorecards_for(session, [restored.study])
        report = await report_summary_for(session, restored.study, restored.study.organization_id)
        assert listed[restored.study.id]["evidence_score"]["headline"] == report["evidence_score"]["headline"]
        assert listed[restored.study.id]["evidence_score"]["parts"] == report["evidence_score"]["parts"]


class TestTheScoreIsPersistedWithItsProvenance:
    """plan_8_4 section 6.4: the versioned record carries the v3 numbers so a later change to how
    evidence is normalized cannot silently rewrite a concluded study's score."""

    @pytest.mark.asyncio
    async def test_the_record_carries_the_exact_and_displayed_numbers(self, session, admin_user):
        from app.services.validation_report_summary import compute_scorecard_record
        from tests.replay import replay_publish, restore

        restored = await restore(session, 55, organization_id=admin_user.organization_id, user_id=admin_user.id)
        await replay_publish(session, restored)
        record = await compute_scorecard_record(session, restored.study)
        evidence_score = record["evidence_score"]
        assert evidence_score["rubric_version"] == 3
        assert evidence_score["exact"]["verified"]
        assert evidence_score["display"]["verified"]
        assert evidence_score["sections"]
        assert evidence_score["scope"]["total"] > 0
        assert evidence_score["profile"]["revision"] >= 1
        assert "attempted" in evidence_score["reproduction"]

    @pytest.mark.asyncio
    async def test_the_stored_record_and_a_fresh_projection_agree(self, session, admin_user):
        from app.services.validation_report_summary import compute_scorecard_record, report_summary_for
        from tests.replay import replay_publish, restore

        restored = await restore(session, 55, organization_id=admin_user.organization_id, user_id=admin_user.id)
        await replay_publish(session, restored)
        record = await compute_scorecard_record(session, restored.study)
        report = await report_summary_for(session, restored.study, restored.study.organization_id)
        assert record["evidence_score"]["headline"] == report["evidence_score"]["headline"]

    @pytest.mark.asyncio
    async def test_the_v2_record_keeps_its_own_rubric_version(self, session, admin_user):
        """Section 6.4: a v2 score of 100 is not relabelled as a v3 score of 100. The two versions
        live in the same record under their own names."""
        from app.services.validation_report_summary import compute_scorecard_record
        from tests.replay import replay_publish, restore

        restored = await restore(session, 55, organization_id=admin_user.organization_id, user_id=admin_user.id)
        await replay_publish(session, restored)
        record = await compute_scorecard_record(session, restored.study)
        assert record["rubric_version"] != 3
        assert record["evidence_score"]["rubric_version"] == 3
