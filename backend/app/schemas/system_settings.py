from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class InstallMetricsSettings(BaseModel):
    """The installation metrics choice as an admin sees it (ADR-075)."""

    preference: Literal["pending", "enabled", "disabled"]
    # True until either choice is committed; then never again for this installation.
    prompt_required: bool
    # Reporting happens: the choice is enabled and the operator override is off.
    effective: bool
    operator_override: bool
    # Whether the setup wizard already completed when the choice is made (the prompt adds that only the
    # current observation date is reported).
    setup_completed: bool
    installation_id: str | None
    notice_version: str
    decided_at: datetime | None
    revision: int


class SystemSettingsResponse(BaseModel):
    install_metrics: InstallMetricsSettings


class InstallMetricsChoiceRequest(BaseModel):
    """Only the choice, the notice version it was made against, and the revision it was read at."""

    model_config = ConfigDict(extra="forbid")

    choice: Literal["enabled", "disabled"]
    notice_version: str
    revision: int
