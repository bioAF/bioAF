from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.dashboard import DashboardLayout
from app.schemas.dashboard import (
    DashboardLayoutResponse,
    DashboardLayoutUpdate,
    DashboardWidgetItem,
)
from app.services.dashboard_service import DashboardService

# The dashboard layout is personal: any authenticated user reads/writes only their
# own. The auth middleware guarantees request.state.current_user on this route, so
# (like /api/auth/me) we read it directly rather than gating on a permission
# resource. Per-widget permission gating lives in the frontend registry and in each
# widget's own (already permission-gated) data endpoint.
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _to_response(layout: DashboardLayout | None) -> DashboardLayoutResponse:
    if layout is None:
        return DashboardLayoutResponse(configured=False, widgets=[])
    return DashboardLayoutResponse(
        configured=True,
        widgets=[DashboardWidgetItem(**w) for w in layout.widgets],
    )


@router.get("/layout", response_model=DashboardLayoutResponse)
async def get_layout(request: Request, session: AsyncSession = Depends(get_session)):
    user_id = int(request.state.current_user["sub"])
    layout = await DashboardService.get_layout(session, user_id)
    return _to_response(layout)


@router.put("/layout", response_model=DashboardLayoutResponse)
async def put_layout(
    body: DashboardLayoutUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user_id = int(request.state.current_user["sub"])
    layout = await DashboardService.save_layout(session, user_id, [w.model_dump() for w in body.widgets])
    await session.commit()
    return _to_response(layout)
