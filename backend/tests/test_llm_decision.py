"""plan_7 step 14a: one shape for asking a model to decide something.

Six modules independently declared the same `_FENCED_JSON_RE`, ran `json.loads` on the group, clamped
a confidence, and returned a hardcoded empty value on failure. The empties had drifted (`None` in
four, `[]` in one, `parse_failure: True` in another) and none of them could say WHY: a provider
outage, a refusal and a malformed answer all arrived at the call site as the same nothing.

What this module owns: transport, the fence, the parse, the clamp, the membership check, and a
structured outcome. What the caller keeps: the shape of its own answer, and what to do about a
failure. Every call returns `{outcome, data, reason, model}` and there is no bare empty value.

ADR-053 already owns the call, the family and the model, and is not re-implemented here.
"""

import json

import pytest

from app.services.llm_decision import (
    OUTCOME_INTERNAL,
    OUTCOME_OK,
    OUTCOME_REFUSAL,
    OUTCOME_UNPARSEABLE,
    OUTCOME_UNREACHABLE,
    Decision,
    confidence_of,
    decide,
    fenced_json,
)
from app.services.llm_provider_clients import ProviderError


def _fenced(obj) -> str:
    return "here you go\n```json\n" + json.dumps(obj) + "\n```\nhope that helps"


class _Client:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    async def submit(self, prompt, payload, model, api_key, attachments=None):
        self.calls.append({"prompt": prompt, "payload": payload, "model": model})
        if self.error is not None:
            raise self.error
        return self.response


async def _decide(client, **kw):
    return await decide(
        intent=kw.pop("intent", "binding the paper's claims"),
        system=kw.pop("system", "decide something"),
        payload=kw.pop("payload", "the evidence"),
        client=client,
        model=kw.pop("model", "claude-opus-4-8"),
        api_key=None,
        **kw,
    )


# ---- the tail every caller had its own copy of ----


class TestFencedJson:
    def test_it_finds_the_block_in_surrounding_prose(self):
        assert fenced_json(_fenced({"a": 1})) == {"a": 1}

    def test_an_unfenced_object_is_still_read(self):
        """Models drop the fence often enough that refusing on it would throw away good answers."""
        assert fenced_json('{"a": 1}') == {"a": 1}

    def test_prose_with_no_json_is_none(self):
        assert fenced_json("I am not able to help with that request.") is None

    def test_malformed_json_is_none_rather_than_an_exception(self):
        assert fenced_json("```json\n{not json,}\n```") is None

    def test_a_json_array_is_none_because_every_caller_asks_for_an_object(self):
        assert fenced_json("```json\n[1, 2, 3]\n```") is None

    def test_empty_input_is_none(self):
        assert fenced_json("") is None
        assert fenced_json(None) is None


class TestConfidenceClamp:
    @pytest.mark.parametrize("raw,expected", [(0.5, 0.5), (1.5, 1.0), (-2, 0.0), (1, 1.0)])
    def test_it_clamps_into_zero_to_one(self, raw, expected):
        assert confidence_of(raw) == expected

    @pytest.mark.parametrize("raw", ["high", None, True, False, {}, []])
    def test_anything_that_is_not_a_number_is_no_confidence_at_all(self, raw):
        assert confidence_of(raw) == 0.0


# ---- the outcomes ----


class TestAGoodAnswer:
    @pytest.mark.asyncio
    async def test_it_returns_ok_with_the_parsed_data(self):
        d = await _decide(_Client(_fenced({"verdict": "likely", "confidence": 0.8})))
        assert d.outcome == OUTCOME_OK
        assert d.ok
        assert d.data["verdict"] == "likely"

    @pytest.mark.asyncio
    async def test_it_names_the_model_that_decided(self):
        """plan_6's rule. Four of the five existing decisions recorded no model at all."""
        d = await _decide(_Client(_fenced({"a": 1})), model="gpt-5")
        assert d.model == "gpt-5"

    @pytest.mark.asyncio
    async def test_the_prompt_and_payload_reach_the_provider_unchanged(self):
        client = _Client(_fenced({"a": 1}))
        await _decide(client, system="SYSTEM", payload="PAYLOAD")
        assert client.calls[0]["prompt"] == "SYSTEM"
        assert client.calls[0]["payload"] == "PAYLOAD"


