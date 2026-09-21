"""plan_8_6 section 11: reassess only the affected obligations, on one shared attempt allowance.

The owner's review of the deployed code, 2026-09-21:

    "Caching and retries do not meet the efficiency requirements. One fingerprint covers every
    packet. Any change invokes the full documentary review again. The comment says 'keyed per
    obligation,' but the actual cache comparison is all-or-nothing. Separately, each initial or
    expanded request calls decide_with_recovery(), which has its own retry. That permits up to four
    semantic attempts, rather than two. Expansion also reuses the original coverage record."

Three things, each measured here rather than described:

- one changed source reruns the obligations whose packet changed, and no others;
- one obligation costs at most two semantic attempts per evidence revision, whether they are spent
  on a malformed answer or on a targeted evidence expansion. The expansion SHARES the recovery
  allowance, per section 11; it does not nest a second retry loop inside it;
- the widened request is judged on the coverage of the evidence it was actually shown, not on the
  record of the first packet, which is what a negative finding rests on (section 8).
"""

import json

import pytest

from app.services.validation_assessment import refresh_documentary_review
from app.services.validation_evidence_index import build_index
from app.services.validation_study_service import ValidationStudyService


def _index(extra=None):
    paragraphs = [
        "Reads were trimmed with Trim Galore and aligned with STAR to GRCh38.",
        "Cells with fewer than 1000 genes were removed and doublets were filtered with Scrublet.",
        "Differential expression was tested with DESeq2 and genes with p adj < 0.05 were reported.",
    ]
    return build_index(
        "",
        sections={
            "index": [
                {"title": "Methods", "kind": "methods", "paragraphs": paragraphs + list(extra or [])},
                {
                    "title": "Figure 1",
                    "kind": "legend",
                    "paragraphs": ["Expression after knockdown. Three biological replicates per group."],
                },
            ]
        },
        source="pasted",
    )


class _Client:
    """A model client at its own boundary, counting what each obligation cost."""

    def __init__(self, outcome="met", malformed=()):
        self.calls: list[str] = []
        self.outcome = outcome
        self.malformed = set(malformed)
        self.seen: dict[str, int] = {}

    async def submit(self, *, prompt, payload, model=None, api_key=None, max_tokens=None, **kw):
        leaf = next((line for line in payload.splitlines() if line.startswith("Obligation (")), "")
        key = leaf.partition("(")[2].partition(")")[0].replace(" ", ".")
        self.calls.append(key)
        self.seen[key] = self.seen.get(key, 0) + 1
        if key in self.malformed:
            return "not json at all"
        after = payload.partition("Evidence:\n")[2]
        return json.dumps(
            {
                "outcome": self.outcome,
                "rationale": "the methods state it",
                "citations": [after.partition("[")[2].partition("]")[0] or "p1"],
                "scope": "the preprocessing of the sequencing data",
                "basis": "absent",
                "confidence": 0.7,
            }
        )


async def _study(session, admin_user, evidence):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_doi="10.1/x", intended_route="deposit"
    )
    study.evidence_json = evidence
    await session.flush()
    return study


