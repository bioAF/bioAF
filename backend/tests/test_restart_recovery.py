"""Local jobs interrupted by a restart are failed at startup.

A literature search, a lab-document URL import, and a paper's PDF extraction are local work with no
external job to reconcile. A restart (orderly or not) leaves their rows unfinished forever, showing
as in progress with nothing running. Startup fails them with an interruption message, as it already
does for hosted reviews and glossary scans; finished rows are untouched. Retrying is a new search,
import, or extraction, as before.
"""

import pytest
from sqlalchemy import select

from app.models.lab_document import LabDocumentUrlImport
from app.models.literature import LiteraturePaper, LiteratureSearch
from app.services import restart_recovery

pytestmark = pytest.mark.asyncio


async def test_startup_fails_interrupted_local_jobs_and_leaves_finished_ones(session, admin_user):
    org, user = admin_user.organization_id, admin_user.id
    searches = [
        LiteratureSearch(organization_id=org, user_id=user, query_text=q, status=status)
        for q, status in [("a", "queued"), ("b", "running"), ("c", "complete")]
    ]
    imports = [
        LabDocumentUrlImport(organization_id=org, initiated_by_user_id=user, url=f"https://x/{s}", status=s)
        for s in ("pending", "running", "complete")
    ]
    papers = [
        LiteraturePaper(organization_id=org, title=t, title_normalized=t, extraction_status=s, provenance="upload")
        for t, s in [("p1", "pending"), ("p2", "complete")]
    ]
    session.add_all([*searches, *imports, *papers])
    await session.commit()

    counts = await restart_recovery.fail_interrupted_local_jobs(session)
    await session.commit()

    assert counts == {"literature_search": 2, "lab_document_url_import": 2, "lit_review_run": 0, "paper_extraction": 1}
    session.expire_all()
    by_query = {s.query_text: s for s in (await session.execute(select(LiteratureSearch))).scalars()}
    assert [by_query[q].status for q in "abc"] == ["failed", "failed", "complete"]
    assert by_query["a"].error_message == restart_recovery.INTERRUPTED
    assert by_query["a"].completed_at is not None
    url_status = {
        i.url: (i.status, i.error_message) for i in (await session.execute(select(LabDocumentUrlImport))).scalars()
    }
    assert url_status["https://x/running"] == ("failed", restart_recovery.INTERRUPTED)
    assert url_status["https://x/complete"] == ("complete", None)
    paper_status = {
        p.title: (p.extraction_status, p.extraction_error)
        for p in (await session.execute(select(LiteraturePaper))).scalars()
    }
    assert paper_status == {"p1": ("failed", restart_recovery.INTERRUPTED), "p2": ("complete", None)}


async def test_startup_runs_the_recovery_as_an_optional_step():
    from app import startup

    step = next(s for s in startup.NORMAL_STEPS if s.name == "interrupted_local_jobs")
    names = [s.name for s in startup.NORMAL_STEPS]
    assert step.policy == "optional"
    assert names.index("interrupted_local_jobs") == names.index("glossary_orphans") + 1


async def test_startup_fails_lit_review_runs_a_crash_left_unfinished(session, admin_user):
    from app.models.experiment import Experiment
    from app.models.literature import LiteratureReviewRun

    experiment = Experiment(organization_id=admin_user.organization_id, name="E")
    session.add(experiment)
    await session.flush()
    runs = {
        status: LiteratureReviewRun(
            organization_id=admin_user.organization_id,
            experiment_id=experiment.id,
            triggered_by_user_id=admin_user.id,
            status=status,
            llm_provider="openai",
            llm_model="m",
        )
        for status in ("queued", "running", "complete")
    }
    session.add_all(runs.values())
    await session.commit()

    counts = await restart_recovery.fail_interrupted_local_jobs(session)
    await session.commit()

    assert counts["lit_review_run"] == 2
    session.expire_all()
    rows = {r.id: r for r in (await session.execute(select(LiteratureReviewRun))).scalars()}
    assert [rows[runs[s].id].status for s in ("queued", "running", "complete")] == ["failed", "failed", "complete"]
    assert rows[runs["running"].id].error_message == restart_recovery.INTERRUPTED
