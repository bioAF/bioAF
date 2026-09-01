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

    # B4 (ADR-069 / spec-08 Level-3): the paper's OWN deposited result set (its DEG table / DA peak
    # list), normalized to a directional FindingSet and confirmed by the human at the C1 gate. This
    # is the ground truth Level-3 concordance scores our reproduction against. Shape:
    # {"kind": "gene"|"interval", "namespace", "source_locator", "contrast", "confirmed": bool,
    #  "thresholds": {"log2fc", "padj"}, "finding_set": FindingSet.to_dict()}. None until confirmed;
    # a paper with no obtainable set stays None (verdict caps at Level-2, never a fabricated set).
    finding_claim_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # The tools the PAPER's own methods section named (aligner, quantifier, cell caller, DE method).
    # Extracted as `method.tools` and, before this column existed, spent on one boolean
    # (`_mentions_nf_core`) and a prose sentence and then discarded. It is the only input divergence
    # attribution has: a cell count that differs because the paper used CellRanger and we used
    # STARsolo is a known, explainable technical difference, not an unexplained discrepancy.
    # NULL means "extracted before this was kept"; [] means "the paper named no tools".
    tools_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # What the accession this study was scoped to declares its data actually IS (the INSDC
    # `library_strategy`: Bisulfite-Seq, ChIP-Seq, ATAC-seq, ...). The deposit is not prose, so where
    # it disagrees with the paper's methods section it is the better evidence and it chooses the
    # pipeline. Kept because the C1 gate has to name it when it refuses a plan, and re-deriving it
    # means an ENA/GEO fetch on every page load. NULL means no accession was scoped, the deposit
    # declared nothing usable, or the plan predates this column.
    library_strategy: Mapped[str | None] = mapped_column(String(100), nullable=True)

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
