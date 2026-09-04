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
from app.services.pipeline_mapper import declared_route_version, deposit_conflict, is_library_strategy_conflict
from app.services import llm_provider_config_service
from app.services.column_resolution import ROLES as COLUMN_ROLES
from app.services.column_resolution import resolve_columns
from app.services.llm_feature_models import FEATURE_LITERATURE_VALIDATION
from app.services.llm_provider_clients import get_client
from app.services.result_set_normalizer import _read_rows, normalize_gene_table, normalize_interval_table
from app.services.validation_autonomy import AUTONOMY_ASSISTED, AUTONOMY_AUTONOMOUS


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


def _clamp(value, limit: int):
    """``value`` cut to what its column holds, or None. Never raises on a long string."""
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit]


async def _autonomy_for(session: AsyncSession, org_id: int) -> str:
    """The org's literature-validation autonomy mode, defaulting to assisted."""
    from app.models.organization import Organization

    org = await session.get(Organization, org_id)
    return (org.lit_validation_autonomy if org else None) or AUTONOMY_ASSISTED


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
        tools: list | None = None,
        reference_genome: str | None = None,
        reference_build: str | None = None,
        mapping_confidence: str | None = None,
        mapping_notes: str | None = None,
        blockers: list | None = None,
        extractor_model: str | None = None,
        extractor_provider: str | None = None,
        library_strategy: str | None = None,
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
            tools_json=tools if tools is not None else [],
            reference_genome=reference_genome,
            reference_build=reference_build,
            mapping_confidence=mapping_confidence,
            mapping_notes=mapping_notes,
            blockers_json=blockers if blockers is not None else [],
            extractor_model=extractor_model,
            extractor_provider=extractor_provider,
            library_strategy=_clamp(library_strategy, 100),
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
                # What the deposited accession declared itself to be, which is what chose the
                # pipeline whenever it disagreed with the paper. Always recorded, including as None:
                # a strategy that AGREED with the prose leaves no other trace anywhere, so without
                # this nothing could answer "was the deposit read for this study, and what did it
                # say". None means unread or ambiguous, which is a different state from absent.
                "library_strategy": library_strategy,
            },
        )
        return plan

    @staticmethod
    async def add_comparison_targets(
        session: AsyncSession, plan: ReproductionPlan, targets: list[dict]
    ) -> list[ComparisonTarget]:
        """Attach the paper's quantitative claims (B2d) to ``plan`` as ComparisonTargets.

        Every text field is clamped to its column. These values are a model's reading of a methods
        section rather than a controlled vocabulary, so their length is not something bioAF gets to
        assume: one over-long unit raised inside the insert, rolled the whole extraction back, and
        left a real paper unplannable behind a 500 (10.1038/s41598-023-33729-4, 2026-08-30). A
        clipped unit is a worse record of a claim than a full one; it is a far better one than no
        plan at all.
        """
        created: list[ComparisonTarget] = []
        for t in targets:
            metric_key = (t.get("metric_key") or "").strip()
            if not metric_key:
                continue
            target = ComparisonTarget(
                reproduction_plan_id=plan.id,
                metric_key=_clamp(metric_key, 100),
                claimed_value=t.get("claimed_value"),
                unit=_clamp(t.get("unit"), 255),
                tolerance=t.get("tolerance"),
                source_locator=_clamp(t.get("source_locator"), 255),
                # plan_6 step 3: the binding decision, when one was made. Absent keys leave NULLs,
                # which is what every caller that predates the binding call writes.
                bound_key=_clamp(t.get("bound_key"), 100),
                binding_reason=t.get("binding_reason"),
                binding_confidence=t.get("binding_confidence"),
                bound_by_model=_clamp(t.get("bound_by_model"), 255),
                bound_by=_clamp(t.get("bound_by"), 20),
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
    async def use_deposit_pipeline(session: AsyncSession, study_id: int, org_id: int, user_id: int) -> ReproductionPlan:
        """Re-point a conflicted plan at the pipeline the deposit's own strategy names.

        The way OUT of the deposit-contradicts-pipeline refusal that fixes its cause. The paper's
        prose chose a pipeline the deposited data cannot be read by; the deposit names the one that
        can, and in the common case the deposit is simply right. Re-pointing is therefore the
        primary action at the gate and the override is the secondary one: an override that is easier
        to click than the correction becomes the default, and then the guard means nothing.

        The version is re-pinned from the declared route rather than carried over, because the
        pinned version is what the catalog installs and what a rerun reproduces. Only the conflict
        blocker is cleared; the advisory ones are still what the scientist has to weigh.
        """
        from app.services.pipeline_assay_fallback import _candidates, _offer_by_key

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
                f"Cannot change the plan's pipeline from '{study.state}'; the study must be in 'plan_ready'.",
            )
        plan = await ReproductionPlanService.get_plan(session, study_id, org_id)
        if plan is None:
            raise HTTPException(404, "No reproduction plan for this study.")

        conflict = deposit_conflict(plan.blockers_json, plan.library_strategy)
        if conflict is None:
            raise HTTPException(400, "This plan does not contradict what the deposit says its data is.")
        suggested = conflict["suggested_pipeline_key"]
        if not suggested:
            raise HTTPException(
                400,
                f"The deposit says this data is {conflict['library_strategy']}, which names no single "
                "pipeline to move to. Choose a different accession, or record why this one should run anyway.",
            )

        # A route has to pin a version: it is what the catalog installs, what the gate's install
        # control needs to be clickable, and what a rerun reproduces. Refuse rather than write a null
        # one, which would repoint the plan onto a pipeline the gate could then not offer to install.
        offer = await _offer_by_key(session, org_id, await _candidates(session, org_id), suggested)
        version = offer[1] if offer else declared_route_version(suggested)
        if not version:
            raise HTTPException(
                400,
                f"{suggested} reads {conflict['library_strategy']} data, but this bioAF has no version "
                "of it to move to: it is neither installed nor in the nf-core registry cache.",
            )

        previous = plan.pipeline_key
        plan.pipeline_key = suggested
        plan.pipeline_version = version
        plan.blockers_json = [b for b in (plan.blockers_json or []) if not is_library_strategy_conflict(b)]
        plan.mapping_notes = (
            f"{plan.mapping_notes or ''}\nRe-pointed from {previous} to {suggested} because "
            f"{conflict['library_strategy']} is what the deposit says this data is."
        ).strip()
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="validation_study",
            entity_id=study_id,
            action="plan_repointed_to_deposit_pipeline",
            details={"from": previous, "to": suggested, "library_strategy": conflict["library_strategy"]},
            previous_value={"pipeline_key": previous},
        )
        return plan

    @staticmethod
    async def set_differential_design(
        session: AsyncSession,
        study_id: int,
        org_id: int,
        user_id: int,
        design: dict,
        selected_contrast_index: int | None = None,
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
        # The normalizer knows contrasts and thresholds, so rebuilding from it deleted the record of
        # WHICH contrast this run reproduces and who chose it. Study 26 lost a 0.97-confidence model
        # decision the moment its sample arms were filled in. Carry it across the edit.
        #
        # The gate saves the ONE contrast it edited (validate_replicates rejects the untouched ones
        # for having no samples), so the surviving index is 0. `selected_contrast_index` is the index
        # the human was looking at in the ORIGINAL list: when it differs from what was chosen for
        # them, the choice is now theirs and the record must say so rather than keep crediting a
        # model for a pick a person overrode.
        previous = (plan.differential_design_json or {}).get("selected_contrast")
        updated = _differential_design_or_none(normalized)
        if updated and previous:
            overridden = (
                selected_contrast_index is not None
                and previous.get("contrast_index") is not None
                and selected_contrast_index != previous.get("contrast_index")
            )
            carried = dict(previous)
            carried["contrast_index"] = (
                0 if len(updated.get("contrasts") or []) == 1 else previous.get("contrast_index")
            )
            if overridden:
                carried.update(
                    decided_by="human",
                    model=None,
                    confidence=None,
                    reason="chosen at the C1 gate, replacing the contrast selected for this run",
                )
            updated["selected_contrast"] = carried
        plan.differential_design_json = updated
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
        column_map: dict | None = None,
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

        # The paper's own stated thresholds normalize its own table, and they belong to the CONTRAST
        # this run reproduces, not to the paper: a DEG cutoff applied to a windowed differential
        # binding table cut 92 significant intervals to 33. Fall back to the paper-level pair, which
        # is what a single-contrast paper means and what every plan written before this carries.
        design_json = plan.differential_design_json or {}
        selected = (design_json.get("selected_contrast") or {}).get("contrast_index")
        contrasts = design_json.get("contrasts") or []
        contrast_thresholds = None
        if isinstance(selected, int) and 0 <= selected < len(contrasts):
            contrast_thresholds = contrasts[selected].get("thresholds")
        from_contrast = bool(contrast_thresholds)
        design_thresholds = contrast_thresholds if from_contrast else (design_json.get("thresholds") or {})
        lfc = lfc_threshold if lfc_threshold is not None else design_thresholds.get("log2fc")
        padj = padj_threshold if padj_threshold is not None else design_thresholds.get("padj")
        # A null on the CONTRAST is the model answering the question it was asked: this cutoff does
        # not apply to this finding, which is the usual case for windowed differential binding. That
        # is 0.0 (significance alone), not the 1.0 default meant for "nobody stated one".
        default_lfc = 0.0 if from_contrast else 1.0
        lfc = float(lfc) if lfc is not None else default_lfc
        padj = float(padj) if padj is not None else 0.05

        def _normalize(cmap: dict | None):
            if kind == "interval":
                return normalize_interval_table(
                    table_text, lfc_threshold=lfc, padj_threshold=padj, contrast=contrast, column_map=cmap
                )
            return normalize_gene_table(
                table_text, lfc_threshold=lfc, padj_threshold=padj, contrast=contrast, column_map=cmap
            )

        fs = _normalize(column_map)
        # A map the caller supplied is the assisted picker's answer, so it is the human's decision.
        mapping = (
            {"columns": column_map, "decided_by": "human", "reason": "", "confidence": None, "model": None}
            if column_map
            else None
        )
        needs_help = None

        # The alias list only knows the spellings someone enumerated, and a real csaw deposit names
        # its columns `regions.seqnames` / `regions.start` / `regions.end`. Rather than report that
        # as an unusable deposit, ask: the model in `autonomous`, a person at the gate in `assisted`.
        if not fs.entities and any("could not locate" in n for n in fs.parse_notes):
            header, _ = _read_rows(table_text)
            resolved = None
            if header and await _autonomy_for(session, org_id) == AUTONOMY_AUTONOMOUS:
                cfg = await llm_provider_config_service.get_for_feature(session, org_id, FEATURE_LITERATURE_VALIDATION)
                if cfg is not None:
                    resolved = await resolve_columns(
                        header, kind=kind, client=get_client(cfg.provider), model=cfg.model, api_key=cfg.api_key
                    )
            if resolved:
                retried = _normalize(resolved["columns"])
                if retried.entities:
                    fs = retried
                    mapping = {**resolved, "decided_by": "model"}
            if not fs.entities:
                # Still unparsed: hand the header and the roles still to fill to a person, which is
                # a question they can answer, unlike "could not locate chrom/start/end columns".
                needs_help = {"header": header, "roles": list(COLUMN_ROLES.get(kind, ()))}

        claim = {
            "kind": kind,
            "namespace": fs.namespace,
            "source_locator": source_locator,
            "contrast": contrast,
            "confirmed": True,
            "thresholds": {"log2fc": lfc, "padj": padj},
            "finding_set": fs.to_dict(),
            "column_mapping": mapping,
            "needs_column_mapping": needs_help,
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
