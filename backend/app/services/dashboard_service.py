from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dashboard import DashboardLayout


class DashboardService:
    @staticmethod
    async def get_layout(session: AsyncSession, user_id: int) -> DashboardLayout | None:
        result = await session.execute(select(DashboardLayout).where(DashboardLayout.user_id == user_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def save_layout(session: AsyncSession, user_id: int, widgets: list[dict]) -> DashboardLayout:
        """Upsert the user's single layout row."""
        layout = await DashboardService.get_layout(session, user_id)
        if layout is None:
            layout = DashboardLayout(user_id=user_id, widgets=widgets)
            session.add(layout)
        else:
            layout.widgets = widgets
        await session.flush()
        return layout
