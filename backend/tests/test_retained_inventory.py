"""plan_8_3 stage 6: what a first attempt ESTABLISHED stands when its recovery never returned an answer.

Study 57's finding inventory established the membership of all eight findings and validated five of the
eight importances. Three quotes did not appear in the paper's text, so one recovery attempt was made,
and the provider answered that the account's credit balance was too low. bioAF recorded the whole
inventory as failed, and the report said the scope was not established: a paper whose five assay types
are correctly outside bioAF's methods was reported as a paper bioAF could not read into findings.

A recovery that never returned an answer is not evidence against the answer that did. The established
parts stand under the provisional scope the rubric already defines, and the record says which parts
remain open and why.

This is driven with study 57's OWN saved proposal, taken from the answer its issue record kept.
"""

import json
import pathlib

import pytest

from app.services.llm_provider_clients import ModelAnswer, ProviderError
from app.services.validation_finding_inventory import PROVISIONAL, UNRESOLVED
from app.services.validation_inventory_stage import run_inventory_stage

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "substrate_stiffness" / "study_57_persisted.json"


def _bundle() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _saved_proposal() -> str:
    """The proposal the live run's first attempt produced, as its issue record kept it."""
    bundle = _bundle()
    return next(
        i["technical_detail"]["answer_text"]
        for i in bundle["issues"]
        if (i.get("technical_detail") or {}).get("attempt") == 1
    )


def _targets() -> list[dict]:
    return [
        {
            "claim_text": t.get("claim_text"),
            "source_locator": t.get("source_locator"),
            "reported_experiment_id": t.get("reported_experiment_id"),
            "contrast_index": t.get("contrast_index"),
        }
        for t in _bundle()["comparison_targets"]
    ]


def _paper_text() -> str:
    """The paper's text as the fixture carries it: the passages the read kept, not the whole article.

    The live run validated five of the eight importances against the full text; against these passages
    a different number of quotes is found, because the text is what a quote is checked against. Which
    importances end up open is therefore a property of this stand-in and is never asserted here. What
    is asserted is the rule: the established membership survives a recovery that never answered, and
    whatever is open is recorded as open.
    """
    return json.dumps(_bundle()["study"]["evidence_json"].get("paper_passages") or {})


def _open_ids(inventory: dict) -> list[str]:
    return [f["id"] for f in inventory["findings"] if f["importance"]["status"] != "validated"]


class _Study:
    id = 57

    def __init__(self, evidence=None):
        self.evidence_json = evidence or {}


class _Cfg:
    model = "a-model"
    provider = "anthropic"
    api_key = "k"


def _client(second):
    """The live run's two attempts: its own saved proposal, then whatever the recovery answered."""
    calls: list[int] = []

    class _Fake:
        @staticmethod
        async def submit(*_args, **kwargs):
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                return ModelAnswer(_saved_proposal(), output_tokens=3636, stop_reason="end_turn")
            if isinstance(second, Exception):
                raise second
            return second

    return _Fake, calls


async def _run(second):
    study = _Study()
    client, calls = _client(second)
    result = await run_inventory_stage(
        study,
        full_text=_paper_text(),
        targets=_targets(),
        experiments=[],
        contrasts=[],
        client=client,
        cfg=_Cfg(),
    )
    return study, result, calls


class TestTheFirstAttemptsProposalIsRejectedAndKeptExactlyAsItWas:
    @pytest.mark.asyncio
    async def test_it_establishes_every_findings_membership_and_leaves_an_importance_open(self):
        _, result, _ = await _run(ProviderError("credit balance is too low", error_class="account"))
        inventory = result.inventory
        assert len(inventory["findings"]) == 8
        assert inventory["membership"]["status"] == "established"
        assert _open_ids(inventory)


class TestAnAccountFailureOnTheRecoveryLeavesWhatWasEstablishedStanding:
    @pytest.mark.asyncio
    async def test_the_stage_does_not_report_the_inventory_as_failed(self):
        _, result, calls = await _run(ProviderError("credit balance is too low", error_class="account"))
        assert len(calls) == 2
        assert result.failed is False
        assert result.inventory["status"] == PROVISIONAL
        assert not result.inventory.get("failed")

    @pytest.mark.asyncio
    async def test_it_records_which_parts_remain_open_and_why_the_recovery_did_not_happen(self):
        _, result, _ = await _run(ProviderError("credit balance is too low", error_class="account"))
        retained = result.inventory["retained"]
        assert retained["open"] == _open_ids(result.inventory)
        assert retained["attempt_outcome"] == "account"
        assert "account" in retained["cause"].lower()
        assert "second attempt" in retained["reason"]

    @pytest.mark.asyncio
    async def test_the_reason_the_reader_sees_is_the_open_importances_not_a_failed_read(self):
        _, result, _ = await _run(ProviderError("credit balance is too low", error_class="account"))
        reason = result.inventory["reason"]
        for finding_id in _open_ids(result.inventory):
            assert f"importance of {finding_id} is not established" in reason
        assert "could not group" not in reason

    @pytest.mark.asyncio
    async def test_the_issue_is_still_raised_so_the_account_problem_is_visible(self):
        _, result, _ = await _run(ProviderError("credit balance is too low", error_class="account"))
        assert [i["outcome"] for i in result.issues] == ["incomplete", "account"]

    @pytest.mark.asyncio
    async def test_a_transport_failure_on_the_recovery_is_treated_the_same_way(self):
        _, result, _ = await _run(ProviderError("connection reset", error_class="transport"))
        assert result.failed is False
        assert result.inventory["status"] == PROVISIONAL


class TestWhatDoesNotSurvive:
    @pytest.mark.asyncio
    async def test_a_recovery_that_answered_and_was_rejected_again_still_fails(self):
        """The recovery DID return an answer, and it was worse. Nothing is retained from the first."""
        _, result, _ = await _run(ModelAnswer('```json\n{"findings": []}\n```', stop_reason="end_turn"))
        assert result.failed is True
        assert result.inventory["status"] == UNRESOLVED

    @pytest.mark.asyncio
    async def test_a_proposal_whose_membership_was_never_established_is_not_retained(self):
        """Membership is the denominator. Without it there is no scope to be provisional about."""
        study = _Study()

        class _Fake:
            calls = 0

            @staticmethod
            async def submit(*_args, **_kwargs):
                _Fake.calls += 1
                if _Fake.calls == 1:
                    return ModelAnswer(
                        '```json\n{"findings": [{"description": "d", "claim_indices": [], "importance": "primary",'
                        ' "rationale": "r", "quote": ""}]}\n```',
                        stop_reason="end_turn",
                    )
                raise ProviderError("credit balance is too low", error_class="account")

        result = await run_inventory_stage(
            study,
            full_text=_paper_text(),
            targets=_targets(),
            experiments=[],
            contrasts=[],
            client=_Fake,
            cfg=_Cfg(),
        )
        assert result.failed is True
        assert result.inventory["status"] == UNRESOLVED
