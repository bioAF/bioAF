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

import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ValidationError
from app.models.reproduction_plan import ReproductionPlan
from app.models.validation_study import ValidationStudy
from app.services import llm_provider_config_service
from app.models.organization import Organization
from app.services.contrast_selection import select_contrast
from app.services.llm_feature_models import FEATURE_LITERATURE_VALIDATION
from app.services.validation_autonomy import AUTONOMY_ASSISTED, AUTONOMY_AUTONOMOUS
from app.services.literature.accession_manifest_service import (
    AccessionManifestService,
    dominant_library_strategy,
)
from app.services.llm_decision import confidence_of, decide, fenced_json
from app.services.llm_provider_clients import get_client
from app.services.validation_issue_service import ValidationIssueService
from app.services.pipeline_assay_fallback import resolve_pipeline_for_assay
from app.services.pipeline_mapper import library_strategy_conflict
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_claim_cutoffs import describe_cutoff
from app.services.validation_classifier_service import (
    BINDING_FAILED,
    CONTROLLED_METRIC_KEYS,
    CONTROLLED_METRIC_SPECS,
)

logger = logging.getLogger("bioaf.validation_extraction")

# The extraction contract. Kept in the system prompt so every provider returns the same shape.
_SCHEMA_HINT = (
    '{"accessions": ["GEO/SRA/ENA ids, or empty"], '
    '"sample_structure": {"organism": "", "sample_count": 0, "library_layout": "", "chemistry": "", "conditions": []}, '
    '"method": {"assay": "e.g. bulk RNA-seq / scRNA-seq", "tools": [], "reference_build": "", "key_params": {}}, '
    '"reported_experiments": [{"id": "e1", "assay": "the ONE assay this experiment used", '
    '"description": "the paper\'s words for it", "conditions": [], "time_points": [], "organism": "", '
    '"reference": {"assembly": "the genome assembly as stated, or empty", "assembly_quote": "the paper\'s words", '
    '"annotation": "the annotation release as stated, or empty", "annotation_quote": "the paper\'s words"}, '
    '"tools": [], "claim_indices": [0], "contrast_indices": [0], '
    '"resources": ["accessions, sub-series or DOIs this experiment\'s data are deposited under"]}], '
    '"resources": [{"identifier": "an accession, DOI, URL or supplement label", '
    '"type": "sequencing_data | proteomics_data | structure | binding_assay | code | supplementary_file | other_data", '
    '"role": "what the paper says it holds, in the paper\'s words", '
    '"stated_in": "data availability | methods | figure legend | supplementary materials", '
    '"quote": "the paper\'s words"}], '
    '"differential_design": {"contrasts": [{"name": "e.g. treated vs control", "test_condition": "", '
    '"reference_condition": "", "test_samples": ["sample ids in the test group"], '
    '"reference_samples": ["sample ids in the reference group"], '
    '"assay": "the assay this contrast was measured on", '
    '"finding_claim_index": "index into claims of the claim stating this contrast\'s headline finding, or null"}]}, '
    '"claims": [{"metric_key": "aligns to a QC metric, or \'\' when nothing measures it", '
    '"claim_text": "the paper\'s own sentence, quoted", "value": 0, "unit": "", "tolerance": null, '
    '"source_locator": "section/figure", "sample_subset": "which samples, e.g. whole embryo", '
    '"qc_stage": "as collected | post-QC | as analysed", "direction": "up | down | null, relative to the '
    'reference arm", "threshold": null, "threshold_kind": "padj | pvalue | abs_log2fc | null", '
    '"contrast": "the name of the contrast this claim reports on, or null", '
    '"cutoffs": [{"kind": "pvalue | padj | fdr | qvalue | abs_log2fc | fold_change", '
    '"operator": "< | <= | > | >=", "value": 0}], '
    '"output_type": "count | percentage | gene_set_size | ratio"}], '
    '"significance_ambiguities": [{"claim_index": 0, "readings": [{"kind": "pvalue | padj | fdr | qvalue", '
    '"operator": "< | <=", "value": 0, "quote": "the paper\'s exact words for this reading"}]}], '
    '"data_availability": "deposited | none | restricted", '
    '"code_availability": [{"kind": "github|gitlab|zenodo|codeocean|supplementary|none", "url": "", '
    '"identifier": "e.g. a DOI", "stated_in": "methods | data availability | code availability", '
    '"language": "R|Python|shell|unknown", "confidence": 0.0}], '
    '"blockers": [{"text": "a reason the paper cannot be reproduced", '
    '"kind": "sample_assignment | data_access | missing_detail | no_accession | method_mismatch | other"}]}'
)


_CODE_KINDS = ("github", "gitlab", "zenodo", "codeocean", "supplementary", "other")

# Host -> kind. The URL is EVIDENCE and the model's `kind` is a claim about it, so where the two
# disagree the URL wins. Same precedence the depositor's `Type` takes over a filename in step 1.
_CODE_HOSTS = (
    ("github.com", "github"),
    ("gitlab.com", "gitlab"),
    ("zenodo.org", "zenodo"),
    ("codeocean.com", "codeocean"),
)


def parse_code_availability(raw) -> list[dict]:
    """Normalize the extractor's `code_availability` block into stored rows.

    Returns [] rather than None for anything unusable, because the COLUMN's null carries a different
    meaning ("planned before this existed") than an empty list ("we looked and the paper named
    none"), and only the caller can tell which of those it is.

    A row with neither a URL nor an identifier is dropped: "code available on request" is not a
    location. A non-http URL is dropped because the C1 gate renders these as links and a
    `javascript:` or `file:` URL must never reach an href.
    """
    if not isinstance(raw, list):
        return []

    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue

        url = str(item.get("url") or "").strip() or None
        if url and not url.lower().startswith(("http://", "https://")):
            url = None
        identifier = str(item.get("identifier") or "").strip() or None
        if not url and not identifier:
            continue

        kind = str(item.get("kind") or "").strip().lower()
        if url:
            for host, host_kind in _CODE_HOSTS:
                if host in url.lower():
                    kind = host_kind
                    break
        # An unenumerated host is still a place the code is. Keeping the row and relabelling it
        # `other` loses a vocabulary entry; dropping it would lose the location.
        if kind not in _CODE_KINDS:
            kind = "other"

        confidence = item.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            confidence = 0.0

        key = (url or "", identifier or "")
        if key in seen:
            continue
        seen.add(key)

        rows.append(
            {
                "kind": kind,
                "url": url,
                "identifier": identifier,
                "stated_in": str(item.get("stated_in") or "").strip() or None,
                "language": str(item.get("language") or "").strip() or "unknown",
                "confidence": max(0.0, min(1.0, float(confidence))),
            }
        )
    return rows


