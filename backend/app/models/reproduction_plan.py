"""ReproductionPlan: the structured, reviewable output of "read the paper" (lit_validation B2/B3).

One plan per extraction attempt for a ValidationStudy. It captures the deposited accessions, the
derived sample structure, the chosen nf-core pipeline mapping (with a confidence and rationale),
any blockers that make the paper unreproducible, and the provenance of the AI extraction. This is
what a scientist ratifies at the C1 gate before any compute is spent. See
``local/lit_validation/spec-02-data-model.md``.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ReproductionPlan(Base):
    __tablename__ = "reproduction_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    validation_study_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("validation_studies.id"), nullable=False, index=True
    )

    accessions_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    sample_sheet_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    pipeline_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pipeline_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    parameters_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reference_genome: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reference_build: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # B2e (ADR-069 / spec-08 Level-3): the paper's differential design, captured from the extractor
    # and ratified/edited by the human at the C1 gate. Shape:
    # {"contrasts": [{"name", "test_condition", "reference_condition", "test_samples": [...],
    #  "reference_samples": [...]}], "thresholds": {"log2fc": float|None, "padj": float|None}}.
    # This is NOT nf-core pipeline params (those stay in parameters_json); it drives the Level-3
    # headless differential notebook. None/empty for a QC-only paper with no differential finding.
    differential_design_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # B3 mapping rationale: how confident the method -> nf-core mapping is, and why.
    mapping_confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)  # exact | partial | none
    mapping_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Reasons the paper cannot be reproduced (no accession, no nf-core equivalent, ...).
    blockers_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Provenance of the AI extraction that produced this plan.
    extractor_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    extractor_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    comparison_targets = relationship(
        "ComparisonTarget", back_populates="reproduction_plan", cascade="all, delete-orphan"
    )