class TestOneChangedSourceRerunsOnlyWhatDependsOnIt:
    @pytest.mark.asyncio
    async def test_an_unchanged_study_costs_no_call_at_all(self, session, admin_user):
        study = await _study(session, admin_user, {"paper_index": _index()})
        first = _Client()
        await refresh_documentary_review(session, study, client=first, model="m", api_key="k")
        assert first.calls
        again = _Client()
        await refresh_documentary_review(session, study, client=again, model="m", api_key="k")
        assert again.calls == [], "replaying unchanged evidence made a model call"

    @pytest.mark.asyncio
    async def test_a_changed_source_reruns_the_obligations_it_changed_and_no_others(self, session, admin_user):
        study = await _study(session, admin_user, {"paper_index": _index()})
        first = _Client()
        await refresh_documentary_review(session, study, client=first, model="m", api_key="k")
        settled = dict(study.evidence_json["rubric_judgments"]["judgments"])

        evidence = dict(study.evidence_json)
        evidence["sample_records"] = {
            "deposits": [
                {
                    "accession": "GSE1",
                    "samples": [{"accession": "GSM1", "title": "treated", "organism": "Homo sapiens"}],
                }
            ]
        }
        study.evidence_json = evidence
        await session.flush()

        again = _Client()
        await refresh_documentary_review(session, study, client=again, model="m", api_key="k")
        asked = set(again.calls)
        assert asked, "the obligations that depend on the deposit are asked again"
        assert asked <= {"S2.A", "S2.B", "S5.A"}, f"unrelated obligations were re-asked: {asked}"
        for leaf, judgment in settled.items():
            if leaf not in asked:
                assert study.evidence_json["rubric_judgments"]["judgments"][leaf] == judgment

    @pytest.mark.asyncio
    async def test_the_cache_records_what_each_obligation_was_judged_on(self, session, admin_user):
        study = await _study(session, admin_user, {"paper_index": _index()})
        await refresh_documentary_review(session, study, client=_Client(), model="m", api_key="k")
        inputs = study.evidence_json["rubric_judgments"]["inputs"]
        assert set(inputs["fingerprints"]) == set(inputs["packets"])
        assert len(set(inputs["fingerprints"].values())) > 1, "one hash for every packet is not per-obligation"


class TestOneObligationCostsAtMostTwoAttempts:
    @pytest.mark.asyncio
    async def test_an_expansion_and_a_malformed_answer_share_one_allowance(self, session, admin_user):
        """The first answer is unparseable, so the recovery is spent correcting it; the obligation is
        not then asked a third time with widened evidence."""
        study = await _study(session, admin_user, {"paper_index": _index()})
        client = _Client(outcome="cannot_establish", malformed={"M1.B"})
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        assert client.seen.get("M1.B", 0) <= 2, client.seen

    @pytest.mark.asyncio
    async def test_an_unsettled_obligation_that_spent_nothing_still_gets_its_expansion(self, session, admin_user):
        study = await _study(session, admin_user, {"paper_index": _index(["Cells were counted." for _ in range(80)])})
        client = _Client(outcome="cannot_establish")
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        assert client.seen.get("M1.B", 0) == 2

    @pytest.mark.asyncio
    async def test_no_obligation_is_asked_more_than_twice(self, session, admin_user):
        study = await _study(session, admin_user, {"paper_index": _index()})
        client = _Client(outcome="cannot_establish")
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        assert max(client.seen.values()) <= 2, client.seen


class TestTheWidenedRequestIsJudgedOnItsOwnCoverage:
    def test_the_packet_carries_the_coverage_of_its_expansion(self):
        from app.services.validation_evidence_packets import packet_for

        index = _index([f"Reads were filtered at a quality threshold of {n}." for n in range(80)])
        packet = packet_for("M1.B", index=index, extras=[], limitations=[], budget_chars=400)
        assert packet["coverage"]["truncated"], "the budget left relevant passages out"
        assert packet["expansion"]
        assert packet["expanded_coverage"]["passages_supplied"] > packet["coverage"]["passages_supplied"]
        assert set(packet["expanded_coverage"]["deferred"]) < set(packet["coverage"]["deferred"])

    def test_the_expanded_coverage_is_never_worse_than_the_first(self):
        from app.services.validation_evidence_packets import packet_for

        packet = packet_for("M1.B", index=_index(), extras=[], limitations=[])
        assert not packet["coverage"]["deferred_relevant"], "nothing relevant was left out of this packet"
        assert packet["expanded_coverage"]["sufficient"] >= packet["coverage"]["sufficient"]
        assert packet["expanded_coverage"]["passages_supplied"] >= packet["coverage"]["passages_supplied"]
