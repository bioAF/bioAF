"""Orchestrator that ties data gathering and rendering together."""

from __future__ import annotations

import io
import json
import zipfile

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.provenance.csv_renderer import CsvRenderer
from app.services.provenance.data_gatherer import ProvenanceDataGatherer
from app.services.provenance.json_renderer import JsonRenderer
from app.services.provenance.markdown_renderer import MarkdownRenderer
from app.services.provenance.pdf_renderer import PdfRenderer
from app.services.provenance.schema import ReportOutput


class ProvenanceReportService:
    @staticmethod
    async def generate(
        session: AsyncSession,
        entity_type: str,
        entity_id: int,
        org_id: int,
        user_email: str,
        format: str = "json",
    ) -> ReportOutput:
        # 1. Gather data
        data = await ProvenanceDataGatherer.gather(session, entity_type, entity_id, org_id)

        # 2. Render JSON (always -- canonical format)
        json_report = JsonRenderer.render(entity_type, data, user_email)

        base = f"provenance_{entity_type}_{entity_id}"

        if format == "json":
            return ReportOutput(
                content=json.dumps(json_report, indent=2, default=str),
                content_type="application/json",
                filename=f"{base}.json",
            )

        if format == "md":
            md = MarkdownRenderer.render(entity_type, json_report)
            return ReportOutput(
                content=md,
                content_type="text/markdown",
                filename=f"{base}.md",
            )

        if format == "pdf":
            pdf_bytes = PdfRenderer.render(entity_type, json_report)
            return ReportOutput(
                content=pdf_bytes,
                content_type="application/pdf",
                filename=f"{base}.pdf",
            )

        if format == "csv":
            csv_files = CsvRenderer.render(entity_type, json_report)
            zip_bytes = _zip_csv_files(csv_files, base)
            return ReportOutput(
                content=zip_bytes,
                content_type="application/zip",
                filename=f"{base}_csv.zip",
            )

        if format == "all":
            return _bundle_all(entity_type, json_report, base)

        raise ValueError(f"Unknown format: {format}")


def _zip_csv_files(csv_files: dict[str, str], prefix: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, content in csv_files.items():
            zf.writestr(f"{prefix}/{filename}", content)
    return buf.getvalue()


def _bundle_all(entity_type: str, json_report: dict, base: str) -> ReportOutput:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # JSON
        zf.writestr(f"{base}.json", json.dumps(json_report, indent=2, default=str))

        # Markdown
        md = MarkdownRenderer.render(entity_type, json_report)
        zf.writestr(f"{base}.md", md)

        # PDF
        pdf_bytes = PdfRenderer.render(entity_type, json_report)
        zf.writestr(f"{base}.pdf", pdf_bytes)

        # CSVs
        csv_files = CsvRenderer.render(entity_type, json_report)
        for filename, content in csv_files.items():
            zf.writestr(f"{base}/{filename}", content)

    return ReportOutput(
        content=buf.getvalue(),
        content_type="application/zip",
        filename=f"{base}_all.zip",
    )
