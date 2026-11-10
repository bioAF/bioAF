"""AI Literature Review run outcomes: recommendations, source classification,
scoring validation, cancellation, and terminal persistence/events.

Every test drives the real pipeline (``_execute_run``) against the test
database. Source adapters and the LLM client are replaced with deterministic
fakes so each source/query attempt and each LLM response is chosen by the test.
"""

from __future__ import annotations

import asyncio
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
from app.services.llm_provider_clients import ProviderError

SOURCES = sorted(ADAPTERS)
REAL_SEARCH = {name: module.search for name, module in ADAPTERS.items()}
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
        if callable(nxt):
            return await nxt()
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

    async def associations(self) -> list[tuple[int, str, int | None]]:
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
    assert sorted(str(p.doi) for p in papers.values()) == sorted([str(a.doi), str(b.doi)])
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
    assert sorted(str(p.doi) for p in papers) == sorted(str(r.doi) for r in (in_lib, dismissed, cached, fresh))
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


# ---------------------------------------------------------------------------
# Source outcome classification
# ---------------------------------------------------------------------------


def fail(exc: BaseException = None):
    return lambda query: exc or RuntimeError("upstream exploded token=SECRET-TOKEN")


def all_sources(h, behavior) -> None:
    for name in SOURCES:
        h.source(name, behavior)


@pytest.mark.asyncio
async def test_every_attempt_failing_fails_the_run_without_scoring(h):
    all_sources(h, fail())
    h.script_llm("q1\nq2")

    run = await h.run()

    assert run.status == "failed"
    assert run.completed_at is not None
    assert run.candidate_count == 0
    assert run.recommendation_count == 0
    expected = "; ".join(f"{name} 2/2 attempts failed" for name in SOURCES)
    assert run.error_message == f"all_literature_sources_failed: {expected}"
    assert len(h.llm.payloads) == 1  # query generation only, no scoring call
    assert h.terminal_events() == [(LITERATURE_REVIEW_RUN_FAILED, {"run_id": run.id})]


@pytest.mark.asyncio
async def test_partial_outage_without_candidates_is_partial_not_complete(h):
    h.source("pubmed", fail())
    h.script_llm("q1\nq2")

    run = await h.run()

    assert run.status == "partial"
    assert run.completed_at is not None
    assert run.candidate_count == 0
    assert run.recommendation_count == 0
    assert run.error_message == "some_literature_sources_failed: pubmed 2/2 attempts failed"
    assert h.terminal_events() == [(LITERATURE_REVIEW_RUN_COMPLETED, {"run_id": run.id, "recommendation_count": 0})]


@pytest.mark.asyncio
async def test_partial_outage_with_candidates_explains_failed_sources(h):
    h.source("europepmc", fail())
    h.source("pubmed", always([paper(1)]))
    h.script_llm("q1", scores((0, 0.9)))

    run = await h.run()

    assert run.status == "partial"
    assert run.recommendation_count == 1
    assert run.error_message == "some_literature_sources_failed: europepmc 1/1 attempts failed"
    assert h.terminal_events() == [(LITERATURE_REVIEW_RUN_COMPLETED, {"run_id": run.id, "recommendation_count": 1})]


@pytest.mark.asyncio
async def test_source_failing_one_query_and_succeeding_another_makes_run_partial(h):
    h.source("pubmed", lambda q: RuntimeError("down") if q == "q1" else [paper(1)])
    h.script_llm("q1\nq2", scores((0, 0.9)))

    run = await h.run()

    assert run.status == "partial"
    assert run.recommendation_count == 1
    assert run.error_message == "some_literature_sources_failed: pubmed 1/2 attempts failed"


@pytest.mark.asyncio
async def test_all_attempts_succeeding_with_zero_results_is_complete(h):
    h.script_llm("q1\nq2")

    run = await h.run()

    assert run.status == "complete"
    assert run.error_message is None
    assert run.candidate_count == 0
    assert run.recommendation_count == 0
    assert len(h.calls) == 2 * len(SOURCES)
    assert h.terminal_events() == [(LITERATURE_REVIEW_RUN_COMPLETED, {"run_id": run.id, "recommendation_count": 0})]


@pytest.mark.asyncio
async def test_all_candidates_excluded_with_complete_sources_is_complete(h):
    known = paper(1)
    await h.add_paper(known, in_library=True)
    h.source("pubmed", always([known]))
    h.script_llm("q1")

    run = await h.run()

    assert run.status == "complete"
    assert run.error_message is None
    assert run.candidate_count == 0
    assert len(h.llm.payloads) == 1