class TestNothingIsSwallowed:
    """ "Never raise" stays. It stops meaning "never explain"."""

    @pytest.mark.asyncio
    async def test_a_refusal_is_its_own_outcome_and_names_the_model(self):
        """An admin needs the model's name to request an account exception. Today a refusal is
        invisible at every layer and the study degrades with nothing on screen."""
        client = _Client(error=ProviderError("blocked", error_class="refusal"))
        d = await _decide(client, model="claude-opus-4-8")
        assert d.outcome == OUTCOME_REFUSAL
        assert "claude-opus-4-8" in d.reason

    @pytest.mark.asyncio
    @pytest.mark.parametrize("error_class", ["auth", "rate_limit", "transport", "server"])
    async def test_a_provider_we_could_not_reach_is_unreachable(self, error_class):
        d = await _decide(_Client(error=ProviderError("boom", error_class=error_class)))
        assert d.outcome == OUTCOME_UNREACHABLE

    @pytest.mark.asyncio
    async def test_an_answer_with_no_json_in_it_is_unparseable(self):
        d = await _decide(_Client("I cannot help with that."))
        assert d.outcome == OUTCOME_UNPARSEABLE

    @pytest.mark.asyncio
    async def test_a_provider_parse_failure_is_unparseable_too(self):
        d = await _decide(_Client(error=ProviderError("bad body", error_class="parse")))
        assert d.outcome == OUTCOME_UNPARSEABLE

    @pytest.mark.asyncio
    async def test_any_other_exception_is_internal_and_does_not_escape(self):
        """Asking a model for help still cannot kill a study."""
        d = await _decide(_Client(error=RuntimeError("a bug in our own code")))
        assert d.outcome == OUTCOME_INTERNAL
        assert d.data == {}

    @pytest.mark.asyncio
    async def test_every_failure_carries_a_sentence_a_scientist_can_read(self):
        """One actionable sentence on screen, technical detail to the logs."""
        for error in (
            ProviderError("x", error_class="refusal"),
            ProviderError("x", error_class="transport"),
            RuntimeError("x"),
        ):
            d = await _decide(_Client(error=error), intent="choosing which deposited file to use")
            assert d.reason
            assert "choosing which deposited file to use" in d.reason
            assert "Traceback" not in d.reason

    @pytest.mark.asyncio
    async def test_a_failure_is_never_confused_with_an_empty_answer(self):
        """The `None` vs `[]` drift, dissolved: both used to mean "the call failed" AND "answered,
        nothing to say", and were indistinguishable at the call site."""
        failed = await _decide(_Client(error=ProviderError("x", error_class="server")))
        answered_nothing = await _decide(_Client(_fenced({})))
        assert failed.outcome != answered_nothing.outcome
        assert answered_nothing.ok


class TestTheAllowList:
    @pytest.mark.asyncio
    async def test_absent_means_unconstrained(self):
        """What lets the extractor and the generated-analysis arm use the same function instead of
        forking it: neither has a closed set to check against."""
        d = await _decide(_Client(_fenced({"anything": "at all"})))
        assert d.member("at all")
        assert d.choice("anything") == "at all"

    @pytest.mark.asyncio
    async def test_a_value_in_the_list_is_taken(self):
        d = await _decide(_Client(_fenced({"verdict": "validated"})), allowed=["validated", "inconclusive"])
        assert d.choice("verdict") == "validated"

    @pytest.mark.asyncio
    async def test_an_invented_value_is_refused_and_said_so(self):
        """The one failure this check exists to remove: an invented key persists as a binding and is
        then compared against a metric that does not exist."""
        d = await _decide(_Client(_fenced({"verdict": "mostly_right"})), allowed=["validated", "inconclusive"])
        assert d.choice("verdict") is None
        assert any("mostly_right" in n for n in d.notes)

    @pytest.mark.asyncio
    async def test_membership_is_available_for_a_shape_the_helper_does_not_own(self):
        """`resolve_columns` answers with a mapping whose VALUES are the closed set. The helper does
        not learn that shape; it lends the check."""
        d = await _decide(_Client(_fenced({"columns": {"id": "gene", "lfc": "made_up"}})), allowed=["gene", "padj"])
        columns = d.data["columns"]
        assert d.member(columns["id"])
        assert not d.member(columns["lfc"])

    @pytest.mark.asyncio
    async def test_a_missing_field_is_none_without_a_complaint(self):
        d = await _decide(_Client(_fenced({})), allowed=["a"])
        assert d.choice("verdict") is None
        assert d.notes == []


class TestTheHelperHasNoRetryConcept:
    @pytest.mark.asyncio
    async def test_a_failure_calls_the_provider_exactly_once(self):
        """Transport retry belongs in the provider clients, below this; the semantic re-ask belongs
        to the caller, which is the only layer that knows a second look is worth paying for."""
        client = _Client(error=ProviderError("429", error_class="rate_limit"))
        await _decide(client)
        assert len(client.calls) == 1


class TestTheRecordItLeaves:
    def test_an_issue_row_says_what_a_person_can_act_on(self):
        d = Decision(
            outcome=OUTCOME_REFUSAL,
            reason="The model claude-opus-4-8 declined to answer while binding the paper's claims.",
            model="claude-opus-4-8",
            intent="binding the paper's claims",
        )
        row = d.as_issue(impact="degraded")
        assert row["step"] == "binding the paper's claims"
        assert row["outcome"] == OUTCOME_REFUSAL
        assert row["impact"] == "degraded"
        assert row["model"] == "claude-opus-4-8"
        assert row["at"]

    def test_impact_distinguishes_a_step_that_carried_on_from_one_that_produced_nothing(self):
        """Without it the section cries wolf and users learn to ignore it."""
        d = Decision(outcome=OUTCOME_UNREACHABLE, reason="r", model="m", intent="i")
        assert d.as_issue(impact="degraded")["impact"] == "degraded"
        assert d.as_issue(impact="blocked")["impact"] == "blocked"

    def test_an_ok_decision_has_no_issue_to_record(self):
        d = Decision(outcome=OUTCOME_OK, model="m", intent="i")
        assert d.as_issue(impact="degraded") is None
