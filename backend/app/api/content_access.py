"""The one answer to "may this caller read these bytes?".

Content URLs go in <img> tags, so they carry a short-lived token with no role, and
a token cannot be gated by a dependency. The decision is made here when the token
is issued, and again here on direct bearer calls, so the two cannot drift apart.

File bytes: files:view opens any file in the Organization. Results view opens only
a file backing a plot or a QC report, because that is what the results pages show.
Thumbnails: results view.
"""

from fastapi import HTTPException, Request
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import RESULTS_VIEW_PERMISSIONS, has_effective_permission
from app.models.file import File
from app.models.plot_archive_entry import PlotArchiveEntry
from app.models.qc_dashboard import QCDashboard
from app.services.file_service import FileService
from app.services.plot_archive_service import PlotArchiveService


async def _has_results_view(session: AsyncSession, user: dict) -> bool:
    for resource, action in RESULTS_VIEW_PERMISSIONS:
        if await has_effective_permission(session, user, resource, action):
            return True
    return False


async def _backs_a_result(session: AsyncSession, file_id: int, org_id: int) -> bool:
    """True when this Organization's plot archive or QC reports name the file.

    Only those associations are trusted, never a filename, tag, or pipeline-output
    marker. plots_json is free-form, so containment on an integer file_id is what
    makes missing, malformed, and string-typed entries simply fail to match.
    """
    backed = await session.execute(
        select(
            or_(
                exists().where(PlotArchiveEntry.organization_id == org_id, PlotArchiveEntry.file_id == file_id),
                exists().where(
                    QCDashboard.organization_id == org_id, QCDashboard.plots_json.contains([{"file_id": file_id}])
                ),
            )
        )
    )
    return bool(backed.scalar())


async def authorize_file(session: AsyncSession, user: dict, file_id: int) -> File:
    org_id = int(user["org_id"])
    file = await FileService.get_file(session, file_id, org_id)
    if not file:
        raise HTTPException(404, "File not found")
    if await has_effective_permission(session, user, "files", "view"):
        return file
    if await _has_results_view(session, user) and await _backs_a_result(session, file_id, org_id):
        return file
    raise HTTPException(403, "role_missing")


async def authorize_thumbnail(session: AsyncSession, user: dict, plot_id: int) -> PlotArchiveEntry:
    plot = await PlotArchiveService.get_plot(session, int(user["org_id"]), plot_id)
    if not plot:
        raise HTTPException(404, "Plot not found")
    if not await _has_results_view(session, user):
        raise HTTPException(403, "role_missing")
    return plot


async def file_for_content(session: AsyncSession, request: Request, file_id: int) -> File:
    """The file a content route may serve. A verified token was authorized when it
    was issued; it still only ever reaches a record in its own Organization."""
    user = request.state.current_user
    if not getattr(request.state, "content_token_verified", False):
        return await authorize_file(session, user, file_id)
    file = await FileService.get_file(session, file_id, int(user["org_id"]))
    if not file:
        raise HTTPException(404, "File not found")
    return file


async def plot_for_content(session: AsyncSession, request: Request, plot_id: int) -> PlotArchiveEntry:
    user = request.state.current_user
    if not getattr(request.state, "content_token_verified", False):
        return await authorize_thumbnail(session, user, plot_id)
    plot = await PlotArchiveService.get_plot(session, int(user["org_id"]), plot_id)
    if not plot:
        raise HTTPException(404, "Plot not found")
    return plot
