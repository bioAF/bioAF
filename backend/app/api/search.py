from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.search import SearchHit, SearchResult
from app.services.search_service import SearchService

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=SearchResult)
async def unified_search(
    request: Request,
    query: str = "",
    entity_types: str | None = None,
    page: int = 1,
    page_size: int = 25,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    types = entity_types.split(",") if entity_types else None

    if not query:
        return SearchResult(results=[], total=0, page=page, page_size=page_size)

    results, total = await SearchService.search(
        session, org_id, query, entity_types=types, page=page, page_size=page_size
    )

    return SearchResult(
        results=[SearchHit(**r) for r in results],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/reindex")
async def reindex(
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    result = await SearchService.reindex_all(session, org_id)
    return result
