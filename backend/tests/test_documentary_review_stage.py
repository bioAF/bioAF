"""plan_8_5 section 3.6: the documentary review, run by the stage that assesses a study.

It runs where the evidence is, not where a page is rendered, it costs nothing when its inputs have
not moved, and a provider that is unreachable leaves the obligations it was asked about grey while
everything else the study established stands.
"""

import json

import pytest

from app.services.validation_assessment import refresh_documentary_review
from app.services.validation_study_service import ValidationStudyService

_EVIDENCE = {
    "paper_passages": {
        "methods": ["Reads were aligned with STAR and quantified with RSEM."],
        "statements": [],
        "claims": [],
    }
}


class _Client:
    def __init__(self, outcome="met"):
        self.calls = 0
        self.outcome = outcome

    async def submit(self, *, prompt, payload, model=None, api_key=None, max_tokens=None, **kw):
        self.calls += 1
        return json.dumps(
            {"outcome": self.outcome, "rationale": "the methods state it", "citations": ["m1"], "confidence": 0.7}
        )


async def _study(session, admin_user, evidence=None):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_doi="10.1/review", intended_route="deposit"
    )
    study.evidence_json = evidence if evidence is not None else dict(_EVIDENCE)
    await session.flush()
    return study


class TestItRunsWhereTheEvidenceIs:
    @pytest.mark.asyncio
    async def test_the_accepted_judgments_are_persisted_with_what_they_were_made_from(self, session, admin_user):
        study = await _study(session, admin_user)
        client = _Client()
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        held = study.evidence_json["rubric_judgments"]
        assert held["judgments"]["E1.A"]["outcome"] == "verified"
        assert held["inputs"]["passages"], "the evidence the judgments rest on is identified"
        assert held["model"] == "m"
        assert client.calls > 0

    @pytest.mark.asyncio
    async def test_unchanged_evidence_costs_no_second_call(self, session, admin_user):
        study = await _study(session, admin_user)
        client = _Client()
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        first = client.calls
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        assert client.calls == first

    @pytest.mark.asyncio
    async def test_changed_passages_are_judged_again(self, session, admin_user):
        study = await _study(session, admin_user)
        client = _Client()
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        first = client.calls
        study.evidence_json = {
            **study.evidence_json,
            "paper_passages": {"methods": ["A different methods sentence entirely."], "statements": [], "claims": []},
        }
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        assert client.calls > first

    @pytest.mark.asyncio
    async def test_a_study_with_no_passages_asks_nothing_and_records_why(self, session, admin_user):
        study = await _study(session, admin_user, evidence={})
        client = _Client()
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        assert client.calls == 0
        assert study.evidence_json["rubric_judgments"]["reason"]

    @pytest.mark.asyncio
    async def test_the_score_that_follows_carries_the_judged_obligations(self, session, admin_user):
        from app.services.validation_report_summary import record_evidence_assessment

        study = await _study(session, admin_user)
        await refresh_documentary_review(session, study, client=_Client(), model="m", api_key="k")
        await record_evidence_assessment(session, study, reason="reviewed")
        outcomes = study.evidence_json["rubric_assessment"]["outcomes"]
        assert outcomes["E1.A"]["outcome"] == "verified"
        assert outcomes["E1.A"]["method"] == "model_assisted"
