from pydantic import BaseModel


class SearchHit(BaseModel):
    entity_type: str
    entity_id: int
    title: str
    snippet: str | None = None
    experiment_id: int | None = None
    relevance_score: float | None = None


class SearchResult(BaseModel):
    results: list[SearchHit]
    total: int
    page: int
    page_size: int


class QuickSearchHit(BaseModel):
    entity_type: str
    entity_id: int
    name: str
    experiment_id: int | None = None


class QuickSearchResult(BaseModel):
    results: list[QuickSearchHit]
