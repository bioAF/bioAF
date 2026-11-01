from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import ColumnElement, exists, false, func, or_, select, union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.analysis_snapshot import AnalysisSnapshot
from app.models.experiment import Experiment
from app.models.project import Project
from app.schemas.analysis_snapshot import SnapshotCreate
from app.services.audit_service import log_action


SNAPSHOT_ACCESS_DENIED = "snapshot_access_denied"


@dataclass(frozen=True)
class SnapshotAccess:
    """Which of the two paths to a snapshot this caller holds (decision 5a)."""

    experiments: bool
    projects: bool


def _with_owned_experiment(org_id: int) -> ColumnElement[bool]:
    return exists().where(Experiment.id == AnalysisSnapshot.experiment_id, Experiment.organization_id == org_id)


def _in_a_project(org_id: int) -> ColumnElement[bool]:
    """The snapshot names a project, or its experiment belongs to one. Only links
    within the Organization count, so a stored foreign link opens no path."""
    direct = exists().where(Project.id == AnalysisSnapshot.project_id, Project.organization_id == org_id)
    nested = exists().where(
        Experiment.id == AnalysisSnapshot.experiment_id,
        Experiment.organization_id == org_id,
        Experiment.project_id.is_not(None),
    )
    return or_(direct, nested)


def _reachable_with(access: SnapshotAccess, org_id: int) -> ColumnElement[bool]:
    paths: list[ColumnElement[bool]] = []
    if access.experiments:
        paths.append(_with_owned_experiment(org_id))
    if access.projects:
        paths.append(_in_a_project(org_id))
    return or_(*paths) if paths else false()


def _derive_cluster_count(clusterings_json: dict | None) -> int | None:
    if not clusterings_json:
        return None
    max_count = 0
    for info in clusterings_json.values():
        if isinstance(info, dict):
            max_count = max(max_count, info.get("n_clusters", 0))
    return max_count if max_count > 0 else None


