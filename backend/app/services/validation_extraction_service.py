"""B2: reproduction-plan extractor (lit_validation, the AI comprehension core).

Reads a paper's full text and, via the org's active LLM provider, emits a structured extraction
(accessions, sample structure, method, quantitative claims, data availability, blockers). The
method is mapped to an nf-core pipeline (B3), and the whole is persisted as a ReproductionPlan +
ComparisonTargets for the human to ratify at the C1 gate.

Built on the existing provider clients and the same fenced-JSON convention the agent-review parser
uses. The extraction is deliberately structured (JSON), not prose, so it can drive the rest of the
pipeline and be shown for approval. Output quality was spiked in spike-00; this service is the
production seam.
"""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ValidationError
from app.models.reproduction_plan import ReproductionPlan
from app.models.validation_study import ValidationStudy
from app.services import llm_provider_config_service
from app.services.literature.accession_manifest_service import (
    AccessionManifestService,
    dominant_library_strategy,
)
from app.services.llm_provider_clients import get_client
from app.services.pipeline_assay_fallback import resolve_pipeline_for_assay
from app.services.pipeline_mapper import library_strategy_conflict
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_classifier_service import CONTROLLED_METRIC_SPECS

logger = logging.getLogger("bioaf.validation_extraction")

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)

# The extraction contract. Kept in the system prompt so every provider returns the same shape.
_SCHEMA_HINT = (
    '{"accessions": ["GEO/SRA/ENA ids, or empty"], '
    '"sample_structure": {"organism": "", "sample_count": 0, "library_layout": "", "chemistry": "", "conditions": []}, '
    '"method": {"assay": "e.g. bulk RNA-seq / scRNA-seq", "tools": [], "reference_build": "", "key_params": {}}, '
    '"differential_design": {"contrasts": [{"name": "e.g. treated vs control", "test_condition": "", '
    '"reference_condition": "", "test_samples": ["sample ids in the test group"], '
    '"reference_samples": ["sample ids in the reference group"]}], '
    '"thresholds": {"log2fc": null, "padj": null}}, '
    '"claims": [{"metric_key": "aligns to a QC metric", "value": 0, "unit": "", "tolerance": null, "source_locator": "section/figure"}], '
    '"data_availability": "deposited | none | restricted", '
    '"blockers": ["reasons the paper cannot be reproduced"]}'
)


_SCALE_HINT = {"fraction": "fraction 0-1", "percent": "percent 0-100", "count": "count"}


def _spec_lines(tier: str) -> str:
    """Render the controlled metrics of one tier as `key | scale | tier | meaning | also written as`.

    One line per metric, pipe-delimited, because the model has to pick between 23 near neighbours and a
    bare key list gives it nothing to pick on. The aliases are the load-bearing column: they are the
    paper's own wording, and they are what turns "8733 significant peaks" into `peak_count`.
    """
    lines = []
    for spec in CONTROLLED_METRIC_SPECS:
        if spec.tier != tier:
            continue
        aliases = ", ".join(spec.aliases) or "no other wording"
        # Where a paper can report the same quantity a different way, say what the computed side is
        # measured on. Without it the model binds a consensus peak count to a per-sample one, which
        # is a wrong answer that looks like a right one.
        basis = f" | computed here as: {spec.basis}" if spec.basis else ""
        lines.append(
            f"  {spec.key} | {_SCALE_HINT.get(spec.scale, spec.scale)} | {spec.tier} | "
            f"{spec.meaning}{basis} | also written as: {aliases}"
        )
    return "\n".join(lines)


def _metric_vocabulary_block() -> str:
    """The controlled QC vocabulary as specs rather than as names, split by evidence tier."""
    return (
        "CONTROLLED QC METRIC VOCABULARY. Each line is:\n"
        "  key | scale | tier | what it measures | also written as: wordings papers use\n\n"
        "FINDING metrics. These are substantive results a paper reports, and they are the ONLY keys "
        "that can earn a validated verdict, so bind one whenever the paper states it:\n"
        f"{_spec_lines('finding')}\n\n"
        "QC FLOOR metrics. These are technical data-quality measures. They show the data is usable, "
        "not that a finding held up:\n"
        f"{_spec_lines('qc_floor')}"
    )


