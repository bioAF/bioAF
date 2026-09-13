"""plan_8_1 section 1.2: the paper read recovers once, and never more than once.

At most two model submissions per extraction cycle: the initial attempt and one recovery attempt shared
across truncation and structural failure. A change of failure type does not reset the count, and neither
does a worker restart, because each attempt is recorded and committed before it is submitted.

- **Truncated**: retried once with a larger budget.
- **Structurally incomplete**: retried once at the same budget, naming the rejected field paths.
- **Anything else** (unparseable, refused, unreachable, timed out): the read ends.

Every attempt records the budget it was given, the output tokens and stop reason the provider reported,
and its elapsed time. The cut-off or incomplete text is kept on the issue record, never parsed.
"""

import json
from types import SimpleNamespace

import pytest

from app.services import validation_read_budget as read_budget
from app.services.llm_provider_clients import ModelAnswer, ProviderError
from app.services.validation_extraction_service import PAPER_READING_INTENT, read_paper
from app.services.validation_read_cycle import begin_cycle, current_cycle, start_attempt


def _answer(**overrides) -> str:
    body = {
        "accessions": ["GSE1"],
        "sample_structure": {"organism": "Mus musculus", "sample_count": 6},
        "method": {"assay": "bulk RNA-seq", "tools": [], "reference_build": "GRCm38"},
        "reported_experiments": [],
        "resources": [],
        "differential_design": {"contrasts": []},
        "claims": [{"metric_key": "", "claim_text": "Hundreds of genes changed.", "value": 300}],
        "significance_ambiguities": [],
        "data_availability": "deposited",
        "code_availability": [],
        "blockers": [],
    }
    body.update(overrides)
    for key in [k for k, v in overrides.items() if v is _DROP]:
        del body[key]
    return "```json\n" + json.dumps(body) + "\n```"


_DROP = object()
_TRUNCATED = ProviderError(
    "cut off", error_class="truncated", text='{"claims": [{"claim', output_tokens=16000, stop_reason="max_tokens"
)


class _Client:
    """Hands back one scripted answer (or failure) per submission, and keeps each call."""

    def __init__(self, *script, log: list | None = None):
        self.script = list(script)
        self.calls: list[dict] = []
        self.log = log if log is not None else []

    async def submit(self, prompt, payload, model, api_key, attachments=None, max_tokens=None):
        self.log.append("submit")
        self.calls.append({"payload": payload, "max_tokens": max_tokens})
        step = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(step, Exception):
            raise step
        return step


_CFG = SimpleNamespace(provider="anthropic", model="claude-opus-4-8", api_key=None)


def _study(evidence: dict | None = None):
    return SimpleNamespace(id=1, evidence_json=evidence or {})


@pytest.fixture(autouse=True)
def _measured(monkeypatch):
    monkeypatch.setattr(
        read_budget,
        "load_record",
        lambda call: {"fingerprint": "f", "chosen_budget": 16000, "models": {"claude-opus-4-8": {"papers": []}}},
    )


async def _read(study, client, *, log=None):
    async def checkpoint():
        if log is not None:
            log.append("checkpoint")

    if current_cycle(study.evidence_json) is None:
        begin_cycle(study, model=_CFG.model, provider=_CFG.provider)
    return await read_paper(
        study, system="read it", payload="the paper", client=client, cfg=_CFG, checkpoint=checkpoint
    )


class TestTruncation:
    @pytest.mark.asyncio
    async def test_one_cutoff_then_success_retries_once_with_a_larger_budget(self):
        study = _study()
        client = _Client(_TRUNCATED, ModelAnswer(_answer(), output_tokens=21000, stop_reason="end_turn"))
        read = await _read(study, client)
        assert read.ok
        assert [c["max_tokens"] for c in client.calls] == [16000, 32000]
        attempts = current_cycle(study.evidence_json)["attempts"]
        assert [a["outcome"] for a in attempts] == ["truncated", "ok"]
        assert [a["max_tokens"] for a in attempts] == [16000, 32000]
        assert attempts[1]["output_tokens"] == 21000
        assert attempts[0]["output_tokens"] == 16000
        assert all(a["elapsed_seconds"] is not None for a in attempts)
        assert read.data["claims"][0]["claim_text"] == "Hundreds of genes changed."

    @pytest.mark.asyncio
    async def test_the_cutoff_text_is_kept_on_the_issue_record_and_never_parsed(self):
        study = _study()
        read = await _read(study, _Client(_TRUNCATED, _answer()))
        (issue,) = read.issues
        assert issue["outcome"] == "truncated"
        assert issue["step"] == PAPER_READING_INTENT
        assert issue["impact"] == "degraded"
        assert issue["technical_detail"]["answer_text"] == '{"claims": [{"claim'

    @pytest.mark.asyncio
    async def test_two_cutoffs_end_the_read_as_a_bioaf_limitation(self):
        study = _study()
        client = _Client(_TRUNCATED, _TRUNCATED)
        read = await _read(study, client)
        assert not read.ok
        assert len(client.calls) == 2
        assert "cut off" in read.cause
        assert read.issues[-1]["impact"] == "blocked"
        assert current_cycle(study.evidence_json)["status"] == "failed"


