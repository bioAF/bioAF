"""plan_8_3 stage 6: an account fact is not bioAF failing to reach the provider.

Study 57's finding inventory established its membership and validated five of eight importances. Three
quotes did not appear in the paper's text, so one recovery attempt was made, and the provider answered
that the account's credit balance was too low. bioAF reported "bioAF could not reach the language
model", discarded the whole inventory, and the report said the scope was not established. Two separate
defects, both here:

- an exhausted credit balance, an exhausted quota and a model the account is not entitled to are
  account facts whose remedy belongs to an administrator. They are named in plain words on screen, the
  provider's own sentence stays in the log, and no semantic retry is spent on a call that cannot
  succeed;
- what a first attempt ESTABLISHED stands when its recovery attempt never returned an answer. A
  recovery that could not be made is not evidence against the answer that was.
"""

import pytest

from app.services.llm_decision import (
    OUTCOME_ACCOUNT,
    OUTCOME_OK,
    SEMANTIC_FAILURES,
    decide,
    decide_with_recovery,
)
from app.services.llm_provider_clients import ModelAnswer, ProviderError, account_fact

_CREDIT = (
    '{"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too low '
    'to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits."}}'
)


class TestTheProviderClientsTellAnAccountFactApart:
    @pytest.mark.parametrize(
        "status, body",
        [
            (400, _CREDIT),
            (429, '{"error":{"code":"insufficient_quota","message":"You exceeded your current quota"}}'),
            (403, '{"error":{"message":"Your account is not authorized to use model claude-opus-5"}}'),
            (429, '{"error":{"status":"RESOURCE_EXHAUSTED","message":"Billing quota exceeded"}}'),
        ],
    )
    def test_an_account_problem_is_recognized_by_what_the_provider_said(self, status, body):
        assert account_fact(status, body) is not None

    @pytest.mark.parametrize(
        "status, body",
        [
            (401, '{"error":{"message":"invalid x-api-key"}}'),
            (429, '{"error":{"message":"Number of requests has exceeded your rate limit"}}'),
            (500, "upstream error"),
            (400, '{"error":{"message":"max_tokens: 999999 > 64000, which is the maximum"}}'),
        ],
    )
    def test_a_bad_key_a_rate_limit_and_a_server_error_are_not_account_facts(self, status, body):
        assert account_fact(status, body) is None

    def test_the_anthropic_client_raises_it_as_its_own_class(self):
        import httpx

        from app.services.llm_provider_clients.anthropic_client import _raise_for_status

        with pytest.raises(ProviderError) as raised:
            _raise_for_status(httpx.Response(400, text=_CREDIT))
        assert raised.value.error_class == "account"
        # The provider's own sentence is kept, for the log.
        assert "credit balance" in str(raised.value)


class TestADecisionNamesTheAccountProblemAndSpendsNothingMore:
    @staticmethod
    def _client(error):
        class _Fake:
            @staticmethod
            async def submit(*_args, **_kwargs):
                raise error

        return _Fake

    @pytest.mark.asyncio
    async def test_the_outcome_is_its_own_and_says_what_to_do(self):
        result = await decide(
            intent="grouping the paper's claims into findings",
            system="s",
            payload="p",
            client=self._client(ProviderError(_CREDIT, error_class="account")),
            model="a-model",
            api_key="k",
            purpose="finding_inventory",
        )
        assert result.outcome == OUTCOME_ACCOUNT
        assert "administrator" in result.reason
        assert "could not reach" not in result.reason

    @pytest.mark.asyncio
    async def test_the_providers_own_words_are_not_what_the_reader_is_shown(self):
        result = await decide(
            intent="grouping the paper's claims into findings",
            system="s",
            payload="p",
            client=self._client(ProviderError(_CREDIT, error_class="account")),
            model="a-model",
            api_key="k",
            purpose="finding_inventory",
        )
        assert "Plans & Billing" not in result.reason

    def test_it_is_never_a_semantic_failure_to_retry(self):
        assert OUTCOME_ACCOUNT not in SEMANTIC_FAILURES

    @pytest.mark.asyncio
    async def test_a_recovery_holds_instead_of_asking_again(self):
        calls: list[int] = []

        class _Fake:
            @staticmethod
            async def submit(*_args, **kwargs):
                calls.append(kwargs.get("max_tokens"))
                raise ProviderError(_CREDIT, error_class="account")

        result = await decide_with_recovery(
            intent="grouping the paper's claims into findings",
            system="s",
            payload="p",
            client=_Fake,
            model="a-model",
            api_key="k",
            purpose="finding_inventory",
        )
        assert result.outcome == OUTCOME_ACCOUNT
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_a_truncation_still_gets_its_one_larger_ask(self):
        """The contrast: a semantic failure is worth asking again, an account fact is not."""
        calls: list[int] = []

        class _Fake:
            @staticmethod
            async def submit(*_args, **kwargs):
                calls.append(kwargs.get("max_tokens"))
                if len(calls) == 1:
                    raise ProviderError("cut off", error_class="truncated", text="{", output_tokens=10)
                return ModelAnswer('```json\n{"ok": true}\n```', output_tokens=10, stop_reason="end_turn")

        result = await decide_with_recovery(
            intent="grouping the paper's claims into findings",
            system="s",
            payload="p",
            client=_Fake,
            model="a-model",
            api_key="k",
            purpose="finding_inventory",
        )
        assert result.outcome == OUTCOME_OK
        assert len(calls) == 2
