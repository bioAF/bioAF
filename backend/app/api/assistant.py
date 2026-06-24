"""Assistant API (ai_pipeline_run). v1 is backend-first; this is the availability gate.

GET /api/assistant/availability tells the UI whether the conversational assistant can run
for this org (an active, tool-capable LLM provider is configured). Gated by assistant:use so
only users who can use the assistant query it. The conversation and confirm endpoints land in
a later slice.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.services.assistant_availability_service import AssistantAvailabilityService

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class AvailabilityResponse(BaseModel):
    enabled: bool
    reason: str | None = None


@router.get("/availability", response_model=AvailabilityResponse)
async def get_availability(
    current_user: dict = require_permission("assistant", "use"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    availability = await AssistantAvailabilityService.get_availability(session, org_id)
    return AvailabilityResponse(enabled=availability.enabled, reason=availability.reason)
