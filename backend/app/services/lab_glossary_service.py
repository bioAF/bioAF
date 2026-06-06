"""Lab Glossary term service (ADR-062).

Manual term CRUD plus the read paths used by the browser and global search. The
duplicate check is case-insensitive to match the functional unique index
``uq_lab_glossary_terms_org_lower_term``. Every mutation is audit-logged; an edit
copies the prior values into ``lab_glossary_term_history`` before overwriting.
Committed entries from scans/imports are written by the proposal-review path in
``lab_glossary_scan_service``; this service owns the manual + read surface.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.lab_glossary import LabGlossaryTerm, LabGlossaryTermHistory
from app.services.audit_service import log_action


class DuplicateTermError(Exception):
    """Raised when a term already exists for the org (case-insensitive)."""

    def __init__(self, existing_term_id: int, term: str):
        self.existing_term_id = existing_term_id
        self.term = term
        super().__init__(f"Term '{term}' already exists (id={existing_term_id})")


class LabGlossaryService:
    @staticmethod
    async def get_by_term(session: AsyncSession, *, org_id: int, term: str) -> LabGlossaryTerm | None:
        """Case-insensitive lookup used for duplicate detection and scan dedup."""
        result = await session.execute(
            select(LabGlossaryTerm).where(
                LabGlossaryTerm.organization_id == org_id,
                func.lower(LabGlossaryTerm.term) == term.strip().lower(),
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_term(
        session: AsyncSession,
        *,
        org_id: int,
        user_id: int,
        term: str,
        definition: str,
        aliases: list[str] | None = None,
        category: str | None = None,
        context: str | None = None,
        source: str = "manual",
    ) -> LabGlossaryTerm:
        existing = await LabGlossaryService.get_by_term(session, org_id=org_id, term=term)
        if existing is not None:
            raise DuplicateTermError(existing.id, term)

        row = LabGlossaryTerm(
            organization_id=org_id,
            term=term.strip(),
            definition=definition,
            aliases=aliases or None,
            category=category,
            context=context,
            source=source,
            created_by_user_id=user_id,
        )
        session.add(row)
        await session.flush()
        await log_action(
            session,
            user_id=user_id,
            entity_type="lab_glossary_term",
            entity_id=row.id,
            action="created",
            details={"term": row.term, "source": source},
        )
        await session.flush()
        return row

    @staticmethod
    async def update_term(
        session: AsyncSession,
        *,
        org_id: int,
        user_id: int,
        term_id: int,
        term: str | None = None,
        definition: str | None = None,
        aliases: list[str] | None = None,
        category: str | None = None,
        context: str | None = None,
    ) -> LabGlossaryTerm | None:
        row = await LabGlossaryService.get_term(session, term_id=term_id, org_id=org_id)
        if row is None:
            return None

        # Snapshot prior values into history before overwriting.
        session.add(
            LabGlossaryTermHistory(
                term_id=row.id,
                previous_definition=row.definition,
                previous_aliases=row.aliases,
                previous_category=row.category,
                previous_context=row.context,
                changed_by_user_id=user_id,
            )
        )

        previous = {"term": row.term, "definition": row.definition}
        if term is not None and term.strip().lower() != row.term.lower():
            dup = await LabGlossaryService.get_by_term(session, org_id=org_id, term=term)
            if dup is not None and dup.id != row.id:
                raise DuplicateTermError(dup.id, term)
            row.term = term.strip()
        if definition is not None:
            row.definition = definition
        if aliases is not None:
            row.aliases = aliases or None
        if category is not None:
            row.category = category
        if context is not None:
            row.context = context

        await log_action(
            session,
            user_id=user_id,
            entity_type="lab_glossary_term",
            entity_id=row.id,
            action="updated",
            details={"term": row.term},
            previous_value=previous,
        )
        await session.flush()
        return row

    @staticmethod
    async def delete_term(session: AsyncSession, *, org_id: int, user_id: int, term_id: int) -> bool:
        row = await LabGlossaryService.get_term(session, term_id=term_id, org_id=org_id)
        if row is None:
            return False
        await log_action(
            session,
            user_id=user_id,
            entity_type="lab_glossary_term",
            entity_id=row.id,
            action="deleted",
            details={
                "term": row.term,
                "definition": row.definition,
                "aliases": row.aliases,
                "category": row.category,
                "context": row.context,
                "source": row.source,
            },
        )
        # Detach history rows so the FK does not block the delete; the deleted
        # content is preserved in the audit log above.
        await session.execute(LabGlossaryTermHistory.__table__.delete().where(LabGlossaryTermHistory.term_id == row.id))
        await session.delete(row)
        await session.flush()
        return True

    @staticmethod
    async def get_term(session: AsyncSession, *, term_id: int, org_id: int) -> LabGlossaryTerm | None:
        result = await session.execute(
            select(LabGlossaryTerm)
            .options(selectinload(LabGlossaryTerm.created_by))
            .where(LabGlossaryTerm.id == term_id, LabGlossaryTerm.organization_id == org_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_terms(
        session: AsyncSession,
        *,
        org_id: int,
        category: str | None = None,
        source: str | None = None,
        query: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[LabGlossaryTerm], int]:
        base = select(LabGlossaryTerm).where(LabGlossaryTerm.organization_id == org_id)
        count_base = select(func.count()).select_from(LabGlossaryTerm).where(LabGlossaryTerm.organization_id == org_id)

        if category:
            base = base.where(LabGlossaryTerm.category == category)
            count_base = count_base.where(LabGlossaryTerm.category == category)
        if source:
            base = base.where(LabGlossaryTerm.source == source)
            count_base = count_base.where(LabGlossaryTerm.source == source)
        if query:
            pattern = f"%{query.strip()}%"
            cond = (
                LabGlossaryTerm.term.ilike(pattern)
                | LabGlossaryTerm.definition.ilike(pattern)
                | func.array_to_string(LabGlossaryTerm.aliases, " ").ilike(pattern)
                | LabGlossaryTerm.context.ilike(pattern)
            )
            base = base.where(cond)
            count_base = count_base.where(cond)

        total = (await session.execute(count_base)).scalar() or 0
        offset = (page - 1) * page_size
        base = (
            base.options(selectinload(LabGlossaryTerm.created_by))
            .order_by(func.lower(LabGlossaryTerm.term).asc())
            .offset(offset)
            .limit(page_size)
        )
        rows = list((await session.execute(base)).scalars().all())
        return rows, total
