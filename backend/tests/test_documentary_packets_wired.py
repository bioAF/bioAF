"""plan_8_6 section 3: the packets, asked through the production caller.

A selector nothing calls is not a repair. This is the wiring: the read builds the section-aware
index while the paper's text is in hand, the assessment builds one packet per obligation from it,
each request carries only that obligation's evidence, and the coverage the packet recorded travels
into the judgment so an absence finding rests on what was inspected.
"""

import json
import pathlib

import pytest

from app.services.validation_assessment import refresh_documentary_review
from app.services.validation_study_service import ValidationStudyService

_JATS = (pathlib.Path(__file__).parent / "fixtures" / "granulosa" / "fulltext_jats.xml").read_text()


def _index():
    from app.services.literature.fulltext_service import _jats_sections, _jats_to_text
    from app.services.validation_evidence_index import build_index

    return build_index(_jats_to_text(_JATS), sections=_jats_sections(_JATS), source="europe_pmc")


class _Client:
    """A model client at its own boundary: it records what each obligation was shown."""

    def __init__(self, outcome="met", rationale="the methods state it", answers=None, unmet=None):
        self.asked: list[dict] = []
        self.outcome = outcome
        self.rationale = rationale
        self.answers = answers or {}
        # One obligation answered unmet, citing its own packet. The others answer the default, so a
        # finding is not reconciled away as the same failure reported by seventeen obligations.
        self.unmet = unmet or {}

    async def submit(self, *, prompt, payload, model=None, api_key=None, max_tokens=None, **kw):
        leaf = next((line for line in payload.splitlines() if line.startswith("Obligation (")), "")
        self.asked.append({"leaf": leaf, "payload": payload})
        key = leaf.partition("(")[2].partition(")")[0].replace(" ", ".")
        answer = self.answers.get(key)
        if answer is None and key in self.unmet:
            answer = {
                "outcome": "unmet",
                "rationale": self.unmet[key],
                "citations": [self._first_id(payload)],
                "scope": "the single-cell preprocessing described in this paper",
                "impact": "a reader cannot repeat the filtering this analysis applied",
                "confidence": 0.7,
            }
        answer = answer or {
            "outcome": self.outcome,
            "rationale": self.rationale,
            "citations": [self._first_id(payload)],
            "confidence": 0.7,
        }
        return json.dumps(answer)

    @staticmethod
    def _first_id(payload: str) -> str:
        after = payload.partition("Evidence:\n")[2]
        return after.partition("[")[2].partition("]")[0] or "p1"

    def payload_for(self, leaf: str) -> str:
        key = f"Obligation ({leaf.replace('.', ' ')})"
        return next(a["payload"] for a in self.asked if a["leaf"].startswith(key))


async def _study(session, admin_user, evidence):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_doi="10.7554/elife.83291", intended_route="deposit"
    )
    study.evidence_json = evidence
    await session.flush()
    return study


class TestEachObligationIsAskedWithItsOwnEvidence:
    @pytest.mark.asyncio
    async def test_the_computational_row_is_not_shown_the_culture_protocol(self, session, admin_user):
        study = await _study(session, admin_user, {"paper_index": _index()})
        client = _Client()
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        payload = client.payload_for("M1.B")
        assert "Cell culture" not in payload
        assert "kallisto" in payload or "Scanpy" in payload

    @pytest.mark.asyncio
    async def test_the_experimental_row_is_shown_the_bench_procedure(self, session, admin_user):
        study = await _study(session, admin_user, {"paper_index": _index()})
        client = _Client()
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        assert "Cell culture" in client.payload_for("E1.B")

    @pytest.mark.asyncio
    async def test_two_obligations_are_not_shown_the_same_packet(self, session, admin_user):
        study = await _study(session, admin_user, {"paper_index": _index()})
        client = _Client()
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        assert client.payload_for("M1.B") != client.payload_for("E1.B")


class TestTheCoverageReachesTheJudgment:
    @pytest.mark.asyncio
    async def test_an_accepted_judgment_records_the_coverage_it_was_made_under(self, session, admin_user):
        study = await _study(session, admin_user, {"paper_index": _index()})
        await refresh_documentary_review(session, study, client=_Client(), model="m", api_key="k")
        judgment = study.evidence_json["rubric_judgments"]["judgments"]["M1.B"]
        assert judgment["coverage"]["packet_version"]
        assert judgment["coverage"]["supplied"]

    @pytest.mark.asyncio
    async def test_an_absence_over_a_source_bioaf_never_retrieved_stays_untested(self, session, admin_user):
        """Section 8: the supplementary bundle was `too_large` twice, so no omission is established."""
        evidence = {
            "paper_index": _index(),
            "pmcid": "PMC9943069",
            "retrieval_ledger": [{"id": "R1", "outcome": "too_large", "source": "europepmc_supplementary_bundle"}],
            "supplements": [{"identity": "elife-83291-supp4.zip", "kind": "attachment", "resolved": False}],
        }
        study = await _study(session, admin_user, evidence)
        client = _Client(outcome="unmet", rationale="the multiple-testing correction is not stated in the methods")
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        judgment = study.evidence_json["rubric_judgments"]["judgments"]["M3.A"]
        assert judgment["outcome"] == "undetermined"
        assert judgment["withheld"]["outcome"] == "unmet"

    @pytest.mark.asyncio
    async def test_the_same_absence_is_a_finding_once_the_sources_were_inspected(self, session, admin_user):
        study = await _study(session, admin_user, {"paper_index": _index()})
        client = _Client(outcome="unmet", rationale="the multiple-testing correction is not stated in the methods")
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        assert study.evidence_json["rubric_judgments"]["judgments"]["M3.A"]["outcome"] == "failed"


