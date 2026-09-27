"""plan_8_7 stage 2: the interpretation review runs in the assessment stage, through production.

An implemented reviewer nothing calls is not a repair (the lesson plan_8_5 recorded about
`validation_judgment`). This is the wiring: the assessment stage supplies the paper's own stated
conclusions, the design, methods and results they rest on, the sample and code evidence, and any
comparison bioAF completed, and stores one review per conclusion on the study's evidence.
"""

import json

import pytest

from app.services.validation_assessment import refresh_interpretation_review
from app.services.validation_study_service import ValidationStudyService

_METHODS = "Significance was assessed with an unpaired t-test across two clones per condition."
_RESULT = "Cells expressing all three factors upregulated FOXL2 relative to controls (p < 0.01)."


class _Client:
    """A model client at its own boundary: it answers about whichever conclusion it was asked about."""

    def __init__(self, outcome="contradicted"):
        self.asked: list[str] = []
        self.outcome = outcome

    async def submit(self, *, prompt, payload, model=None, api_key=None, max_tokens=None, **kw):
        self.asked.append(payload)
        identity = payload.partition("Conclusion id: ")[2].partition("\n")[0].strip()
        cited = [line.partition("[")[2].partition("]")[0] for line in payload.splitlines() if line.startswith("[")]
        return json.dumps(
            {
                "conclusion_id": identity,
                "outcome": self.outcome,
                "rationale": "the two clones are different parent lines, so the comparison cannot separate them",
                "inferential_step": "treating two parental lines as biological replicates",
                "impact": "the stated sufficiency rests on a confounded comparison",
                "citations": cited[:1],
                "confidence": 0.7,
            }
        )


async def _study(session, admin_user, evidence, *, claims=("Three factors are sufficient to specify the fate.",)):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="GSE1", intended_route="assessment"
    )
    study.evidence_json = evidence
    await session.flush()
    if claims:
        from app.models.comparison_target import ComparisonTarget
        from app.models.reproduction_plan import ReproductionPlan

        plan = ReproductionPlan(validation_study_id=study.id, pipeline_key="nf-core/rnaseq")
        session.add(plan)
        await session.flush()
        study.reproduction_plan_id = plan.id
        for text in claims:
            session.add(ComparisonTarget(reproduction_plan_id=plan.id, claim_text=text, source_locator="Figure 3"))
        await session.flush()
    return study


def _evidence():
    return {
        "paper_index": {
            "passages": [
                {"id": "p1", "kind": "methods", "section": "Methods", "text": _METHODS, "source": "the paper"},
                {"id": "p2", "kind": "results", "section": "Results", "text": _RESULT, "source": "the paper"},
            ]
        }
    }


class TestItRunsInTheStage:
    @pytest.mark.asyncio
    async def test_each_stated_conclusion_receives_a_review(self, session, admin_user):
        study = await _study(session, admin_user, _evidence())
        found = await refresh_interpretation_review(session, study, client=_Client(), model="m", api_key="k")
        assert len(found["reviews"]) == 1
        assert next(iter(found["reviews"].values()))["outcome"] == "contradicted"

    @pytest.mark.asyncio
    async def test_the_review_is_stored_on_the_evidence(self, session, admin_user):
        study = await _study(session, admin_user, _evidence())
        await refresh_interpretation_review(session, study, client=_Client(), model="m", api_key="k")
        assert study.evidence_json["interpretation_review"]["reviews"]

    @pytest.mark.asyncio
    async def test_it_is_shown_the_methods_and_the_results(self, session, admin_user):
        study = await _study(session, admin_user, _evidence())
        client = _Client()
        await refresh_interpretation_review(session, study, client=client, model="m", api_key="k")
        assert _METHODS in client.asked[0]
        assert _RESULT in client.asked[0]

    @pytest.mark.asyncio
    async def test_unchanged_evidence_costs_no_second_call(self, session, admin_user):
        study = await _study(session, admin_user, _evidence())
        client = _Client()
        await refresh_interpretation_review(session, study, client=client, model="m", api_key="k")
        await refresh_interpretation_review(session, study, client=client, model="m", api_key="k")
        assert len(client.asked) == 1


class TestItNeedsNoExecution:
    @pytest.mark.asyncio
    async def test_a_study_that_executed_nothing_still_gets_a_review(self, session, admin_user):
        study = await _study(session, admin_user, _evidence())
        found = await refresh_interpretation_review(session, study, client=_Client(), model="m", api_key="k")
        assert found["reproduction"] is None
        assert "no reproduction" in found["scope"]

    @pytest.mark.asyncio
    async def test_the_prompt_says_a_missing_reproduction_is_bioafs_limitation(self, session, admin_user):
        study = await _study(session, admin_user, _evidence())
        client = _Client()
        await refresh_interpretation_review(session, study, client=client, model="m", api_key="k")
        assert "None. bioAF has not reproduced any result" in client.asked[0]

    @pytest.mark.asyncio
    async def test_a_paper_with_no_stated_conclusion_costs_no_call(self, session, admin_user):
        study = await _study(session, admin_user, _evidence(), claims=())
        client = _Client()
        found = await refresh_interpretation_review(session, study, client=client, model="m", api_key="k")
        assert client.asked == []
        assert found["reviews"] == {}


class TestTheStageCallsIt:
    @pytest.mark.asyncio
    async def test_running_the_assessment_stage_reviews_the_interpretations(self, session, admin_user, monkeypatch):
        from app.services import validation_assessment

        study = await _study(session, admin_user, _evidence())
        called = []

        async def _review(session, study, **kw):
            called.append(study.id)
            return {"reviews": {}}

        monkeypatch.setattr(validation_assessment, "refresh_interpretation_review", _review)
        await validation_assessment.run_assessment(session, study)
        assert called == [study.id]
