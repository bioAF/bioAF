from pydantic import BaseModel


class SearchHit(BaseModel):
    entity_type: str
    entity_id: int
    title: str
    snippet: str | None = None
    # In-app destination for this hit, computed server-side (pipeline definitions
    # differ for built-in vs custom, so the client cannot derive it from id alone).
    url: str = ""
    experiment_id: int | None = None
    relevance_score: float | None = None


class SearchResult(BaseModel):
    results: list[SearchHit]
    total: int
    page: int
    page_size: int
    # Per-type match counts for the searched types, used to label the type filter.
    type_counts: dict[str, int] = {}


class QuickSearchHit(BaseModel):
    entity_type: str
    entity_id: int
    name: str
    experiment_id: int | None = None


class QuickSearchResult(BaseModel):
    results: list[QuickSearchHit]