class TestNothingIsAskedWithoutEvidence:
    @pytest.mark.asyncio
    async def test_an_obligation_with_an_empty_packet_is_untested_and_costs_no_call(self, session, admin_user):
        from app.services.validation_evidence_index import build_index

        only_intro = build_index(
            "",
            sections={"index": [{"title": "Introduction", "kind": "introduction", "paragraphs": ["Cells matter."]}]},
            source="pasted",
        )
        study = await _study(session, admin_user, {"paper_index": only_intro})
        client = _Client()
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        judgments = study.evidence_json["rubric_judgments"]["judgments"]
        assert judgments["M1.B"]["outcome"] == "undetermined"
        assert judgments["M1.B"]["next_action"]
        assert not any("M1 B" in a["leaf"] for a in client.asked), "no model call with nothing to judge"


class TestTheOneTargetedExpansion:
    @pytest.mark.asyncio
    async def test_an_unsettled_obligation_is_asked_once_more_with_more_evidence(self, session, admin_user):
        study = await _study(session, admin_user, {"paper_index": _index()})
        client = _Client(outcome="cannot_establish", rationale="the evidence supplied does not settle it")
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        asked = [a for a in client.asked if a["leaf"].startswith("Obligation (M1 B)")]
        assert len(asked) >= 2, "one expansion, within the existing recovery allowance"
        assert len(asked[-1]["payload"]) > len(asked[0]["payload"])

    @pytest.mark.asyncio
    async def test_a_settled_obligation_is_not_asked_twice(self, session, admin_user):
        study = await _study(session, admin_user, {"paper_index": _index()})
        client = _Client()
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        asked = [a for a in client.asked if a["leaf"].startswith("Obligation (M1 B)")]
        assert len(asked) == 1


class TestReuse:
    @pytest.mark.asyncio
    async def test_unchanged_evidence_costs_no_second_call(self, session, admin_user):
        study = await _study(session, admin_user, {"paper_index": _index()})
        client = _Client()
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        first = len(client.asked)
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        assert len(client.asked) == first

    @pytest.mark.asyncio
    async def test_a_study_recorded_before_the_index_still_has_its_passages_judged(self, session, admin_user):
        """A study whose held evidence predates this record is not silently left unassessed."""
        evidence = {
            "paper_passages": {
                "methods": ["Reads were aligned with STAR 2.7.9a and quantified with RSEM 1.3.3."],
                "statements": [],
                "claims": [],
            }
        }
        study = await _study(session, admin_user, evidence)
        client = _Client()
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        assert study.evidence_json["rubric_judgments"]["judgments"]
        assert "RSEM" in client.payload_for("M1.A")


class TestStudy65sOwnCodeReachesTheObligationsThroughThisCaller:
    """Section 10: a defect is accepted on the evidence it was found in, through the production caller.

    The fixtures are study 65's own: its Europe PMC document and its repository at the revision the
    paper archived. What the carrying step used to cut is exactly what the owner's review turned on.
    """

    def _evidence(self):
        from app.services.validation_code_inspection import inspect_archive

        archive = (
            pathlib.Path(__file__).parent / "fixtures" / "granulosa" / "repo_at_cited_revision.tar.gz"
        ).read_bytes()
        found = inspect_archive(archive, origin="https://github.com/programmablebio/granulosa")
        return {
            "paper_index": _index(),
            "code_inspection": {"sources": found["sources"], "manifests": found["manifests"]},
        }

    @pytest.mark.asyncio
    async def test_the_statistical_obligation_is_shown_that_the_dazl_test_is_commented_out(self, session, admin_user):
        """M3.B was deducted for a cell-level test as though the supplied notebook ran it.

        The executed `rank_genes_groups` call is on the Leiden clusters; the DAZL one is commented
        out, 188 lines into a 199-line notebook, and the eight-excerpt rule stopped before it.
        """
        study = await _study(session, admin_user, self._evidence())
        client = _Client()
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        payload = client.payload_for("M3.B")
        assert "sc.tl.rank_genes_groups(adata, 'leiden'" in payload
        assert "#sc.tl.rank_genes_groups(adata, 'DAZL_plus'" in payload

    @pytest.mark.asyncio
    async def test_the_environment_obligation_is_shown_the_versions_past_the_letter_b(self, session, admin_user):
        study = await _study(session, admin_user, self._evidence())
        client = _Client()
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        payload = client.payload_for("C5.B")
        assert "pandas=0.23.4" in payload
        assert "numpy=1.15.4" in payload

    @pytest.mark.asyncio
    async def test_the_code_a_whole_repository_adds_does_not_stop_an_absence_finding(self, session, admin_user):
        """Carrying whole files must not make every obligation permanently untestable (section 8)."""
        study = await _study(session, admin_user, self._evidence())
        client = _Client(unmet={"M1.B": "the doublet-filtering threshold is not stated in the methods"})
        await refresh_documentary_review(session, study, client=client, model="m", api_key="k")
        assert study.evidence_json["rubric_judgments"]["judgments"]["M1.B"]["outcome"] == "failed"