class TestStructuralFailure:
    @pytest.mark.asyncio
    async def test_missing_claims_then_complete_retries_once_at_the_same_budget_naming_the_path(self):
        study = _study()
        client = _Client(_answer(claims=_DROP), _answer())
        read = await _read(study, client)
        assert read.ok
        assert [c["max_tokens"] for c in client.calls] == [16000, 16000]
        assert "claims is missing" in client.calls[1]["payload"]
        assert current_cycle(study.evidence_json)["attempts"][0]["problems"] == ["claims is missing"]

    @pytest.mark.asyncio
    async def test_an_empty_method_triggers_recovery(self):
        study = _study()
        client = _Client(_answer(method={}), _answer())
        read = await _read(study, client)
        assert read.ok
        assert "method.assay is missing" in client.calls[1]["payload"]

    @pytest.mark.asyncio
    async def test_a_claim_missing_its_claim_text_triggers_recovery(self):
        client = _Client(_answer(claims=[{"metric_key": "x", "value": 1}]), _answer())
        read = await _read(_study(), client)
        assert read.ok
        assert "claims[0].claim_text is missing" in client.calls[1]["payload"]

    @pytest.mark.asyncio
    async def test_present_empty_claims_is_an_answer_and_spends_no_recovery(self):
        client = _Client(_answer(claims=[]))
        read = await _read(_study(), client)
        assert read.ok
        assert len(client.calls) == 1


class TestMixedFailuresShareOneRecovery:
    @pytest.mark.asyncio
    async def test_truncation_then_structural_failure_stops_at_two(self):
        client = _Client(_TRUNCATED, _answer(claims=_DROP), _answer())
        read = await _read(_study(), client)
        assert not read.ok
        assert len(client.calls) == 2
        assert "cut off" in read.cause and "claims is missing" in read.cause

    @pytest.mark.asyncio
    async def test_structural_failure_then_truncation_stops_at_two(self):
        client = _Client(_answer(method={}), _TRUNCATED, _answer())
        read = await _read(_study(), client)
        assert not read.ok
        assert len(client.calls) == 2


class TestTerminalFailures:
    @pytest.mark.asyncio
    async def test_an_unparseable_answer_ends_the_read(self):
        client = _Client("I read the paper and it is about genes.")
        read = await _read(_study(), client)
        assert not read.ok
        assert len(client.calls) == 1
        assert "format" in read.cause

    @pytest.mark.asyncio
    async def test_a_refusal_ends_the_read(self):
        client = _Client(ProviderError("no", error_class="refusal"))
        read = await _read(_study(), client)
        assert not read.ok and len(client.calls) == 1
        assert "declined" in read.cause

    @pytest.mark.asyncio
    async def test_an_unreachable_provider_ends_the_read(self):
        client = _Client(ProviderError("down", error_class="transport"))
        read = await _read(_study(), client)
        assert not read.ok and len(client.calls) == 1
        assert "could not reach" in read.cause


class TestAttemptsSurviveARestart:
    @pytest.mark.asyncio
    async def test_each_attempt_is_committed_before_it_is_submitted(self):
        log: list = []
        client = _Client(_TRUNCATED, _answer(), log=log)
        await _read(_study(), client, log=log)
        submits = [i for i, entry in enumerate(log) if entry == "submit"]
        assert len(submits) == 2
        assert all(i > 0 and log[i - 1] == "checkpoint" for i in submits)

    @pytest.mark.asyncio
    async def test_an_attempt_interrupted_by_a_restart_still_counts(self):
        study = _study()
        begin_cycle(study, model=_CFG.model, provider=_CFG.provider)
        start_attempt(study, max_tokens=16000)  # the worker stopped while this call was out
        client = _Client(_TRUNCATED)
        read = await _read(study, client)
        assert not read.ok
        assert len(client.calls) == 1
        assert [a["outcome"] for a in current_cycle(study.evidence_json)["attempts"]] == ["interrupted", "truncated"]

    @pytest.mark.asyncio
    async def test_a_cycle_whose_two_attempts_are_spent_submits_nothing_more(self):
        study = _study()
        begin_cycle(study, model=_CFG.model, provider=_CFG.provider)
        start_attempt(study, max_tokens=16000)
        start_attempt(study, max_tokens=16000)
        client = _Client(_answer())
        read = await _read(study, client)
        assert not read.ok
        assert client.calls == []
        assert "interrupted" in read.cause

    @pytest.mark.asyncio
    async def test_a_restart_after_a_recorded_truncation_resumes_with_the_larger_budget(self):
        study = _study()
        begin_cycle(study, model=_CFG.model, provider=_CFG.provider)
        start_attempt(study, max_tokens=16000)
        from app.services.validation_read_cycle import finish_attempt

        finish_attempt(study, outcome="truncated", output_tokens=16000)
        client = _Client(_answer())
        read = await _read(study, client)
        assert read.ok
        assert [c["max_tokens"] for c in client.calls] == [32000]


class TestTheBudgetOnTheProvenance:
    @pytest.mark.asyncio
    async def test_a_measured_model_records_its_budget(self):
        study = _study()
        await _read(study, _Client(_answer()))
        cycle = current_cycle(study.evidence_json)
        assert cycle["budget"]["max_tokens"] == 16000
        assert cycle["budget"]["measured"] is True
        assert cycle["status"] == "succeeded"

    @pytest.mark.asyncio
    async def test_an_unmeasured_model_reads_and_its_provenance_says_so(self):
        study = _study()
        begin_cycle(study, model="claude-sonnet-5", provider="anthropic")
        read = await read_paper(
            study,
            system="read it",
            payload="the paper",
            client=_Client(_answer()),
            cfg=SimpleNamespace(provider="anthropic", model="claude-sonnet-5", api_key=None),
            checkpoint=None,
        )
        assert read.ok
        assert current_cycle(study.evidence_json)["budget"]["note"] == "budget not measured for this model"
