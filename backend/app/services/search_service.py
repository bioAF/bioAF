import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.experiment import Experiment
from app.models.file import File
from app.models.sample import Sample

logger = logging.getLogger("bioaf.search_service")


class SearchService:
    @staticmethod
    async def search(
        session: AsyncSession,
        org_id: int,
        query: str,
        entity_types: list[str] | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[dict], int]:
        """Unified search across experiments, samples, files, documents.
        Uses PostgreSQL full-text search. Delegates to Meilisearch if enabled."""

        # Check if Meilisearch is enabled
        if await SearchService._is_meilisearch_enabled(session):
            return await SearchService._search_meilisearch(org_id, query, entity_types, page, page_size)

        # Fall back to PostgreSQL full-text search
        return await SearchService._search_postgres(session, org_id, query, entity_types, page, page_size)

    @staticmethod
    async def _search_postgres(
        session: AsyncSession,
        org_id: int,
        query: str,
        entity_types: list[str] | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict], int]:
        results = []
        types = entity_types or ["experiment", "sample", "file", "document"]
        ts_query = func.plainto_tsquery("english", query)

        if "experiment" in types:
            exp_result = await session.execute(
                select(Experiment)
                .where(
                    Experiment.organization_id == org_id,
                    func.to_tsvector(
                        "english", func.coalesce(Experiment.name, "") + " " + func.coalesce(Experiment.description, "")
                    ).op("@@")(ts_query),
                )
                .limit(page_size)
            )
            for exp in exp_result.scalars():
                results.append(
                    {
                        "entity_type": "experiment",
                        "entity_id": exp.id,
                        "title": exp.name,
                        "snippet": (exp.description or "")[:200],
                        "experiment_id": exp.id,
                        "relevance_score": None,
                    }
                )

        if "sample" in types:
            sample_result = await session.execute(
                select(Sample)
                .where(
                    Sample.experiment_id.in_(select(Experiment.id).where(Experiment.organization_id == org_id)),
                    func.to_tsvector(
                        "english",
                        func.coalesce(Sample.sample_id_unique, "")
                        + " "
                        + func.coalesce(Sample.organism, "")
                        + " "
                        + func.coalesce(Sample.tissue_type, ""),
                    ).op("@@")(ts_query),
                )
                .limit(page_size)
            )
            for s in sample_result.scalars():
                results.append(
                    {
                        "entity_type": "sample",
                        "entity_id": s.id,
                        "title": s.sample_id_unique or f"Sample {s.id}",
                        "snippet": f"{s.organism or ''} - {s.tissue_type or ''}",
                        "experiment_id": s.experiment_id,
                        "relevance_score": None,
                    }
                )

        if "file" in types:
            file_result = await session.execute(
                select(File)
                .where(
                    File.organization_id == org_id,
                    File.filename.ilike(f"%{query}%"),
                )
                .limit(page_size)
            )
            for f in file_result.scalars():
                results.append(
                    {
                        "entity_type": "file",
                        "entity_id": f.id,
                        "title": f.filename,
                        "snippet": f"{f.file_type} - {f.size_bytes or 0} bytes",
                        "experiment_id": None,
                        "relevance_score": None,
                    }
                )

        if "document" in types:
            doc_result = await session.execute(
                select(Document)
                .where(
                    Document.organization_id == org_id,
                    func.to_tsvector(
                        "english", func.coalesce(Document.title, "") + " " + func.coalesce(Document.extracted_text, "")
                    ).op("@@")(ts_query),
                )
                .limit(page_size)
            )
            for d in doc_result.scalars():
                results.append(
                    {
                        "entity_type": "document",
                        "entity_id": d.id,
                        "title": d.title or "Untitled",
                        "snippet": (d.extracted_text or "")[:200],
                        "experiment_id": d.linked_experiment_id,
                        "relevance_score": None,
                    }
                )

        total = len(results)
        offset = (page - 1) * page_size
        paginated = results[offset : offset + page_size]

        return paginated, total

    @staticmethod
    async def _is_meilisearch_enabled(session: AsyncSession) -> bool:
        """Check if Meilisearch component is enabled."""
        from app.models.component import ComponentState

        result = await session.execute(
            select(ComponentState.enabled).where(ComponentState.component_key == "meilisearch")
        )
        enabled = result.scalar_one_or_none()
        return bool(enabled)

    @staticmethod
    async def _search_meilisearch(
        org_id: int,
        query: str,
        entity_types: list[str] | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict], int]:
        """Search using Meilisearch."""
        try:
            import meilisearch

            client = meilisearch.Client("http://meilisearch:7700")
            results = []
            total = 0
            types = entity_types or ["experiment", "sample", "file", "document"]

            for entity_type in types:
                index_name = f"{entity_type}s_{org_id}"
                try:
                    index = client.index(index_name)
                    search_result = index.search(query, {"limit": page_size, "offset": (page - 1) * page_size})
                    for hit in search_result.get("hits", []):
                        results.append(
                            {
                                "entity_type": entity_type,
                                "entity_id": hit.get("id"),
                                "title": hit.get("title", ""),
                                "snippet": hit.get("_formatted", {}).get("content", "")[:200],
                                "experiment_id": hit.get("experiment_id"),
                                "relevance_score": None,
                            }
                        )
                    total += search_result.get("estimatedTotalHits", 0)
                except Exception:
                    pass

            return results, total
        except ImportError:
            return [], 0

    @staticmethod
    async def reindex_all(session: AsyncSession, org_id: int) -> dict:
        """Reindex all entities for an organization."""
        if not await SearchService._is_meilisearch_enabled(session):
            return {"status": "skipped", "reason": "Meilisearch not enabled"}

        try:
            import meilisearch

            client = meilisearch.Client("http://meilisearch:7700")
            indexed = {"experiments": 0, "samples": 0, "files": 0, "documents": 0}

            # Index experiments
            result = await session.execute(select(Experiment).where(Experiment.organization_id == org_id))
            experiments = list(result.scalars().all())
            docs = [
                {"id": e.id, "title": e.name, "description": e.description or "", "status": e.status}
                for e in experiments
            ]
            if docs:
                client.index(f"experiments_{org_id}").add_documents(docs)
                indexed["experiments"] = len(docs)

            # Index files
            result = await session.execute(select(File).where(File.organization_id == org_id))
            files = list(result.scalars().all())
            docs = [{"id": f.id, "title": f.filename, "file_type": f.file_type} for f in files]
            if docs:
                client.index(f"files_{org_id}").add_documents(docs)
                indexed["files"] = len(docs)

            # Index documents
            result = await session.execute(select(Document).where(Document.organization_id == org_id))
            documents = list(result.scalars().all())
            docs = [
                {
                    "id": d.id,
                    "title": d.title or "",
                    "content": (d.extracted_text or "")[:10000],
                    "experiment_id": d.linked_experiment_id,
                }
                for d in documents
            ]
            if docs:
                client.index(f"documents_{org_id}").add_documents(docs)
                indexed["documents"] = len(docs)

            return {"status": "completed", "indexed": indexed}
        except ImportError:
            return {"status": "error", "reason": "meilisearch package not installed"}
        except Exception as e:
            return {"status": "error", "reason": str(e)}
