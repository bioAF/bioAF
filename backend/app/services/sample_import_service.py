"""Sample import shared by CSV confirm, legacy CSV upload, and Google Sheet confirm.

Each accepted row is one unit of work inside its own savepoint: the sample, its
custom fields, any batch it creates, and its audit records are flushed together
and either all stay or all roll back. An expected row failure (a domain error or
a unique-index conflict) loses only that row; anything else fails the import.
The caller commits the surviving rows together.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import DomainError, NotFoundError, ValidationError
from app.models.experiment import Experiment
from app.models.sample_custom_field import SampleCustomField
from app.services.csv_service import ParsedSampleRow, parse_sample_rows
from app.services.sample_service import SampleService, http_error_text

CUSTOM_FIELD_NAME_LIMIT = 255


@dataclass(frozen=True)
class SampleImportResult:
    created_count: int
    errors: list[str]
    # Every custom field name in the accepted rows, whether or not each row was created.
    custom_fields_created: list[str]


class NothingToImportError(ValidationError):
    """Every record in the file was rejected; ``errors`` lists why."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("No sample rows could be imported")
        self.errors = errors


async def experiment_column_aliases(session: AsyncSession, experiment_id: int, org_id: int) -> dict[str, str] | None:
    """The experiment's persisted header aliases. A foreign or missing experiment is NotFound."""
    row = (
        await session.execute(
            select(Experiment.column_aliases).where(
                Experiment.id == experiment_id, Experiment.organization_id == org_id
            )
        )
    ).first()
    if row is None:
        raise NotFoundError("Experiment not found")
    return row[0]


def _unique_conflict_text(exc: IntegrityError, row: ParsedSampleRow) -> str | None:
    """Readable text for a unique-index conflict, or None when the error is not one."""
    orig = exc.orig
    if getattr(orig, "sqlstate", None) != "23505" and getattr(orig, "pgcode", None) != "23505":
        return None
    constraint = getattr(getattr(orig, "__cause__", None), "constraint_name", None)
    if constraint == "uq_samples_experiment_external_id":
        return f"Sample ID “{row.sample.external_id}” already exists in this experiment."
    if constraint == "uq_sample_seq_batch_position":
        return (
            f"Position {row.sample.sequencing_batch_position} in sequencing batch "
            f"“{row.sample.sequencing_batch_code}” is already taken."
        )
    return "It conflicts with a record that already exists."


async def _create_row(session: AsyncSession, experiment_id: int, user_id: int, row: ParsedSampleRow) -> None:
    sample = await SampleService.create_sample(session, experiment_id, user_id, row.sample)
    for field_name, field_value in row.custom_fields.items():
        if len(field_name) > CUSTOM_FIELD_NAME_LIMIT:
            raise ValidationError(f"Custom field name is longer than {CUSTOM_FIELD_NAME_LIMIT} characters.")
        session.add(SampleCustomField(sample_id=sample.id, field_name=field_name, field_value=str(field_value)))
    await session.flush()


async def import_samples(
    session: AsyncSession,
    *,
    experiment_id: int,
    org_id: int,
    user_id: int,
    content: bytes,
    column_mappings: dict[str, str] | None = None,
) -> SampleImportResult:
    """Parse ``content`` and create its samples row by row. Does not commit.

    Raises NotFoundError for an experiment outside ``org_id`` and NothingToImportError
    when the file has errors and no acceptable row.
    """
    aliases = await experiment_column_aliases(session, experiment_id, org_id)
    rows, parse_errors = parse_sample_rows(content, column_mappings or None, aliases)
    if not rows and parse_errors:
        raise NothingToImportError(parse_errors)

    created = 0
    create_errors: list[str] = []
    # "Sample N" numbers the accepted rows, the established label for creation errors.
    for number, row in enumerate(rows, start=1):
        try:
            async with session.begin_nested():
                await _create_row(session, experiment_id, user_id, row)
        except HTTPException as exc:
            create_errors.append(f"Sample {number}: {http_error_text(exc.detail)}")
        except DomainError as exc:
            create_errors.append(f"Sample {number}: {exc}")
        except IntegrityError as exc:
            reason = _unique_conflict_text(exc, row)
            if reason is None:
                raise
            create_errors.append(f"Sample {number}: {reason}")
        else:
            created += 1

    return SampleImportResult(
        created_count=created,
        errors=parse_errors + create_errors,
        custom_fields_created=sorted({name for row in rows for name in row.custom_fields}),
    )
