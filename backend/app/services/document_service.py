import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.document import Document
from app.models.file import File
from app.services.audit_service import log_action
from app import runtime

logger = logging.getLogger("bioaf.document_service")


class DocumentService:
    @staticmethod
    async def upload_document(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        filename: str,
        content: bytes,
        title: str | None = None,
        experiment_id: int | None = None,
        sample_id: int | None = None,
    ) -> Document:
        """Upload a PDF document, create file + document records."""
        from app.services.upload_service import UploadService

        file = await UploadService.simple_upload(session, org_id, user_id, filename, content, file_type="pdf")

        doc = Document(
            organization_id=org_id,
            file_id=file.id,
            title=title or filename,
            linked_experiment_id=experiment_id,
            linked_sample_id=sample_id,
        )
        session.add(doc)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="document",
            entity_id=doc.id,
            action="create",
            details={"title": doc.title, "filename": filename},
        )

        # Trigger background text extraction (fire-and-forget)
        runtime.spawn(DocumentService._extract_text_background(doc.id, content), role="document-text-extraction")

        return doc

    @staticmethod
    async def _extract_text_background(document_id: int, content: bytes) -> None:
        """Extract text from PDF content and update the document."""
        try:
            import pdfplumber
            import io

            extracted = ""
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        extracted += page_text + "\n"

            if extracted.strip():
                from app.database import async_session_factory

                async with async_session_factory() as session:
                    doc = await session.get(Document, document_id)
                    if doc:
                        doc.extracted_text = extracted.strip()
                        await session.commit()
                        logger.info("Text extracted for document %d (%d chars)", document_id, len(extracted))
        except ImportError:
            logger.warning("pdfplumber not installed, skipping text extraction for document %d", document_id)
        except Exception as e:
            logger.error("Text extraction failed for document %d: %s", document_id, e)

    @staticmethod
    async def search_documents(
        session: AsyncSession,
        org_id: int,
        query: str | None = None,
        experiment_id: int | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[Document], int]:
        """Search documents with PostgreSQL full-text search."""
        base = (
            select(Document)
            .options(selectinload(Document.file).selectinload(File.uploader))
            .where(Document.organization_id == org_id)
        )
        count_base = select(func.count(Document.id)).where(Document.organization_id == org_id)

        if experiment_id:
            base = base.where(Document.linked_experiment_id == experiment_id)
            count_base = count_base.where(Document.linked_experiment_id == experiment_id)

        if query:
            # PostgreSQL full-text search on extracted_text and title
            ts_query = func.plainto_tsquery("english", query)
            base = base.where(
                func.to_tsvector(
                    "english", func.coalesce(Document.title, "") + " " + func.coalesce(Document.extracted_text, "")
                ).op("@@")(ts_query)
            )
            count_base = count_base.where(
                func.to_tsvector(
                    "english", func.coalesce(Document.title, "") + " " + func.coalesce(Document.extracted_text, "")
                ).op("@@")(ts_query)
            )

        total_result = await session.execute(count_base)
        total = total_result.scalar() or 0

        offset = (page - 1) * page_size
        base = base.order_by(Document.created_at.desc()).offset(offset).limit(page_size)

        result = await session.execute(base)
        docs = list(result.scalars().all())
        return docs, total

    @staticmethod
    async def get_document(session: AsyncSession, document_id: int, org_id: int) -> Document | None:
        result = await session.execute(
            select(Document)
            .options(selectinload(Document.file).selectinload(File.uploader))
            .where(Document.id == document_id, Document.organization_id == org_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_document(
        session: AsyncSession, document_id: int, org_id: int, user_id: int, title: str | None = None
    ) -> Document | None:
        doc = await DocumentService.get_document(session, document_id, org_id)
        if not doc:
            return None

        previous = {"title": doc.title}
        if title is not None:
            doc.title = title

        await log_action(
            session,
            user_id=user_id,
            entity_type="document",
            entity_id=document_id,
            action="update",
            details={"title": title},
            previous_value=previous,
        )
        await session.flush()
        return doc

    @staticmethod
    async def link_document(
        session: AsyncSession,
        document_id: int,
        org_id: int,
        user_id: int,
        experiment_id: int | None = None,
        sample_id: int | None = None,
        pipeline_run_id: int | None = None,
    ) -> Document | None:
        doc = await DocumentService.get_document(session, document_id, org_id)
        if not doc:
            return None

        if experiment_id is not None:
            doc.linked_experiment_id = experiment_id
        if sample_id is not None:
            doc.linked_sample_id = sample_id
        if pipeline_run_id is not None:
            doc.linked_pipeline_run_id = pipeline_run_id

        await log_action(
            session,
            user_id=user_id,
            entity_type="document",
            entity_id=document_id,
            action="link",
            details={
                "experiment_id": experiment_id,
                "sample_id": sample_id,
                "pipeline_run_id": pipeline_run_id,
            },
        )
        await session.flush()
        return doc

    @staticmethod
    async def delete_document(session: AsyncSession, document_id: int, org_id: int, user_id: int) -> bool:
        doc = await DocumentService.get_document(session, document_id, org_id)
        if not doc:
            return False

        await log_action(
            session,
            user_id=user_id,
            entity_type="document",
            entity_id=document_id,
            action="delete",
            details={"title": doc.title},
        )
        await session.delete(doc)
        await session.flush()
        return True