@pytest.mark.asyncio
async def test_all_candidates_excluded_with_partial_sources_is_partial(h):
    known = paper(1)
    await h.add_paper(known, in_library=True)
    h.source("pubmed", always([known]))
    h.source("biorxiv", fail())
    h.script_llm("q1")

    run = await h.run()

    assert run.status == "partial"
    assert run.error_message == "some_literature_sources_failed: biorxiv 1/1 attempts failed"


@pytest.mark.asyncio
async def test_no_scores_above_threshold_is_complete(h):
    h.source("pubmed", always([paper(1)]))
    h.script_llm("q1", scores((0, 0.1)))

    run = await h.run(score_threshold=0.5)

    assert run.status == "complete"
    assert run.error_message is None
    assert run.recommendation_count == 0


@pytest.mark.asyncio
async def test_source_diagnostics_in_audit_are_safe(h):
    async with _database.async_session_factory() as s:
        row = (
            await s.execute(
                select(LiteratureSourcesConfig).where(
                    LiteratureSourcesConfig.organization_id == h.org_id, LiteratureSourcesConfig.source == "pubmed"
                )
            )
        ).scalar_one()
        row.api_key = "SECRET-API-KEY"
        await s.commit()
    h.source("pubmed", lambda q: TimeoutError() if q == "q1" else RuntimeError("payload token=SECRET-TOKEN"))
    h.script_llm("q1\nq2")

    run = await h.run()

    audit = [a for a in await h.audits(run.id) if a.action == "complete"]
    assert len(audit) == 1
    details = audit[0].details_json
    assert details["status"] == "partial"
    assert details["sources"]["pubmed"] == {
        "attempts": 2,
        "failed": 2,
        "failure_categories": ["RuntimeError", "TimeoutError"],
    }
    assert details["sources"]["europepmc"] == {"attempts": 2, "failed": 0, "failure_categories": []}
    flat = json.dumps(details) + (run.error_message or "")
    assert "SECRET" not in flat


# ---------------------------------------------------------------------------
# No enabled sources: creation, execution race, scheduled sweep
# ---------------------------------------------------------------------------