def build_extraction_prompt(full_text: str) -> tuple[str, str]:
    """Return (system, payload) instructing the model to extract the reproduction plan as JSON."""
    system = (
        "You are a computational biology reproduction analyst. Read the paper's full text and extract "
        "only what is needed to reproduce its primary data processing. Respond with a SINGLE fenced "
        "JSON block (```json ... ```) and nothing else, matching exactly this schema:\n\n"
        f"{_SCHEMA_HINT}\n\n"
        "Rules: report a data accession only if the paper actually deposits one; if none, set "
        'accessions to [] and data_availability to "none". Capture the QC-level numbers the paper '
        "reports (alignment rate, read/cell counts, saturation, peaks called, etc.) as claims with a "
        "metric_key drawn from the controlled vocabulary below.\n\n"
        f"{_metric_vocabulary_block()}\n\n"
        "Use the EXACT controlled key whenever the paper reports the quantity that key describes, however the "
        "paper words it, so the claim can be compared automatically. Match on what is measured, not on the "
        "paper's phrasing: \"8733 significant peaks\" is peak_count. Report the claim on the key's own scale "
        "and state the unit you read. Do not qualify a controlled key with the sample or condition it came "
        "from (peak_count, never samd1_chip_peaks). If a claim genuinely measures something no controlled key "
        "describes, or measures only a subset of one (peaks gained in a single condition is not peak_count), "
        "use a clear snake_case key of your own instead of forcing a wrong match. Do not invent values. Use "
        "null when unknown.\n\n"
        "Where a key says what it is computed here as, the paper must be reporting the SAME basis for the "
        "claim to be comparable. A consensus or merged peak set across replicates is not a per-sample peak "
        "call, and a post-trim read count is not a raw one. If the paper reports the other basis, say so in "
        'the unit ("consensus peaks", "reads after trimming") so the claim is shown as evidence '
        "rather than scored against a number it does not correspond to.\n\n"
        "For reference_build, give BOTH the genome assembly and the ANNOTATION the paper aligned "
        'against, exactly as the paper words it (e.g. "GRCh38 / GENCODE v32", "mm10 / Ensembl 102", '
        '"CellRanger refdata-gex-GRCh38-2020-A"). The assembly alone is not the reference: two papers '
        "on the same assembly with different annotation releases do not share a gene set, and that "
        "difference lands in the differential result we are compared against. Leave it empty if the "
        "paper does not say.\n\n"
        "Also capture the paper's PRIMARY DIFFERENTIAL DESIGN in differential_design: the contrast(s) it "
        "tests (which condition is compared against which reference), the sample ids belonging to each "
        "group, and the significance thresholds it used (|log2 fold-change| and adjusted p / FDR). This is "
        "the finding to be reproduced, not the pipeline's parameters. If the paper reports no differential "
        "comparison (a descriptive/QC-only paper), set contrasts to [] and leave thresholds null. Never "
        "fabricate a contrast or a threshold."
    )
    payload = f"Paper full text:\n\n{full_text}"
    return system, payload


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _to_float(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                return None
    return None


# The model reports a free-form reference build ("GRCh38 / Gencode 29", "hg19", "mm10"), but
# plan.reference_genome must be a controlled-vocabulary token or launch_run 422s at the setup gate and
# errors the study. Map common aliases to the canonical assembly token; an unrecognized build resolves
# to None (the launch picks a default) rather than a value guaranteed to fail validation.
#
# Only the CURRENT assembly of each organism is recognized, and deliberately: Zv9 is not GRCz11 and
# Rnor_6.0 is not mRatBN7.2, so folding an older spelling onto the current token would align against
# a genome the paper never used and report the difference as biology. An unrecognized build resolves
# to None and the plan carries a blocker saying so.
_REFERENCE_GENOME_ALIASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("grch38", "hg38"), "GRCh38"),
    (("grch37", "hg19"), "GRCh37"),
    (("grcm39", "mm39"), "GRCm39"),
    (("grcm38", "mm10"), "GRCm38"),
    (("t2t", "chm13"), "T2T-CHM13"),
    (("grcz11", "danrer11"), "GRCz11"),
    (("mratbn7", "rn7"), "mRatBN7.2"),
    # BDGP6 is the assembly FAMILY: Ensembl publishes point releases (BDGP6.32, BDGP6.46) that share
    # a coordinate system and differ in annotation. The token names the family and the launch pins
    # one release, exactly as GRCh38 pins Ensembl 112; `reference_build` keeps the paper's own words
    # beside it so an annotation-driven divergence can still be attributed.
    (("bdgp6", "dm6"), "BDGP6"),
    (("wbcel235", "ce11"), "WBcel235"),
    (("tair10",), "TAIR10"),
)