_SCALE_HINT = {"fraction": "fraction 0-1", "percent": "percent 0-100", "count": "count"}


def _spec_lines(tier: str) -> str:
    """Render the controlled metrics of one tier as `key | scale | tier | meaning | also written as`.

    One line per metric, pipe-delimited, because the model has to pick between 23 near neighbours and a
    bare key list gives it nothing to pick on. The aliases are the load-bearing column: they are the
    paper's own wording, and they are what turns a sentence counting "significant peaks" into
    `peak_count`.
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
        'paper\'s phrasing: a sentence counting "significant peaks" is peak_count. Report the claim on the '
        "key's own scale and state the unit you read. Do not qualify a controlled key with the sample or "
        "condition it came from (peak_count, never condition_a_peaks). If a claim genuinely measures something no controlled key "
        "describes, or measures only a subset of one (peaks gained in a single condition is not peak_count), "
        "use a clear snake_case key of your own instead of forcing a wrong match. Do not invent values. Use "
        "null when unknown.\n\n"
        "Where a key says what it is computed here as, the paper must be reporting the SAME basis for the "
        "claim to be comparable. A consensus or merged peak set across replicates is not a per-sample peak "
        "call, and a post-trim read count is not a raw one. If the paper reports the other basis, say so in "
        'the unit ("consensus peaks", "reads after trimming") so the claim is shown as evidence '
        "rather than scored against a number it does not correspond to.\n\n"
        # change_7.5 section 2.2: each experiment separately. One paper-level method for two assays
        # chose the workflow of the first and refused the claims of the second.
        "Describe EACH EXPERIMENT the paper reports separately in reported_experiments, with one assay "
        "each: a paper that ran ChIP-seq and RNA-seq reports two experiments, never one experiment "
        "naming both. Place every claim and every contrast in exactly one experiment, by its index in "
        "claims and in differential_design.contrasts. Give each experiment its reference as the paper "
        "states it for THAT experiment: the genome assembly and the annotation release separately, each "
        "with the paper's own words as its quote; leave a part empty where the paper does not state it, "
        "and never fill one in from the organism. List in its resources the accessions, sub-series or "
        "DOIs the paper says hold that experiment's data.\n\n"
        "List EVERY resource the paper names in resources, whether or not it is sequencing data: every "
        "accession (GEO, SRA, ENA, EGA, ArrayExpress, PRIDE, MassIVE, PDB, EMDB), every repository or "
        "DOI (GitHub, GitLab, Zenodo, figshare) and every supplementary file or table, each with what "
        "the paper says it holds in the paper's own words and where the paper says it.\n\n"
        "For reference_build, give BOTH the genome assembly and the ANNOTATION the paper aligned "
        'against, exactly as the paper words it (e.g. "GRCh38 / GENCODE v32", "mm10 / Ensembl 102", '
        '"CellRanger refdata-gex-GRCh38-2020-A"). The assembly alone is not the reference: two papers '
        "on the same assembly with different annotation releases do not share a gene set, and that "
        "difference lands in the differential result we are compared against. Leave it empty if the "
        "paper does not say.\n\n"
        "Also capture the paper's PRIMARY DIFFERENTIAL DESIGN in differential_design: the contrast(s) it "
        "tests (which condition is compared against which reference), the sample ids belonging to each "
        "group, and the assay each contrast was measured on. This is the finding to be reproduced, not "
        "the pipeline's parameters. A contrast carries no cutoffs of its own: its cutoffs are the ones its "
        "claims state, below. If the paper reports no differential comparison (a descriptive/QC-only "
        "paper), set contrasts to []. Never fabricate a contrast.\n\n"
        # change_7.5 section 1.1: the measure as stated. The old wording named one convention as usual,
        # and study 38's stated nominal P value came back as a blocker calling it ambiguous.
        "Record every significance measure EXACTLY AS THE PAPER STATES IT, on the claim it governs: its "
        "kind, its operator and its value. A P value is pvalue, an adjusted P value is padj, an FDR is "
        "fdr and a q-value is qvalue. Each of these is the paper's own definition of its finding, and "
        "none of them is more usual or more correct than another. Never infer a measure the text does not "
        "state, never turn one kind into another, and never supply a cutoff the paper does not state.\n\n"
        # change_7.3 section 7: cutoffs belong to claims, and a set and its subset are two claims.
        'Give every claim its own cutoffs, one entry per cutoff: "padj < 0.05 and |log2FC| > 2" is two '
        "cutoffs. Name the contrast a claim reports on, and for each contrast give finding_claim_index, "
        "the claim that states its headline finding. A sentence that states a set and a subset of it "
        '("N genes were significant, M of which changed more than two-fold") makes TWO claims, each '
        "with its own value and cutoffs; never merge them. Likewise, when the paper says samples were "
        'excluded, the count before the exclusions is one claim with qc_stage "as collected" and the '
        'number analysed after them is another with qc_stage "as analysed".\n\n'
        "A claim's significance is ambiguous ONLY when the paper itself supports more than one reading "
        "for that same claim: for example, a results sentence gives the counts at P < 0.001 while the "
        "methods say the same counts were taken at FDR < 0.05. Record that in significance_ambiguities: "
        "the claim's index, and each reading with the paper's exact words as its quote. Never write a "
        "blocker saying a stated measure is missing, ambiguous, nominal or unadjusted. A stated measure is "
        "the paper's definition; a real ambiguity is shown with its quotes, or not recorded at all.\n\n"
        "Give each blocker a kind: sample_assignment when which sample belongs to which group is not "
        "stated, data_access when the data sits behind an access agreement, missing_detail for an "
        "unstated methods detail, no_accession when no data deposit is named, method_mismatch when the "
        "paper's method is not one a standard pipeline runs, other for anything else."
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
# errors the study. change_7.5 section 1.3: the tables live in `validation_reference`, matched as whole
# tokens, and an older assembly (mm9, hg18, Zv9) is recognised as the assembly it is. It never resolves
# to the current token, and a paper naming one gets no launch token at all: nothing substitutes the
# nearest current assembly.


def _builds_named_in(raw) -> list[str]:
    """Every CURRENT assembly ``raw`` names, in the order the PAPER names them.

    Scanning the alias table in declaration order made bioAF's own row ordering decide the answer.
    A real paper (10.1038/s41598-023-33729-4) writes "Human hg19, UCSC (RNA-seq annotation); hg38
    implied for EPIC array", and the plan recorded GRCh38 because GRCh38 is the first row. Nothing
    about the paper said so.

    Ordering by position in the TEXT is a claim about the paper instead: a methods section states
    the build it primarily worked in before the ones it mentions in passing. Two spellings of one
    assembly ("GRCh38 (hg38)") collapse to a single entry, so they are never a disagreement.
    """
    from app.services.validation_reference import assemblies_named

    return [a["assembly"] for a in assemblies_named(raw) if not a["historical"]]


def _normalize_reference_genome(raw) -> str | None:
    """The assembly the paper names FIRST, or None when it names none bioAF recognizes as current, or
    names an older assembly anywhere (one reference per paper, never a substitution)."""
    from app.services.validation_reference import assemblies_named

    named = assemblies_named(raw)
    if not named or any(a["historical"] for a in named):
        return None
    return named[0]["assembly"]


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


def _contrast_thresholds(own, paper_level: dict) -> dict:
    """This contrast's cutoffs, falling back to the paper-level pair when it states none of its own.

    A contrast that explicitly says a cutoff does not apply (``log2fc: null`` on a windowed binding
    analysis) must keep that null rather than inherit the paper's DEG cutoff, so the fallback is
    per-key and only fills a key the contrast omitted entirely.
    """
    own_d = _as_dict(own)
    out = {}
    for key in ("log2fc", "padj"):
        out[key] = _to_float(own_d[key]) if key in own_d else _to_float(paper_level.get(key))
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
                # Which assay measured this contrast. The selector needs it: a paper can report two
                # contrasts on the SAME assay, and then the pipeline key alone cannot tell them apart.
                "assay": _str_or_none(c.get("assay")),
                "reference_condition": _str_or_none(c.get("reference_condition")),
                "test_samples": [str(s).strip() for s in _as_list(c.get("test_samples")) if str(s).strip()],
                "reference_samples": [str(s).strip() for s in _as_list(c.get("reference_samples")) if str(s).strip()],
                # Optional matched-pairs / blocked design (ADR-069, item #2): a per-sample subject/block
                # label so the DE notebook can run `~ block + condition` (cancels donor-to-donor baseline
                # variance). Empty for the default unpaired design. Human-supplied at the C1 gate (the
                # donor->sample mapping lives in GEO sample metadata, not the paper text).
                "subjects": _normalize_subjects(c.get("subjects")),
                # A paper states its cutoffs PER FINDING: DEGs at |log2FC| >= 1 and FDR < 0.05,
                # differential binding on FDR alone. One pair for the whole paper flattens those into
                # a number that is wrong for at least one of its assays, and the wrongness is silent:
                # the RNA-seq cutoff applied to a windowed csaw table cut 92 significant intervals to
                # 33. The paper-level pair below is the fallback, for a model that answered the older
                # prompt with one pair, and for every plan written before this existed.
                "thresholds": _contrast_thresholds(c.get("thresholds"), thresholds),
            }
        )
        # change_7.3 section 7: which of the claims is this contrast's finding, so its threshold can
        # be taken from that claim rather than from whichever pair the extractor attached. Only kept
        # when the model named one, so the stored design is unchanged for every other contrast.
        finding = c.get("finding_claim_index")
        if isinstance(finding, int) and not isinstance(finding, bool):
            contrasts[-1]["finding_claim_index"] = finding
    return {
        "contrasts": contrasts,
        # Kept: stored plans and the Level-3 wiring read it, and a single-contrast paper has exactly
        # one answer either way.
        "thresholds": {"log2fc": _to_float(thresholds.get("log2fc")), "padj": _to_float(thresholds.get("padj"))},
    }


def _differential_design_or_none(design: dict) -> dict | None:
    """Persist the design only when there is a differential finding to reproduce. A QC-only paper
    (no contrasts) stores None so the plan stays Level-2-only and the driver skips ``reproducing``."""
    return design if design.get("contrasts") else None


def parse_extraction(response_text: str, *, full_text: str | None = None) -> dict:
    """Pull the fenced JSON extraction and normalize it. Never raises; flags parse failure instead.

    ``full_text`` is the paper the model read. change_7.5 section 1.1: a significance ambiguity is kept
    only when each of its quotes is found there, so without it none can be shown and none is kept.
    """
    empty = {
        "accessions": [],
        "sample_structure": {},
        "method": {},
        "differential_design": _normalize_differential_design(None),
        "claims": [],
        "data_availability": "unknown",
        # Where the authors said their analysis code lives (plan_7 step 3). The prompt has always
        # asked for it and the column has always existed; this dict never carried it, so
        # `extract`'s `parsed.get("code_availability")` read None on every paper and the column
        # shipped empty. Found by step 13, which needs the answer to fill the checklist's code rows.
        "code_availability": [],
        "significance_ambiguities": [],
        "reported_experiments": [],
        "resources": [],
        "blockers": [],
        "blocker_kinds": [],
        "parse_failure": True,
    }
    data = fenced_json(response_text)
    if data is None:
        return empty

    claims = [c for c in _as_list(data.get("claims")) if isinstance(c, dict)]
    blockers, blocker_kinds = _typed_blockers(data.get("blockers"))
    blockers, blocker_kinds = _without_stated_significance_blockers(blockers, blocker_kinds, claims)
    return {
        "accessions": [str(a).strip() for a in _as_list(data.get("accessions")) if str(a).strip()],
        "sample_structure": _as_dict(data.get("sample_structure")),
        "method": _as_dict(data.get("method")),
        "differential_design": _normalize_differential_design(data.get("differential_design")),
        "claims": claims,
        "data_availability": str(data.get("data_availability") or "unknown"),
        # Normalized by `parse_code_availability` at the point of storage; kept raw here so a paper
        # that named nothing reads as `[]` ("we looked and it named none") rather than as a missing
        # key, which step 13 renders as UNKNOWN ("we never asked").
        "code_availability": [c for c in _as_list(data.get("code_availability")) if isinstance(c, dict)],
        "significance_ambiguities": _shown_significance_ambiguities(
            data.get("significance_ambiguities"), full_text, claim_count=len(claims)
        ),
        # change_7.5 stage 2: validated in `extract`, where the claims and contrasts they index are known.
        "reported_experiments": _as_list(data.get("reported_experiments")),
        "resources": [r for r in _as_list(data.get("resources")) if isinstance(r, dict)],
        "blockers": blockers,
        "blocker_kinds": blocker_kinds,
        "parse_failure": False,
    }


# A blocker sentence that is about a statistical significance measure. Matched only to decide whether
# the sentence contradicts a measure the paper states; never to read what the measure is.
_SIGNIFICANCE_BLOCKER = re.compile(
    r"\b(?:p[\s-]?values?|padj|p\.adj|adjusted\s+p|fdr|false\s+discovery|q[\s-]?values?|nominal|"
    r"significance\s+(?:threshold|cutoff|cut-off|level|definition|measure|basis))\b|\bp\s*[<≤=]",
    re.IGNORECASE,
)


def _without_stated_significance_blockers(
    blockers: list[str], blocker_kinds: list[dict], claims: list[dict]
) -> tuple[list[str], list[dict]]:
    """Drop a blocker about significance when a claim states its significance measure.

    change_7.5 section 1.1: study 38 stated its counts at P < 0.01 and the extraction wrote a blocker
    calling that cutoff ambiguous. A stated measure is the paper's definition, and a real ambiguity is
    recorded with its quotes in ``significance_ambiguities``. Where no claim states any measure, a
    blocker saying none is stated contradicts nothing, and it stands.
    """
    from app.services.validation_claim_cutoffs import SIGNIFICANCE_KINDS, claim_cutoffs

    stated = any(cut["kind"] in SIGNIFICANCE_KINDS for claim in claims for cut in claim_cutoffs(claim))
    if not stated:
        return blockers, blocker_kinds
    dropped = [b for b in blockers if _SIGNIFICANCE_BLOCKER.search(b)]
    if dropped:
        logger.info("dropped %d blocker(s) about a significance measure a claim states: %s", len(dropped), dropped)
    kept = [b for b in blockers if b not in dropped]
    return kept, [k for k in blocker_kinds if k["text"] in kept]


def _shown_significance_ambiguities(raw, full_text: str | None, *, claim_count: int) -> list[dict]:
    """The ambiguities the paper's own text shows. Anything asserted and not shown is dropped.

    change_7.5 section 1.1: an entry is kept only when it has at least two distinct readings, each a
    well-formed significance cutoff whose quote is found in the paper. Otherwise the claim's stated
    reading stands. Two spellings of one definition (an FDR and a q-value) are one reading.
    """
    from app.services.validation_claim_cutoffs import significance_cutoff
    from app.services.validation_passages import quote_in_text

    if not full_text:
        return []
    kept: list[dict] = []
    for entry in _as_list(raw):
        entry = _as_dict(entry)
        index = entry.get("claim_index")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < claim_count:
            continue
        readings: list[dict] = []
        shown = True
        for raw_reading in _as_list(entry.get("readings")):
            reading = _as_dict(raw_reading)
            cutoff = significance_cutoff(reading.get("kind"), reading.get("operator"), reading.get("value"))
            quote = str(reading.get("quote") or "").strip()
            if cutoff is None or not quote or not quote_in_text(quote, full_text):
                shown = False
                break
            readings.append({**cutoff, "quote": quote})
        distinct = {(r["kind"], r["operator"], r["value"]) for r in readings}
        if shown and len(distinct) >= 2:
            kept.append({"claim_index": index, "readings": readings})
    return kept


def _typed_blockers(raw) -> tuple[list[str], list[dict]]:
    """Blocker sentences, and the kind the extraction gave each one.

    change_7.3 section 7: the consistency pass read blockers with two regexes, and a paraphrase read
    as no contradiction. The model now states a kind beside each sentence; a bare string (an older
    answer, or a model ignoring the schema) is kept as a sentence with no kind, and only then does
    the regex fallback apply.
    """
    from app.services.validation_consistency import BLOCKER_KINDS

    sentences: list[str] = []
    kinds: list[dict] = []
    for item in _as_list(raw):
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            kind = str(item.get("kind") or "").strip().lower()
            if not text:
                continue
            sentences.append(text)
            if kind in BLOCKER_KINDS:
                kinds.append({"text": text, "kind": kind})
        elif str(item).strip():
            sentences.append(str(item).strip())
    return sentences, kinds


# ---- plan_6 step 2: the binding call ----------------------------------------------------------
#
# The extraction call reads a whole paper and emits its claims. This call does one thing: decide,
# per claim, which controlled metric it measures, WHY, and how sure it is. It exists because the
# extraction call's binding was unattributable and unreviewable: a claim either landed on a key or it
# did not, and nothing recorded whether that was a judgment or an accident of the alias table.
#
# Declining is a first-class answer here. A per-condition subset is not a total, and a paper that
# reports nothing bioAF computes must be able to say so without a wrong binding standing in for it.


def build_binding_prompt(
    claims: list[dict],
    *,
    previous: list[dict] | None = None,
    inventory: str | None = None,
    statements: list[str] | None = None,
) -> tuple[str, str]:
    """Return (system, payload) asking the model to bind each claim to a controlled metric, or decline.

    change_7.1 section 3: the claim now travels with the sentence it came from and, where the
    supplements have been inspected, with what they hold. Binding "transcripts detected = 10500"
    from a metric name alone meant deciding without the sentence saying which samples it describes,
    without the table saying 54 were collected and 51 analysed, and without the results table where
    88 of 194 rows clear the fold-change cutoff. All three distinctions were then lost.
    """
    system = (
        "You are binding a paper's quantitative claims to a controlled vocabulary of QC metrics. For "
        "each claim you are given, decide which ONE controlled metric it measures, or decline.\n\n"
        f"{_metric_vocabulary_block()}\n\n"
        "Respond with a SINGLE fenced JSON block (```json ... ```) and nothing else:\n"
        '{"bindings": [{"claim_index": 0, "bound_key": "peak_count" or null, "reason": "one sentence", '
        '"confidence": 0.0 to 1.0, "sample_subset": "which samples the number describes, or null", '
        '"qc_stage": "as collected | post-QC | as analysed | null", '
        '"direction": "up | down | null, relative to the reference arm", '
        '"threshold": null, "threshold_kind": "padj | pvalue | abs_log2fc | null", '
        '"output_type": "count | percentage | gene_set_size | ratio | null", '
        '"measurement_basis": "cell | sample | library | subject | cohort | null"}]}\n\n'
        # change_7.3 section 7: the context fields were only ever volunteered. A reconciliation call
        # that can correct "54 samples, post-QC" to "as collected" has to be ASKED for qc_stage.
        "The context fields say what the number actually is. Fill each one the evidence settles and "
        "leave it null otherwise; null keeps the earlier reading rather than blanking it.\n\n"
        "Rules:\n"
        "- Bind only when the claim measures the SAME quantity the metric describes. A per-condition or "
        "differential subset is not a total: peaks gained in one condition is not peak_count, and "
        "differentially expressed genes is not total_genes_detected.\n"
        "- Respect the basis. Where a metric says what it is computed here as, a claim measured another "
        "way is not the same number: a consensus or merged peak set across replicates is not a "
        "per-sample peak call, and a post-trim read count is not a raw one. Decline those, and say which "
        "basis the paper used.\n"
        "- DECLINING IS A CORRECT ANSWER. Use bound_key null whenever no controlled metric measures the "
        "claim, and give the reason. A wrong binding is worse than no binding, because it is compared "
        "against a number it does not correspond to and the difference is reported as the paper's.\n"
        "- Never invent a key. bound_key must be one of the keys listed above, or null.\n"
        "- Every claim gets exactly one row, in the order given, and every row carries a reason.\n"
        "- confidence is your own certainty in THIS binding: 1.0 when the claim states the metric in so "
        "many words, lower when you are reading intent from context.\n"
        "- Where a claim lists its stated cutoffs, they are the paper's own definition. Never change their "
        "kind: a P value is not an adjusted P value. Leave threshold and threshold_kind null unless you are "
        "restating one of them exactly."
    )
    if statements:
        system += (
            "\n\nYou are also given the paper's own statements about its samples and quality control. "
            "When the paper says samples were excluded, a count from before the exclusions is `as "
            "collected` and the number analysed after them is a different population; set qc_stage to "
            "say which one each claim describes."
        )
    if inventory:
        system += (
            "\n\nYou are also given what the paper's SUPPLEMENTS hold. Use them to decide what a claim "
            "actually counts. A supplement can show that a number describes a different population "
            "than the claim's wording suggests (samples collected versus samples analysed after QC), "
            "or that two numbers in one sentence are two claims at different thresholds. Where the "
            "supplement contradicts your reading of the prose, say so in the reason and bind to what "
            "the data supports. A supplement bioAF has not retrieved proves nothing either way."
        )
    lines = []
    for i, c in enumerate(claims):
        line = (
            f"[{i}] key={c.get('metric_key')!r} value={c.get('value')!r} unit={c.get('unit')!r} "
            f"where={c.get('source_locator')!r}"
        )
        # The paper's own sentence. A metric name is the extractor's paraphrase; this is evidence.
        passage = (c.get("claim_text") or "").strip()
        if passage:
            line += f"\n     the paper says: {passage}"
        cutoffs = [cut for cut in c.get("cutoffs") or [] if isinstance(cut, dict)]
        if cutoffs:
            line += "\n     stated cutoffs: " + " and ".join(describe_cutoff(cut) for cut in cutoffs)
        # change_7.3 section 7: the passage around it, kept at read time, so the sentence arrives with
        # the context that says which samples and which stage it counts.
        context = (c.get("passage") or "").strip()
        if context and context != passage:
            line += f"\n     in context: {context}"
        lines.append(line)
    payload = "Claims to bind:\n\n" + "\n".join(lines)
    if statements:
        payload += "\n\nThe paper's statements about its samples and quality control:\n" + "\n".join(
            f"- {statement}" for statement in statements
        )
    if inventory:
        payload += "\n\n" + inventory
    if previous:
        prior = "\n".join(
            f"[{d.get('claim_index')}] you answered {d.get('bound_key')!r} because: {d.get('reason')}" for d in previous
        )
        payload += (
            "\n\nYou already reviewed these claims and bound NONE of them:\n\n"
            f"{prior}\n\n"
            "Reconsider. Binding nothing is the right answer when this paper genuinely reports no "
            "quantity in the vocabulary, and it is the wrong one if you were reading a claim too "
            "narrowly. Answer again for every claim, and keep a decline where a decline is correct."
        )
    return system, payload


# The clamp lives in `llm_decision` now, with every other caller's copy of it. Kept as a local
# name because this module's own tests and helpers read better with it.
_confidence = confidence_of


def parse_binding(response_text: str) -> list[dict]:
    """Pull the fenced binding decisions. Never raises; a key outside the vocabulary is refused here.

    The prompt asks the model not to invent a key, and the parser enforces it: an invented key would
    persist as a binding and be compared against a metric that does not exist, which is the one
    failure this call is meant to remove.
    """
    data = fenced_json(response_text)
    if data is None:
        return []

    rows = []
    for item in _as_list(data.get("bindings")):
        if not isinstance(item, dict):
            continue
        raw_key = item.get("bound_key")
        key = str(raw_key).strip() if raw_key is not None else None
        reason = str(item.get("reason") or "").strip()
        confidence = _confidence(item.get("confidence"))
        # An explicit null is the model declining; an invented key is the model trying and failing.
        # Both leave the claim unbound and they are not the same event, so step 4 can tell them apart.
        declined = raw_key is None
        if key and key not in CONTROLLED_METRIC_KEYS:
            reason = (
                f"the model answered '{key}', which is not a controlled metric key, so the claim is "
                f"left unbound{': ' + reason if reason else ''}"
            )
            key, confidence = None, 0.0
        row = {
            "claim_index": item.get("claim_index"),
            "bound_key": key or None,
            "reason": reason,
            "confidence": confidence,
            "declined": declined,
        }
        # change_7.1 section 6: a reconciliation call can correct the claim's CONTEXT even when it
        # declines the metric, and those corrections are the point of showing it the supplements.
        # Dropping them here meant the reconciliation ran, answered, and changed nothing.
        for field in _CONTEXT_DECISION_FIELDS:
            value = item.get(field)
            if value is not None:
                row[field] = value
        rows.append(row)
    return rows


# The claim-context fields a binding decision may revise, alongside the metric it binds to.
_CONTEXT_DECISION_FIELDS = (
    "sample_subset",
    "qc_stage",
    "direction",
    "threshold",
    "threshold_kind",
    "output_type",
    "measurement_basis",
)


BINDING_FAILURE_BLOCKER = "The model could not map any of this paper's claims to a measurable metric."

# The step in the user's language, for the issues section of the report.
CLAIM_BINDING_INTENT = "binding the paper's claims to measurable metrics"
PAPER_READING_INTENT = "reading the paper and extracting its methods and claims"


def binding_failure_blocker(decisions: list[dict]) -> str | None:
    """The plan blocker for a claim set that bound nothing, or None while anything bound.

    Two different events produce zero bindings and they need different sentences. Study 4 reported
    "none of the paper's claimed metrics could be compared to a computed QC metric", which reads
    exactly like an unreproducible paper and was in fact a paper whose claims are DE gene counts: real
    results that bioAF does not compute. Blaming the model there is as wrong as blaming the paper when
    the model is the one that failed.
    """
    if not decisions or any(d.get("bound_key") for d in decisions):
        return None
    if all(d.get("declined") for d in decisions):
        return (
            "This paper's quantitative claims do not correspond to any metric bioAF computes, so "
            "there is nothing to compare at Level 2. The model reviewed every claim and declined "
            "each one with a reason."
        )
    return BINDING_FAILURE_BLOCKER


async def bind_claims(
    claims: list[dict],
    *,
    client,
    model: str,
    api_key: str | None,
    inventory: str | None = None,
    previous: list[dict] | None = None,
    on_issue=None,
    statements: list[str] | None = None,
) -> list[dict]:
    """Ask the model which controlled metric each claim measures. One row per claim, in claim order.

    A claim the model said nothing about comes back undecided rather than missing, so a short or
    scrambled answer cannot silently drop a claim out of the plan.

    ``previous`` re-asks with the last attempt's own answers in front of it. It is used once, when a
    whole paper bound nothing, because that is the case where a second look is worth its cost.
    """
    if not claims:
        return []

    system, payload = build_binding_prompt(claims, previous=previous, inventory=inventory, statements=statements)
    # `allowed` is the controlled vocabulary. An invented key would persist as a binding and be
    # compared against a metric that does not exist, which is the one failure this call removes.
    decision = await decide(
        intent=CLAIM_BINDING_INTENT,
        system=system,
        payload=payload,
        client=client,
        model=model,
        api_key=api_key,
        allowed=CONTROLLED_METRIC_KEYS,
    )
    if not decision.ok:
        # change_7.1 section 3. This used to return [], which left every target on `alias_table`
        # with a NULL bound_key, and the comparison then resolved the claim through the alias table
        # and printed a verdict. Groff's binding failed exactly this way and the study still showed
        # comparisons as though a model had approved them.
        #
        # A FAILURE is not a DECLINE. Declining is an answer the model gives on purpose, and the
        # alias table is the right fallback for it. An unreadable response is not an answer, and
        # letting it fall back manufactures agreement out of an outage.
        if on_issue:
            on_issue(decision.as_issue(impact="degraded"))
        return [
            {
                "claim_index": i,
                "bound_key": None,
                "reason": "the binding call did not return a readable decision for this claim",
                "confidence": 0.0,
                "declined": False,
                "bound_by": BINDING_FAILED,
            }
            for i in range(len(claims))
        ]

    by_index = {}
    for row in parse_binding(decision.text):
        idx = row.get("claim_index")
        if isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < len(claims):
            by_index.setdefault(idx, row)

    return [
        by_index.get(
            i,
            {
                "claim_index": i,
                "bound_key": None,
                "reason": "the model returned no binding decision for this claim",
                "confidence": 0.0,
                "declined": False,
            },
        )
        for i in range(len(claims))
    ]


CUTOFF_STEP = "settling a claim's statistical cutoff"


def _cutoff_issue(disagreement: str) -> dict:
    """change_7.4 section 1.6: a claim whose threshold and cutoffs disagree, as an issue. The cutoff
    is not settled, which is a step that could not be performed, not a model's failure."""
    return {
        "step": CUTOFF_STEP,
        "outcome": "not_performed",
        "impact": "degraded",
        "message": disagreement,
        "model": None,
    }


