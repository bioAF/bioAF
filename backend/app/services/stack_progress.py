"""Cumulative progress across the phases of a stack deployment.

Terraform reports counts per module; the deployment reports them across the
stack, so a compute resource reads as "7 of 9", not "1 of 3". This is pure: a
phase name and an event go in, the next progress and the event to show come out.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.services.terraform_stream import TerraformProgressEvent


@dataclass(frozen=True)
class PhaseCounts:
    completed: int = 0
    planned: int = 0


@dataclass(frozen=True)
class StackProgress:
    phases: dict[str, PhaseCounts] = field(default_factory=dict)

    @property
    def completed(self) -> int:
        return sum(counts.completed for counts in self.phases.values())

    @property
    def planned(self) -> int:
        return sum(counts.planned for counts in self.phases.values())


def reduce_progress(
    progress: StackProgress,
    phase: str,
    event: TerraformProgressEvent,
) -> tuple[StackProgress, TerraformProgressEvent]:
    """Apply one module event; return the new progress and the event with stack totals."""
    counts = progress.phases.get(phase, PhaseCounts())
    if event.event_type == "apply_complete":
        # The module's final count is authoritative, including zero.
        counts = PhaseCounts(event.resources_completed, event.resources_total)
    else:
        counts = PhaseCounts(
            completed=max(counts.completed, event.resources_completed),
            planned=event.resources_total or counts.planned,
        )

    next_progress = StackProgress({**progress.phases, phase: counts})
    shown = replace(
        event,
        resources_completed=next_progress.completed,
        resources_total=next_progress.planned,
    )
    return next_progress, shown
