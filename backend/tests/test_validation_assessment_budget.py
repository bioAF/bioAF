"""One assessment allowance includes semantic and transport retries and survives recovery."""

from types import SimpleNamespace

import httpx
import pytest

from app.services.llm_decision import decide, decide_with_recovery
from app.services.llm_provider_clients import ProviderError
from tests.support.fake_llm import FakeClient


async def ask(client, recovery=False):
    fn = decide_with_recovery if recovery else decide
    return await fn(
        intent="checking evidence",
        system="system",
        payload="evidence",
        client=client,
        model="m",
        api_key=None,
        purpose="documentary_judgment",
        **({"validate": lambda _: []} if recovery else {}),
    )


@pytest.mark.asyncio
async def test_semantic_retry_cannot_exceed_shared_allowance():
    from app.services.validation_decision_budgets import AssessmentBudget

    budget = AssessmentBudget(max_requests=1)
    client = FakeClient(["not JSON", {"outcome": "met"}])
    with budget.activate():
        await ask(client, recovery=True)
        result = await ask(client)
    assert len(client.asked) == 1
    assert budget.record()["requests"] == 1
    assert result.outcome == "budget_exhausted"


@pytest.mark.asyncio
async def test_account_failure_stops_later_stages_and_recovery():
    from app.services.validation_decision_budgets import AssessmentBudget

    client = FakeClient([ProviderError("no credit", error_class="account", account_fact="credit_exhausted")])
    budget = AssessmentBudget()
    with budget.activate():
        await ask(client)
        await ask(client)
    resumed = AssessmentBudget(saved=budget.record())
    with resumed.activate():
        result = await ask(client)
    assert len(client.asked) == 1
    assert result.outcome == "account"
    assert "credit" in result.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["characters", "time"])
async def test_character_and_elapsed_limits_stop_before_a_call(limit):
    from app.services.validation_decision_budgets import AssessmentBudget

    budget = AssessmentBudget(max_chars=1) if limit == "characters" else AssessmentBudget(max_seconds=0)
    client = FakeClient([{"outcome": "met"}])
    with budget.activate():
        result = await ask(client)
    assert not client.asked
    assert result.outcome == "budget_exhausted"


@pytest.mark.asyncio
async def test_transport_retry_is_a_second_actual_attempt(monkeypatch):
    from app.services.validation_decision_budgets import AssessmentBudget
    from app.services.llm_provider_clients import transport

    sends = []

    async def send():
        sends.append(True)
        return httpx.Response(503)

    async def submit(**_):
        await transport.request_with_retry(send, what="test")
        return '```json\n{"outcome":"met"}\n```'

    monkeypatch.setattr(transport, "_BACKOFF_SECONDS", (0, 0))
    budget = AssessmentBudget(max_requests=1)
    with budget.activate():
        result = await ask(SimpleNamespace(submit=submit))
    assert len(sends) == 1
    assert budget.record()["requests"] == 1
    assert result.outcome == "budget_exhausted"


@pytest.mark.asyncio
async def test_uncharged_cached_work_and_unrelated_calls_keep_their_allowance():
    from app.services.validation_decision_budgets import AssessmentBudget

    budget = AssessmentBudget(max_requests=1)
    client = FakeClient([{"outcome": "met"}] * 3)
    with budget.activate():
        await ask(client)
    await ask(client)
    resumed = AssessmentBudget(saved=budget.record(), max_requests=1)
    with resumed.activate():
        await ask(client)
    assert len(client.asked) == 2


@pytest.mark.asyncio
async def test_explicit_retry_clears_account_blocker_but_keeps_spent_allowance(monkeypatch):
    from unittest.mock import AsyncMock
    from app.services import validation_study_service as service

    study = SimpleNamespace(
        id=1,
        state="error",
        experiment_id=None,
        evidence_json={
            "assessment_budget": {
                "requests": 9,
                "input_characters": 1200,
                "blocker": {"outcome": "account", "reason": "credit exhausted"},
            },
            "assessment": {"model": "m"},
        },
    )
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: study)), flush=AsyncMock()
    )
    monkeypatch.setattr(service, "failed_read_of", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_has_runnable_samples", AsyncMock(return_value=False))
    monkeypatch.setattr(service.ValidationStudyService, "transition", AsyncMock(return_value=study))
    await service.ValidationStudyService.retry_study(session, 1, 1, 1)
    saved = study.evidence_json["assessment_budget"]
    assert saved["requests"] == 9 and saved["input_characters"] == 1200
    assert saved.get("blocker") is None
    assert "assessment" not in study.evidence_json


@pytest.mark.asyncio
async def test_questions_skipped_for_account_failure_are_not_cached_as_settled():
    from app.services.validation_decision_budgets import AssessmentBudget
    from app.services.validation_documentary_review import review_documents

    budget = AssessmentBudget(saved={"blocker": {"outcome": "account", "reason": "credit exhausted"}})
    with budget.activate():
        result = await review_documents(
            passages=[{"id": "p1", "kind": "methods", "text": "The stated method."}],
            leaves=("M1.A", "C4.A"),
            client=FakeClient([]),
            model="m",
            api_key=None,
        )
    assert {f["leaf"] for f in result["failures"]} == {"M1.A", "C4.A"}
