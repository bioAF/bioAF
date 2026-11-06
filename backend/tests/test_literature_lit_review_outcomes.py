"""AI Literature Review run outcomes: recommendations, source classification,
scoring validation, cancellation, and terminal persistence/events.

Every test drives the real pipeline (``_execute_run``) against the test
database. Source adapters and the LLM client are replaced with deterministic
fakes so each source/query attempt and each LLM response is chosen by the test.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select, text

import app.database as _database
from app.models.audit_log import AuditLog
from app.models.literature import (
    PROVENANCE_LIT_REVIEW_RUN,
    PROVENANCE_SOURCE_SEARCH,
    REC_ACCEPTED,
    REC_PENDING,
    SCOPE_EXPERIMENT,
    TRIGGER_SCHEDULED,
    LiteratureAssociation,
    LiteraturePaper,
    LiteraturePaperDismissal,
    LiteratureRecommendation,
    LiteratureReviewRun,
    LiteratureSourcesConfig,
)
from app.services.event_bus import event_bus
from app.services.event_types import (
    LITERATURE_AUTO_REVIEW_RECOMMENDATIONS,
    LITERATURE_REVIEW_RUN_COMPLETED,
    LITERATURE_REVIEW_RUN_FAILED,
)
from app.services.literature import lit_review_run_service
from app.services.literature.sources import ADAPTERS, PaperRecord

SOURCES = sorted(ADAPTERS)
TERMINAL_EVENTS = (LITERATURE_REVIEW_RUN_COMPLETED, LITERATURE_REVIEW_RUN_FAILED)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def paper(n: int, *, doi: str | None = "auto", title: str | None = None) -> PaperRecord:
    return PaperRecord(
        source="pubmed",
        title=title or f"Candidate paper {n}",
        authors=[{"family": f"Author{n}", "given": "A"}],
        doi=f"10.9999/cand-{n}" if doi == "auto" else doi,
        journal="Cell",
        publication_date=date(2024, 1, 1),
        abstract=f"Abstract {n}",
    )


def scores(*entries: tuple[int, float]) -> str:
    return json.dumps([{"index": i, "score": s, "reasoning": f"reason {i}"} for i, s in entries])


class FakeLlm:
    """Returns (or raises) the scripted responses in order and records each payload."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.payloads: list[str] = []

    async def submit(self, prompt, payload, model, api_key, attachments=None):
        self.payloads.append(payload)
        if not self.responses:
            return ""
        nxt = self.responses.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


