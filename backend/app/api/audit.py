import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_session
from app.api.dependencies import require_role
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas.audit import AuditLogEntry, AuditLogResponse
from app.schemas.experiment import UserSummary

router = APIRouter(prefix="/api/audit", tags=["audit"])


def _build_query(
    entity_type: str | None,
    action: str | None,
    user_id: int | None,
    start_date: date | None,
    end_date: date | None,
):
    query = select(AuditLog)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if action:
        query = query.where(AuditLog.action == action)
    if user_id is not None:
        query = query.where(AuditLog.user_id == user_id)
    if start_date:
        query = query.where(func.date(AuditLog.timestamp) >= start_date)
    if end_date:
        query = query.where(func.date(AuditLog.timestamp) <= end_date)
    return query


async def _resolve_user(session: AsyncSession, uid: int | None) -> UserSummary | None:
    if uid is None:
        return None
    result = await session.execute(select(User).where(User.id == uid))
    u = result.scalar_one_or_none()
    if u is None:
        return None
    return UserSummary(id=u.id, email=u.email, name=u.name)


@router.get("/export")
async def export_audit_log(
    format: str = Query("csv"),
    entity_type: str | None = Query(None),
    action: str | None = Query(None),
    user_id: int | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    current_user: dict = require_role("admin"),
    session: AsyncSession = Depends(get_session),
):
    query = _build_query(entity_type, action, user_id, start_date, end_date)
    result = await session.execute(query.order_by(AuditLog.timestamp.desc()).limit(10000))
    entries = list(result.scalars().all())

    # Resolve user emails in bulk
    user_ids = {e.user_id for e in entries if e.user_id is not None}
    user_map: dict[int, str] = {}
    if user_ids:
        users_result = await session.execute(select(User).where(User.id.in_(user_ids)))
        for u in users_result.scalars().all():
            user_map[u.id] = u.email

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "user_email", "entity_type", "entity_id", "action", "details"])
    for e in entries:
        writer.writerow(
            [
                e.timestamp.isoformat() if e.timestamp else "",
                user_map.get(e.user_id, "") if e.user_id else "system",
                e.entity_type,
                e.entity_id,
                e.action,
                str(e.details_json) if e.details_json else "",
            ]
        )

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
    )


@router.get("", response_model=AuditLogResponse)
async def list_audit_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    entity_type: str | None = Query(None),
    action: str | None = Query(None),
    user_id: int | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    current_user: dict = require_role("admin"),
    session: AsyncSession = Depends(get_session),
):
    query = _build_query(entity_type, action, user_id, start_date, end_date)

    count_result = await session.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar()

    result = await session.execute(
        query.order_by(AuditLog.timestamp.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    entries = list(result.scalars().all())

    # Resolve user emails in bulk
    user_ids = {e.user_id for e in entries if e.user_id is not None}
    user_map: dict[int, User] = {}
    if user_ids:
        users_result = await session.execute(select(User).where(User.id.in_(user_ids)))
        for u in users_result.scalars().all():
            user_map[u.id] = u

    return AuditLogResponse(
        entries=[
            AuditLogEntry(
                id=e.id,
                timestamp=e.timestamp,
                user=UserSummary(id=u.id, email=u.email, name=u.name)
                if (u := user_map.get(e.user_id)) is not None  # type: ignore[arg-type]
                else None,
                entity_type=e.entity_type,
                entity_id=e.entity_id,
                action=e.action,
                details=e.details_json,
                previous_value=e.previous_value_json,
            )
            for e in entries
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