def _flatten_params(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Recursively flatten a nested dict into dotted-path keys."""
    result: dict[str, Any] = {}
    if not isinstance(obj, dict):
        return result
    for key, value in obj.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_flatten_params(value, path))
        else:
            result[path] = value
    return result


class SnapshotService:
    @staticmethod
    async def create_snapshot(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        data: SnapshotCreate,
    ) -> AnalysisSnapshot:
        if not data.experiment_id and not data.project_id:
            raise HTTPException(422, "At least one of experiment_id or project_id is required")

        if data.experiment_id:
            result = await session.execute(select(Experiment).where(Experiment.id == data.experiment_id))
            if not result.scalar_one_or_none():
                raise HTTPException(404, "Experiment not found")

        if data.project_id:
            result = await session.execute(select(Project).where(Project.id == data.project_id))
            if not result.scalar_one_or_none():
                raise HTTPException(404, "Project not found")

        snapshot = AnalysisSnapshot(
            organization_id=org_id,
            user_id=user_id,
            experiment_id=data.experiment_id,
            project_id=data.project_id,
            notebook_session_id=data.notebook_session_id,
            label=data.label,
            notes=data.notes,
            object_type=data.object_type,
            cell_count=data.cell_count,
            gene_count=data.gene_count,
            parameters_json=data.parameters_json,
            embeddings_json=data.embeddings_json,
            clusterings_json=data.clusterings_json,
            layers_json=data.layers_json,
            metadata_columns_json=data.metadata_columns_json,
            command_log_json=data.command_log_json,
            figure_file_id=data.figure_file_id,
            checkpoint_file_id=data.checkpoint_file_id,
        )
        session.add(snapshot)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="analysis_snapshot",
            entity_id=snapshot.id,
            action="created",
            details={"label": data.label, "object_type": data.object_type},
        )
        return snapshot

    @staticmethod
    async def list_snapshots(
        session: AsyncSession,
        org_id: int,
        access: SnapshotAccess,
        experiment_id: int | None = None,
        project_id: int | None = None,
        user_id: int | None = None,
        notebook_session_id: int | None = None,
        starred: bool | None = None,
    ) -> list[AnalysisSnapshot]:
        stmt = (
            select(AnalysisSnapshot)
            .options(selectinload(AnalysisSnapshot.user))
            .options(selectinload(AnalysisSnapshot.figure_file))
            .where(AnalysisSnapshot.organization_id == org_id, _reachable_with(access, org_id))
            .order_by(AnalysisSnapshot.created_at.desc())
        )

        if experiment_id is not None:
            stmt = stmt.where(AnalysisSnapshot.experiment_id == experiment_id)
        if project_id is not None:
            # A project's timeline includes its experiments' snapshots. Membership
            # through project_samples is deliberately not a link here.
            stmt = stmt.where(
                or_(
                    AnalysisSnapshot.project_id == project_id,
                    exists().where(
                        Experiment.id == AnalysisSnapshot.experiment_id,
                        Experiment.organization_id == org_id,
                        Experiment.project_id == project_id,
                    ),
                )
            )
        if user_id is not None:
            stmt = stmt.where(AnalysisSnapshot.user_id == user_id)
        if notebook_session_id is not None:
            stmt = stmt.where(AnalysisSnapshot.notebook_session_id == notebook_session_id)
        if starred is not None:
            stmt = stmt.where(AnalysisSnapshot.starred == starred)

        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_snapshot(session: AsyncSession, snapshot_id: int, org_id: int) -> AnalysisSnapshot | None:
        result = await session.execute(
            select(AnalysisSnapshot)
            .options(selectinload(AnalysisSnapshot.user))
            .options(selectinload(AnalysisSnapshot.figure_file))
            .options(selectinload(AnalysisSnapshot.checkpoint_file))
            .where(AnalysisSnapshot.id == snapshot_id, AnalysisSnapshot.organization_id == org_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def counts_by_project(session: AsyncSession, org_id: int, project_ids: list[int]) -> dict[int, int]:
        """How many snapshots each project's timeline shows. The union keeps one
        row per (project, snapshot), so a snapshot linked both ways counts once."""
        direct = select(
            AnalysisSnapshot.project_id.label("project_id"), AnalysisSnapshot.id.label("snapshot_id")
        ).where(
            AnalysisSnapshot.organization_id == org_id,
            AnalysisSnapshot.project_id.in_(project_ids),
        )
        nested = (
            select(Experiment.project_id.label("project_id"), AnalysisSnapshot.id.label("snapshot_id"))
            .join(Experiment, Experiment.id == AnalysisSnapshot.experiment_id)
            .where(
                AnalysisSnapshot.organization_id == org_id,
                Experiment.organization_id == org_id,
                Experiment.project_id.in_(project_ids),
            )
        )
        links = union(direct, nested).subquery()
        result = await session.execute(
            select(links.c.project_id, func.count(links.c.snapshot_id)).group_by(links.c.project_id)
        )
        return {project_id: count for project_id, count in result.all()}

    @staticmethod
    async def reachable_ids(
        session: AsyncSession, snapshot_ids: list[int], org_id: int, access: SnapshotAccess
    ) -> set[int]:
        """The subset of these IDs this caller may open."""
        result = await session.execute(
            select(AnalysisSnapshot.id).where(
                AnalysisSnapshot.id.in_(snapshot_ids),
                AnalysisSnapshot.organization_id == org_id,
                _reachable_with(access, org_id),
            )
        )
        return set(result.scalars().all())

    @staticmethod
    async def toggle_star(session: AsyncSession, snapshot_id: int, user_id: int) -> AnalysisSnapshot | None:
        result = await session.execute(select(AnalysisSnapshot).where(AnalysisSnapshot.id == snapshot_id))
        snapshot = result.scalar_one_or_none()
        if not snapshot:
            return None

        snapshot.starred = not snapshot.starred
        await session.flush()

        action = "starred" if snapshot.starred else "unstarred"
        await log_action(
            session,
            user_id=user_id,
            entity_type="analysis_snapshot",
            entity_id=snapshot.id,
            action=action,
            details={"label": snapshot.label},
        )
        return snapshot

    @staticmethod
    async def compare_snapshots(session: AsyncSession, ids: list[int], org_id: int, access: SnapshotAccess) -> dict:
        if len(ids) < 2 or len(ids) > 5:
            raise HTTPException(422, "Must compare between 2 and 5 snapshots")

        result = await session.execute(
            select(AnalysisSnapshot)
            .options(selectinload(AnalysisSnapshot.user))
            .options(selectinload(AnalysisSnapshot.figure_file))
            .options(selectinload(AnalysisSnapshot.checkpoint_file))
            .where(AnalysisSnapshot.id.in_(ids), AnalysisSnapshot.organization_id == org_id)
        )
        snapshots = list(result.scalars().all())

        if len(snapshots) != len(ids):
            found_ids = {s.id for s in snapshots}
            missing = set(ids) - found_ids
            raise HTTPException(404, f"Snapshots not found: {sorted(missing)}")

        if await SnapshotService.reachable_ids(session, ids, org_id, access) != set(ids):
            raise HTTPException(403, SNAPSHOT_ACCESS_DENIED)

        # Sort by created_at for consistent ordering
        snapshots.sort(key=lambda s: s.created_at)

        parameter_diff = _compute_parameter_diff(snapshots)
        embedding_diff = _compute_embedding_diff(snapshots)
        clustering_diff = _compute_clustering_diff(snapshots)
        command_log_diff = _compute_command_log_diff(snapshots)
        cell_count_series = _compute_cell_count_series(snapshots)

        return {
            "snapshots": snapshots,
            "parameter_diff": parameter_diff,
            "embedding_diff": embedding_diff,
            "clustering_diff": clustering_diff,
            "command_log_diff": command_log_diff,
            "cell_count_series": cell_count_series,
        }


def _compute_parameter_diff(snapshots: list[AnalysisSnapshot]) -> list[dict]:
    all_flat: dict[int, dict[str, Any]] = {}
    all_paths: set[str] = set()

    for snap in snapshots:
        flat = _flatten_params(snap.parameters_json or {})
        all_flat[snap.id] = flat
        all_paths.update(flat.keys())

    result = []
    for path in sorted(all_paths):
        values = {}
        for snap in snapshots:
            values[snap.id] = all_flat[snap.id].get(path)

        unique_values = set()
        for v in values.values():
            # Convert to a hashable representation for comparison
            try:
                unique_values.add(v)
            except TypeError:
                unique_values.add(str(v))

        result.append(
            {
                "parameter_path": path,
                "values": values,
                "changed": len(unique_values) > 1,
            }
        )
    return result


def _compute_embedding_diff(snapshots: list[AnalysisSnapshot]) -> list[dict]:
    all_names: set[str] = set()
    for snap in snapshots:
        if snap.embeddings_json:
            all_names.update(snap.embeddings_json.keys())

    result = []
    for name in sorted(all_names):
        dimensions: dict[int, int | None] = {}
        present_in: list[int] = []

        for snap in snapshots:
            emb = (snap.embeddings_json or {}).get(name)
            if emb is not None:
                present_in.append(snap.id)
                dimensions[snap.id] = emb.get("n_components") if isinstance(emb, dict) else None
            else:
                dimensions[snap.id] = None

        result.append(
            {
                "embedding_name": name,
                "dimensions": dimensions,
                "present_in": present_in,
            }
        )
    return result


def _compute_clustering_diff(snapshots: list[AnalysisSnapshot]) -> list[dict]:
    all_names: set[str] = set()
    for snap in snapshots:
        if snap.clusterings_json:
            all_names.update(snap.clusterings_json.keys())

    result = []
    for name in sorted(all_names):
        n_clusters: dict[int, int] = {}
        distributions: dict[int, dict[str, int]] = {}
        present_in: list[int] = []

        # Collect all cluster labels across all snapshots for this clustering
        all_labels: set[str] = set()
        for snap in snapshots:
            cl = (snap.clusterings_json or {}).get(name)
            if cl and isinstance(cl, dict):
                dist = cl.get("distribution", {})
                all_labels.update(str(k) for k in dist.keys())

        for snap in snapshots:
            cl = (snap.clusterings_json or {}).get(name)
            if cl and isinstance(cl, dict):
                present_in.append(snap.id)
                n_clusters[snap.id] = cl.get("n_clusters", 0)
                raw_dist = cl.get("distribution", {})
                # Use union of all labels with zeros for missing
                distributions[snap.id] = {
                    label: int(raw_dist.get(label, raw_dist.get(int(label) if label.isdigit() else label, 0)))
                    for label in sorted(all_labels)
                }
            else:
                n_clusters[snap.id] = 0
                distributions[snap.id] = {label: 0 for label in sorted(all_labels)}

        result.append(
            {
                "clustering_name": name,
                "n_clusters": n_clusters,
                "distributions": distributions,
                "present_in": present_in,
            }
        )
    return result


def _compute_command_log_diff(snapshots: list[AnalysisSnapshot]) -> list[dict] | None:
    # Only for Seurat snapshots
    has_seurat = any(s.object_type == "seurat" for s in snapshots)
    if not has_seurat:
        return None

    # Collect all unique command names in order
    all_commands: list[str] = []
    seen: set[str] = set()
    for snap in snapshots:
        for cmd in snap.command_log_json or []:
            name = cmd.get("name", "")
            if name and name not in seen:
                all_commands.append(name)
                seen.add(name)

    result = []
    for cmd_name in all_commands:
        present_in: list[int] = []
        params: dict[int, dict] = {}

        for snap in snapshots:
            cmd_entry = None
            for cmd in snap.command_log_json or []:
                if cmd.get("name") == cmd_name:
                    cmd_entry = cmd
                    break
            if cmd_entry:
                present_in.append(snap.id)
                params[snap.id] = cmd_entry.get("params", {})

        # Check if params differ across snapshots that have this command
        params_differ = False
        param_values = list(params.values())
        if len(param_values) > 1:
            params_differ = any(p != param_values[0] for p in param_values[1:])

        result.append(
            {
                "command_name": cmd_name,
                "present_in": present_in,
                "params_differ": params_differ,
                "params": params if params_differ else None,
            }
        )
    return result


def _compute_cell_count_series(snapshots: list[AnalysisSnapshot]) -> list[dict]:
    # Already sorted by created_at from compare_snapshots
    return [
        {
            "snapshot_id": snap.id,
            "label": snap.label,
            "cell_count": snap.cell_count or 0,
            "created_at": snap.created_at.isoformat(),
        }
        for snap in snapshots
    ]
