"""Lab Documents service layer (ADR-059, ADR-060, ADR-061).

Pure DB/business logic. GCS bytes are handled at the API layer through the
existing signed-URL UploadService; callers pass an already-resolved ``gcs_uri``
and the GCS-reported ``md5_checksum``. This mirrors how FileService (DB records)
is split from UploadService (GCS I/O) elsewhere in the codebase.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.lab_document import (
    LabDocument,
    LabDocumentTag,
    LabDocumentTagAssignment,
    LabDocumentVersion,
)
from app.services.audit_service import log_action


class TagInUseError(Exception):
    """Raised when deleting a tag that is still assigned to one or more documents."""

    def __init__(self, document_titles: list[str]):
        self.document_titles = document_titles
        super().__init__(f"Tag is in use by {len(document_titles)} document(s)")


class LabDocumentService:
    @staticmethod
    async def create_document(
        session: AsyncSession,
        *,
        org_id: int,
        user_id: int,
        title: str,
        file_name: str,
        gcs_uri: str,
        description: str | None = None,
        file_size_bytes: int | None = None,
        mime_type: str | None = None,
        md5_checksum: str | None = None,
        tag_ids: list[int] | None = None,
    ) -> LabDocument:
        doc = LabDocument(
            organization_id=org_id,
            title=title,
            description=description,
            gcs_uri=gcs_uri,
            current_version=1,
            file_name=file_name,
            file_size_bytes=file_size_bytes,
            mime_type=mime_type,
            md5_checksum=md5_checksum,
            created_by_user_id=user_id,
        )
        session.add(doc)
        await session.flush()

        session.add(
            LabDocumentVersion(
                document_id=doc.id,
                version_number=1,
                gcs_uri=gcs_uri,
                file_name=file_name,
                file_size_bytes=file_size_bytes,
                md5_checksum=md5_checksum,
                uploaded_by_user_id=user_id,
            )
        )
        await LabDocumentService._assign_tags(session, org_id, doc.id, tag_ids or [])

        await log_action(
            session,
            user_id=user_id,
            entity_type="lab_document",
            entity_id=doc.id,
            action="created",
            details={"title": title, "file_name": file_name, "tag_ids": tag_ids or []},
        )
        await session.flush()
        return doc

    @staticmethod
    async def add_version(
        session: AsyncSession,
        *,
        org_id: int,
        user_id: int,
        document_id: int,
        gcs_uri: str,
        file_name: str,
        file_size_bytes: int | None = None,
        md5_checksum: str | None = None,
        change_note: str | None = None,
    ) -> LabDocument | None:
        doc = await LabDocumentService.get_document(session, document_id=document_id, org_id=org_id)
        if doc is None:
            return None

        new_number = doc.current_version + 1
        session.add(
            LabDocumentVersion(
                document_id=doc.id,
                version_number=new_number,
                gcs_uri=gcs_uri,
                file_name=file_name,
                file_size_bytes=file_size_bytes,
                md5_checksum=md5_checksum,
                change_note=change_note,
                uploaded_by_user_id=user_id,
            )
        )
        doc.current_version = new_number
        doc.gcs_uri = gcs_uri
        doc.file_name = file_name
        doc.file_size_bytes = file_size_bytes
        doc.md5_checksum = md5_checksum

        await log_action(
            session,
            user_id=user_id,
            entity_type="lab_document",
            entity_id=doc.id,
            action="new_version_uploaded",
            details={"version_number": new_number, "change_note": change_note},
        )
        await session.flush()
        return doc

    @staticmethod
    async def update_metadata(
        session: AsyncSession,
        *,
        org_id: int,
        user_id: int,
        document_id: int,
        title: str | None = None,
        description: str | None = None,
        tag_ids: list[int] | None = None,
    ) -> LabDocument | None:
        doc = await LabDocumentService.get_document(session, document_id=document_id, org_id=org_id)
        if doc is None:
            return None

        previous = {"title": doc.title, "description": doc.description}
        if title is not None:
            doc.title = title
        if description is not None:
            doc.description = description
        if tag_ids is not None:
            await LabDocumentService._replace_tags(session, org_id, doc, tag_ids)

        await log_action(
            session,
            user_id=user_id,
            entity_type="lab_document",
            entity_id=doc.id,
            action="metadata_updated",
            details={"title": title, "description": description, "tag_ids": tag_ids},
            previous_value=previous,
        )
        await session.flush()
        return doc

    @staticmethod
    async def set_archived(
        session: AsyncSession, *, org_id: int, user_id: int, document_id: int, archived: bool
    ) -> LabDocument | None:
        doc = await LabDocumentService.get_document(session, document_id=document_id, org_id=org_id)
        if doc is None:
            return None
        doc.is_archived = archived
        await log_action(
            session,
            user_id=user_id,
            entity_type="lab_document",
            entity_id=doc.id,
            action="archived" if archived else "restored",
            details={"is_archived": archived},
        )
        await session.flush()
        return doc

    @staticmethod
    async def get_document(session: AsyncSession, *, document_id: int, org_id: int) -> LabDocument | None:
        result = await session.execute(
            select(LabDocument)
            .options(
                selectinload(LabDocument.versions),
                selectinload(LabDocument.tag_assignments),
                selectinload(LabDocument.created_by),
            )
            .where(LabDocument.id == document_id, LabDocument.organization_id == org_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_documents(
        session: AsyncSession,
        *,
        org_id: int,
        tag_ids: list[int] | None = None,
        query: str | None = None,
        include_archived: bool = False,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[LabDocument], int]:
        base = select(LabDocument).where(LabDocument.organization_id == org_id)
        count_base = select(func.count(func.distinct(LabDocument.id))).where(
            LabDocument.organization_id == org_id
        )

        if not include_archived:
            base = base.where(LabDocument.is_archived.is_(False))
            count_base = count_base.where(LabDocument.is_archived.is_(False))

        if tag_ids:
            base = base.join(LabDocumentTagAssignment).where(LabDocumentTagAssignment.tag_id.in_(tag_ids))
            count_base = count_base.join(LabDocumentTagAssignment).where(
                LabDocumentTagAssignment.tag_id.in_(tag_ids)
            )

        if query:
            ts_query = func.plainto_tsquery("english", query)
            text_expr = func.to_tsvector(
                "english",
                func.coalesce(LabDocument.title, "") + " " + func.coalesce(LabDocument.description, ""),
            )
            base = base.where(text_expr.op("@@")(ts_query))
            count_base = count_base.where(text_expr.op("@@")(ts_query))

        total = (await session.execute(count_base)).scalar() or 0
        offset = (page - 1) * page_size
        base = (
            base.options(selectinload(LabDocument.tag_assignments), selectinload(LabDocument.created_by))
            .order_by(LabDocument.updated_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        docs = list((await session.execute(base)).scalars().unique().all())
        return docs, total

    # --- tag assignment helpers ---------------------------------------------

    @staticmethod
    async def _assign_tags(session: AsyncSession, org_id: int, document_id: int, tag_ids: list[int]) -> None:
        if not tag_ids:
            return
        valid = (
            await session.execute(
                select(LabDocumentTag.id).where(
                    LabDocumentTag.organization_id == org_id, LabDocumentTag.id.in_(tag_ids)
                )
            )
        ).scalars().all()
        for tag_id in valid:
            session.add(LabDocumentTagAssignment(document_id=document_id, tag_id=tag_id))
        await session.flush()

    @staticmethod
    async def _replace_tags(
        session: AsyncSession, org_id: int, doc: LabDocument, tag_ids: list[int]
    ) -> None:
        # Mutate through the relationship so delete-orphan removes stale rows
        # cleanly (deleting children still held by the parent collection would
        # otherwise be a no-op at flush).
        doc.tag_assignments.clear()
        await session.flush()
        valid = (
            await session.execute(
                select(LabDocumentTag.id).where(
                    LabDocumentTag.organization_id == org_id, LabDocumentTag.id.in_(tag_ids)
                )
            )
        ).scalars().all()
        for tag_id in valid:
            doc.tag_assignments.append(LabDocumentTagAssignment(tag_id=tag_id))
        await session.flush()


class LabDocumentTagService:
    @staticmethod
    async def create_tag(session: AsyncSession, *, org_id: int, user_id: int, name: str) -> LabDocumentTag:
        tag = LabDocumentTag(organization_id=org_id, name=name, created_by_user_id=user_id)
        session.add(tag)
        await session.flush()
        await log_action(
            session,
            user_id=user_id,
            entity_type="lab_document_tag",
            entity_id=tag.id,
            action="created",
            details={"name": name},
        )
        await session.flush()
        return tag

    @staticmethod
    async def list_tags(session: AsyncSession, *, org_id: int) -> list[LabDocumentTag]:
        rows = await session.execute(
            select(LabDocumentTag)
            .where(LabDocumentTag.organization_id == org_id)
            .order_by(LabDocumentTag.name.asc())
        )
        return list(rows.scalars().all())

    @staticmethod
    async def delete_tag(session: AsyncSession, *, org_id: int, user_id: int, tag_id: int) -> bool:
        tag = (
            await session.execute(
                select(LabDocumentTag).where(
                    LabDocumentTag.id == tag_id, LabDocumentTag.organization_id == org_id
                )
            )
        ).scalar_one_or_none()
        if tag is None:
            return False

        in_use = (
            await session.execute(
                select(LabDocument.title)
                .join(LabDocumentTagAssignment, LabDocumentTagAssignment.document_id == LabDocument.id)
                .where(LabDocumentTagAssignment.tag_id == tag_id)
            )
        ).scalars().all()
        if in_use:
            raise TagInUseError(list(in_use))

        await log_action(
            session,
            user_id=user_id,
            entity_type="lab_document_tag",
            entity_id=tag.id,
            action="deleted",
            details={"name": tag.name},
        )
        await session.delete(tag)
        await session.flush()
        return True