class Harness:
    def __init__(self, session, admin_user, monkeypatch):
        self.session = session
        self.user = admin_user
        self.org_id = admin_user.organization_id
        self.monkeypatch = monkeypatch
        self.events: list[tuple[str, dict]] = []
        self.calls: list[tuple[str, str]] = []
        self.behaviors: dict[str, Callable] = {}
        self.llm = FakeLlm([])
        self.experiment_id: int | None = None

        async def fake_emit(event_type, payload):
            self.events.append((event_type, payload))

        monkeypatch.setattr(event_bus, "emit", fake_emit)
        monkeypatch.setattr(lit_review_run_service, "get_client", lambda provider: self.llm)
        for name, module in ADAPTERS.items():
            monkeypatch.setattr(module, "search", self._search_for(name))

    def _search_for(self, name: str):
        async def search(query, max_results, api_key):
            self.calls.append((name, query))
            behavior = self.behaviors.get(name)
            if behavior is None:
                return []
            result = behavior(query)
            if isinstance(result, BaseException):
                raise result
            if hasattr(result, "__await__"):
                result = await result
            return list(result)

        return search

    async def setup(self, *, sources: tuple[str, ...] | None = None):
        from app.services import llm_provider_config_service
        from app.services.bootstrap_literature import seed_literature_sources

        await seed_literature_sources(self.session, self.org_id)
        if sources is not None:
            rows = (
                (
                    await self.session.execute(
                        select(LiteratureSourcesConfig).where(LiteratureSourcesConfig.organization_id == self.org_id)
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                row.enabled = row.source in sources
        await llm_provider_config_service.upsert(
            self.session,
            org_id=self.org_id,
            provider="anthropic",
            api_key="sk-test-fake-LAST5",
            model="claude-test",
            actor_user_id=self.user.id,
        )
        await llm_provider_config_service.set_active(
            self.session, org_id=self.org_id, provider="anthropic", actor_user_id=self.user.id
        )
        result = await self.session.execute(
            text(
                """
                INSERT INTO experiments (name, status, organization_id, owner_user_id, project_id)
                VALUES ('Outcome Experiment', 'registered', :org, :uid, NULL)
                RETURNING id
                """
            ).bindparams(org=self.org_id, uid=self.user.id)
        )
        self.experiment_id = result.scalar_one()
        await self.session.commit()

    def source(self, name: str, behavior: Callable) -> None:
        self.behaviors[name] = behavior

    def script_llm(self, *responses) -> None:
        self.llm.responses = list(responses)

    async def create_run(self, **kwargs) -> int:
        kwargs.setdefault("score_threshold", 0.4)
        run = await lit_review_run_service.create_run(
            self.session,
            org_id=self.org_id,
            experiment_id=self.experiment_id,
            triggered_by_user_id=self.user.id,
            **kwargs,
        )
        await self.session.commit()
        return run.id

    async def execute(self, run_id: int) -> None:
        await lit_review_run_service._execute_run(run_id)

    async def run(self, **kwargs) -> LiteratureReviewRun:
        run_id = await self.create_run(**kwargs)
        await self.execute(run_id)
        return await self.load(run_id)

    async def load(self, run_id: int) -> LiteratureReviewRun:
        async with _database.async_session_factory() as s:
            return (await s.execute(select(LiteratureReviewRun).where(LiteratureReviewRun.id == run_id))).scalar_one()

    async def recommendations(self, run_id: int | None = None) -> list[LiteratureRecommendation]:
        async with _database.async_session_factory() as s:
            q = select(LiteratureRecommendation).where(LiteratureRecommendation.experiment_id == self.experiment_id)
            if run_id is not None:
                q = q.where(LiteratureRecommendation.review_run_id == run_id)
            return list((await s.execute(q.order_by(LiteratureRecommendation.id))).scalars().all())

    async def papers(self) -> list[LiteraturePaper]:
        async with _database.async_session_factory() as s:
            q = select(LiteraturePaper).where(LiteraturePaper.organization_id == self.org_id)
            return list((await s.execute(q.order_by(LiteraturePaper.id))).scalars().all())

    async def associations(self) -> list[tuple[int, str, int]]:
        async with _database.async_session_factory() as s:
            q = select(LiteratureAssociation).where(LiteratureAssociation.removed_at.is_(None))
            rows = (await s.execute(q)).scalars().all()
            return sorted((a.paper_id, a.scope_type, a.scope_id) for a in rows)

    async def audits(self, run_id: int) -> list[AuditLog]:
        async with _database.async_session_factory() as s:
            q = select(AuditLog).where(AuditLog.entity_type == "literature_review_run", AuditLog.entity_id == run_id)
            return list((await s.execute(q.order_by(AuditLog.id))).scalars().all())

    async def add_paper(self, rec: PaperRecord, *, in_library: bool, provenance: str = PROVENANCE_SOURCE_SEARCH) -> int:
        from app.services.literature import paper_service

        p = await paper_service.create_paper(
            self.session,
            org_id=self.org_id,
            user_id=self.user.id,
            title=rec.title,
            authors=rec.authors,
            doi=rec.doi,
            provenance=provenance,
            source="pubmed",
            in_library=in_library,
        )
        await self.session.commit()
        return p.id

    async def dismiss(self, paper_id: int) -> None:
        self.session.add(
            LiteraturePaperDismissal(paper_id=paper_id, organization_id=self.org_id, dismissed_by_user_id=self.user.id)
        )
        await self.session.commit()

    def terminal_events(self) -> list[tuple[str, dict]]:
        return [(et, p) for et, p in self.events if et in TERMINAL_EVENTS]


@pytest_asyncio.fixture
async def h(session, admin_user, monkeypatch):
    harness = Harness(session, admin_user, monkeypatch)
    await harness.setup()
    return harness


def always(records: list[PaperRecord]):
    return lambda query: records


# ---------------------------------------------------------------------------
# Characterization: successful recommendation behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_run_auto_accepts_above_threshold_into_library(h):
    a, b, c = paper(1), paper(2), paper(3)
    # pubmed returns all three for both queries; europepmc repeats `a` with a
    # differently-cased DOI. Candidates are deduplicated by lowercased DOI.
    dup_a = PaperRecord(source="europepmc", title="Other title", doi=a.doi.upper())
    h.source("pubmed", always([a, b, c]))
    h.source("europepmc", always([dup_a]))
    h.script_llm("query one\nquery two", scores((0, 0.9), (1, 0.4), (2, 0.39)))

    run = await h.run(score_threshold=0.4, max_recommendations=10)

    assert run.status == "complete"
    assert run.error_message is None
    assert run.completed_at is not None
    assert run.started_at is not None
    assert run.expansion_queries_json == ["query one", "query two"]
    assert run.candidate_count == 3
    assert run.recommendation_count == 2

    recs = await h.recommendations(run.id)
    papers = {p.id: p for p in await h.papers()}
    assert [papers[r.paper_id].doi for r in recs] == [a.doi, b.doi]
    assert [r.relevance_score for r in recs] == [0.9, 0.4]
    assert [r.relevance_bucket for r in recs] == ["high", "medium"]
    assert [r.reasoning for r in recs] == ["reason 0", "reason 1"]
    assert {r.status for r in recs} == {REC_ACCEPTED}
    assert {r.decided_by_user_id for r in recs} == {h.user.id}
    assert all(r.decided_at is not None for r in recs)

    # Only kept papers are created, all in the Library with lit-review provenance,
    # each associated with the run's experiment.
    assert sorted(p.doi for p in papers.values()) == sorted([a.doi, b.doi])
    assert {p.in_library for p in papers.values()} == {True}
    assert {p.provenance for p in papers.values()} == {PROVENANCE_LIT_REVIEW_RUN}
    assert await h.associations() == sorted((pid, SCOPE_EXPERIMENT, h.experiment_id) for pid in papers)

    assert h.terminal_events() == [(LITERATURE_REVIEW_RUN_COMPLETED, {"run_id": run.id, "recommendation_count": 2})]
    complete = [a for a in await h.audits(run.id) if a.action == "complete"]
    assert len(complete) == 1
    assert complete[0].details_json["status"] == "complete"
    assert complete[0].details_json["candidate_count"] == 3
    assert complete[0].details_json["recommendation_count"] == 2


@pytest.mark.asyncio
async def test_max_recommendations_keeps_highest_scores(h):
    h.source("pubmed", always([paper(1), paper(2), paper(3)]))
    h.script_llm("q1", scores((0, 0.5), (1, 0.95), (2, 0.7)))

    run = await h.run(score_threshold=0.1, max_recommendations=2)

    assert run.recommendation_count == 2
    recs = await h.recommendations(run.id)
    assert sorted(r.relevance_score for r in recs) == [0.7, 0.95]


@pytest.mark.asyncio
async def test_candidates_capped_at_fifty_before_scoring(h):
    h.source("pubmed", always([paper(i) for i in range(60)]))
    h.script_llm("q1", scores((0, 0.9), (49, 0.8)))

    run = await h.run()

    assert run.candidate_count == 50
    assert run.recommendation_count == 2
    scoring_payload = h.llm.payloads[1]
    assert "[49] Candidate paper 49" in scoring_payload
    assert "[50]" not in scoring_payload


@pytest.mark.asyncio
async def test_library_and_dismissed_papers_are_excluded_but_cached_results_stay_eligible(h):
    in_lib, dismissed, cached, fresh = paper(1), paper(2), paper(3), paper(4)
    await h.add_paper(in_lib, in_library=True)
    await h.dismiss(await h.add_paper(dismissed, in_library=False))
    cached_id = await h.add_paper(cached, in_library=False)
    h.source("pubmed", always([in_lib, dismissed, cached, fresh]))
    # After exclusion the scoring prompt sees [0]=cached, [1]=fresh.
    h.script_llm("q1", scores((0, 0.8), (1, 0.7)))

    run = await h.run()

    assert run.status == "complete"
    assert run.candidate_count == 2
    assert run.recommendation_count == 2
    assert "Candidate paper 1" not in h.llm.payloads[1]
    assert "Candidate paper 2" not in h.llm.payloads[1]

    papers = await h.papers()
    # The cached paper is reused (no duplicate row) and joins the Library.
    assert sorted(p.doi for p in papers) == sorted([in_lib.doi, dismissed.doi, cached.doi, fresh.doi])
    by_doi = {p.doi: p for p in papers}
    assert by_doi[cached.doi].id == cached_id
    assert by_doi[cached.doi].in_library is True
    assert by_doi[dismissed.doi].in_library is False
    rec_papers = {r.paper_id for r in await h.recommendations(run.id)}
    assert rec_papers == {by_doi[cached.doi].id, by_doi[fresh.doi].id}


@pytest.mark.asyncio
async def test_existing_recommendation_and_user_decision_are_preserved(h):
    earlier = paper(1)
    pid = await h.add_paper(earlier, in_library=False)
    earlier_run = await h.create_run()
    h.session.add(
        LiteratureRecommendation(
            organization_id=h.org_id,
            paper_id=pid,
            experiment_id=h.experiment_id,
            review_run_id=earlier_run,
            relevance_score=0.5,
            relevance_bucket="medium",
            reasoning="earlier",
            status=REC_PENDING,
        )
    )
    await h.session.commit()
    h.source("pubmed", always([earlier, paper(2)]))
    h.script_llm("q1", scores((0, 0.9), (1, 0.8)))

    run = await h.run()

    assert run.status == "complete"
    assert run.recommendation_count == 1
    recs = await h.recommendations()
    prior = [r for r in recs if r.paper_id == pid]
    assert len(prior) == 1
    assert prior[0].status == REC_PENDING
    assert prior[0].review_run_id == earlier_run
    assert prior[0].reasoning == "earlier"
    by_id = {p.id: p for p in await h.papers()}
    assert by_id[pid].in_library is False
    assert not [a for a in await h.associations() if a[0] == pid]


@pytest.mark.asyncio
async def test_score_equal_to_threshold_is_kept_and_omitted_candidates_dropped(h):
    h.source("pubmed", always([paper(1), paper(2), paper(3)]))
    h.script_llm("q1", scores((1, 0.4)))

    run = await h.run(score_threshold=0.4)

    assert run.status == "complete"
    assert run.recommendation_count == 1
    assert run.candidate_count == 3


@pytest.mark.asyncio
async def test_scoring_tolerates_fences_duplicates_out_of_range_and_clamps(h):
    h.source("pubmed", always([paper(1), paper(2), paper(3)]))
    body = json.dumps(
        [
            {"index": 0, "score": 1.7, "reasoning": "first"},
            {"index": 0, "score": 0.5, "reasoning": "duplicate index ignored"},
            {"index": 9, "score": 0.9, "reasoning": "out of range"},
            {"index": -1, "score": 0.9, "reasoning": "negative"},
            {"index": "1", "score": "0.6"},
            {"index": 2, "score": -3},
            {"score": 0.9},
            {"index": 2, "score": "high"},
        ]
    )
    h.script_llm("q1", f"Here you go:\n```json\n{body}\n```")

    run = await h.run(score_threshold=0.0)

    assert run.status == "complete"
    recs = await h.recommendations(run.id)
    assert sorted((r.relevance_score, r.reasoning) for r in recs) == [(0.0, ""), (0.6, ""), (1.0, "first")]


@pytest.mark.asyncio
async def test_scoring_accepts_array_wrapped_in_prose(h):
    h.source("pubmed", always([paper(1)]))
    h.script_llm("q1", "Sure. " + scores((0, 0.8)) + " Hope this helps.")

    run = await h.run()

    assert run.status == "complete"
    assert run.recommendation_count == 1


@pytest.mark.asyncio
async def test_candidates_without_doi_deduplicate_by_title(h):
    h.source("pubmed", always([paper(1, doi=None, title="Same Title"), paper(2, doi=None, title="same title")]))
    h.script_llm("q1", scores((0, 0.9)))

    run = await h.run()

    assert run.candidate_count == 1
    assert run.recommendation_count == 1


@pytest.mark.asyncio
async def test_scheduled_run_notifies_with_pick_count(h):
    h.source("pubmed", always([paper(1), paper(2)]))
    h.script_llm("q1", scores((0, 0.9), (1, 0.8)))

    run = await h.run(trigger=TRIGGER_SCHEDULED)

    notes = [p for et, p in h.events if et == LITERATURE_AUTO_REVIEW_RECOMMENDATIONS]
    assert len(notes) == 1
    assert notes[0]["entity_id"] == run.id
    assert notes[0]["metadata"] == {"experiment_id": h.experiment_id, "run_id": run.id, "recommendation_count": 2}
    assert notes[0]["title"] == "AI Lit Review added 2 papers to Outcome Experiment"


@pytest.mark.asyncio
async def test_manual_run_sends_no_auto_review_notification(h):
    h.source("pubmed", always([paper(1)]))
    h.script_llm("q1", scores((0, 0.9)))

    await h.run()

    assert [et for et, _ in h.events if et == LITERATURE_AUTO_REVIEW_RECOMMENDATIONS] == []
