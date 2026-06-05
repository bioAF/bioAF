"""Scientific Decision Record service (ADR-063, ADR-064).

Owns the SDR lifecycle: creation (with org-scoped numbering via CodeService),
the guarded status machine, edits, owner reassignment, category vocabulary, and
the daily re-assessment trigger evaluation. The status machine is enforced here,
in the service layer, so invalid transitions are impossible regardless of caller
(ADR-063); the API maps the service exceptions to HTTP 422.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.sdr import ScientificDecisionRecord, SdrCategory, SdrStatusTransition
from app.services.audit_service import log_action
from app.services.code_service import CodeService
from app.services.notification_channels.in_app import InAppChannel

SDR_CODE_KIND = "sdr"

# The status machine (ADR-063). An invalid transition is one whose target is not
# in the source status's allowed set. ``superseded`` and ``repealed`` are terminal.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"active"},
    "active": {"flagged_for_review", "superseded", "repealed"},
    "flagged_for_review": {"active", "superseded", "repealed"},
    "superseded": set(),
    "repealed": set(),
}

# Statuses hidden from the default browser view (AC-C10).
HISTORICAL_STATUSES = ("superseded", "repealed")


class InvalidTransitionError(Exception):
    """Raised when a status transition is not permitted by the status machine."""


class TransitionNoteRequiredError(Exception):
    """Raised when flagged_for_review -> active is attempted without a note."""


class SupersededByRequiredError(Exception):
    """Raised when a -> superseded transition lacks a valid superseding SDR."""


class CategoryInUseError(Exception):
    """Raised when deleting a category that still has SDRs assigned."""


class SdrReadOnlyError(Exception):
    """Raised when editing a terminal (superseded/repealed) SDR."""


class SdrService:
    # ------------------------------------------------------------------ reads
    @staticmethod
    async def get_sdr(
        session: AsyncSession, *, sdr_id: int, org_id: int
    ) -> ScientificDecisionRecord | None:
        result = await session.execute(
            select(ScientificDecisionRecord)
            .options(
                selectinload(ScientificDecisionRecord.category),
                selectinload(ScientificDecisionRecord.owner),
                selectinload(ScientificDecisionRecord.created_by),
                selectinload(ScientificDecisionRecord.superseded_by),
                selectinload(ScientificDecisionRecord.supersedes),
                selectinload(ScientificDecisionRecord.transitions).selectinload(
                    SdrStatusTransition.transitioned_by
                ),
            )
            .where(
                ScientificDecisionRecord.id == sdr_id,
                ScientificDecisionRecord.organization_id == org_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_sdrs(
        session: AsyncSession,
        *,
        org_id: int,
        statuses: list[str] | None = None,
        category_ids: list[int] | None = None,
        owner_user_id: int | None = None,
        query: str | None = None,
        include_historical: bool = False,
        sort: str = "number",
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[ScientificDecisionRecord], int]:
        base = select(ScientificDecisionRecord).where(
            ScientificDecisionRecord.organization_id == org_id
        )
        count_base = (
            select(func.count())
            .select_from(ScientificDecisionRecord)
            .where(ScientificDecisionRecord.organization_id == org_id)
        )

        def _apply(stmt):
            if statuses:
                stmt = stmt.where(ScientificDecisionRecord.status.in_(statuses))
            elif not include_historical:
                stmt = stmt.where(ScientificDecisionRecord.status.notin_(HISTORICAL_STATUSES))
            if category_ids:
                stmt = stmt.where(ScientificDecisionRecord.category_id.in_(category_ids))
            if owner_user_id is not None:
                stmt = stmt.where(ScientificDecisionRecord.owner_user_id == owner_user_id)
            if query:
                pattern = f"%{query.strip()}%"
                stmt = stmt.where(
                    ScientificDecisionRecord.title.ilike(pattern)
                    | ScientificDecisionRecord.decision.ilike(pattern)
                    | ScientificDecisionRecord.justification.ilike(pattern)
                )
            return stmt

        base = _apply(base)
        count_base = _apply(count_base)

        total = (await session.execute(count_base)).scalar() or 0

        order = {
            "number": ScientificDecisionRecord.sdr_number.desc(),
            "title": ScientificDecisionRecord.title.asc(),
            "updated": ScientificDecisionRecord.updated_at.desc(),
        }.get(sort, ScientificDecisionRecord.sdr_number.desc())

        offset = (max(page, 1) - 1) * page_size
        base = (
            base.options(
                selectinload(ScientificDecisionRecord.category),
                selectinload(ScientificDecisionRecord.owner),
            )
            .order_by(order)
            .offset(offset)
            .limit(page_size)
        )
        rows = list((await session.execute(base)).scalars().all())
        return rows, total

    # ------------------------------------------------------------- mutations
    @staticmethod
    async def create_sdr(
        session: AsyncSession,
        *,
        org_id: int,
        user_id: int,
        title: str,
        decision: str,
        justification: str,
        category_id: int | None = None,
        trigger_date: date | None = None,
    ) -> ScientificDecisionRecord:
        number = await CodeService._next_counter(session, org_id, SDR_CODE_KIND)
        row = ScientificDecisionRecord(
            organization_id=org_id,
            sdr_number=number,
            title=title.strip(),
            status="draft",
            category_id=category_id,
            decision=decision,
            justification=justification,
            owner_user_id=user_id,
            created_by_user_id=user_id,
            trigger_date=trigger_date,
        )
        session.add(row)
        await session.flush()
        await log_action(
            session,
            user_id=user_id,
            entity_type="sdr",
            entity_id=row.id,
            action="created",
            details={"sdr_number": number, "title": row.title},
        )
        await session.flush()
        return row

    @staticmethod
    async def update_sdr(
        session: AsyncSession,
        *,
        org_id: int,
        user_id: int,
        sdr_id: int,
        title: str | None = None,
        category_id: int | None = None,
        category_id_set: bool = True,
        decision: str | None = None,
        justification: str | None = None,
        trigger_date: date | None = None,
        trigger_date_set: bool = True,
    ) -> ScientificDecisionRecord | None:
        """Update editable fields.

        ``draft``: all fields editable. ``active``/``flagged_for_review``: title,
        category, and trigger date editable; decision/justification edits are
        recorded as an append-only note row (a record of the edit, not a status
        change) per F-LKC-02. Terminal SDRs are read-only.

        ``category_id_set`` / ``trigger_date_set`` distinguish "set to NULL" from
        "field omitted" so a PATCH can clear a category or trigger date.
        """
        row = await SdrService.get_sdr(session, sdr_id=sdr_id, org_id=org_id)
        if row is None:
            return None
        if row.status in HISTORICAL_STATUSES:
            raise SdrReadOnlyError(f"SDR {sdr_id} is {row.status} and cannot be edited")

        is_draft = row.status == "draft"
        prev_decision, prev_justification = row.decision, row.justification

        if title is not None:
            row.title = title.strip()
        if category_id_set:
            row.category_id = category_id
        if trigger_date_set and trigger_date != row.trigger_date:
            row.trigger_date = trigger_date
            # Re-arm the once-only 7-day warning for the new cycle (ADR-064).
            row.trigger_warning_sent_at = None

        decision_changed = decision is not None and decision != row.decision
        justification_changed = justification is not None and justification != row.justification
        if decision is not None:
            row.decision = decision
        if justification is not None:
            row.justification = justification

        # On an active/flagged SDR, record decision/justification edits as a note.
        if not is_draft and (decision_changed or justification_changed):
            parts = []
            if decision_changed:
                parts.append(f"decision (was: {prev_decision})")
            if justification_changed:
                parts.append(f"justification (was: {prev_justification})")
            session.add(
                SdrStatusTransition(
                    sdr_id=row.id,
                    from_status=row.status,
                    to_status=row.status,
                    note="Edited " + "; ".join(parts),
                    transitioned_by_user_id=user_id,
                )
            )

        await log_action(
            session,
            user_id=user_id,
            entity_type="sdr",
            entity_id=row.id,
            action="updated",
            details={"sdr_number": row.sdr_number},
        )
        await session.flush()
        return row

    @staticmethod
    async def transition(
        session: AsyncSession,
        *,
        org_id: int,
        user_id: int | None,
        sdr_id: int,
        to_status: str,
        note: str | None = None,
        superseded_by_sdr_id: int | None = None,
        system: bool = False,
    ) -> ScientificDecisionRecord | None:
        """Move an SDR to ``to_status`` through the status-machine guard.

        Raises InvalidTransitionError, TransitionNoteRequiredError, or
        SupersededByRequiredError when the move is not allowed. ``system=True``
        marks a system-initiated transition (no acting user) for the trigger loop.
        """
        row = await SdrService.get_sdr(session, sdr_id=sdr_id, org_id=org_id)
        if row is None:
            return None

        from_status = row.status
        if to_status not in ALLOWED_TRANSITIONS.get(from_status, set()):
            raise InvalidTransitionError(f"{from_status} -> {to_status} is not permitted")

        if from_status == "flagged_for_review" and to_status == "active" and not (note and note.strip()):
            raise TransitionNoteRequiredError("A 'decision upheld' note is required")

        if to_status == "superseded":
            target = None
            if superseded_by_sdr_id is not None:
                target = await SdrService.get_sdr(
                    session, sdr_id=superseded_by_sdr_id, org_id=org_id
                )
            if target is None or target.id == row.id:
                raise SupersededByRequiredError(
                    "A valid superseding SDR in the same organization is required"
                )
            row.superseded_by_sdr_id = target.id
            if target.supersedes_sdr_id is None:
                target.supersedes_sdr_id = row.id

        row.status = to_status
        session.add(
            SdrStatusTransition(
                sdr_id=row.id,
                from_status=from_status,
                to_status=to_status,
                note=note,
                transitioned_by_user_id=None if system else user_id,
            )
        )
        await log_action(
            session,
            user_id=None if system else user_id,
            entity_type="sdr",
            entity_id=row.id,
            action="status_transitioned",
            details={
                "from": from_status,
                "to": to_status,
                "system": system,
                "superseded_by_sdr_id": superseded_by_sdr_id,
            },
        )
        await session.flush()
        return row

    @staticmethod
    async def reassign_owner(
        session: AsyncSession,
        *,
        org_id: int,
        user_id: int,
        sdr_id: int,
        new_owner_user_id: int,
    ) -> ScientificDecisionRecord | None:
        row = await SdrService.get_sdr(session, sdr_id=sdr_id, org_id=org_id)
        if row is None:
            return None
        previous_owner = row.owner_user_id
        row.owner_user_id = new_owner_user_id
        await log_action(
            session,
            user_id=user_id,
            entity_type="sdr",
            entity_id=row.id,
            action="owner_reassigned",
            details={"sdr_number": row.sdr_number, "new_owner_user_id": new_owner_user_id},
            previous_value={"owner_user_id": previous_owner},
        )
        await InAppChannel.deliver(
            session,
            org_id=org_id,
            user_id=new_owner_user_id,
            event_type="sdr_owner_assigned",
            title="You are now an SDR owner",
            message=f"You have been assigned as owner of SDR-{row.sdr_number:03d}: {row.title}",
            severity="info",
            metadata={"sdr_id": row.id},
        )
        await session.flush()
        return row

    @staticmethod
    async def delete_sdr(
        session: AsyncSession, *, org_id: int, user_id: int, sdr_id: int
    ) -> bool:
        row = await SdrService.get_sdr(session, sdr_id=sdr_id, org_id=org_id)
        if row is None:
            return False
        await log_action(
            session,
            user_id=user_id,
            entity_type="sdr",
            entity_id=row.id,
            action="deleted",
            details={
                "sdr_number": row.sdr_number,
                "title": row.title,
                "status": row.status,
                "decision": row.decision,
                "justification": row.justification,
            },
        )
        # Detach any other SDR that points at this one so FKs do not block delete.
        for other in (
            await session.execute(
                select(ScientificDecisionRecord).where(
                    (ScientificDecisionRecord.superseded_by_sdr_id == row.id)
                    | (ScientificDecisionRecord.supersedes_sdr_id == row.id)
                )
            )
        ).scalars().all():
            if other.superseded_by_sdr_id == row.id:
                other.superseded_by_sdr_id = None
            if other.supersedes_sdr_id == row.id:
                other.supersedes_sdr_id = None
        await session.execute(
            SdrStatusTransition.__table__.delete().where(SdrStatusTransition.sdr_id == row.id)
        )
        await session.delete(row)
        await session.flush()
        return True

    # ------------------------------------------------------------ categories
    @staticmethod
    async def list_categories(session: AsyncSession, *, org_id: int) -> list[SdrCategory]:
        rows = (
            await session.execute(
                select(SdrCategory)
                .where(SdrCategory.organization_id == org_id)
                .order_by(func.lower(SdrCategory.name))
            )
        ).scalars().all()
        return list(rows)

    @staticmethod
    async def get_category(
        session: AsyncSession, *, org_id: int, category_id: int
    ) -> SdrCategory | None:
        return (
            await session.execute(
                select(SdrCategory).where(
                    SdrCategory.id == category_id, SdrCategory.organization_id == org_id
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def create_category(
        session: AsyncSession, *, org_id: int, user_id: int, name: str
    ) -> SdrCategory:
        row = SdrCategory(organization_id=org_id, name=name.strip(), created_by_user_id=user_id)
        session.add(row)
        await session.flush()
        await log_action(
            session,
            user_id=user_id,
            entity_type="sdr_category",
            entity_id=row.id,
            action="created",
            details={"name": row.name},
        )
        await session.flush()
        return row

    @staticmethod
    async def delete_category(
        session: AsyncSession, *, org_id: int, user_id: int, category_id: int
    ) -> bool:
        row = await SdrService.get_category(session, org_id=org_id, category_id=category_id)
        if row is None:
            return False
        in_use = (
            await session.execute(
                select(func.count())
                .select_from(ScientificDecisionRecord)
                .where(ScientificDecisionRecord.category_id == category_id)
            )
        ).scalar() or 0
        if in_use:
            raise CategoryInUseError(f"Category {category_id} has {in_use} SDR(s) assigned")
        await log_action(
            session,
            user_id=user_id,
            entity_type="sdr_category",
            entity_id=row.id,
            action="deleted",
            details={"name": row.name},
        )
        await session.delete(row)
        await session.flush()
        return True

    # --------------------------------------------------------------- triggers
    @staticmethod
    async def evaluate_triggers(
        session: AsyncSession, *, org_id: int | None = None, today: date | None = None
    ) -> dict[str, int]:
        """Daily re-assessment trigger evaluation (ADR-064).

        For each ``active`` SDR with a ``trigger_date``: if the date has been
        reached, transition it to ``flagged_for_review`` (system) and notify the
        owner; if the date is within the next 7 days and no warning has been sent,
        send the once-only advance warning. Returns ``{"flagged": n, "warned": n}``.
        """
        if today is None:
            today = datetime.now(UTC).date()
        warn_cutoff = today + timedelta(days=7)

        stmt = select(ScientificDecisionRecord).where(
            ScientificDecisionRecord.status == "active",
            ScientificDecisionRecord.trigger_date.is_not(None),
            ScientificDecisionRecord.trigger_date <= warn_cutoff,
        )
        if org_id is not None:
            stmt = stmt.where(ScientificDecisionRecord.organization_id == org_id)
        rows = (await session.execute(stmt)).scalars().all()

        flagged = warned = 0
        for row in rows:
            if row.trigger_date <= today:
                await SdrService.transition(
                    session,
                    org_id=row.organization_id,
                    user_id=None,
                    sdr_id=row.id,
                    to_status="flagged_for_review",
                    note="Automatic transition: review trigger date reached",
                    system=True,
                )
                await InAppChannel.deliver(
                    session,
                    org_id=row.organization_id,
                    user_id=row.owner_user_id,
                    event_type="sdr_reassessment_flagged",
                    title="SDR flagged for review",
                    message=(
                        f"SDR-{row.sdr_number:03d}: {row.title} has been flagged for review. "
                        "The re-assessment date you set has been reached."
                    ),
                    severity="warning",
                    metadata={"sdr_id": row.id},
                )
                flagged += 1
            elif row.trigger_warning_sent_at is None:
                await InAppChannel.deliver(
                    session,
                    org_id=row.organization_id,
                    user_id=row.owner_user_id,
                    event_type="sdr_reassessment_warning",
                    title="SDR re-assessment due soon",
                    message=f"SDR-{row.sdr_number:03d}: {row.title} is due for re-assessment in 7 days.",
                    severity="info",
                    metadata={"sdr_id": row.id},
                )
                row.trigger_warning_sent_at = datetime.now(UTC)
                warned += 1
        await session.flush()
        return {"flagged": flagged, "warned": warned}
