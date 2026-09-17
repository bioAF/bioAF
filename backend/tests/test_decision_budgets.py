"""plan_8_3 stage 6: every decision bioAF asks a model to make runs under an explicit output budget.

Study 50's 16 claim bindings all failed after the answer was cut off at the Anthropic client's 4,096-token
default, and its input choice was cut off too. Only the paper read's two calls had been budgeted. This
audit gives every decision call site a registered purpose whose budget, recovery policy and measurement
evidence are recorded, and a caller that names no purpose fails here rather than inheriting a default.
"""

import ast
import json
from pathlib import Path

import pytest

from app.services import validation_decision_budgets as budgets
from app.services.llm_decision import OUTCOME_OK, OUTCOME_TRUNCATED, decide, decide_with_recovery
from app.services.llm_provider_clients import ModelAnswer, ProviderError

_APP = Path(__file__).resolve().parent.parent / "app"


def _decide_call_sites() -> list[tuple[str, int, set[str], str | None]]:
    """Every live ``decide(...)`` call in the application, with the keywords and purpose it names.

    ``llm_decision`` itself is the contract, not a caller: its one call forwards the purpose it was
    given.
    """
    sites = []
    for path in sorted(_APP.rglob("*.py")):
        if path.name == "llm_decision.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name not in ("decide", "decide_with_recovery"):
                continue
            named = {k.arg: k.value for k in node.keywords if k.arg}
            purpose = named.get("purpose")
            sites.append(
                (
                    str(path.relative_to(_APP.parent)),
                    node.lineno,
                    set(named),
                    getattr(budgets, getattr(purpose, "attr", getattr(purpose, "id", "")), None),
                )
            )
    return sites


class TestTheAuditCoversEveryLiveCaller:
    def test_every_decide_call_names_a_registered_purpose(self):
        """An added decision caller cannot silently inherit an unreviewed default: it fails here."""
        unnamed = [(f, line) for f, line, kw, purpose in _decide_call_sites() if purpose is None]
        assert unnamed == []

    def test_every_call_site_the_audit_names_still_exists(self):
        """A registry entry whose module no longer calls a model is stale and must be reviewed."""
        calling = {f for f, _, _, _ in _decide_call_sites()}
        for policy in budgets.DECISIONS.values():
            assert policy.module in calling, f"{policy.purpose} names {policy.module}, which asks no model"

    def test_the_audit_covers_exactly_the_purposes_the_application_asks_for(self):
        assert {purpose for _, _, _, purpose in _decide_call_sites()} == set(budgets.DECISIONS)


class TestEveryEntryHasAPolicyAndEvidence:
    @pytest.mark.parametrize("purpose", sorted(budgets.DECISIONS))
    def test_it_states_a_budget_a_recovery_policy_and_how_the_budget_was_established(self, purpose):
        policy = budgets.DECISIONS[purpose]
        assert policy.max_tokens >= 1000
        assert policy.recovery in budgets.RECOVERY_POLICIES
        assert policy.basis.strip()
        assert policy.decision.strip()

    @pytest.mark.parametrize("purpose", sorted(budgets.DECISIONS))
    def test_a_measured_entry_names_the_record_that_measured_it(self, purpose):
        policy = budgets.DECISIONS[purpose]
        if policy.measurement != budgets.MEASURED:
            assert policy.measurement in budgets.MEASUREMENT_STATES
            return
        record = json.loads((budgets.RECORDS / f"{policy.record}.json").read_text(encoding="utf-8"))
        largest = max(p["output_tokens"] for m in record["models"].values() for p in m["papers"])
        assert largest < policy.max_tokens, "a measured budget must hold the largest measured answer"


class TestTheBudgetForAPurpose:
    def test_a_purpose_the_audit_does_not_name_is_refused(self):
        with pytest.raises(KeyError):
            budgets.budget_for("guessing", "claude-opus-4-8")

    def test_the_budget_never_exceeds_the_models_documented_maximum(self, monkeypatch):
        monkeypatch.setattr(budgets, "model_output_limit", lambda model: 8192)
        assert budgets.budget_for(budgets.CLAIM_BINDING, "a-small-model").max_tokens == 8192

    @pytest.mark.parametrize("purpose", sorted(budgets.DECISIONS))
    def test_no_entry_asks_for_more_than_a_known_model_allows(self, purpose):
        from app.services.validation_read_budget import model_output_limit

        assert budgets.budget_for(purpose, "gpt-4o").max_tokens <= model_output_limit("gpt-4o")

    def test_it_carries_whether_this_model_was_measured(self):
        chosen = budgets.budget_for(budgets.CLAIM_BINDING, "a-model-nobody-measured")
        assert chosen.measured is False
        assert chosen.note


