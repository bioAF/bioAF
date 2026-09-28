"""plan_8_7 stage 4: the superseded derivation is removed, not moved.

    "Migrate overlapping scientific judgments from `signal_assessment` into this ownership; retain
    useful execution-cause diagnostics without a second authoritative verdict on the same question."

`signal_assessment` asked two questions. One of them, "could the paper's result be noise read as
signal?", is a judgment about whether the paper's result supports what the paper concluded from it,
which is exactly what `validation_interpretation_review` now owns and answers from the paper's own
design rather than from one pair of numbers. Two authoritative answers to one question is the
condition this consolidation exists to end.

The other, "what could explain the difference between their number and ours?", is a diagnostic about
bioAF's own run. It is kept, because nothing else answers it and it is not a verdict about the paper.

    "Each implementation stage must identify the old derivations it replaces, migrate their callers
    and remove superseded active helpers... merely moving the same competing decisions between files
    does not satisfy consolidation."
"""

import pytest

from app.services import signal_assessment


class TestTheCompetingVerdictIsGone:
    def test_the_noise_verdict_is_no_longer_implemented(self):
        assert not hasattr(signal_assessment, "assess_signal")

    def test_nothing_in_the_application_calls_it(self):
        import pathlib

        root = pathlib.Path(signal_assessment.__file__).parent.parent
        hits = [path for path in root.rglob("*.py") if "assess_signal(" in path.read_text(encoding="utf-8")]
        assert hits == [], hits

    def test_its_budget_entry_is_gone_from_the_audit(self):
        from app.services import validation_decision_budgets as budgets

        assert "signal_verdict" not in budgets.DECISIONS


class TestTheDiagnosticIsKept:
    def test_the_cause_question_is_still_implemented(self):
        assert callable(signal_assessment.assess_causes)

    def test_bioafs_own_side_is_still_first_among_the_candidates(self):
        assert signal_assessment.CAUSE_CANDIDATES[0].startswith("bioaf")

    def test_it_still_declines_to_indict_the_paper(self):
        assert "code_defect" in signal_assessment.CAUSE_CANDIDATES
        assert "authors_misinterpreted" not in signal_assessment.CAUSE_CANDIDATES


class TestTheQuestionItAnsweredIsAnsweredElsewhere:
    def test_the_interpretation_review_owns_it_and_needs_no_comparison(self):
        from app.services.validation_interpretation_review import review_interpretations

        assert callable(review_interpretations)

    @pytest.mark.asyncio
    async def test_a_study_that_executed_nothing_still_gets_that_judgment(self):
        from app.services.validation_interpretation_review import review_interpretations
        from tests.support.fake_llm import FakeClient

        answer = {
            "conclusion_id": "c1",
            "outcome": "contradicted",
            "rationale": "the effect rests on two clones from different parent lines",
            "inferential_step": "treating two parental lines as replicates",
            "impact": "the claim rests on a confounded comparison",
            "citations": ["d1"],
            "confidence": 0.7,
        }
        found = await review_interpretations(
            conclusions=[{"id": "c1", "statement": "the three factors are sufficient"}],
            context=[{"id": "d1", "source": "the paper", "text": "Two clones per condition, each a different line."}],
            comparisons=[],
            client=FakeClient([answer]),
            model="m",
            api_key=None,
        )
        assert found["reviews"]["c1"]["outcome"] == "contradicted"
        assert found["reviews"]["c1"]["reproduction"] is None
