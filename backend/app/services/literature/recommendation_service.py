"""Literature Recommendations queue: list, accept, dismiss."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.literature import (
    LiteratureRecommendation,
    REC_ACCEPTED,
    REC_DISMISSED,
    REC_PENDING,
    SCOPE_EXPERIMENT,
)
from app.services import audit_service
from app.services.literature import association_service, dismissal_service


class RecommendationNotFound(Exception):
    pass


class RecommendationAlreadyDecided(Exception):
    pass


async def list_for_org(
    session: AsyncSession,
    *,
    org_id: int,
    experiment_id: int | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[LiteratureRecommendation], int]:
    query = select(LiteratureRecommendation).where(
        LiteratureRecommendation.organization_id == org_id
    )
    if experiment_id is not None:
        query = query.where(LiteratureRecommendation.experiment_id == experiment_id)
    if status:
        query = query.where(LiteratureRecommendation.status == status)
    query = query.order_by(
        LiteratureRecommendation.experiment_id, LiteratureRecommendation.relevance_score.desc()
    )
    from sqlalchemy import func as sa_func

    total = int(
        (
            await session.execute(select(sa_func.count()).select_from(query.subquery()))
        ).scalar_one()
    )
    offset = (max(page, 1) - 1) * max(page_size, 1)
    rs = await session.execute(query.limit(page_size).offset(offset))
    return list(rs.scalars().all()), total


async def get_recommendation(
    session: AsyncSession, *, org_id: int, recommendation_id: int
) -> LiteratureRecommendation:
    rs = await session.execute(
        select(LiteratureRecommendation).where(
            LiteratureRecommendation.id == recommendation_id,
            LiteratureRecommendation.organization_id == org_id,
        )
    )
    rec = rs.scalar_one_or_none()
    if rec is None:
        raise RecommendationNotFound(f"recommendation {recommendation_id} not found")
    return rec


async def accept(
    session: AsyncSession,
    *,
    org_id: int,
    recommendation_id: int,
    user_id: int,
    api_key_id: int | None = None,
) -> LiteratureRecommendation:
    """Mark a recommendation accepted and add an experiment-scope association
    for its Paper. Returns the updated recommendation."""
    rec = await get_recommendation(session, org_id=org_id, recommendation_id=recommendation_id)
    if rec.status != REC_PENDING:
        raise RecommendationAlreadyDecided(f"recommendation already {rec.status}")
    rec.status = REC_ACCEPTED
    rec.decided_by_user_id = user_id
    rec.decided_at = datetime.now(UTC)
    await session.flush()

    # Auto-associate the paper with the experiment.
    from app.services.literature import paper_service

    paper = await paper_service.get_paper(session, org_id, rec.paper_id)
    await association_service.get_or_create(
        session,
        paper=paper,
        user_id=user_id,
        scope_type=SCOPE_EXPERIMENT,
        scope_id=rec.experiment_id,
    )

    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_recommendation",
        entity_id=rec.id,
        action="accept",
        details={"paper_id": rec.paper_id, "experiment_id": rec.experiment_id},
    )
    return rec


async def dismiss(
    session: AsyncSession,
    *,
    org_id: int,
    recommendation_id: int,
    user_id: int,
    api_key_id: int | None = None,
) -> LiteratureRecommendation:
    rec = await get_recommendation(session, org_id=org_id, recommendation_id=recommendation_id)
    if rec.status != REC_PENDING:
        raise RecommendationAlreadyDecided(f"recommendation already {rec.status}")
    rec.status = REC_DISMISSED
    rec.decided_by_user_id = user_id
    rec.decided_at = datetime.now(UTC)
    await session.flush()

    # Org-wide dismissal of the paper itself.
    await dismissal_service.dismiss(
        session,
        paper_id=rec.paper_id,
        org_id=org_id,
        user_id=user_id,
        reason="lit_review_run recommendation dismissed",
    )

    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_recommendation",
        entity_id=rec.id,
        action="dismiss",
        details={"paper_id": rec.paper_id, "experiment_id": rec.experiment_id},
    )
    return rec
