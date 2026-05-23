from pydantic import BaseModel, Field


class DashboardWidgetItem(BaseModel):
    """One widget in a user's dashboard layout. ``settings`` carries per-user,
    per-widget options (e.g. the failed-runs time window)."""

    key: str
    settings: dict = Field(default_factory=dict)


class DashboardLayoutResponse(BaseModel):
    # configured=False means the user has never saved a layout, so the frontend
    # should seed the role default instead of rendering an empty dashboard.
    configured: bool
    widgets: list[DashboardWidgetItem]


class DashboardLayoutUpdate(BaseModel):
    widgets: list[DashboardWidgetItem]
