"""plan_8_5 section 3.6: the judgments that had a contract and no caller.

The contract was built (what may be asked about one obligation, and what is done with the answer)
and nothing ever called it, so every experimental and most computational obligations stayed
declared capability limits. This is the production caller: it supplies the paper's own passages,
asks one question per obligation, checks the answer against the evidence it was given, and persists
what it accepted.

The rules it is held to: one obligation per request, a failure that affects only its own obligation,
a bounded budget, no judgment at render time, and no reuse of an answer whose evidence has moved.
"""

import pytest

from app.services.validation_documentary_review import (
    JUDGED_LEAVES,
    review_documents,
)


def _passages():
    return [
        {"id": "p1", "source": "methods", "text": "Embryos were cultured to day 5 and sequenced with Smart-seq2."},
        {"id": "p2", "source": "methods", "text": "Differential expression used DESeq2 with a Wald test."},
    ]


class _Client:
    """A model client at its own boundary: it records what it was asked and answers what it is told."""

    def __init__(self, answers, fail=()):
        self.answers = answers
        self.fail = set(fail)
        self.asked = []

    async def submit(self, *, prompt, payload, model=None, api_key=None, max_tokens=None, **kw):
        self.asked.append({"prompt": payload, "system": prompt, "max_tokens": max_tokens})
        leaf = next((leaf for leaf in JUDGED_LEAVES if f"({leaf.replace('.', ' ')})" in payload), None)
        if leaf in self.fail:
            from app.services.llm_provider_clients import ProviderError

            raise ProviderError("the provider is unreachable")
        answer = self.answers.get(leaf) or {
            "outcome": "cannot_establish",
            "rationale": "the evidence supplied does not settle it",
            "citations": [],
        }
        import json

        return json.dumps(answer)


def _met(rationale="the methods state the procedure", citations=("p1",)):
    return {"outcome": "met", "rationale": rationale, "citations": list(citations), "confidence": 0.8}


class TestOneQuestionPerObligation:
    @pytest.mark.asyncio
    async def test_each_obligation_is_asked_on_its_own_with_its_own_words(self):
        client = _Client({})
        await review_documents(passages=_passages(), client=client, model="m", api_key="k")
        assert len(client.asked) == len(JUDGED_LEAVES)
        assert all("Obligation (" in a["prompt"] for a in client.asked)
        assert all(a["max_tokens"] for a in client.asked), "every call runs under a declared budget"

    @pytest.mark.asyncio
    async def test_no_request_asks_for_a_score_or_a_rating(self):
        client = _Client({})
        await review_documents(passages=_passages(), client=client, model="m", api_key="k")
        for asked in client.asked:
            assert "out of 100" not in asked["prompt"]
            assert "rate the paper" not in asked["prompt"].lower()


class TestWhatIsAcceptedAndWhatIsNot:
    @pytest.mark.asyncio
    async def test_an_evidence_backed_judgment_is_accepted_with_its_citation(self):
        client = _Client({"E1.A": _met()})
        found = await review_documents(passages=_passages(), client=client, model="m", api_key="k")
        judgment = found["judgments"]["E1.A"]
        assert judgment["outcome"] == "verified"
        assert judgment["evidence"]["citations"] == ["p1"]
        assert judgment["method"] == "model_assisted"

    @pytest.mark.asyncio
    async def test_a_judgment_citing_something_that_was_never_supplied_earns_nothing(self):
        client = _Client({"E1.A": _met(citations=["p99"])})
        found = await review_documents(passages=_passages(), client=client, model="m", api_key="k")
        assert found["judgments"]["E1.A"]["outcome"] == "undetermined"

    @pytest.mark.asyncio
    async def test_a_provider_failure_affects_only_its_own_obligation(self):
        client = _Client({"E1.A": _met(), "E2.A": _met(citations=["p2"])}, fail=["E1.B"])
        found = await review_documents(passages=_passages(), client=client, model="m", api_key="k")
        assert found["judgments"]["E1.A"]["outcome"] == "verified"
        assert found["judgments"]["E1.B"]["outcome"] == "undetermined"
        assert found["judgments"]["E1.B"]["next_action"]
        assert found["failures"], "the failure is recorded rather than silently dropped"

    @pytest.mark.asyncio
    async def test_every_judgment_records_the_model_and_the_contract_it_was_made_under(self):
        client = _Client({"E1.A": _met()})
        found = await review_documents(passages=_passages(), client=client, model="claude-x", api_key="k")
        assessor = found["judgments"]["E1.A"]["assessor"]
        assert assessor["model"] == "claude-x"
        assert assessor["contract_version"]
        assert found["at"]


