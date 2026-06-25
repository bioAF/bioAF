"""recommend_pipeline: deterministic, rule-based pipeline recommendation.

Given an experiment, inspect its samples (molecule type, single-cell prep signals,
organism) and recommend an nf-core pipeline plus a reference genome. This is a shared
bioAF service: it backs an agent tool in ai_pipeline_run and (later) a deterministic
step in lit_validation. It makes no LLM call; the rules are explicit and auditable.

v1 scope: bulk RNA -> nf-core/rnaseq, single-cell RNA -> nf-core/scrnaseq. Any other
assay returns a "cannot recommend" result (not a guess), per spec-04 of ai_pipeline_run.

Assay is determined per sample by a hybrid rule: an explicit, controlled-vocabulary
``Sample.assay`` value wins (high confidence); otherwise the assay is inferred from the
free-text molecule_type / chemistry_version / library_prep_method fields (medium confidence
when a positive signal is present, low when only the default molecule type points to bulk).
The result carries that ``confidence`` plus the human-readable ``signals`` it relied on, so a
caller (including the assistant) can ask the user to confirm a low-confidence guess.
"""

from collections import Counter
from dataclasses import dataclass, field

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

# Assay categories used internally to rank samples to a pipeline.
_SCRNA = "scrna"
_BULK_RNA = "bulk_rna"
_NON_RNA = "non_rna"

# Map the controlled-vocabulary Sample.assay value to an internal category.
_ASSAY_TO_CATEGORY = {
    "scrna": _SCRNA,
    "bulk_rna": _BULK_RNA,
    "other": _NON_RNA,
}

# Internal classification source, in decreasing confidence.
_SOURCE_EXPLICIT = "explicit"
_SOURCE_HEURISTIC = "heuristic"
_SOURCE_HEURISTIC_DEFAULT = "heuristic_default"


@dataclass(frozen=True)
class PipelineRecommendation:
    """The result of a recommendation.

    When ``recommended`` is False, ``pipeline_key`` is None and ``rationale`` explains
    why nothing was recommended. ``version`` and ``parameters`` are populated only when
    the recommended pipeline is already installed in the org's catalog.

    ``confidence`` is "high" when an explicit ``Sample.assay`` drove the choice, "medium"
    when a positive heuristic signal did, and "low" when only the default molecule type did
    (None when nothing was recommended). ``signals`` lists the human-readable evidence used.
    """

    recommended: bool
    rationale: str
    pipeline_key: str | None = None
    version: str | None = None
    parameters: dict | None = None
    reference_genome: str | None = None
    confidence: str | None = None
    signals: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Classification:
    """How one sample was classified, and the evidence behind it."""

    sample: Sample
    category: str  # _SCRNA | _BULK_RNA | _NON_RNA
    source: str  # _SOURCE_EXPLICIT | _SOURCE_HEURISTIC | _SOURCE_HEURISTIC_DEFAULT
    signal: str


def _is_rna(molecule_type: str | None) -> bool:
    return molecule_type is not None and "rna" in molecule_type.lower()


def _has_single_cell_signal(sample: Sample) -> bool:
    if sample.chemistry_version:
        return True
    prep = (sample.library_prep_method or "").lower()
    return any(signal in prep for signal in _SINGLE_CELL_SIGNALS)


def _sample_label(sample: Sample) -> str:
    return sample.external_id or f"sample {sample.id}"


def _classify_sample(sample: Sample) -> _Classification:
    """Classify a single sample's assay, preferring an explicit field over the heuristic."""
    label = _sample_label(sample)

    if sample.assay:
        category = _ASSAY_TO_CATEGORY.get(sample.assay, _NON_RNA)
        if category == _SCRNA:
            signal = f"Sample {label}: assay recorded as single-cell RNA (scrna)."
        elif category == _BULK_RNA:
            signal = f"Sample {label}: assay recorded as bulk RNA (bulk_rna)."
        else:
            signal = f"Sample {label}: assay recorded as '{sample.assay}', which has no v1 pipeline."
        return _Classification(sample, category, _SOURCE_EXPLICIT, signal)

    if _is_rna(sample.molecule_type):
        if _has_single_cell_signal(sample):
            evidence = sample.library_prep_method or "single-cell chemistry version set"
            return _Classification(
                sample,
                _SCRNA,
                _SOURCE_HEURISTIC,
                f"Sample {label}: single-cell library prep detected ({evidence}).",
            )
        if sample.library_prep_method:
            return _Classification(
                sample,
                _BULK_RNA,
                _SOURCE_HEURISTIC,
                f"Sample {label}: RNA library ({sample.library_prep_method}) with no single-cell signal.",
            )
        return _Classification(
            sample,
            _BULK_RNA,
            _SOURCE_HEURISTIC_DEFAULT,
            f"Sample {label}: assumed bulk RNA from molecule type '{sample.molecule_type}' with no prep details.",
        )

    return _Classification(
        sample,
        _NON_RNA,
        _SOURCE_HEURISTIC,
        f"Sample {label}: molecule type '{sample.molecule_type or 'unspecified'}' is not RNA.",
    )


def _confidence_for(contributing: list[_Classification], category: str) -> str:
    """Derive the confidence of the chosen recommendation from the samples that drove it."""
    sources = {c.source for c in contributing}
    if _SOURCE_EXPLICIT in sources:
        return "high"
    if category == _SCRNA:
        return "medium"  # a positive single-cell signal was matched
    if _SOURCE_HEURISTIC in sources:
        return "medium"  # bulk inferred from an actual prep method
    return "low"  # bulk inferred from only the default molecule type


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

        classifications = [_classify_sample(s) for s in samples]

        # Precedence: any single-cell sample steers the whole experiment to scrnaseq; else any
        # bulk-RNA sample steers it to rnaseq; else nothing maps to a v1 pipeline.
        chosen_category: str | None = None
        for candidate in (_SCRNA, _BULK_RNA):
            if any(c.category == candidate for c in classifications):
                chosen_category = candidate
                break

        if chosen_category is None:
            molecule_types = sorted({(s.molecule_type or "unspecified") for s in samples})
            return PipelineRecommendation(
                recommended=False,
                rationale=(
                    "No RNA assay could be determined ("
                    + ", ".join(molecule_types)
                    + "). v1 recommends only RNA-seq pipelines."
                ),
                confidence=None,
                signals=[c.signal for c in classifications],
            )

        contributing = [c for c in classifications if c.category == chosen_category]
        pipeline_key = _SCRNASEQ if chosen_category == _SCRNA else _RNASEQ
        assay_label = "single-cell RNA" if chosen_category == _SCRNA else "bulk RNA"
        confidence = _confidence_for(contributing, chosen_category)
        signals = [c.signal for c in contributing]

        organism = _most_common_organism([c.sample for c in contributing])
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

        rationale_parts.append(f"Assay confidence: {confidence}.")

        if entry is None:
            rationale_parts.append(f"{pipeline_key} is not installed in the catalog yet; install it before launching.")
            return PipelineRecommendation(
                recommended=True,
                pipeline_key=pipeline_key,
                version=None,
                parameters=None,
                reference_genome=reference_genome,
                rationale=" ".join(rationale_parts),
                confidence=confidence,
                signals=signals,
            )

        return PipelineRecommendation(
            recommended=True,
            pipeline_key=pipeline_key,
            version=entry.version,
            parameters=entry.default_params_json,
            reference_genome=reference_genome,
            rationale=" ".join(rationale_parts),
            confidence=confidence,
            signals=signals,
        )
