from pydantic import BaseModel


class UserSummary(BaseModel):
    """A user as other records reference them: who launched, owns, uploaded, or operated something."""

    id: int
    name: str | None = None
    email: str

    model_config = {"from_attributes": True}