class TestItNeverAsksWithoutEvidence:
    @pytest.mark.asyncio
    async def test_with_no_passages_nothing_is_asked_and_nothing_is_judged(self):
        client = _Client({"E1.A": _met()})
        found = await review_documents(passages=[], client=client, model="m", api_key="k")
        assert client.asked == []
        assert found["judgments"] == {}
        assert found["reason"]

    @pytest.mark.asyncio
    async def test_with_no_provider_nothing_is_asked(self):
        found = await review_documents(passages=_passages(), client=None, model="", api_key=None)
        assert found["judgments"] == {}
        assert found["reason"]


class TestTheEvidenceTheAssessorIsGiven:
    """plan_8_5 section 3.6: the paper's own passages, the deposit's own records, and the tools the
    analysis declares. Each one carries where it came from, so a citation can be resolved."""

    _EVIDENCE = {
        "paper_passages": {
            "methods": ["Reads were aligned with STAR and quantified with RSEM."],
            "statements": ["Three biopsies were excluded after quality control."],
            "claims": [{"claim_text": "1204 genes", "passage": "We found 1204 genes changed."}],
        },
        "sample_records": {
            "deposits": [
                {
                    "accession": "GSE9",
                    "samples": [
                        {
                            "accession": "GSM1",
                            "title": "WT rep1",
                            "organism": "Homo sapiens",
                            "source_name": "HepG2 cells",
                            "characteristics": {"condition": "WT"},
                            "unkeyed": [],
                        }
                    ],
                }
            ],
            "limitations": [],
        },
        "code_inspection": {"sources": [{"path": "a.R", "language": "r", "text": "library(DESeq2)\nprint(1)"}]},
    }
    _PLAN = {"method": {"assay": "bulk RNA-seq", "tools": ["STAR", "RSEM", "DESeq2"]}}

    def test_the_methods_sentences_are_passages_with_their_source(self):
        from app.services.validation_documentary_review import passages_for

        rows = passages_for(evidence=self._EVIDENCE, plan=self._PLAN)
        methods = [p for p in rows if p["source"] == "the paper's methods"]
        assert methods and "STAR" in methods[0]["text"]
        assert all(p["id"] for p in rows)

    def test_a_deposited_sample_record_is_its_own_citable_passage(self):
        from app.services.validation_documentary_review import passages_for

        rows = passages_for(evidence=self._EVIDENCE, plan=self._PLAN)
        records = [p for p in rows if "GSM1" in p["id"]]
        assert records and "HepG2" in records[0]["text"]

    def test_the_tools_the_analysis_declares_travel_with_it(self):
        from app.services.validation_documentary_review import passages_for

        rows = passages_for(evidence=self._EVIDENCE, plan=self._PLAN)
        tools = [p for p in rows if p["source"] == "the tools this analysis declares"]
        assert tools and "DESeq2" in tools[0]["text"]

    def test_every_passage_id_is_unique_so_a_citation_resolves_to_one_thing(self):
        from app.services.validation_documentary_review import passages_for

        rows = passages_for(evidence=self._EVIDENCE, plan=self._PLAN)
        assert len({p["id"] for p in rows}) == len(rows)

    def test_a_study_holding_nothing_yields_no_passages(self):
        from app.services.validation_documentary_review import passages_for

        assert passages_for(evidence={}, plan={}) == []
