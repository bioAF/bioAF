"""plan_8_7 stage 2: a synthesis may summarise accepted outcomes, and may do nothing else.

"The synthesis may be model-assisted within the assessment budget, but it may only summarize
accepted scoped outcomes. It cannot award points, create new findings or upgrade reproduction
status. Its references are validated against the published revision, and a failed synthesis falls
back to a factual summary of those outcomes."
"""

import pytest

from app.services.validation_report_synthesis import synthesize
from tests.support.fake_llm import FakeClient

_SUMMARY = {
    "assessment_revision": 3,
    "supported": [{"leaf": "S1.A", "statement": "the paper states the organism for every experiment"}],
    "concerns": [
        {
            "leaf": "M3.B",
            "statement": "three harvests of one donor are treated as independent",
            "impact": "the reported significance is overstated",
        }
    ],
    "untested": [{"leaf": "C4.A", "statement": "which claimed steps the source covers is not established"}],
    "supported_count": 1,
    "concern_count": 1,
    "untested_count": 1,
    "counts": {"obligations_attempted": 3, "obligations_conclusive": 2, "findings_conclusive": 1},
    "reproduction": {"attempted": False, "agreed": False, "unresolved": False},
    "method": "factual",
}


def _answer(**extra):
    return {
        "lead": "The paper's samples are fully described; its statistical design treats repeated harvests as independent.",
        "most_consequential": ["M3.B"],
        "untested": ["C4.A"],
        **extra,
    }


@pytest.mark.asyncio
class TestItSummarisesAndNothingMore:
    async def test_a_valid_synthesis_leads_the_report(self):
        found = await synthesize(summary=_SUMMARY, client=FakeClient([_answer()]), model="m", api_key=None)
        assert found["method"] == "model_assisted"
        assert "repeated harvests" in found["lead"]

    async def test_it_carries_no_score_and_no_new_finding(self):
        found = await synthesize(
            summary=_SUMMARY,
            client=FakeClient([_answer(score=88, findings=["the authors fabricated the data"])]),
            model="m",
            api_key=None,
        )
        assert "score" not in found
        assert "findings" not in found

    async def test_a_reference_to_an_obligation_nobody_settled_is_dropped(self):
        found = await synthesize(
            summary=_SUMMARY, client=FakeClient([_answer(most_consequential=["M3.B", "ZZ.9"])]), model="m", api_key=None
        )
        assert found["most_consequential"] == ["M3.B"]

    async def test_it_cannot_upgrade_the_reproduction_status(self):
        found = await synthesize(
            summary=_SUMMARY, client=FakeClient([_answer(reproduction={"attempted": True})]), model="m", api_key=None
        )
        assert found["reproduction"]["attempted"] is False


@pytest.mark.asyncio
class TestFailureFallsBackToTheFacts:
    async def test_no_model_returns_the_factual_summary(self):
        found = await synthesize(summary=_SUMMARY, client=None, model="", api_key=None)
        assert found["method"] == "factual"
        assert found["lead"]

    async def test_an_unusable_answer_returns_the_factual_summary(self):
        found = await synthesize(summary=_SUMMARY, client=FakeClient(["not json at all"]), model="m", api_key=None)
        assert found["method"] == "factual"
        assert "1 demonstrated concern" in found["lead"] or "concern" in found["lead"]

    async def test_a_synthesis_of_nothing_says_so(self):
        empty = {
            **_SUMMARY,
            "assessment_revision": None,
            "supported": [],
            "concerns": [],
            "untested": [],
            "supported_count": 0,
            "concern_count": 0,
            "untested_count": 0,
            "reason": "bioAF has not published an assessment of this paper yet",
        }
        found = await synthesize(summary=empty, client=None, model="", api_key=None)
        assert "not published" in found["lead"]
