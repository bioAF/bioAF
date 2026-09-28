"""plan_8_7 stage 2: whether the result supports the conclusion, read without waiting for a run.

The owner's September 21 assessment:

    "Interpretation review is limited to execution-related numbers and short context."

`signal_assessment` asks two questions, and both are gated on an arm that executed code and produced
a number to set against the paper's. So a paper whose data cannot be acquired, whose assay bioAF
cannot run, or whose code will not resolve receives no interpretation review at all, however plainly
its own results and design settle the question.

This review is about the inference: does the result, in the design, uncertainty and population
actually examined, support the conclusion the paper states? It works from the paper's own evidence.
A completed comparison is extra context when there is one, and its absence is not evidence that an
effect disappeared.
"""

import pytest

from app.services.validation_interpretation_review import (
    CONTRADICTED,
    REVIEW_VERSION,
    SUPPORTED,
    UNRESOLVED,
    review_interpretations,
)
from tests.support.fake_llm import FakeClient

_CONCLUSION = {
    "id": "c1",
    "statement": "Overexpression of the three factors is sufficient to specify granulosa-like cells.",
    "passage": "Cells expressing all three factors upregulated FOXL2 and AMH relative to controls (p < 0.01).",
}
_CONTEXT = [
    {
        "id": "d1",
        "source": "the paper's design",
        "text": "Two clones per condition, each from a different parent line.",
    },
    {"id": "m1", "source": "the paper's methods", "text": "Significance was assessed with an unpaired t-test."},
]


def _answer(outcome, **extra):
    return {
        "conclusion_id": "c1",
        "outcome": outcome,
        "rationale": "the two clones are the replication unit and they are confounded with the parent line",
        "inferential_step": "treating two parental lines as biological replicates of one condition",
        "impact": "the stated sufficiency rests on a comparison that cannot separate factor from line",
        "citations": ["d1", "m1"],
        "confidence": 0.7,
        **extra,
    }


@pytest.mark.asyncio
class TestItRunsWithoutAnExecution:
    async def test_a_conclusion_is_reviewed_with_no_comparison_at_all(self):
        client = FakeClient([_answer("contradicted")])
        found = await review_interpretations(
            conclusions=[_CONCLUSION], context=_CONTEXT, comparisons=[], client=client, model="m", api_key=None
        )
        assert found["reviews"]["c1"]["outcome"] == CONTRADICTED
        assert found["reviews"]["c1"]["inferential_step"]
        assert found["review_version"] == REVIEW_VERSION

    async def test_an_unavailable_execution_is_never_read_as_an_effect_disappearing(self):
        client = FakeClient([_answer("supported")])
        found = await review_interpretations(
            conclusions=[_CONCLUSION], context=_CONTEXT, comparisons=[], client=client, model="m", api_key=None
        )
        review = found["reviews"]["c1"]
        assert review["outcome"] == SUPPORTED
        assert review["reproduction"] is None
        assert "no reproduction" in found["scope"]

    async def test_the_question_never_asks_the_model_to_invent_a_number(self):
        client = FakeClient([_answer("supported")])
        await review_interpretations(
            conclusions=[_CONCLUSION], context=_CONTEXT, comparisons=[], client=client, model="m", api_key=None
        )
        system = client.asked[0]["system"]
        assert "do not produce" in system.lower() or "no number" in system.lower()


@pytest.mark.asyncio
class TestThreeConclusionsStayDistinct:
    async def test_output_agreement_is_not_inferential_support(self):
        """plan_8_7 section 3: reproducing the authors' output, independently supporting a result and
        supporting the authors' interpretation are three conclusions. Success at one is not the others."""
        client = FakeClient([_answer("contradicted")])
        found = await review_interpretations(
            conclusions=[_CONCLUSION],
            context=_CONTEXT,
            comparisons=[{"id": "r1", "metric": "FOXL2 log2 fold change", "paper": 2.1, "ours": 2.0, "agrees": True}],
            client=client,
            model="m",
            api_key=None,
        )
        review = found["reviews"]["c1"]
        assert review["outcome"] == CONTRADICTED
        assert review["reproduction"] == "agreed"

    async def test_a_completed_comparison_travels_as_context(self):
        client = FakeClient([_answer("supported")])
        await review_interpretations(
            conclusions=[_CONCLUSION],
            context=_CONTEXT,
            comparisons=[{"id": "r1", "metric": "FOXL2 log2 fold change", "paper": 2.1, "ours": 2.0, "agrees": True}],
            client=client,
            model="m",
            api_key=None,
        )
        assert "FOXL2 log2 fold change" in client.asked[0]["payload"]


@pytest.mark.asyncio
class TestAnUnsupportedInterpretationStaysUnresolved:
    async def test_a_citation_bioaf_never_supplied_leaves_it_unresolved(self):
        client = FakeClient([_answer("contradicted", citations=["z9"])])
        found = await review_interpretations(
            conclusions=[_CONCLUSION], context=_CONTEXT, comparisons=[], client=client, model="m", api_key=None
        )
        assert found["reviews"]["c1"]["outcome"] == UNRESOLVED
        assert "z9" in found["reviews"]["c1"]["rationale"]

    async def test_an_answer_about_another_conclusion_settles_nothing(self):
        client = FakeClient([_answer("contradicted", conclusion_id="c9")])
        found = await review_interpretations(
            conclusions=[_CONCLUSION], context=_CONTEXT, comparisons=[], client=client, model="m", api_key=None
        )
        assert found["reviews"]["c1"]["outcome"] == UNRESOLVED

    async def test_no_model_leaves_every_conclusion_unresolved_with_the_reason(self):
        found = await review_interpretations(
            conclusions=[_CONCLUSION], context=_CONTEXT, comparisons=[], client=None, model="", api_key=None
        )
        assert found["reviews"]["c1"]["outcome"] == UNRESOLVED
        assert found["reason"]

    async def test_no_conclusions_costs_no_call(self):
        client = FakeClient([])
        found = await review_interpretations(
            conclusions=[], context=_CONTEXT, comparisons=[], client=client, model="m", api_key=None
        )
        assert found["reviews"] == {}
        assert client.calls == 0

    async def test_one_conclusion_per_request_and_a_bound_on_how_many(self):
        from app.services.validation_interpretation_review import MAX_CONCLUSIONS

        many = [{**_CONCLUSION, "id": f"c{i}"} for i in range(MAX_CONCLUSIONS + 4)]
        client = FakeClient([_answer("supported", conclusion_id=f"c{i}") for i in range(MAX_CONCLUSIONS)])
        found = await review_interpretations(
            conclusions=many, context=_CONTEXT, comparisons=[], client=client, model="m", api_key=None
        )
        assert client.calls == MAX_CONCLUSIONS
        assert found["deferred"] == 4
