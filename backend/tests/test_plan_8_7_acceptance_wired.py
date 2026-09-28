"""plan_8_7 stage 4: the frozen cases run through the production assessor, not through a stub of it.

    "Use saved real artifacts through production callers, stubbing transport/provider boundaries only
    where appropriate. Do not stub the selection, scoped allocation, execution-contract propagation or
    final projection whose behavior is being accepted."

So the harness builds the case's evidence, runs `packets_for` and `review_documents` over it, and
evaluates what comes back. Only the provider is a double: the answers here are the three runs a live
evaluation would produce, and a live one supplies them from the configured model instead.
"""

import json

import pytest

from app.services.validation_acceptance import MATERIAL_ERROR, PASSED, frozen_case
from app.services.validation_acceptance_runner import evidence_for, run_case


class _Client:
    """The provider boundary. It answers with the run it was given, in order."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked: list[str] = []

    async def submit(self, *, prompt, payload, model=None, api_key=None, max_tokens=None, **kw):
        self.asked.append(payload)
        answer = self.answers[min(len(self.asked) - 1, len(self.answers) - 1)]
        return json.dumps(answer)


def _met(rationale, citations=("p1",)):
    return {
        "outcome": "met",
        "rationale": rationale,
        "citations": list(citations),
        "scope": "the differential expression",
        "confidence": 0.8,
    }


def _unmet(rationale, citations=("p1", "code:script.R#1"), basis="contradiction"):
    return {
        "outcome": "unmet",
        "rationale": rationale,
        "citations": list(citations),
        "scope": "the differential expression of treated versus control",
        "basis": basis,
        "impact": "more genes are reported as significant than the stated criterion selects",
        "observations": [
            {"citation": citations[0], "states": "the methods state a Benjamini-Hochberg adjusted threshold"},
            {"citation": citations[-1], "states": "the script selects on the uncorrected p value"},
        ],
        "confidence": 0.8,
    }


_CAUGHT = _unmet(
    "the methods state an adjusted threshold and the script selects on the uncorrected p value, so which "
    "genes are called significant does not follow the stated criterion"
)


class TestTheEvidenceComesFromTheFrozenCase:
    def test_the_methods_and_the_script_are_both_carried(self):
        evidence = evidence_for(frozen_case("multiple_comparison"))
        assert any("Benjamini" in str(p["text"]) for p in evidence["paper_index"]["passages"])
        assert evidence["code_inspection"]["sources"][0]["text"].startswith("res <- results")

    def test_a_case_with_no_script_holds_no_code(self):
        evidence = evidence_for(frozen_case("multiple_comparison_no_code"))
        assert evidence["code_inspection"]["sources"] == []


@pytest.mark.asyncio
class TestOneCaseThroughTheProductionAssessor:
    async def test_the_assessor_is_shown_the_methods_and_the_script(self):
        client = _Client([_CAUGHT])
        await run_case(frozen_case("multiple_comparison"), answers=[_CAUGHT], client=client, model="m")
        payload = client.asked[0]
        assert "Benjamini" in payload
        assert "res$pvalue" in payload

    async def test_a_caught_discrepancy_passes_the_case(self):
        found = await run_case(
            frozen_case("multiple_comparison"), answers=[_CAUGHT], client=_Client([_CAUGHT]), model="m"
        )
        assert found["verdict"] == PASSED

    async def test_a_missed_discrepancy_is_a_material_error(self):
        missed = _met("the decision criteria are specified and unambiguous")
        found = await run_case(
            frozen_case("multiple_comparison"), answers=[missed], client=_Client([missed]), model="m"
        )
        assert found["verdict"] == MATERIAL_ERROR

    async def test_with_no_code_an_unsupported_positive_is_a_material_error(self):
        claimed = _met("the correction was applied as the methods state")
        found = await run_case(
            frozen_case("multiple_comparison_no_code"), answers=[claimed], client=_Client([claimed]), model="m"
        )
        assert found["verdict"] == MATERIAL_ERROR

    async def test_the_judgment_that_was_evaluated_is_recorded(self):
        found = await run_case(
            frozen_case("multiple_comparison"), answers=[_CAUGHT], client=_Client([_CAUGHT]), model="m"
        )
        assert found["judgment"]["outcome"] in ("verified", "failed", "undetermined")
        assert found["judgment"]["rationale"]