def _builds_named_in(raw) -> list[str]:
    """Every assembly ``raw`` names, in the order the PAPER names them.

    Scanning the alias table in declaration order made bioAF's own row ordering decide the answer.
    A real paper (10.1038/s41598-023-33729-4) writes "Human hg19, UCSC (RNA-seq annotation); hg38
    implied for EPIC array", and the plan recorded GRCh38 because GRCh38 is the first row. Nothing
    about the paper said so.

    Ordering by position in the TEXT is a claim about the paper instead: a methods section states
    the build it primarily worked in before the ones it mentions in passing. Two spellings of one
    assembly ("GRCh38 (hg38)") collapse to a single entry, so they are never a disagreement.
    """
    text = str(raw or "").lower()
    if not text.strip():
        return []
    found: list[tuple[int, str]] = []
    for needles, token in _REFERENCE_GENOME_ALIASES:
        positions = [text.index(n) for n in needles if n in text]
        if positions:
            found.append((min(positions), token))
    return [token for _at, token in sorted(found)]


def _normalize_reference_genome(raw) -> str | None:
    """The assembly the paper names FIRST, or None when it names none bioAF recognizes."""
    builds = _builds_named_in(raw)
    return builds[0] if builds else None


def reference_genome_alternatives(raw) -> list[str]:
    """The other assemblies the paper named, which the run will NOT align against.

    A multi-assay paper naming a build per assay is the normal case, and one of them is about to be
    chosen for a run that costs money. Empty when the paper names one assembly, or none.
    """
    return _builds_named_in(raw)[1:]


async def scoped_library_strategy(study: ValidationStudy) -> str | None:
    """What the accession this study was scoped to says its data actually IS, or None.

    The paper is prose and prose is compound: a methods section saying "RRBS and RNA-seq" gives the
    mapper one string naming two assays, and the first declared marker wins. The deposit is not
    prose. ENA records a controlled ``library_strategy`` per run, chosen by the depositor, and where
    it contradicts the prose it is the better evidence.

    Best-effort in both directions. Nothing is fetched when no accession has been scoped, and an
    unreachable or multi-assay deposit yields None, which leaves the paper's own words deciding
    exactly as they did before. A study is never blocked on this lookup.
    """
    requested = (study.source_accession or "").strip()
    if not requested:
        logger.info("study %s: no accession scoped, so the paper's own words decide the pipeline", study.id)
        return None
    try:
        manifest = await AccessionManifestService.fetch_manifest(requested)
    except Exception:  # fetch_manifest documents never-raises; a regression there must not error a study
        logger.warning("study %s: reading %s to learn its library strategy failed", study.id, requested, exc_info=True)
        return None

    strategy = dominant_library_strategy(manifest.samples)
    if strategy:
        logger.info("study %s: %s is deposited as %r", study.id, requested, strategy)
    else:
        declared = sorted({(s.get("library_strategy") or "").strip() for s in manifest.samples} - {""})
        why = (
            f"its runs declare more than one ({', '.join(declared)})"
            if len(declared) > 1
            else (manifest.unavailable_reason or "its runs declare no library strategy")
        )
        logger.info(
            "study %s: %s says nothing usable about what it is (%s), so the paper's own words decide",
            study.id,
            requested,
            why,
        )
    return strategy