async def _set_sources_enabled(h, enabled: bool) -> None:
    async with _database.async_session_factory() as s:
        rows = (
            (
                await s.execute(
                    select(LiteratureSourcesConfig).where(LiteratureSourcesConfig.organization_id == h.org_id)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.enabled = enabled
        await s.commit()


async def _run_count(h) -> int:
    async with _database.async_session_factory() as s:
        rows = await s.execute(select(LiteratureReviewRun).where(LiteratureReviewRun.organization_id == h.org_id))
        return len(rows.scalars().all())


@pytest.mark.asyncio
async def test_manual_creation_with_every_optional_source_off_still_runs_on_openalex(
    h, client, admin_token, monkeypatch
):
    # OpenAlex is always on (owner, 2026-11-10): turning every other source off never leaves an
    # organization without a source. Changed with owner approval (plan 15).
    await _set_sources_enabled(h, False)
    scheduled: list[int] = []

    async def record_schedule(*, run_id):
        scheduled.append(run_id)

    monkeypatch.setattr(lit_review_run_service, "schedule_run", record_schedule)

    resp = await client.post(
        f"/api/literature/experiments/{h.experiment_id}/lit-review-runs",
        json={},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert resp.status_code in (200, 201), resp.text
    assert await _run_count(h) == 1
    assert len(scheduled) == 1


@pytest.mark.asyncio
async def test_create_run_service_with_every_optional_source_off_searches_openalex_only(h):
    # OpenAlex is always on (owner, 2026-11-10): turning every other source off never leaves an
    # organization without a source. Changed with owner approval (plan 15).
    await _set_sources_enabled(h, False)
    h.script_llm("q1", scores((0, 0.9)))

    run = await h.run()

    assert run.status == "complete"
    assert h.calls == [("openalex", "q1")]


@pytest.mark.asyncio
async def test_sources_disabled_after_creation_leave_the_run_on_openalex(h):
    # OpenAlex is always on (owner, 2026-11-10): turning every other source off never leaves an
    # organization without a source. Changed with owner approval (plan 15).
    run_id = await h.create_run()
    await _set_sources_enabled(h, False)
    h.script_llm("q1", scores((0, 0.9)))

    await h.execute(run_id)
    run = await h.load(run_id)

    assert run.status == "complete"
    assert run.completed_at is not None
    assert h.calls == [("openalex", "q1")]
    assert [event for event, _ in h.terminal_events()] == [LITERATURE_REVIEW_RUN_COMPLETED]


async def _make_due(h) -> None:
    from datetime import UTC, datetime, timedelta

    from app.models.organization import Organization
    from app.models.sample import Sample

    org = (await h.session.execute(select(Organization).where(Organization.id == h.org_id))).scalar_one()
    org.lit_review_auto_enabled = True
    org.lit_review_auto_cadence = "daily"
    h.session.add(
        Sample(experiment_id=h.experiment_id, status="registered", created_at=datetime.now(UTC) - timedelta(days=1))
    )
    await h.session.commit()


@pytest.mark.asyncio
async def test_scheduled_sweep_runs_on_openalex_when_every_optional_source_is_off(h, monkeypatch):
    # OpenAlex is always on (owner, 2026-11-10): turning every other source off never leaves an
    # organization without a source. Changed with owner approval (plan 15).
    from app.services.literature import lit_review_auto_service

    await _make_due(h)
    await _set_sources_enabled(h, False)
    scheduled: list[int] = []

    async def record_schedule(*, run_id):
        scheduled.append(run_id)

    monkeypatch.setattr(lit_review_run_service, "schedule_run", record_schedule)

    result = await lit_review_auto_service.run_due_sweep(h.session, h.org_id)

    assert result["skipped_reason"] is None
    assert result["ran"]


@pytest.mark.asyncio
async def test_auto_loop_advances_schedule_one_cadence_after_a_skipped_sweep(h, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.services.literature import lit_review_auto_service as auto
    from app.tasks.jobs import PERIODIC_JOBS
    from app.tasks.periodic import run_periodic

    await _make_due(h)
    # The sweep skips because no LLM provider is active (changed with owner approval, plan 15:
    # OpenAlex is always on, so turning sources off no longer produces a skipped sweep).
    from app.services import llm_provider_config_service

    await llm_provider_config_service.deactivate_all(h.session, h.org_id, h.user.id)
    await h.session.commit()

    # The loop is hard-wired to org 1; route its calls to this test's org.
    real_sweep, real_advance = auto.run_due_sweep, auto.advance_next_run
    sweeps: list[dict] = []

    async def due(session, org_id, **kw):
        return True

    async def seeded(session, org_id, **kw):
        return None

    async def sweep(session, org_id, **kw):
        sweeps.append(await real_sweep(session, h.org_id, **kw))
        return sweeps[-1]

    async def advance(session, org_id, **kw):
        await real_advance(session, h.org_id, **kw)

    monkeypatch.setattr(auto, "is_tick_due", due)
    monkeypatch.setattr(auto, "ensure_next_run_seeded", seeded)
    monkeypatch.setattr(auto, "run_due_sweep", sweep)
    monkeypatch.setattr(auto, "advance_next_run", advance)

    ticks = {"n": 0}

    async def fake_wait(seconds, stopping):
        ticks["n"] += 1
        return ticks["n"] > 1

    job = next(j for j in PERIODIC_JOBS if j.role == "lit-review-auto")
    before = datetime.now(UTC)
    await run_periodic(job, wait=fake_wait)
    after = datetime.now(UTC)

    assert [s["skipped_reason"] for s in sweeps] == ["no_active_llm_provider"]
    async with _database.async_session_factory() as s:
        next_run = await auto.get_next_run(s)
    assert next_run is not None
    assert before + timedelta(hours=24) <= next_run <= after + timedelta(hours=24)


# ---------------------------------------------------------------------------
# Query generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_generation_provider_error_fails_before_source_calls(h):
    h.script_llm(ProviderError("rate limited", error_class="rate_limit"))

    run = await h.run()

    assert run.status == "failed"
    assert run.error_message == "llm_query_generation: rate limited"
    assert run.completed_at is not None
    assert h.calls == []
    assert h.terminal_events() == [(LITERATURE_REVIEW_RUN_FAILED, {"run_id": run.id})]


@pytest.mark.asyncio
async def test_query_generation_without_valid_queries_fails(h):
    h.script_llm("\n  - \n1.\n")

    run = await h.run()

    assert run.status == "failed"
    assert run.error_message == "llm_generated_no_valid_queries"
    assert run.completed_at is not None
    assert h.calls == []
    assert h.terminal_events() == [(LITERATURE_REVIEW_RUN_FAILED, {"run_id": run.id})]


# ---------------------------------------------------------------------------
# Scoring validation
# ---------------------------------------------------------------------------


async def _scored_run(h, response, **kwargs) -> LiteratureReviewRun:
    h.source("pubmed", always([paper(1), paper(2)]))
    h.script_llm("q1", response)
    return await h.run(**kwargs)


@pytest.mark.asyncio
async def test_scoring_provider_error_fails_without_recommendations(h):
    run = await _scored_run(h, ProviderError("server overloaded", error_class="server"))

    assert run.status == "failed"
    assert run.error_message == "llm_ranking: server overloaded"
    assert run.completed_at is not None
    assert run.candidate_count == 2
    assert run.recommendation_count == 0
    assert await h.recommendations() == []
    assert await h.papers() == []
    assert h.terminal_events() == [(LITERATURE_REVIEW_RUN_FAILED, {"run_id": run.id})]


@pytest.mark.parametrize(
    ("response", "diagnostic"),
    [
        ("I could not score these papers.", "llm_ranking: response is not valid JSON"),
        ("", "llm_ranking: response is not valid JSON"),
        ("```json\n[{broken\n```", "llm_ranking: response is not valid JSON"),
        (json.dumps({"index": 0, "score": 0.9}), "llm_ranking: response is not a JSON array"),
        (json.dumps({"results": [{"index": 0, "score": 0.9}]}), "llm_ranking: response is not a JSON array"),
        (json.dumps([{"idx": 0}, "text", 3, None, [0, 0.9]]), "llm_ranking: response has no usable entries"),
        (json.dumps([{"index": 7, "score": 0.9}]), "llm_ranking: response has no usable entries"),
        (
            json.dumps([{"index": True, "score": 0.9}, {"index": 0, "score": True}]),
            "llm_ranking: response has no usable entries",
        ),
        (
            '[{"index": 0, "score": NaN}, {"index": 1, "score": Infinity}]',
            "llm_ranking: response has no usable entries",
        ),
    ],
)
@pytest.mark.asyncio
async def test_unusable_scoring_output_fails_the_run(h, response, diagnostic):
    run = await _scored_run(h, response)

    assert run.status == "failed"
    assert run.error_message == diagnostic
    assert run.completed_at is not None
    assert await h.recommendations() == []
    assert await h.papers() == []
    assert h.terminal_events() == [(LITERATURE_REVIEW_RUN_FAILED, {"run_id": run.id})]


@pytest.mark.parametrize("response", ["[]", "```json\n[]\n```", "None of these are relevant: []"])
@pytest.mark.asyncio
async def test_valid_empty_ranking_is_a_successful_review(h, response):
    run = await _scored_run(h, response)

    assert run.status == "complete"
    assert run.error_message is None
    assert run.candidate_count == 2
    assert run.recommendation_count == 0
    assert h.terminal_events() == [(LITERATURE_REVIEW_RUN_COMPLETED, {"run_id": run.id, "recommendation_count": 0})]


@pytest.mark.asyncio
async def test_malformed_entries_are_skipped_when_valid_entries_remain(h):
    response = (
        '[{"index": 0, "score": 0.9, "reasoning": "good"}, "garbage", null, [1, 2],'
        ' {"index": true, "score": 0.8}, {"index": 1, "score": NaN}, {"index": 1, "score": 0.7, "reasoning": 5}]'
    )
    run = await _scored_run(h, response, score_threshold=0.0)

    assert run.status == "complete"
    recs = await h.recommendations(run.id)
    assert sorted((r.relevance_score, r.reasoning) for r in recs) == [(0.7, "5"), (0.9, "good")]


@pytest.mark.asyncio
async def test_scoring_failure_after_partial_sources_keeps_source_diagnostics_in_audit(h):
    h.source("biorxiv", fail())
    run = await _scored_run(h, "not json")

    assert run.status == "failed"
    assert run.error_message == "llm_ranking: response is not valid JSON"
    audit = [a for a in await h.audits(run.id) if a.action == "fail"]
    assert len(audit) == 1
    assert audit[0].details_json["status"] == "failed"
    assert audit[0].details_json["error"] == "llm_ranking: response is not valid JSON"
    assert audit[0].details_json["sources"]["biorxiv"] == {
        "attempts": 1,
        "failed": 1,
        "failure_categories": ["RuntimeError"],
    }


# ---------------------------------------------------------------------------
# Terminal persistence, events, and cancellation
# ---------------------------------------------------------------------------


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    async def poll():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


@pytest.mark.asyncio
async def test_missing_experiment_in_run_org_fails_with_terminal_event(h):
    from app.models.organization import Organization

    run_id = await h.create_run()
    other = Organization(name="Other Org", setup_complete=True)
    h.session.add(other)
    await h.session.flush()
    await h.session.execute(
        text("UPDATE experiments SET organization_id = :o WHERE id = :e").bindparams(o=other.id, e=h.experiment_id)
    )
    await h.session.commit()
    h.script_llm("q1")

    await h.execute(run_id)
    run = await h.load(run_id)

    assert run.status == "failed"
    assert run.error_message == "experiment not found"
    assert run.completed_at is not None
    assert h.llm.payloads == []
    assert h.terminal_events() == [(LITERATURE_REVIEW_RUN_FAILED, {"run_id": run_id})]
    fail_audit = [a for a in await h.audits(run_id) if a.action == "fail"]
    assert [a.details_json["error"] for a in fail_audit] == ["experiment not found"]


@pytest.mark.asyncio
async def test_missing_provider_configuration_fails_with_terminal_event(h):
    from app.models.llm_provider_config import LlmProviderConfig

    run_id = await h.create_run()
    await h.session.execute(LlmProviderConfig.__table__.delete().where(LlmProviderConfig.organization_id == h.org_id))
    await h.session.commit()

    await h.execute(run_id)
    run = await h.load(run_id)

    assert run.status == "failed"
    assert run.error_message == "llm provider configuration disappeared"
    assert run.completed_at is not None
    assert h.terminal_events() == [(LITERATURE_REVIEW_RUN_FAILED, {"run_id": run_id})]


@pytest.mark.asyncio
async def test_unexpected_exception_persists_failure_once(h, monkeypatch):
    h.source("pubmed", always([paper(1)]))
    h.script_llm("q1", scores((0, 0.9)))

    async def explode(*args, **kwargs):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(lit_review_run_service.association_service, "get_or_create", explode)

    run = await h.run()

    assert run.status == "failed"
    assert run.error_message == "unexpected_error: RuntimeError: database hiccup"
    assert run.completed_at is not None
    assert run.candidate_count == 1
    assert run.recommendation_count == 0
    assert await h.recommendations() == []
    assert h.terminal_events() == [(LITERATURE_REVIEW_RUN_FAILED, {"run_id": run.id})]


@pytest.mark.asyncio
async def test_completed_event_failure_does_not_rewrite_outcome(h, monkeypatch):
    h.source("pubmed", always([paper(1)]))
    h.script_llm("q1", scores((0, 0.9)))
    recorded: list[str] = []

    async def failing_emit(event_type, payload):
        recorded.append(event_type)
        if event_type == LITERATURE_REVIEW_RUN_COMPLETED:
            raise RuntimeError("subscriber exploded")

    monkeypatch.setattr(event_bus, "emit", failing_emit)

    run = await h.run()

    assert run.status == "complete"
    assert run.error_message is None
    assert run.recommendation_count == 1
    assert recorded == [LITERATURE_REVIEW_RUN_COMPLETED]


@pytest.mark.asyncio
async def test_notification_failure_after_commit_keeps_scheduled_run_complete(h, monkeypatch):
    h.source("pubmed", always([paper(1)]))
    h.script_llm("q1", scores((0, 0.9)))
    recorded: list[str] = []

    async def failing_emit(event_type, payload):
        recorded.append(event_type)
        if event_type == LITERATURE_AUTO_REVIEW_RECOMMENDATIONS:
            raise RuntimeError("notification router down")

    monkeypatch.setattr(event_bus, "emit", failing_emit)

    run = await h.run(trigger=TRIGGER_SCHEDULED)

    assert run.status == "complete"
    assert run.error_message is None
    assert run.recommendation_count == 1
    assert recorded == [LITERATURE_REVIEW_RUN_COMPLETED, LITERATURE_AUTO_REVIEW_RECOMMENDATIONS]


@pytest.mark.asyncio
async def test_partial_scheduled_run_with_picks_still_notifies(h):
    h.source("pubmed", always([paper(1)]))
    h.source("openalex", fail())
    h.script_llm("q1", scores((0, 0.9)))

    run = await h.run(trigger=TRIGGER_SCHEDULED)

    assert run.status == "partial"
    notes = [p for et, p in h.events if et == LITERATURE_AUTO_REVIEW_RECOMMENDATIONS]
    assert [n["metadata"]["recommendation_count"] for n in notes] == [1]


@pytest.mark.asyncio
async def test_cancellation_during_source_collection_cleans_up_and_propagates(h):
    started: set[str] = set()
    cleaned: set[str] = set()

    def hanging(name):
        async def hang():
            started.add(name)
            try:
                await asyncio.sleep(3600)
            finally:
                cleaned.add(name)
            return []

        return lambda query: hang()

    for name in SOURCES:
        h.source(name, hanging(name))
    h.script_llm("q1\nq2", scores((0, 0.9)))
    run_id = await h.create_run()

    task = asyncio.create_task(h.execute(run_id))
    await _wait_for(lambda: started == set(SOURCES))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert cleaned == set(SOURCES)
    run = await h.load(run_id)
    assert run.status == "failed"
    assert run.error_message == "cancelled"
    assert run.completed_at is not None
    assert len(h.llm.payloads) == 1  # never reached scoring
    assert h.terminal_events() == [(LITERATURE_REVIEW_RUN_FAILED, {"run_id": run_id})]


@pytest.mark.asyncio
async def test_cancellation_during_scoring_fails_run_without_recommendations(h):
    h.source("pubmed", always([paper(1)]))
    scoring = asyncio.Event()

    async def hang():
        scoring.set()
        await asyncio.sleep(3600)

    h.script_llm("q1", hang)
    run_id = await h.create_run()

    task = asyncio.create_task(h.execute(run_id))
    await asyncio.wait_for(scoring.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    run = await h.load(run_id)
    assert run.status == "failed"
    assert run.error_message == "cancelled"
    assert run.completed_at is not None
    assert await h.recommendations() == []
    assert h.terminal_events() == [(LITERATURE_REVIEW_RUN_FAILED, {"run_id": run_id})]


@pytest.mark.asyncio
async def test_cancellation_after_terminal_commit_keeps_committed_result(h, monkeypatch):
    h.source("pubmed", always([paper(1)]))
    h.script_llm("q1", scores((0, 0.9)))
    notifying = asyncio.Event()
    recorded: list[str] = []

    async def slow_emit(event_type, payload):
        recorded.append(event_type)
        if event_type == LITERATURE_AUTO_REVIEW_RECOMMENDATIONS:
            notifying.set()
            await asyncio.sleep(3600)

    monkeypatch.setattr(event_bus, "emit", slow_emit)
    run_id = await h.create_run(trigger=TRIGGER_SCHEDULED)

    task = asyncio.create_task(h.execute(run_id))
    await asyncio.wait_for(notifying.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    run = await h.load(run_id)
    assert run.status == "complete"
    assert run.error_message is None
    assert run.recommendation_count == 1
    assert recorded == [LITERATURE_REVIEW_RUN_COMPLETED, LITERATURE_AUTO_REVIEW_RECOMMENDATIONS]


@pytest.mark.asyncio
async def test_every_failed_path_writes_one_fail_audit_with_status_and_error(h):
    all_sources(h, fail())
    h.script_llm("q1")

    run = await h.run()

    audits = await h.audits(run.id)
    assert [a.action for a in audits] == ["create", "fail"]
    details = audits[-1].details_json
    assert details["status"] == "failed"
    assert details["error"] == run.error_message
    assert details["candidate_count"] == 0
    assert details["recommendation_count"] == 0
    assert set(details["sources"]) == set(SOURCES)


# ---------------------------------------------------------------------------
# Both callers run the same pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_launch_through_schedule_run_reaches_partial_with_diagnostics(h, client, admin_token):
    h.source("pubmed", always([paper(1)]))
    h.source("semanticscholar", fail())
    h.script_llm("q1", scores((0, 0.9)))
    headers = {"Authorization": f"Bearer {admin_token}"}

    resp = await client.post(
        f"/api/literature/experiments/{h.experiment_id}/lit-review-runs",
        json={"score_threshold": 0.5},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["id"]

    body: dict = {}

    async def finished():
        nonlocal body
        while True:
            body = (await client.get(f"/api/literature/lit-review-runs/{run_id}", headers=headers)).json()
            if body["status"] not in ("queued", "running"):
                return
            await asyncio.sleep(0.05)

    await asyncio.wait_for(finished(), 10)
    assert body["status"] == "partial"
    assert body["error_message"] == "some_literature_sources_failed: semanticscholar 1/1 attempts failed"
    assert body["recommendation_count"] == 1
    assert body["completed_at"] is not None
    assert h.terminal_events() == [(LITERATURE_REVIEW_RUN_COMPLETED, {"run_id": run_id, "recommendation_count": 1})]


@pytest.mark.asyncio
async def test_scheduled_sweep_runs_pipeline_to_terminal_outcome_and_notifies(h):
    from app.services.literature import lit_review_auto_service

    await _make_due(h)
    h.source("pubmed", always([paper(1)]))
    h.source("biorxiv", fail())
    h.script_llm("q1", scores((0, 0.9)))

    result = await lit_review_auto_service.run_due_sweep(h.session, h.org_id)

    assert result["skipped_reason"] is None
    assert result["ran"] == [h.experiment_id]
    async with _database.async_session_factory() as s:
        runs = (
            (await s.execute(select(LiteratureReviewRun).where(LiteratureReviewRun.organization_id == h.org_id)))
            .scalars()
            .all()
        )
    assert [(r.trigger, r.status, r.recommendation_count) for r in runs] == [(TRIGGER_SCHEDULED, "partial", 1)]
    assert runs[0].error_message == "some_literature_sources_failed: biorxiv 1/1 attempts failed"
    notes = [p for et, p in h.events if et == LITERATURE_AUTO_REVIEW_RECOMMENDATIONS]
    assert [n["entity_id"] for n in notes] == [runs[0].id]
    assert len(h.terminal_events()) == 1


# ---------------------------------------------------------------------------
# Review follow-ups: cancellation cleanup, numeric overflow, safe audit, event handoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_retrieves_already_failed_source_exceptions(h):
    import gc

    loop = asyncio.get_running_loop()
    unretrieved: list[dict] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unretrieved.append(context))
    try:
        hanging = asyncio.Event()

        async def hang():
            hanging.set()
            await asyncio.sleep(3600)

        # Sources run in name order: biorxiv is awaited first and hangs while
        # pubmed has already failed with a message carrying a credential.
        h.source("biorxiv", lambda q: hang())
        h.source("pubmed", fail(RuntimeError("GET /search?api_key=SECRET-KEY failed")))
        h.script_llm("q1")
        run_id = await h.create_run()

        task = asyncio.create_task(h.execute(run_id))
        await asyncio.wait_for(hanging.wait(), 5)
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        del task
        for _ in range(3):
            gc.collect()
            await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous)

    assert [c.get("message") for c in unretrieved] == []
    assert (await h.load(run_id)).status == "failed"


@pytest.mark.asyncio
async def test_overflowing_index_is_skipped_without_discarding_valid_entries(h):
    response = '[{"index": 0, "score": 0.9, "reasoning": "ok"}, {"index": 1e400, "score": 0.8}]'
    run = await _scored_run(h, response, score_threshold=0.0)

    assert run.status == "complete"
    assert run.recommendation_count == 1


@pytest.mark.asyncio
async def test_overflow_only_ranking_is_unusable(h):
    run = await _scored_run(h, '[{"index": 1e400, "score": 0.8}]')

    assert run.status == "failed"
    assert run.error_message == "llm_ranking: response has no usable entries"


@pytest.mark.parametrize(
    ("phase_responses", "audit_error"),
    [
        (
            [ProviderError('HTTP 500 body={"echo": "SECRET-PAYLOAD"}', error_class="server")],
            "llm_query_generation: provider_error (server)",
        ),
        (
            ["q1", ProviderError('HTTP 401 body={"key": "SECRET-PAYLOAD"}', error_class="auth")],
            "llm_ranking: provider_error (auth)",
        ),
    ],
)
@pytest.mark.asyncio
async def test_provider_error_bodies_stay_out_of_audit(h, phase_responses, audit_error):
    h.source("pubmed", always([paper(1)]))
    h.script_llm(*phase_responses)

    run = await h.run()

    assert run.status == "failed"
    audit = [a for a in await h.audits(run.id) if a.action == "fail"]
    assert len(audit) == 1
    assert audit[0].details_json["error"] == audit_error
    assert "SECRET" not in json.dumps(audit[0].details_json)


@pytest.mark.asyncio
async def test_unexpected_error_text_stays_out_of_audit(h, monkeypatch):
    h.source("pubmed", always([paper(1)]))
    h.script_llm("q1", scores((0, 0.9)))

    async def explode(*args, **kwargs):
        raise RuntimeError("response body SECRET-PAYLOAD")

    monkeypatch.setattr(lit_review_run_service.association_service, "get_or_create", explode)

    run = await h.run()

    assert run.error_message == "unexpected_error: RuntimeError: response body SECRET-PAYLOAD"
    audit = [a for a in await h.audits(run.id) if a.action == "fail"]
    assert audit[0].details_json["error"] == "unexpected_error: RuntimeError"
    assert "SECRET" not in json.dumps(audit[0].details_json)


@pytest.mark.asyncio
async def test_cancellation_right_after_terminal_commit_still_emits_terminal_event(h):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    h.source("pubmed", always([paper(1)]))
    h.script_llm("q1", scores((0, 0.9)))
    run_id = await h.create_run()
    holder: dict = {}

    def mark(session, flush_context, instances):
        if any(isinstance(o, AuditLog) and o.action == "complete" for o in session.new):
            session.info["cancel_after_commit"] = True

    def cancel(session):
        if session.info.pop("cancel_after_commit", False):
            holder["task"].cancel()

    event.listen(Session, "before_flush", mark)
    event.listen(Session, "after_commit", cancel)
    try:
        holder["task"] = asyncio.create_task(h.execute(run_id))
        with pytest.raises(asyncio.CancelledError):
            await holder["task"]
    finally:
        event.remove(Session, "after_commit", cancel)
        event.remove(Session, "before_flush", mark)

    run = await h.load(run_id)
    assert run.status == "complete"
    assert run.recommendation_count == 1
    assert h.terminal_events() == [(LITERATURE_REVIEW_RUN_COMPLETED, {"run_id": run_id, "recommendation_count": 1})]


@pytest.mark.asyncio
async def test_cancellation_right_after_failed_outcome_commit_still_emits_failed_event(h):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    h.source("pubmed", always([paper(1)]))
    h.script_llm("q1", ProviderError("overloaded", error_class="server"))
    run_id = await h.create_run()
    holder: dict = {}

    def mark(session, flush_context, instances):
        if any(isinstance(o, AuditLog) and o.action == "fail" for o in session.new):
            session.info["cancel_after_commit"] = True

    def cancel(session):
        if session.info.pop("cancel_after_commit", False):
            holder["task"].cancel()

    event.listen(Session, "before_flush", mark)
    event.listen(Session, "after_commit", cancel)
    try:
        holder["task"] = asyncio.create_task(h.execute(run_id))
        with pytest.raises(asyncio.CancelledError):
            await holder["task"]
    finally:
        event.remove(Session, "after_commit", cancel)
        event.remove(Session, "before_flush", mark)

    run = await h.load(run_id)
    assert run.status == "failed"
    assert run.error_message == "llm_ranking: overloaded"
    assert h.terminal_events() == [(LITERATURE_REVIEW_RUN_FAILED, {"run_id": run_id})]


@pytest.mark.asyncio
async def test_rate_limited_source_makes_review_partial_not_complete(h, monkeypatch):
    import respx

    from app.services.literature.sources import semanticscholar

    # Real Semantic Scholar adapter against a mocked 429, as seen live on blackbird.
    monkeypatch.setattr(semanticscholar, "search", REAL_SEARCH["semanticscholar"])
    h.source("pubmed", always([paper(1)]))
    h.script_llm("q1", scores((0, 0.9)))

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{semanticscholar._BASE}/paper/search").respond(status_code=429)
        run = await h.run()

    assert run.status == "partial"
    assert run.error_message == "some_literature_sources_failed: semanticscholar 1/1 attempts failed"
    audit = [a for a in await h.audits(run.id) if a.action == "complete"]
    assert audit[0].details_json["sources"]["semanticscholar"]["failure_categories"] == ["HTTPStatusError"]


@pytest.mark.asyncio
async def test_authorless_candidate_reuses_existing_authorless_paper(h):
    title = "8 Single Cell Transcriptomic Investigation of Renal Cell Carcinoma"
    existing_id = await h.add_paper(
        PaperRecord(source="europepmc", title=title, authors=[], doi=None), in_library=False
    )
    h.source("semanticscholar", always([PaperRecord(source="semanticscholar", title=title, authors=[], doi=None)]))
    h.script_llm("q1", scores((0, 0.9)))

    run = await h.run()

    assert run.status == "complete"
    assert run.recommendation_count == 1
    papers = await h.papers()
    assert [(p.id, p.in_library) for p in papers] == [(existing_id, True)]
    assert [r.paper_id for r in await h.recommendations(run.id)] == [existing_id]


@pytest.mark.asyncio
async def test_unreadable_pubmed_metadata_makes_review_partial_not_complete(h, monkeypatch):
    import respx

    from app.services.literature.sources import pubmed

    # Real PubMed adapter: esearch finds ids, efetch returns unreadable XML.
    monkeypatch.setattr(pubmed, "search", REAL_SEARCH["pubmed"])
    h.source("openalex", always([paper(1)]))
    h.script_llm("q1", scores((0, 0.9)))

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{pubmed._BASE}/esearch.fcgi").respond(json={"esearchresult": {"idlist": ["123"]}})
        mock.get(f"{pubmed._BASE}/efetch.fcgi").respond(status_code=200, text="<html>maintenance")
        run = await h.run()

    assert run.status == "partial"
    assert run.error_message == "some_literature_sources_failed: pubmed 1/1 attempts failed"
    audit = [a for a in await h.audits(run.id) if a.action == "complete"]
    assert audit[0].details_json["sources"]["pubmed"]["failure_categories"] == ["ParseError"]
