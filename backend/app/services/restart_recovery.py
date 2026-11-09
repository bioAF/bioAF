"""Fail local jobs a restart interrupted (plan 14).

A literature search, an AI lit-review run, a lab-document URL import, and a paper's PDF extraction run in this process
with no external job to reconcile, so after a restart nothing will ever finish them. Startup marks
them failed, as it does for hosted reviews and glossary scans. Work with external effects
(deployments, readiness pollers, reference imports, pipeline runs) is not touched here: a missing
local observer says nothing about the external job.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab_document import LabDocumentUrlImport
from app.models.literature import (
    EXTRACTION_FAILED,
    EXTRACTION_PENDING,
    SEARCH_FAILED,
    SEARCH_QUEUED,
    SEARCH_RUNNING,
    LiteraturePaper,
    LiteratureReviewRun,
    LiteratureSearch,
)

INTERRUPTED = "bioAF restarted while this was in progress. Start it again."


async def fail_interrupted_local_jobs(session: AsyncSession) -> dict[str, int]:
    now = datetime.now(UTC)
    searches = await session.execute(
        update(LiteratureSearch)
        .where(LiteratureSearch.status.in_((SEARCH_QUEUED, SEARCH_RUNNING)))
        .values(status=SEARCH_FAILED, error_message=INTERRUPTED, completed_at=now)
    )
    imports = await session.execute(
        update(LabDocumentUrlImport)
        .where(LabDocumentUrlImport.status.in_(("pending", "running")))
        .values(status="failed", error_message=INTERRUPTED, completed_at=now)
    )
    # An orderly stop already fails a lit-review run as cancelled; this covers a crash.
    reviews = await session.execute(
        update(LiteratureReviewRun)
        .where(LiteratureReviewRun.status.in_((SEARCH_QUEUED, SEARCH_RUNNING)))
        .values(status=SEARCH_FAILED, error_message=INTERRUPTED, completed_at=now)
    )
    papers = await session.execute(
        update(LiteraturePaper)
        .where(LiteraturePaper.extraction_status == EXTRACTION_PENDING)
        .values(extraction_status=EXTRACTION_FAILED, extraction_error=INTERRUPTED)
    )
    return {
        "literature_search": searches.rowcount,  # type: ignore[attr-defined]
        "lab_document_url_import": imports.rowcount,  # type: ignore[attr-defined]
        "lit_review_run": reviews.rowcount,  # type: ignore[attr-defined]
        "paper_extraction": papers.rowcount,  # type: ignore[attr-defined]
    }