def _str_or_none(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_subjects(value) -> dict:
    """Coerce a per-sample subject/block map ({sample_id: label}) to a stable {str: str} shape,
    dropping blank keys/values. A non-dict (or empty) yields {} (the default unpaired design)."""
    out: dict[str, str] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            key = str(k).strip()
            label = str(v).strip()
            if key and label:
                out[key] = label
    return out


def _normalize_differential_design(value) -> dict:
    """B2e: coerce the model's differential_design to a stable, human-editable shape.

    Honest-None on missing sub-fields; a QC-only paper yields empty contrasts and null thresholds.
    Never fabricates a contrast. This is the draft the human ratifies/edits at the C1 gate.
    """
    data = _as_dict(value)
    thresholds = _as_dict(data.get("thresholds"))
    contrasts = []
    for c in _as_list(data.get("contrasts")):
        c = _as_dict(c)
        contrasts.append(
            {
                "name": _str_or_none(c.get("name")),
                "test_condition": _str_or_none(c.get("test_condition")),
                "reference_condition": _str_or_none(c.get("reference_condition")),
                "test_samples": [str(s).strip() for s in _as_list(c.get("test_samples")) if str(s).strip()],
                "reference_samples": [str(s).strip() for s in _as_list(c.get("reference_samples")) if str(s).strip()],
                # Optional matched-pairs / blocked design (ADR-069, item #2): a per-sample subject/block
                # label so the DE notebook can run `~ block + condition` (cancels donor-to-donor baseline
                # variance). Empty for the default unpaired design. Human-supplied at the C1 gate (the
                # donor->sample mapping lives in GEO sample metadata, not the paper text).
                "subjects": _normalize_subjects(c.get("subjects")),
            }
        )
    return {
        "contrasts": contrasts,
        "thresholds": {"log2fc": _to_float(thresholds.get("log2fc")), "padj": _to_float(thresholds.get("padj"))},
    }


def _differential_design_or_none(design: dict) -> dict | None:
    """Persist the design only when there is a differential finding to reproduce. A QC-only paper
    (no contrasts) stores None so the plan stays Level-2-only and the driver skips ``reproducing``."""
    return design if design.get("contrasts") else None


def parse_extraction(response_text: str) -> dict:
    """Pull the fenced JSON extraction and normalize it. Never raises; flags parse failure instead."""
    empty = {
        "accessions": [],
        "sample_structure": {},
        "method": {},
        "differential_design": _normalize_differential_design(None),
        "claims": [],
        "data_availability": "unknown",
        "blockers": [],
        "parse_failure": True,
    }
    match = _FENCED_JSON_RE.search(response_text or "")
    if not match:
        return empty
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return empty
    if not isinstance(data, dict):
        return empty

    return {
        "accessions": [str(a).strip() for a in _as_list(data.get("accessions")) if str(a).strip()],
        "sample_structure": _as_dict(data.get("sample_structure")),
        "method": _as_dict(data.get("method")),
        "differential_design": _normalize_differential_design(data.get("differential_design")),
        "claims": [c for c in _as_list(data.get("claims")) if isinstance(c, dict)],
        "data_availability": str(data.get("data_availability") or "unknown"),
        "blockers": [str(b) for b in _as_list(data.get("blockers")) if str(b).strip()],
        "parse_failure": False,
    }


class ValidationExtractionService:
    @staticmethod
    async def extract(
        session: AsyncSession,
        study: ValidationStudy,
        full_text: str,
        org_id: int,
        user_id: int,
    ) -> ReproductionPlan:
        """Extract a ReproductionPlan (+ ComparisonTargets) for ``study`` from ``full_text``.

        Uses the org's active LLM provider. The method is mapped to an nf-core pipeline (B3) and any
        gaps (no accession, unmappable method, parse failure) are recorded as plan blockers rather
        than raised, so the C1 gate can show them.
        """
        cfg = await llm_provider_config_service.get_active(session, org_id)
        if not cfg:
            raise ValidationError("No active LLM provider is configured for this organization.")

        system, payload = build_extraction_prompt(full_text)
        client = get_client(cfg.provider)
        output = await client.submit(prompt=system, payload=payload, model=cfg.model, api_key=cfg.api_key)
        parsed = parse_extraction(output)

        method = parsed["method"]
        library_strategy = await scoped_library_strategy(study)
        # Declared routes first, corrected by what the scoped accession says its data is; anything
        # else is matched against the pipelines this instance can actually run, so a lab that
        # installed the right pipeline is not told its paper is unreproducible. A fallback match is
        # capped at Level-2 by having no _WIRING entry.
        mapping = await resolve_pipeline_for_assay(
            session,
            org_id,
            method.get("assay"),
            method.get("tools"),
            method.get("reference_build"),
            # What the scoped deposit declares itself to be. It outranks the paper's prose where the
            # two disagree, which is the only reason a multi-assay paper can reach the right pipeline.
            library_strategy=library_strategy,
        )

        blockers = list(parsed["blockers"]) + list(mapping.blockers)

        # The deposit could not be honoured: `resolve_pipeline_for_assay` can only offer a pipeline
        # this instance is able to run, so where the right one is neither installed nor in the
        # registry cache the paper's prose route stands and would read the wrong data. Record it as a
        # blocker rather than as a classification: the study is still reproducible, this instance
        # just cannot do it yet, and the C1 gate is where a human decides what to do about that.
        conflict = library_strategy_conflict(mapping.pipeline_key, library_strategy)
        if conflict:
            blockers.append(conflict)

        if parsed["parse_failure"]:
            blockers.append("could not parse a structured extraction from the model response")
        accessions = parsed["accessions"]

        # A requester who named the study's accession has already scoped it, so that is the dataset
        # to reproduce. The extractor's list is a reading of the paper's prose, and prose does not
        # distinguish the data a paper DEPOSITS from the data it merely cites: for
        # 10.1038/s41598-021-93509-w the model returned its own GSE157174 plus GSE114064
        # (transcriptomic) and GSE118189 (another lab's ATAC), and since no endpoint edits
        # `accessions_json`, approving would have fetched all three.
        #
        # The requested accession wins even when the model did not return it, because the paper is
        # often not open access and the requester can know what the extracted text does not say.
        requested = (study.source_accession or "").strip()
        if requested:
            dropped = [a for a in accessions if a.strip().upper() != requested.upper()]
            accessions = [requested]
            if dropped:
                blockers.append(
                    f"The paper also names {', '.join(dropped)}, which is not the accession this "
                    f"study was requested for ({requested}). Only {requested} will be fetched."
                )

        if (parsed["data_availability"] == "none" or not accessions) and not any(
            "accession" in b.lower() for b in blockers
        ):
            blockers.append("no data accession found in the paper")

        raw_genome = method.get("reference_build")
        named_builds = _builds_named_in(raw_genome)
        reference_genome = named_builds[0] if named_builds else None
        if raw_genome and reference_genome is None:
            blockers.append(
                f"could not map the paper's reference genome '{raw_genome}' to a known assembly; "
                "the analysis run will use a default"
            )
        # A paper that ran several assays names a build per assay, and only one of them can be what
        # this run aligns against. Say which was taken and which were not, because the choice is
        # made from word order in a methods section and a scientist can see in one glance whether
        # it is the build their own dataset used.
        if len(named_builds) > 1:
            blockers.append(
                f"The paper names more than one reference genome ({', '.join(named_builds)}). "
                f"{named_builds[0]} was taken, because the paper names it first. Confirm it is the build "
                f"this dataset was aligned to before approving."
            )

        plan = await ReproductionPlanService.create_plan(
            session,
            study,
            user_id,
            accessions=accessions,
            sample_sheet=parsed["sample_structure"],
            pipeline_key=mapping.pipeline_key,
            pipeline_version=mapping.pipeline_version,
            # The model's key_params are experimental metadata (PCR cycles, DE thresholds, ...), not
            # nf-core pipeline parameters; forwarding them makes the analysis run fail param validation.
            # Phase 1 runs the pipeline with its defaults, so do not seed parameters_json from them.
            parameters={},
            # B2e: capture the differential design (the finding to reproduce) for the C1 gate and
            # Level-3. None when the paper reports no contrast, keeping the plan Level-2-only.
            differential_design=_differential_design_or_none(parsed["differential_design"]),
            # Keep the paper's own tool list. It is what lets a divergence be attributed to a named
            # cause (CellRanger vs STARsolo) instead of merely reported.
            tools=[str(t).strip() for t in _as_list(method.get("tools")) if str(t).strip()],
            reference_genome=reference_genome,
            # The controlled token collapses "GRCh38 / Gencode 29" and "GRCh38 / Ensembl 112" onto
            # one value, and the ANNOTATION is the half that decides which genes exist and what they
            # are called. Keep the paper's own words beside it: a DEG concordance can diverge purely
            # because two correct gene sets came from different annotation releases, and that is an
            # attribution a verdict should be able to make rather than blame on the science.
            reference_build=_str_or_none(raw_genome),
            mapping_confidence=mapping.mapping_confidence,
            mapping_notes=mapping.mapping_notes,
            blockers=blockers,
            extractor_model=cfg.model,
            extractor_provider=cfg.provider,
            # Audited, so the deposit's own declaration is on the record even when it agreed with the
            # paper and left no other trace.
            library_strategy=library_strategy,
        )

        targets = []
        for c in parsed["claims"]:
            metric_key = (c.get("metric_key") or "").strip()
            if not metric_key:
                continue
            targets.append(
                {
                    "metric_key": metric_key,
                    "claimed_value": _to_float(c.get("value")),
                    "unit": c.get("unit"),
                    "tolerance": _to_float(c.get("tolerance")),
                    "source_locator": c.get("source_locator"),
                }
            )
        await ReproductionPlanService.add_comparison_targets(session, plan, targets)
        return plan
