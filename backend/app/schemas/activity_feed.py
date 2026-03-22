from datetime import datetime

from pydantic import BaseModel


class ActivityFeedItem(BaseModel):
    id: int
    user_id: int | None = None
    user_email: str | None = None
    event_type: str
    entity_type: str | None = None
    entity_id: int | None = None
    summary: str
    severity: str = "info"
    metadata_json: dict = {}
    created_at: datetime

    model_config = {"from_attributes": True}


class ActivityFeedListResponse(BaseModel):
    events: list[ActivityFeedItem]
    total: int
    page: int
    page_size: int
