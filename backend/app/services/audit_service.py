from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


async def log_action(
    session: AsyncSession,
    user_id: int | None,
    entity_type: str,
    entity_id: int,
    action: str,
    details: dict | None = None,
    previous_value: dict | None = None,
    snapshot: dict | None = None,
) -> None:
    """Write to audit log and optional entity snapshot. MUST be called within
    the same transaction as the state change. If this write fails, the
    transaction rolls back."""
    entry = AuditLog(
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        details_json=details,
        previous_value_json=previous_value,
    )
    session.add(entry)

    if snapshot is not None:
        from app.models.entity_snapshot import EntitySnapshot

        snap = EntitySnapshot(
            entity_type=entity_type,
            entity_id=entity_id,
            snapshot_json=snapshot,
        )
        session.add(snap)
    # Do NOT commit here — let the caller's transaction handle it
