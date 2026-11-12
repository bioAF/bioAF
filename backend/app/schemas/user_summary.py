from pydantic import BaseModel


class UserSummary(BaseModel):
    """A user as other records reference them: who launched, owns, uploaded, or operated something."""

    id: int
    name: str | None = None
    email: str

    # Every field is always emitted, so the response schema marks defaulted fields required too
    # (frontend transport types are generated from it). Runtime validation is unchanged.
    model_config = {"json_schema_serialization_defaults_required": True, "from_attributes": True}