async def _scoped_sample_titles(study) -> list[str]:
    """The titles of the samples this run is actually scoped to, for the contrast selector.

    Two contrasts measured on the same assay are indistinguishable from the pipeline key alone; what
    separates them is which conditions were sequenced in THIS deposit. Best-effort: an unreachable
    manifest just means the selector decides on names, as it did before.
    """
    requested = (study.source_accession or "").strip()
    if not requested:
        return []
    try:
        manifest = await AccessionManifestService.fetch_manifest(requested)
    except Exception:  # noqa: BLE001 - the selector degrades, it does not fail
        return []
    return [str(s.get("title") or "").strip() for s in manifest.samples if (s.get("title") or "").strip()]


async def _select_contrast_for(
    session, study, contrasts, pipeline_key, assay, cfg, client, *, on_issue=None, library_strategy=None
) -> dict | None:
    """Which contrast this run reproduces: asked of the model in autonomous mode, and left to a
    person at the gate in assisted mode.

    change_7.4 section 1.5: the deterministic check settles some answers with no one asked, in
    either mode: every contrast measured on an assay this workflow does not analyze is a recorded
    null, and one contrast whose stated assay it does analyze is selected. A sole contrast with no
    stated assay is a real question, and goes to the selector like any other.
    """
    org = await session.get(Organization, study.organization_id)
    autonomous = ((org.lit_validation_autonomy if org else None) or AUTONOMY_ASSISTED) == AUTONOMY_AUTONOMOUS
    return await select_contrast(
        contrasts,
        pipeline_key=pipeline_key,
        assay=assay,
        client=client,
        model=cfg.model,
        api_key=cfg.api_key,
        accession=(study.source_accession or "").strip() or None,
        sample_titles=await _scoped_sample_titles(study) if autonomous and len(contrasts) > 1 else None,
        on_issue=on_issue,
        library_strategy=library_strategy,
        ask=autonomous,
    )


