"""recommend_pipeline: deterministic, rule-based pipeline recommendation.

Given an experiment, inspect its samples (molecule type, single-cell prep signals,
organism) and recommend an nf-core pipeline plus a reference genome. This is a shared
bioAF service: it backs an agent tool in ai_pipeline_run and (later) a deterministic
step in lit_validation. It makes no LLM call; the rules are explicit and auditable.

v1 scope: bulk RNA -> nf-core/rnaseq, single-cell RNA -> nf-core/scrnaseq. Any other
assay returns a "cannot recommend" result (not a guess), per spec-04 of ai_pipeline_run.
"""

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experiment import Experiment
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.sample import Sample

# Substrings (matched case-insensitively against library_prep_method) that mark a
# single-cell RNA library. chemistry_version being set is also treated as a signal.
_SINGLE_CELL_SIGNALS = (
    "10x",
    "chromium",
    "single cell",
    "single-cell",
    "scrna",
    "sc-rna",
    "smart-seq",
    "smartseq",
)

# organism substring -> reference genome assembly.
_ORGANISM_REFERENCE = (
    (("mus musculus", "mouse"), "GRCm39"),
    (("homo sapiens", "human"), "GRCh38"),
)

_RNASEQ = "nf-core/rnaseq"
_SCRNASEQ = "nf-core/scrnaseq"


@dataclass(frozen=True)
class PipelineRecommendation:
    """The result of a recommendation.

    When ``recommended`` is False, ``pipeline_key`` is None and ``rationale`` explains
    why nothing was recommended. ``version`` and ``parameters`` are populated only when
    the recommended pipeline is already installed in the org's catalog.
    """

    recommended: bool
    rationale: str
    pipeline_key: str | None = None
    version: str | None = None
    parameters: dict | None = None
    reference_genome: str | None = None


def _is_rna(molecule_type: str | None) -> bool:
    return molecule_type is not None and "rna" in molecule_type.lower()


def _has_single_cell_signal(sample: Sample) -> bool:
    if sample.chemistry_version:
        return True
    prep = (sample.library_prep_method or "").lower()
    return any(signal in prep for signal in _SINGLE_CELL_SIGNALS)


def _reference_for_organism(organism: str | None) -> str | None:
    if not organism:
        return None
    lowered = organism.lower()
    for needles, reference in _ORGANISM_REFERENCE:
        if any(needle in lowered for needle in needles):
            return reference
    return None


def _most_common_organism(samples: list[Sample]) -> str | None:
    organisms = [s.organism for s in samples if s.organism]
    if not organisms:
        return None
    return Counter(organisms).most_common(1)[0][0]


class RecommendPipelineService:
    @staticmethod
    async def recommend(session: AsyncSession, *, org_id: int, experiment_id: int) -> PipelineRecommendation:
        """Recommend a pipeline + reference for an experiment, or decline with a reason.

        Raises LookupError if the experiment does not exist in the org (the caller, e.g.
        the agent tool wrapper, is expected to have already resolved a real entity).
        """
        experiment = (
            await session.execute(
                select(Experiment).where(
                    Experiment.id == experiment_id,
                    Experiment.organization_id == org_id,
                )
            )
        ).scalar_one_or_none()
        if experiment is None:
            raise LookupError(f"experiment {experiment_id} not found in org {org_id}")

        samples = list((await session.execute(select(Sample).where(Sample.experiment_id == experiment_id))).scalars())
        if not samples:
            return PipelineRecommendation(
                recommended=False,
                rationale="The experiment has no samples to characterize, so no pipeline can be recommended.",
            )

        rna_samples = [s for s in samples if _is_rna(s.molecule_type)]
        if not rna_samples:
            molecule_types = sorted({(s.molecule_type or "unspecified") for s in samples})
            return PipelineRecommendation(
                recommended=False,
                rationale=(
                    "No RNA samples were found ("
                    + ", ".join(molecule_types)
                    + "). v1 recommends only RNA-seq pipelines."
                ),
            )

        is_single_cell = any(_has_single_cell_signal(s) for s in rna_samples)
        pipeline_key = _SCRNASEQ if is_single_cell else _RNASEQ
        assay_label = "single-cell RNA" if is_single_cell else "bulk RNA"

        organism = _most_common_organism(rna_samples)
        reference_genome = _reference_for_organism(organism)

        entry = (
            await session.execute(
                select(PipelineCatalogEntry).where(
                    PipelineCatalogEntry.organization_id == org_id,
                    PipelineCatalogEntry.pipeline_key == pipeline_key,
                    PipelineCatalogEntry.enabled.is_(True),
                )
            )
        ).scalar_one_or_none()

        rationale_parts = [
            f"Detected {assay_label} samples; recommending {pipeline_key}.",
        ]
        if organism and reference_genome:
            rationale_parts.append(f"Organism {organism} maps to reference {reference_genome}.")
        elif organism:
            rationale_parts.append(
                f"Organism {organism} has no built-in reference mapping; choose a reference manually."
            )
        else:
            rationale_parts.append("No organism was recorded; choose a reference manually.")

        if entry is None:
            rationale_parts.append(f"{pipeline_key} is not installed in the catalog yet; install it before launching.")
            return PipelineRecommendation(
                recommended=True,
                pipeline_key=pipeline_key,
                version=None,
                parameters=None,
                reference_genome=reference_genome,
                rationale=" ".join(rationale_parts),
            )

        return PipelineRecommendation(
            recommended=True,
            pipeline_key=pipeline_key,
            version=entry.version,
            parameters=entry.default_params_json,
            reference_genome=reference_genome,
            rationale=" ".join(rationale_parts),
        )