class _BudgetClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.budgets: list = []

    async def submit(self, prompt, payload, model, api_key, attachments=None, max_tokens=None):
        self.budgets.append(max_tokens)
        answer = self.responses[min(len(self.budgets) - 1, len(self.responses) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return answer


_ANSWER = "```json\n" + json.dumps({"claims": []}) + "\n```"


async def _decide(client, **kw):
    return await decide(
        intent="binding the paper's claims",
        system="bind them",
        payload="the claims",
        client=client,
        model="claude-opus-4-8",
        api_key=None,
        **kw,
    )


class TestTheBudgetReachesTheProvider:
    @pytest.mark.asyncio
    async def test_a_purpose_sends_its_registered_budget_to_the_client(self):
        client = _BudgetClient(_ANSWER)
        decision = await _decide(client, purpose=budgets.CLAIM_BINDING)
        assert decision.ok
        assert client.budgets == [budgets.DECISIONS[budgets.CLAIM_BINDING].max_tokens]

    @pytest.mark.asyncio
    async def test_an_explicit_budget_still_wins_so_a_caller_that_measures_its_own_keeps_it(self):
        client = _BudgetClient(_ANSWER)
        await _decide(client, purpose=budgets.PAPER_READING, max_tokens=64000)
        assert client.budgets == [64000]

    @pytest.mark.asyncio
    async def test_the_decision_records_the_purpose_it_ran_under(self):
        decision = await _decide(_BudgetClient(_ANSWER), purpose=budgets.CLAIM_BINDING)
        assert decision.purpose == budgets.CLAIM_BINDING


def _truncated() -> ProviderError:
    error = ProviderError("cut off", error_class="truncated")
    error.text = '{"claims": ['
    error.output_tokens = 4096
    error.stop_reason = "max_tokens"
    return error


class TestOneSemanticRecoveryPerDecision:
    @pytest.mark.asyncio
    async def test_a_cut_off_answer_is_asked_again_once_with_a_larger_budget(self):
        client = _BudgetClient(_truncated(), _ANSWER)
        decision = await decide_with_recovery(
            intent="binding the paper's claims",
            system="bind them",
            payload="the claims",
            client=client,
            model="claude-opus-4-8",
            api_key=None,
            purpose=budgets.CLAIM_BINDING,
        )
        assert decision.ok
        assert len(client.budgets) == 2
        assert client.budgets[1] > client.budgets[0]
        assert [a["outcome"] for a in decision.attempts] == [OUTCOME_TRUNCATED, OUTCOME_OK]

    @pytest.mark.asyncio
    async def test_a_second_failure_ends_the_decision_rather_than_asking_again(self):
        client = _BudgetClient(_truncated(), _truncated(), _ANSWER)
        decision = await decide_with_recovery(
            intent="binding the paper's claims",
            system="bind them",
            payload="the claims",
            client=client,
            model="claude-opus-4-8",
            api_key=None,
            purpose=budgets.CLAIM_BINDING,
        )
        assert decision.outcome == OUTCOME_TRUNCATED
        assert len(client.budgets) == 2
        assert "cut off" in decision.reason

    @pytest.mark.asyncio
    async def test_an_answer_that_fails_the_callers_schema_is_asked_again_with_what_was_wrong(self):
        good = "```json\n" + json.dumps({"claims": [{"claim_index": 0}]}) + "\n```"
        client = _BudgetClient(_ANSWER, good)
        seen: list[str] = []

        async def _submit(prompt, payload, model, api_key, attachments=None, max_tokens=None):
            seen.append(payload)
            return client.responses[min(len(seen) - 1, len(client.responses) - 1)]

        client.submit = _submit
        decision = await decide_with_recovery(
            intent="binding the paper's claims",
            system="bind them",
            payload="the claims",
            client=client,
            model="claude-opus-4-8",
            api_key=None,
            purpose=budgets.CLAIM_BINDING,
            validate=lambda data: [] if data.get("claims") else ["no claim was decided"],
        )
        assert decision.ok
        assert len(seen) == 2
        assert "no claim was decided" in seen[1]

    @pytest.mark.asyncio
    async def test_a_schema_failure_that_repeats_is_reported_and_not_asked_a_third_time(self):
        client = _BudgetClient(_ANSWER, _ANSWER, _ANSWER)
        decision = await decide_with_recovery(
            intent="binding the paper's claims",
            system="bind them",
            payload="the claims",
            client=client,
            model="claude-opus-4-8",
            api_key=None,
            purpose=budgets.CLAIM_BINDING,
            validate=lambda data: ["no claim was decided"],
        )
        assert decision.outcome == budgets.OUTCOME_SCHEMA_REJECTED
        assert len(client.budgets) == 2
        assert "no claim was decided" in decision.reason

    @pytest.mark.asyncio
    async def test_a_model_already_at_its_maximum_is_not_asked_again(self):
        client = _BudgetClient(_truncated(), _ANSWER)
        decision = await decide_with_recovery(
            intent="binding the paper's claims",
            system="bind them",
            payload="the claims",
            client=client,
            model="claude-opus-4-8",
            api_key=None,
            purpose=budgets.CLAIM_BINDING,
            max_tokens=128000,
        )
        assert decision.outcome == OUTCOME_TRUNCATED
        assert len(client.budgets) == 1

    @pytest.mark.asyncio
    async def test_an_unreachable_provider_is_not_a_semantic_failure_and_is_not_re_asked(self):
        client = _BudgetClient(ProviderError("down", error_class="transport"), _ANSWER)
        decision = await decide_with_recovery(
            intent="binding the paper's claims",
            system="bind them",
            payload="the claims",
            client=client,
            model="claude-opus-4-8",
            api_key=None,
            purpose=budgets.CLAIM_BINDING,
        )
        assert decision.outcome == "unreachable"
        assert len(client.budgets) == 1


class TestEachAttemptIsRecorded:
    @pytest.mark.asyncio
    async def test_every_attempt_carries_its_budget_outcome_and_what_the_model_produced(self):
        client = _BudgetClient(_truncated(), ModelAnswer(_ANSWER, output_tokens=812, stop_reason="end_turn"))
        decision = await decide_with_recovery(
            intent="binding the paper's claims",
            system="bind them",
            payload="the claims",
            client=client,
            model="claude-opus-4-8",
            api_key=None,
            purpose=budgets.CLAIM_BINDING,
        )
        first, second = decision.attempts
        assert first["max_tokens"] == budgets.DECISIONS[budgets.CLAIM_BINDING].max_tokens
        assert first["output_tokens"] == 4096 and first["stop_reason"] == "max_tokens"
        assert second["output_tokens"] == 812 and second["outcome"] == OUTCOME_OK
        assert second["headroom"] == second["max_tokens"] - 812