def _reported_experiments(parsed: dict, method: dict, design_contrasts: list[dict]):
    """The reading's experiments, validated; or, when the reading described none, the paper's method
    read as its one experiment.

    A single-assay paper has one experiment whether or not the model listed it. A compound method
    ("ChIP-seq and bulk RNA-seq") read that way is one experiment naming two assays, which is a
    blocker, so a paper-level method never chooses a workflow for two assays.
    """
    from app.services.reported_experiments import normalize_reported_experiments

    claim_count, contrast_count = len(parsed["claims"]), len(design_contrasts)
    raw = parsed.get("reported_experiments") or []
    if not raw and not parsed.get("parse_failure"):
        raw = [
            {
                "id": "e1",
                "assay": method.get("assay"),
                "tools": method.get("tools"),
                "reference": {"assembly": method.get("reference_build")},
                "claim_indices": list(range(claim_count)),
                "contrast_indices": list(range(contrast_count)),
            }
        ]
        reading = normalize_reported_experiments(raw, claim_count=claim_count, contrast_count=contrast_count)
        for experiment in reading.experiments:
            experiment["status"] = "inferred_from_method"
        return reading
    return normalize_reported_experiments(raw, claim_count=claim_count, contrast_count=contrast_count)


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
        # plan_6 step 6: validation runs on its own model when the org named one. The paper is a
        # whole document and the vocabulary is 23 near neighbours; that is a different demand from
        # scoring an abstract, and an org should not have to pick one model for both.
        cfg = await llm_provider_config_service.get_for_feature(session, org_id, FEATURE_LITERATURE_VALIDATION)
        if not cfg:
            raise ValidationError("No active LLM provider is configured for this organization.")

        system, payload = build_extraction_prompt(full_text)
        client = get_client(cfg.provider)
        # Every step that asks a model something appends here when it could not get an answer. The
        # list is study-scoped rather than plan-scoped because a refusal can happen before a plan
        # exists, and it reaches the report through `ValidationStudy.evidence_json` (step 14c).
        issues: list[dict] = []
        reading = await decide(
            intent=PAPER_READING_INTENT,
            system=system,
            payload=payload,
            client=client,
            model=cfg.model,
            api_key=cfg.api_key,
        )
        if not reading.ok:
            # `blocked`: with no extraction there is no plan, so this one really did produce nothing.
            issues.append(reading.as_issue(impact="blocked"))
        parsed = parse_extraction(reading.text, full_text=full_text)

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
        # change_7.5 section 1.5: every accession the model read, kept for discovery. A requested
        # accession narrows what the plan fetches below; it does not change what the paper names.
        study.evidence_json = {**(study.evidence_json or {}), "extracted_accessions": list(accessions)}

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
        # change_7.5 section 1.3: the reference is recorded as it is. There is no default: an
        # operation that depends on a reference is refused when the paper's is not one bioAF supplies.
        from app.services.validation_reference import USABLE, paper_reference, reference_blocker

        reference = paper_reference(_str_or_none(raw_genome), mapping.pipeline_key)
        reference_genome = _normalize_reference_genome(raw_genome) if reference["status"] == USABLE else None
        if reference["status"] != USABLE and _normalize_reference_genome(raw_genome) is not None:
            # Recognised, and not one bioAF supplies (T2T-CHM13): the token is kept, as before, so a
            # pinned launch still refuses rather than aligning against its seed.
            reference_genome = _normalize_reference_genome(raw_genome)
        blocker = reference_blocker(reference)
        if blocker:
            blockers.append(blocker)
        # A paper that ran several assays names a build per assay, and only one of them can be what
        # this run aligns against. Say which was taken and which were not, because the choice is
        # made from word order in a methods section and a scientist can see in one glance whether
        # it is the build their own dataset used.
        if reference["status"] == USABLE and len(named_builds) > 1:
            blockers.append(
                f"The paper names more than one reference genome ({', '.join(named_builds)}). "
                f"{named_builds[0]} was taken, because the paper names it first. Confirm it is the build "
                f"this dataset was aligned to before approving."
            )

        from app.services.validation_claim_cutoffs import (
            claim_cutoffs,
            contrast_index_for,
            derive_contrast_thresholds,
            describe_ambiguity,
            threshold_disagreement,
        )

        # change_7.5 section 1.1: a significance the paper's own text reads two ways, shown with both
        # quotes. It rides on the claim so the target and the contrast it defines are both unresolved.
        for ambiguity in parsed["significance_ambiguities"]:
            parsed["claims"][ambiguity["claim_index"]]["significance_unresolved"] = describe_ambiguity(ambiguity)

        design_contrasts = parsed["differential_design"].get("contrasts") or []
        # change_7.5 section 2.2: the experiments, each claim and contrast linked to exactly one.
        reading = _reported_experiments(parsed, method, design_contrasts)
        blockers.extend(reading.blockers)
        targets = []
        claims_to_bind = []
        for position, c in enumerate(parsed["claims"]):
            metric_key = (c.get("metric_key") or "").strip()
            # A claim with no measurable metric is STILL one of the paper's claims. It used to be
            # dropped here, so Groff's digital-karyotype and TE-WE concordance findings never
            # reached the plan and the report was silent about them.
            if not metric_key and not (c.get("claim_text") or "").strip():
                continue
            targets.append(
                {
                    "metric_key": metric_key,
                    # change_7.1 section 3: the paper's own sentence, and what was actually
                    # measured. Without these a claim is a number with a paraphrased name, checked
                    # against whichever metric an alias table happened to match.
                    "claim_text": c.get("claim_text"),
                    "claimed_value": _to_float(c.get("value")),
                    "unit": c.get("unit"),
                    "tolerance": _to_float(c.get("tolerance")),
                    "source_locator": c.get("source_locator"),
                    "sample_subset": c.get("sample_subset"),
                    "qc_stage": c.get("qc_stage"),
                    "direction": c.get("direction"),
                    "threshold": _to_float(c.get("threshold")),
                    "threshold_kind": c.get("threshold_kind"),
                    "output_type": c.get("output_type"),
                    # change_7.3 section 7: the claim's own cutoffs and the contrast it reports on.
                    "cutoffs": claim_cutoffs(c) or None,
                    "contrast_index": contrast_index_for(c, design_contrasts),
                    # Until the binding call answers, the alias table is what decides, exactly as before.
                    "bound_by": "alias_table",
                    "reported_experiment_id": reading.claim_experiment.get(position),
                }
            )
            # change_7.4 section 1.6: a scalar threshold that disagrees with the claim's own cutoffs
            # leaves the cutoff unresolved, on the record. Neither reading overwrites the other.
            disagreement = threshold_disagreement(
                _to_float(c.get("threshold")), c.get("threshold_kind"), claim_cutoffs(c)
            )
            for unresolved in (c.get("significance_unresolved"), disagreement):
                if unresolved:
                    targets[-1]["unresolved_reason"] = " ".join(
                        r for r in (targets[-1].get("unresolved_reason"), unresolved) if r
                    )
                    issues.append(_cutoff_issue(unresolved))
            claims_to_bind.append(
                {
                    "metric_key": metric_key,
                    "claim_text": c.get("claim_text"),
                    "value": c.get("value"),
                    "unit": c.get("unit"),
                    "source_locator": c.get("source_locator"),
                    # change_7.5 section 1.1: the binding call sees the cutoffs the paper stated, so it
                    # never re-derives their kind without them.
                    "cutoffs": claim_cutoffs(c) or None,
                }
            )

        # plan_6 step 2/3: ask the model which controlled metric each claim measures, and record the
        # answer with its reason. This is an improvement on the alias table, not a dependency of the
        # extraction: a provider failure here must not cost the plan, because the claims are still the
        # paper's claims and the alias table still resolves what it always did.
        # The blanket `except Exception` that used to wrap this is gone (plan_7 step 14a). A provider
        # failure no longer escapes `bind_claims`: it comes back as an empty decision list AND as a
        # row on `issues`, so the degrade to the alias table is on the record rather than in a log
        # line nobody reads. Same behaviour, now visible.
        decisions = await bind_claims(
            claims_to_bind, client=client, model=cfg.model, api_key=cfg.api_key, on_issue=issues.append
        )
        # plan_6 step 4: a whole paper that bound nothing gets one more look, before the C1 gate
        # and before any compute. Bounded at one retry, because a second failure is an answer. The
        # re-ask stays HERE rather than in the helper: the trigger is domain logic, and only this
        # layer knows whether the second attempt succeeded and therefore whether an issue happened.
        if binding_failure_blocker(decisions) is not None:
            decisions = await bind_claims(
                claims_to_bind,
                client=client,
                model=cfg.model,
                api_key=cfg.api_key,
                previous=decisions,
                on_issue=issues.append,
            )

        for target, decision in zip(targets, decisions):
            target.update(
                bound_key=decision["bound_key"],
                binding_reason=decision["reason"],
                binding_confidence=decision["confidence"],
                bound_by_model=cfg.model,
                # A decision the model made is `model`; one it could not make at all is
                # `binding_failed`, and the comparison treats the two differently.
                bound_by=decision.get("bound_by") or "model",
            )

        binding_blocker = binding_failure_blocker(decisions)
        if binding_blocker:
            blockers.append(binding_blocker)

        # Which of the paper's contrasts THIS run could reproduce. A paper reports one per finding
        # across every assay it ran; the plan runs one pipeline. The gate used to take contrasts[0],
        # so a paper listing its ChIP-seq contrast last handed a chipseq run an RNA-seq knockout.
        design = _differential_design_or_none(parsed["differential_design"])
        for index, contrast in enumerate((design or {}).get("contrasts") or []):
            contrast["reported_experiment_id"] = reading.contrast_experiment.get(index)
        if design and design.get("contrasts"):
            # change_7.3 section 7: a contrast's threshold comes from the claim that is its finding.
            # The pair the extractor attached to the contrast drove the ground truth, and on a contrast
            # with a set and a stricter subset it was the subset's.
            design["contrasts"] = derive_contrast_thresholds(design["contrasts"], parsed["claims"])
            selection = await _select_contrast_for(
                session,
                study,
                design["contrasts"],
                mapping.pipeline_key,
                method.get("assay"),
                cfg,
                client,
                on_issue=issues.append,
                library_strategy=library_strategy,
            )
            if selection is not None:
                design["selected_contrast"] = selection

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
            differential_design=design,
            # Keep the paper's own tool list. It is what lets a divergence be attributed to a named
            # cause (CellRanger vs STARsolo) instead of merely reported.
            tools=[str(t).strip() for t in _as_list(method.get("tools")) if str(t).strip()],
            # Where the authors said their own analysis code lives. Shown at the C1 gate, never run.
            code_availability=parse_code_availability(parsed.get("code_availability")),
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
            reported_experiments=reading.experiments,
        )

        # The kind of each blocker that survived into the plan, beside the sentences every other
        # consumer reads.
        kept = set(blockers)
        plan.blocker_kinds_json = [k for k in parsed.get("blocker_kinds") or [] if k["text"] in kept] or None
        await ReproductionPlanService.add_comparison_targets(session, plan, targets)
        # Everything that could not get an answer from a model while reading this paper, on the
        # record. Study-scoped rather than plan-scoped: the extraction refusal above happens before
        # a plan exists, so it has nothing plan-shaped to hang off.
        await ValidationIssueService.record(session, study, issues)
        return plan
