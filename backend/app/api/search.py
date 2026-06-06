from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.search import QuickSearchHit, QuickSearchResult, SearchHit, SearchResult
from app.services import role_service
from app.services.search_service import FULL_SEARCH_TYPES, SearchService

router = APIRouter(prefix="/api/search", tags=["search"])

# The (resource, action) a user's role must hold to see each entity type in full
# search. Types the user cannot view are hidden entirely (no 403 dead-ends). Both
# pipeline runs and pipeline definitions are gated on "pipelines: view".
_TYPE_PERMISSIONS: dict[str, tuple[str, str]] = {
    "experiment": ("experiments", "view"),
    "sample": ("samples", "view"),
    "pipeline_run": ("pipelines", "view"),
    "file": ("files", "view"),
    "project": ("projects", "view"),
    "pipeline_definition": ("pipelines", "view"),
    "literature_paper": ("literature", "view"),
    "lab_document": ("lab_documents", "view"),
    "lab_glossary_term": ("lab_glossary", "view"),
    "sdr": ("sdr", "view"),
}


def _scope_ok(user: dict, resource: str, action: str) -> bool:
    """API-key requests are narrowed to the key's scope envelope (ADR-049); JWT
    requests (api_key_id None) are not."""
    if user.get("api_key_id") is None:
        return True
    return f"{resource}:{action}" in (user.get("scopes") or [])


async def _permitted_types(session: AsyncSession, user: dict) -> list[str]:
    if "role_id" not in user:
        return []
    role_id = int(user["role_id"])
    permitted = []
    for t in FULL_SEARCH_TYPES:
        resource, action = _TYPE_PERMISSIONS[t]
        if await role_service.has_permission(session, role_id, resource, action) and _scope_ok(user, resource, action):
            permitted.append(t)
    return permitted


@router.get("/quick", response_model=QuickSearchResult)
async def quick_search(
    request: Request,
    q: str = "",
    session: AsyncSession = Depends(get_session),
):
    """Name-only "jump to" search for the header (experiments, samples, runs, files)."""
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    if not q.strip():
        return QuickSearchResult(results=[])

    hits = await SearchService.quick_search(session, org_id, q)
    return QuickSearchResult(results=[QuickSearchHit(**h) for h in hits])


@router.get("", response_model=SearchResult)
async def unified_search(
    request: Request,
    query: str = "",
    entity_types: str | None = None,
    page: int = 1,
    page_size: int = 25,
    session: AsyncSession = Depends(get_session),
):
    """Full search for the dedicated results page (`/search`).

    Searches the entity types the user may view; a requested ``entity_types`` filter
    (comma list, typically a single type) narrows that further. Results are a single
    ranked list with per-type counts for the type filter.
    """
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    empty = SearchResult(results=[], total=0, page=page, page_size=page_size, type_counts={})
    if not query.strip():
        return empty

    permitted = await _permitted_types(session, current_user)
    if not permitted:
        return empty

    requested = [t.strip() for t in entity_types.split(",")] if entity_types else None
    types = [t for t in (requested or permitted) if t in permitted]
    if not types:
        return empty

    results, total, counts = await SearchService.full_search(
        session, org_id, query, entity_types=types, page=page, page_size=page_size, count_types=permitted
    )

    return SearchResult(
        results=[SearchHit(**r) for r in results],
        total=total,
        page=page,
        page_size=page_size,
        type_counts=counts,
    )


@router.post("/reindex")
async def reindex(
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    result = await SearchService.reindex_all(session, org_id)
    return result
