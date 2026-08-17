"""Request and response shapes for a saved samplesheet design.

Values arrive and leave flat (``{"12": {"group": "gut"}}``). The stored form
carries who set each value and when, which the service adds and strips: the
browser sends a design, not an authorship record, and it must not be able to
claim one.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.samplesheet_mapping import MAPPING_SCOPES


class SamplesheetMappingSaveRequest(BaseModel):
    pipeline_key: str
    # experiment by default, promotable to project and then organization. Each
    # rung is a deliberate act, so the scope is always stated rather than
    # inferred from which id happens to be set.
    scope: str = "experiment"
    experiment_id: int | None = None
    project_id: int | None = None
    # The design itself, keyed by sample id then column. Sample ids arrive as
    # strings because JSON object keys are strings.
    values: dict[str, dict[str, str]] = Field(default_factory=dict)
    # Rules naming no sample: which file column takes the assembly, a literal
    # strandedness. These travel to any scope; per-sample values only apply
    # where the samples they name exist.
    bindings: dict[str, str] = Field(default_factory=dict)


class SamplesheetMappingResponse(BaseModel):
    pipeline_key: str
    # Null when nothing applies, which is an answer rather than an absence: the
    # grid says "nothing carried over" instead of showing an empty design that
    # could equally mean a failed lookup.
    scope: str | None = None
    experiment_id: int | None = None
    project_id: int | None = None
    values: dict[str, dict[str, str]] = Field(default_factory=dict)
    bindings: dict[str, str] = Field(default_factory=dict)
    updated_at: datetime | None = None


class SamplesheetPrefill(BaseModel):
    """What a saved design would contribute to this launch, and what it misses.

    Reported alongside the sheet a launch would submit, never folded into it.
    A design that fits six samples may be wrong for twelve, and a prefilled
    value looks plausible precisely because it was right last time.
    """

    scope: str | None = None
    values: dict[str, dict[str, str]] = Field(default_factory=dict)
    bindings: dict[str, str] = Field(default_factory=dict)
    # Selected samples this design does not name. They arrive blank in the grid
    # and the step says so, rather than presenting a design that looks complete.
    samples_without_values: list[int] = Field(default_factory=list)


__all__ = [
    "MAPPING_SCOPES",
    "SamplesheetMappingSaveRequest",
    "SamplesheetMappingResponse",
    "SamplesheetPrefill",
]
