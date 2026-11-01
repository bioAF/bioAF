from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import has_effective_permission, require_any_permission, require_permission
from app.database import get_session
from app.schemas.analysis_snapshot import (
    SnapshotComparison,
    SnapshotCreate,
    SnapshotDetailResponse,
    SnapshotListResponse,
    SnapshotResponse,
)
from app.services.snapshot_service import (
    SNAPSHOT_ACCESS_DENIED,
    SnapshotAccess,
    SnapshotService,
    _derive_cluster_count,
)

router = APIRouter(prefix="/api/snapshots", tags=["snapshots"])

# Either grant opens the route; which snapshots it then reaches is decided per
# record, because a snapshot can hang off an experiment, a project, or both.
SNAPSHOT_VIEW_PERMISSIONS: list[tuple[str, str]] = [
    ("experiments", "view"),
    ("projects", "view"),
]


async def _access_of(session: AsyncSession, user: dict) -> SnapshotAccess:
    return SnapshotAccess(
        experiments=await has_effective_permission(session, user, "experiments", "view"),
        projects=await has_effective_permission(session, user, "projects", "view"),
    )


def _snapshot_to_response(snap) -> dict:
    figure_url = None
    if snap.figure_file and hasattr(snap.figure_file, "storage_uri"):
        figure_url = snap.figure_file.storage_uri

    return {
        "id": snap.id,
        "experiment_id": snap.experiment_id,
        "project_id": snap.project_id,
        "notebook_session_id": snap.notebook_session_id,
        "user_id": snap.user_id,
        "user_name": snap.user.name if snap.user and snap.user.name else snap.user.email if snap.user else "Unknown",
        "label": snap.label,
        "notes": snap.notes,
        "object_type": snap.object_type,
        "cell_count": snap.cell_count,
        "gene_count": snap.gene_count,
        "cluster_count": _derive_cluster_count(snap.clusterings_json),
        "starred": snap.starred,
        "figure_url": figure_url,
        "created_at": snap.created_at.isoformat(),
    }


def _snapshot_to_detail(snap) -> dict:
    base = _snapshot_to_response(snap)
    checkpoint_url = None
    if snap.checkpoint_file and hasattr(snap.checkpoint_file, "storage_uri"):
        checkpoint_url = snap.checkpoint_file.storage_uri

    base.update(
        {
            "parameters_json": snap.parameters_json,
            "embeddings_json": snap.embeddings_json,
            "clusterings_json": snap.clusterings_json,
            "layers_json": snap.layers_json,
            "metadata_columns_json": snap.metadata_columns_json,
            "command_log_json": snap.command_log_json,
            "checkpoint_url": checkpoint_url,
        }
    )
    return base


@router.post("", response_model=SnapshotResponse)
async def create_snapshot(
    body: SnapshotCreate,
    current_user: dict = require_permission("experiments", "change_status"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    snapshot = await SnapshotService.create_snapshot(session, org_id, user_id, body)
    await session.commit()

    # Reload with relationships
    snapshot = await SnapshotService.get_snapshot(session, snapshot.id, org_id)
    return SnapshotResponse(**_snapshot_to_response(snapshot))


@router.get("", response_model=SnapshotListResponse)
async def list_snapshots(
    experiment_id: int | None = Query(None),
    project_id: int | None = Query(None),
    user_id: int | None = Query(None),
    notebook_session_id: int | None = Query(None),
    starred: bool | None = Query(None),
    current_user: dict = require_any_permission(SNAPSHOT_VIEW_PERMISSIONS),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])

    snapshots = await SnapshotService.list_snapshots(
        session,
        org_id,
        await _access_of(session, current_user),
        experiment_id=experiment_id,
        project_id=project_id,
        user_id=user_id,
        notebook_session_id=notebook_session_id,
        starred=starred,
    )
    items = [SnapshotResponse(**_snapshot_to_response(s)) for s in snapshots]
    return SnapshotListResponse(snapshots=items, total=len(items))


@router.get("/compare", response_model=SnapshotComparison)
async def compare_snapshots(
    ids: str = Query(..., description="Comma-separated snapshot IDs (2-5)"),
    current_user: dict = require_any_permission(SNAPSHOT_VIEW_PERMISSIONS),
    session: AsyncSession = Depends(get_session),
):
    try:
        id_list = [int(x.strip()) for x in ids.split(",")]
    except ValueError:
        raise HTTPException(422, "ids must be comma-separated integers")

    result = await SnapshotService.compare_snapshots(
        session, id_list, int(current_user["org_id"]), await _access_of(session, current_user)
    )

    # Convert snapshots to detail responses
    detail_snapshots = [SnapshotDetailResponse(**_snapshot_to_detail(s)) for s in result["snapshots"]]
    return SnapshotComparison(
        snapshots=detail_snapshots,
        parameter_diff=result["parameter_diff"],
        embedding_diff=result["embedding_diff"],
        clustering_diff=result["clustering_diff"],
        command_log_diff=result["command_log_diff"],
        cell_count_series=result["cell_count_series"],
    )


@router.get("/{snapshot_id}", response_model=SnapshotDetailResponse)
async def get_snapshot(
    snapshot_id: int,
    current_user: dict = require_any_permission(SNAPSHOT_VIEW_PERMISSIONS),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    snapshot = await SnapshotService.get_snapshot(session, snapshot_id, org_id)
    if not snapshot:
        raise HTTPException(404, "Snapshot not found")
    access = await _access_of(session, current_user)
    if not await SnapshotService.reachable_ids(session, [snapshot_id], org_id, access):
        raise HTTPException(403, SNAPSHOT_ACCESS_DENIED)
    return SnapshotDetailResponse(**_snapshot_to_detail(snapshot))


@router.post("/{snapshot_id}/star", response_model=SnapshotResponse)
async def toggle_star(
    snapshot_id: int,
    current_user: dict = require_permission("experiments", "change_status"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    snapshot = await SnapshotService.toggle_star(session, snapshot_id, user_id)
    if not snapshot:
        raise HTTPException(404, "Snapshot not found")
    await session.commit()

    # Reload with relationships
    snapshot = await SnapshotService.get_snapshot(session, snapshot.id, int(current_user["org_id"]))
    return SnapshotResponse(**_snapshot_to_response(snapshot))
