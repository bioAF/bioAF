"""The HTTP side of sample import, shared by the CSV and Google Sheet routes.

Routes check experiment ownership themselves before reading an upload or a
sheet, so the 404 comes before any remote read.
"""

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.sample_import_service import NothingToImportError, SampleImportResult, import_samples


async def run_sample_import(
    session: AsyncSession,
    current_user: dict,
    experiment_id: int,
    content: bytes,
    column_mappings: dict[str, str] | None = None,
) -> SampleImportResult:
    """Import and commit the rows that succeeded; 400 with the errors when none could."""
    try:
        result = await import_samples(
            session,
            experiment_id=experiment_id,
            org_id=int(current_user["org_id"]),
            user_id=int(current_user["sub"]),
            content=content,
            column_mappings=column_mappings,
        )
    except NothingToImportError as exc:
        raise HTTPException(400, detail={"errors": exc.errors}) from exc
    if result.created_count:
        await session.commit()
    return result
