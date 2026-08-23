"""Service for the ReproductionPlan aggregate (lit_validation B2/B3 output, C1 input).

Persists the plan produced by the extractor and its ComparisonTargets, and reads it back org-scoped
through the owning study. The extractor calls create_plan + add_comparison_targets; the C1 gate and
the result views read via get_plan.
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.comparison_target import ComparisonTarget
from app.models.reproduction_plan import ReproductionPlan
from app.models.validation_study import ValidationStudy
from app.services.audit_service import log_action
from app.services.result_set_normalizer import normalize_gene_table, normalize_interval_table


# DESeq2 estimates per-gene dispersion from WITHIN-group variance, so an arm with one sample has none
# to estimate from. Two is valid (underpowered; three is the usual recommendation) and refusing at three
# would turn away legitimate small-lab studies, so the floor is two and there is no warning at exactly
# two. This bites hardest on the scRNA-seq path: pseudobulk collapses thousands of cells to one column
# per SAMPLE, so an scRNA study's replicate count is its sample count, routinely 2-4.
MIN_SAMPLES_PER_ARM = 2


def validate_replicates(design: dict) -> list[str]:
    """Guard against a contrast DESeq2 cannot fit. Returns human-readable problems (empty == valid).

    Independent of ``validate_paired_designs``: both run on the same design and a human fixing a
    rejection should see everything wrong with it in one pass.
    """
    errors: list[str] = []
    for c in design.get("contrasts") or []:
        name = c.get("name") or "contrast"
        for arm in ("test", "reference"):
            samples = c.get(f"{arm}_samples") or []
            if len(samples) < MIN_SAMPLES_PER_ARM:
                errors.append(
                    f"{name}: the {arm} arm has {len(samples)} sample(s); differential analysis needs at "
                    f"least {MIN_SAMPLES_PER_ARM} per arm to estimate within-group variance."
                )
    return errors


def validate_paired_designs(design: dict) -> list[str]:
    """C1-gate guard for the optional matched-pairs / blocked design (ADR-069 item #2).

    A per-contrast ``subjects`` map ({sample_id: block_label}) drives ``design = ~ block + condition``
    in the DE notebook. DESeq2 errors ("model matrix not full rank") if that block factor is confounded
    with condition, so we reject a bad pairing BEFORE any fetch/compute spend rather than let the run
    fail. Returns a list of human-readable problems (empty == valid). Contrasts with no ``subjects`` are
    the default unpaired design and are never flagged. For a contrast that does declare subjects:

    - every sample in the contrast must have a block label (no unlabeled sample);
    - no stray labels for samples outside the contrast;
    - at least 2 distinct block labels (a constant block factor has one level and cannot be modeled);
    - each block label must appear in BOTH arms (a label confined to one arm is confounded).
    """
    errors: list[str] = []
    for c in design.get("contrasts") or []:
        subjects = c.get("subjects") or {}
        if not subjects:
            continue
        name = c.get("name") or "contrast"
        test_samples = c.get("test_samples") or []
        reference_samples = c.get("reference_samples") or []
        samples = list(test_samples) + list(reference_samples)

        unlabeled = [s for s in samples if s not in subjects]
        if unlabeled:
            errors.append(f"{name}: samples with no subject/block label: {', '.join(unlabeled)}.")
        stray = [k for k in subjects if k not in samples]
        if stray:
            errors.append(f"{name}: subject labels for samples not in the contrast: {', '.join(stray)}.")

        test_labels = {subjects[s] for s in test_samples if s in subjects}
        ref_labels = {subjects[s] for s in reference_samples if s in subjects}
        all_labels = test_labels | ref_labels
        if len(all_labels) < 2:
            errors.append(f"{name}: a paired/blocked design needs >= 2 distinct subject labels.")
        confounded = sorted((all_labels - test_labels) | (all_labels - ref_labels))
        if confounded:
            errors.append(
                f"{name}: subject(s) present in only one arm (confounded with condition): "
                f"{', '.join(confounded)}. Each subject must appear in both the test and reference arms."
            )
    return errors


class ReproductionPlanService:
    @staticmethod
    async def create_plan(
        session: AsyncSession,
        study: ValidationStudy,
        user_id: int,
        *,
        accessions: list | None = None,
        sample_sheet: dict | None = None,
        pipeline_key: str | None = None,
        pipeline_version: str | None = None,
        parameters: dict | None = None,
        differential_design: dict | None = None,
        reference_genome: str | None = None,
        reference_build: str | None = None,
        mapping_confidence: str | None = None,
        mapping_notes: str | None = None,
        blockers: list | None = None,
        extractor_model: str | None = None,
        extractor_provider: str | None = None,
    ) -> ReproductionPlan:
        """Create a plan for ``study`` and point the study at it (its current plan). Audited."""
        plan = ReproductionPlan(
            validation_study_id=study.id,
            accessions_json=accessions if accessions is not None else [],
            sample_sheet_json=sample_sheet,
            pipeline_key=pipeline_key,
            pipeline_version=pipeline_version,
            parameters_json=parameters,
            differential_design_json=differential_design,
            reference_genome=reference_genome,
            reference_build=reference_build,
            mapping_confidence=mapping_confidence,
            mapping_notes=mapping_notes,
            blockers_json=blockers if blockers is not None else [],
            extractor_model=extractor_model,
            extractor_provider=extractor_provider,
        )
        session.add(plan)
        await session.flush()

        study.reproduction_plan_id = plan.id
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="reproduction_plan",
            entity_id=plan.id,
            action="create",
            details={
                "validation_study_id": study.id,
                "pipeline_key": pipeline_key,
                "mapping_confidence": mapping_confidence,
                "accession_count": len(plan.accessions_json or []),
            },
        )
        return plan

    @staticmethod
    async def add_comparison_targets(
        session: AsyncSession, plan: ReproductionPlan, targets: list[dict]
    ) -> list[ComparisonTarget]:
        """Attach the paper's quantitative claims (B2d) to ``plan`` as ComparisonTargets."""
        created: list[ComparisonTarget] = []
        for t in targets:
            metric_key = (t.get("metric_key") or "").strip()
            if not metric_key:
                continue
            target = ComparisonTarget(
                reproduction_plan_id=plan.id,
                metric_key=metric_key,
                claimed_value=t.get("claimed_value"),
                unit=t.get("unit"),
                tolerance=t.get("tolerance"),
                source_locator=t.get("source_locator"),
            )
            session.add(target)
            created.append(target)
        await session.flush()
        return created

    @staticmethod
    async def get_plan(session: AsyncSession, study_id: int, org_id: int) -> ReproductionPlan | None:
        """Load the study's current plan (with its targets), scoped to the org via the study."""
        study = (
            await session.execute(
                select(ValidationStudy).where(
                    ValidationStudy.id == study_id,
                    ValidationStudy.organization_id == org_id,
                )
            )
        ).scalar_one_or_none()
        if not study or not study.reproduction_plan_id:
            return None
        return (
            await session.execute(
                select(ReproductionPlan)
                .where(ReproductionPlan.id == study.reproduction_plan_id)
                .options(selectinload(ReproductionPlan.comparison_targets))
            )
        ).scalar_one_or_none()

    @staticmethod
    async def set_differential_design(
        session: AsyncSession, study_id: int, org_id: int, user_id: int, design: dict
    ) -> ReproductionPlan:
        """B2e edit: persist the human-ratified differential design onto the plan at the C1 gate.

        The extractor drafts the design, but its sample labels rarely match the analysis matrix's
        column names, so the human corrects the contrast(s) + thresholds before Level-3 runs it. The
        design is normalized to the canonical shape (honest-None on missing sub-fields); an empty
        contrast list clears it to None so the plan stays Level-2. Only valid at ``plan_ready``.
        """
        # Local import: validation_extraction_service imports this module, so import its normalizer
        # lazily to avoid a circular import at load time.
        from app.services.validation_extraction_service import (
            _differential_design_or_none,
            _normalize_differential_design,
        )

        study = (
            await session.execute(
                select(ValidationStudy).where(ValidationStudy.id == study_id, ValidationStudy.organization_id == org_id)
            )
        ).scalar_one_or_none()
        if not study:
            raise HTTPException(404, "Validation study not found")
        if study.state != "plan_ready":
            raise HTTPException(
                400,
                f"Cannot edit the differential design from '{study.state}'; the study must be in 'plan_ready'.",
            )
        plan = await ReproductionPlanService.get_plan(session, study_id, org_id)
        if plan is None:
            raise HTTPException(404, "No reproduction plan to attach the differential design to.")

        normalized = _normalize_differential_design(design)
        # Reject a design no differential analysis can fit, at the C1 gate, before any spend: too few
        # replicates per arm, or a confounded/unbalanced matched-pairs block factor. Report both guards
        # together so one round trip surfaces everything wrong with the design.
        errors = validate_replicates(normalized) + validate_paired_designs(normalized)
        if errors:
            raise HTTPException(400, "Invalid differential design. " + " ".join(errors))
        plan.differential_design_json = _differential_design_or_none(normalized)
        await session.flush()
        await log_action(
            session,
            user_id=user_id,
            entity_type="reproduction_plan",
            entity_id=plan.id,
            action="edit_differential_design",
            details={
                "validation_study_id": study_id,
                "n_contrasts": len((plan.differential_design_json or {}).get("contrasts", [])),
            },
        )
        return plan

    @staticmethod
    async def set_finding_claim(
        session: AsyncSession,
        study_id: int,
        org_id: int,
        user_id: int,
        *,
        kind: str,
        table_text: str,
        contrast: str | None = None,
        lfc_threshold: float | None = None,
        padj_threshold: float | None = None,
        source_locator: str | None = None,
    ) -> dict:
        """B4 (ADR-069): normalize the paper's deposited result table into a directional FindingSet
        and persist it as the plan's confirmed ground-truth claim (the C1 gate).

        The human supplies the paper's own DEG table (``kind="gene"``) or DA peak table
        (``kind="interval"``); we normalize it with the paper's stated thresholds (defaulting to the
        differential design captured in B2e) and store it so plan approval can read it into
        ``evidence["level3"].paper_finding_set``. Returns the claim (finding set + parse notes) so the
        UI can show the parse for confirmation/correction. Only valid at the ``plan_ready`` C1 gate.
        """
        if kind not in ("gene", "interval"):
            raise HTTPException(400, f"Unknown finding-set kind '{kind}'; expected 'gene' or 'interval'.")

        study = (
            await session.execute(
                select(ValidationStudy).where(
                    ValidationStudy.id == study_id,
                    ValidationStudy.organization_id == org_id,
                )
            )
        ).scalar_one_or_none()
        if not study:
            raise HTTPException(404, "Validation study not found")
        if study.state != "plan_ready":
            raise HTTPException(
                400,
                f"Cannot confirm a ground-truth set from '{study.state}'; the study must be in 'plan_ready'.",
            )
        plan = await ReproductionPlanService.get_plan(session, study_id, org_id)
        if plan is None:
            raise HTTPException(404, "No reproduction plan to attach the ground-truth set to.")

        # The paper's own stated thresholds normalize its own table. Default to the captured design.
        design_thresholds = (plan.differential_design_json or {}).get("thresholds") or {}
        lfc = lfc_threshold if lfc_threshold is not None else design_thresholds.get("log2fc")
        padj = padj_threshold if padj_threshold is not None else design_thresholds.get("padj")
        lfc = float(lfc) if lfc is not None else 1.0
        padj = float(padj) if padj is not None else 0.05

        if kind == "interval":
            fs = normalize_interval_table(table_text, lfc_threshold=lfc, padj_threshold=padj, contrast=contrast)
        else:
            fs = normalize_gene_table(table_text, lfc_threshold=lfc, padj_threshold=padj, contrast=contrast)

        claim = {
            "kind": kind,
            "namespace": fs.namespace,
            "source_locator": source_locator,
            "contrast": contrast,
            "confirmed": True,
            "thresholds": {"log2fc": lfc, "padj": padj},
            "finding_set": fs.to_dict(),
        }
        plan.finding_claim_json = claim
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="reproduction_plan",
            entity_id=plan.id,
            action="confirm_finding_claim",
            details={
                "validation_study_id": study_id,
                "kind": kind,
                "n_sig": len(fs.entities),
                "namespace": fs.namespace,
                "source_locator": source_locator,
            },
        )
        return claim
