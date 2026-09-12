"""change_7.5 section 2.4: claim binding with the context it needs, binding only on a match.

Study 38 bound "8733 significant peaks" as bioAF's headline per-sample peak call, bound an L3MBTL3
antibody's 576 peaks to the same metric, and set every claim's `measurement_basis` to `sample`. The
binding saw the claim's text, value, unit and locator and nothing else: passages were built after it,
the full text was one string with no addressable methods or legends, and `MetricSpec` did not say
what bioAF computes. Today's `peak_count` is described as "ONE sample" and computed as the mean over
every IP sample.

No model call is made here.
"""

import json

import pytest

from app.services.literature.fulltext_service import _jats_sections
from app.services.validation_binding import (
    AGGREGATIONS,
    MAX_CONTEXT_CHARS,
    binding_context,
    binding_match,
    merge_binding_facts,
    parse_binding_facts,
)
from app.services.validation_classifier_service import CONTROLLED_METRIC_SPECS
from app.services.validation_extraction_service import build_binding_prompt

_JATS = (
    '<article xmlns="http://jats.nlm.nih.gov"><body>'
    '<sec sec-type="results"><title>Results</title><p>Knockout cells lost the mark.</p>'
    '<fig id="f1"><label>Fig. 1</label><caption><p>Peaks called in the knockout ChIP-seq.</p></caption></fig>'
    '<table-wrap id="t1"><label>Table 2</label><caption><p>Genes changed in RNA-seq.</p></caption></table-wrap>'
    "</sec>"
    '<sec sec-type="methods"><title>Materials and Methods</title>'
    "<sec><title>ChIP-seq</title><p>ChIP-seq libraries were aligned with Bowtie2 and peaks called with MACS2.</p></sec>"
    "<sec><title>RNA-seq</title><p>RNA-seq reads were quantified with Salmon.</p></sec>"
    "</sec></body></article>"
)


# ---- section-aware text ----


def test_methods_and_captions_are_addressable():
    sections = _jats_sections(_JATS)
    assert any("MACS2" in p for p in sections["methods"])
    assert any("Salmon" in p for p in sections["methods"])
    assert sections["captions"]["figure 1"] == "Peaks called in the knockout ChIP-seq."
    assert sections["captions"]["table 2"] == "Genes changed in RNA-seq."


def test_unparseable_markup_has_no_sections():
    assert _jats_sections("<not xml") == {"methods": [], "captions": {}}


# ---- the context each claim is bound with ----

_EXPERIMENT = {
    "id": "e1",
    "assay": "ChIP-seq",
    "conditions": ["knockout", "wild type"],
    "reference": {"assembly": {"stated": "mm9"}, "annotation": {"stated": None}},
}
_RESOURCES = [
    {
        "identifier": "GSE555001",
        "type": "sequencing_data",
        "role": "ChIP-seq and RNA-seq data",
        "reported_experiment_ids": ["e1"],
        "listing": {"kinds": {"coverage_track": 12, "de_table": 1}, "samples": 20, "result_tables": ["x.txt.gz"]},
    },
    {"identifier": "PXD000001", "type": "proteomics_data", "role": "proteome", "reported_experiment_ids": ["e2"]},
]


def test_a_claim_is_bound_with_its_passage_legend_methods_experiment_resources_and_cutoffs():
    context = binding_context(
        {"claim_text": "we called many peaks", "source_locator": "Fig. 1G", "cutoffs": [{"kind": "padj", "operator": "<", "value": 0.05}]},
        passage="In knockout cells we called many peaks, mostly at promoters.",
        sections=_jats_sections(_JATS),
        experiment=_EXPERIMENT,
        resources=_RESOURCES,
    )
    assert "mostly at promoters" in context["passage"]
    assert context["legend"] == "Peaks called in the knockout ChIP-seq."
    assert any("MACS2" in p for p in context["methods"])
    assert not any("Salmon" in p for p in context["methods"])
    assert "ChIP-seq" in context["experiment"]
    assert "GSE555001" in context["resources"]
    assert "PXD000001" not in context["resources"]
    assert "adjusted P < 0.05" in context["cutoffs"]


def test_the_context_is_bounded_per_claim():
    long = "word " * 5000
    context = binding_context(
        {"claim_text": "x", "source_locator": "Fig. 1"},
        passage=long,
        sections={"methods": [f"ChIP-seq {long}"] * 10, "captions": {"figure 1": long}},
        experiment=_EXPERIMENT,
        resources=_RESOURCES * 50,
    )
    assert sum(len(str(v)) for v in context.values()) <= MAX_CONTEXT_CHARS


def test_the_binding_payload_carries_the_context():
    claims = [
        {
            "metric_key": "peak_count",
            "claim_text": "we called many peaks",
            "value": 1000,
            "context": {"legend": "Peaks called in the knockout ChIP-seq.", "experiment": "e1: ChIP-seq"},
        }
    ]
    system, payload = build_binding_prompt(claims)
    assert "legend: Peaks called in the knockout ChIP-seq." in payload
    assert "experiment: e1: ChIP-seq" in payload
    assert "population" in system and "aggregation" in system and "denominator" in system


# ---- the three facts, with quotes ----


def _decision(**facts):
    return {"claim_index": 0, "bound_key": "peak_count", "reason": "r", "confidence": 0.9, **facts}


def test_the_binding_keeps_population_aggregation_and_denominator_with_their_quotes():
    facts = parse_binding_facts(
        _decision(
            population={"value": "the knockout ChIP-seq sample", "scope": "one_sample", "quote": "in the KO ChIP"},
            aggregation={"value": "per_sample", "quote": "we called many peaks in the KO ChIP"},
            denominator={"value": None, "quote": None},
        )
    )
    assert facts["population"] == {"value": "the knockout ChIP-seq sample", "scope": "one_sample", "quote": "in the KO ChIP"}
    assert facts["aggregation"]["value"] == "per_sample"
    assert facts["denominator"]["value"] is None


def test_an_aggregation_outside_the_vocabulary_is_not_stated():
    facts = parse_binding_facts(_decision(aggregation={"value": "averaged somehow", "quote": "q"}))
    assert facts["aggregation"]["value"] == "not_stated"
    assert "not_stated" in AGGREGATIONS


# ---- every metric declares what bioAF computes ----


def test_every_metric_declares_its_population_aggregation_and_denominator():
    for spec in CONTROLLED_METRIC_SPECS:
        assert spec.population, spec.key
        assert spec.aggregation, spec.key
        assert spec.denominator is not None, spec.key


def test_peak_count_is_declared_as_the_mean_over_every_ip_sample():
    spec = next(s for s in CONTROLLED_METRIC_SPECS if s.key == "peak_count")
    assert "IP sample" in spec.population
    assert spec.aggregation == "mean_over_samples"
    assert "ONE sample" not in spec.meaning


# ---- bind only on a match ----


def _facts(scope="all_samples", aggregation="per_sample", denominator=None):
    return {
        "population": {"value": "every sample", "scope": scope, "quote": "q"},
        "aggregation": {"value": aggregation, "quote": "q"},
        "denominator": {"value": denominator, "quote": "q" if denominator else None},
    }


def test_a_claim_about_one_named_sample_never_binds_to_a_mean_over_all_samples():
    ok, status, reason = binding_match("peak_count", _facts(scope="one_sample"))
    assert not ok
    assert status == "unavailable"
    assert "one" in reason and "mean" in reason


def test_a_consensus_count_never_binds_to_a_per_sample_metric():
    ok, status, _ = binding_match("peak_count", _facts(aggregation="consensus"))
    assert not ok and status == "unavailable"


def test_an_unstated_aggregation_leaves_the_qc_check_unresolved():
    ok, status, reason = binding_match("peak_count", _facts(aggregation="not_stated"))
    assert not ok and status == "unresolved"
    assert "does not state" in reason


def test_a_mean_over_every_sample_binds():
    assert binding_match("total_sequences", _facts())[0] is True


def test_a_fraction_over_another_denominator_never_binds():
    ok, status, reason = binding_match("reads_mapped_genome", _facts(denominator="mapped_reads"))
    assert not ok and status == "unavailable"
    assert "denominator" in reason


# ---- aggregation is validated on write, and a stated value is never overwritten by a default ----


def test_a_stated_aggregation_is_not_overwritten_by_a_later_not_stated():
    merged = merge_binding_facts(_facts(aggregation="per_group"), _facts(aggregation="not_stated"))
    assert merged["aggregation"]["value"] == "per_group"


def test_a_later_stated_aggregation_revises_an_earlier_one():
    merged = merge_binding_facts(_facts(aggregation="not_stated"), _facts(aggregation="consensus"))
    assert merged["aggregation"]["value"] == "consensus"


# ---- what a binding decision lands as ----


def test_a_matched_binding_lands_with_its_facts_and_aggregation():
    from app.services.validation_binding import settle_binding

    settled = settle_binding("total_sequences", _facts(), workflow="nf-core/rnaseq")
    assert settled["bound_key"] == "total_sequences"
    assert settled["aggregation"] == "per_sample"
    assert settled["binding_facts"]["match"] == {"ok": True, "status": None, "reason": None}


def test_an_unmatched_binding_keeps_the_proposed_key_and_binds_nothing():
    from app.services.validation_binding import settle_binding

    settled = settle_binding("peak_count", _facts(scope="one_sample"), workflow="nf-core/chipseq")
    assert settled["bound_key"] is None
    assert settled["binding_facts"]["proposed_key"] == "peak_count"
    assert settled["binding_facts"]["match"]["ok"] is False
    assert "one sample" in settled["reason"]


def test_a_later_reading_that_states_the_missing_fact_binds_the_proposed_key():
    from app.services.validation_binding import settle_binding

    earlier = settle_binding("total_sequences", _facts(aggregation="not_stated"), workflow=None)["binding_facts"]
    assert earlier["match"]["status"] == "unresolved"
    later = settle_binding(None, _facts(), workflow=None, earlier=earlier)
    assert later["bound_key"] == "total_sequences"


def test_the_computation_is_matched_per_workflow():
    # nf-core/chipseq and atacseq compute duplication from Picard over mapped reads, not FastQC.
    facts = _facts(denominator="sequenced_reads")
    assert binding_match("percent_duplicates", facts, "nf-core/rnaseq")[0] is True
    assert binding_match("percent_duplicates", facts, "nf-core/chipseq")[0] is False


def test_a_first_listed_sample_metric_never_binds():
    ok, status, reason = binding_match("cell_count", _facts(aggregation="per_experiment"))
    assert not ok and status == "unavailable"
    ok, status, _ = binding_match("cell_count", _facts(scope="one_sample"))
    assert not ok and status == "unresolved"


def test_the_vocabulary_says_what_each_metric_computes():
    from app.services.validation_extraction_service import _metric_vocabulary_block

    block = _metric_vocabulary_block()
    assert "bioAF computes: the mean over every IP sample in the run" in block
    assert "on nf-core/chipseq:" in block


def test_every_claim_context_together_is_bounded():
    from app.services.validation_binding import MAX_TOTAL_CONTEXT_CHARS, bound_contexts

    long = "ChIP-seq " + "word " * 5000
    one = binding_context(
        {"claim_text": "x", "source_locator": "Fig. 1"},
        passage=long,
        sections={"methods": [long] * 10, "captions": {"figure 1": long}},
        experiment=_EXPERIMENT,
        resources=_RESOURCES * 50,
    )
    contexts = bound_contexts([json.loads(json.dumps(one)) for _ in range(40)])
    assert sum(len(str(v)) for c in contexts for v in c.values()) <= MAX_TOTAL_CONTEXT_CHARS


@pytest.mark.asyncio
async def test_an_aggregation_outside_the_vocabulary_is_not_written(session, admin_user):
    from app.services.reproduction_plan_service import ReproductionPlanService
    from app.services.validation_study_service import ValidationStudyService

    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    plan = await ReproductionPlanService.create_plan(session, study, admin_user.id, accessions=["GSE1"])
    rows = await ReproductionPlanService.add_comparison_targets(
        session,
        plan,
        [
            {"metric_key": "peak_count", "claimed_value": 1.0, "aggregation": "averaged somehow"},
            {"metric_key": "peak_count", "claimed_value": 2.0, "aggregation": "consensus"},
        ],
    )
    assert [r.aggregation for r in rows] == [None, "consensus"]


# ---- reconciliation writes through the same merge and match ----


def _target(**over):
    from types import SimpleNamespace

    fields = dict(
        metric_key="total_sequences", claim_text="reads per sample", bound_key="total_sequences", bound_by="model",
        binding_reason="r", binding_confidence=0.9, binding_facts={**_facts(), "match": {"ok": True}},
        aggregation="per_sample", sample_subset=None, qc_stage=None, direction=None, threshold=None,
        threshold_kind=None, output_type=None, measurement_basis=None, reported_experiment_id="e1",
    )
    fields.update(over)
    return SimpleNamespace(**fields)


def test_reconciliation_that_reads_one_sample_unbinds_with_the_reason():
    from app.services.validation_reconciliation import _apply

    target = _target()
    decision = {"claim_index": 0, "bound_key": "total_sequences", "reason": "the S2 table says one sample",
                "confidence": 0.8, "facts": _facts(scope="one_sample")}
    revisions = _apply([target], [decision], workflow_for=lambda t: "nf-core/rnaseq")
    assert target.bound_key is None
    assert target.binding_facts["proposed_key"] == "total_sequences"
    assert target.binding_facts["population"]["scope"] == "one_sample"
    assert revisions and revisions[0]["after"]["bound_key"] is None


def test_reconciliation_never_overwrites_a_stated_aggregation_with_not_stated():
    from app.services.validation_reconciliation import _apply

    target = _target()
    decision = {"claim_index": 0, "bound_key": None, "reason": "r", "confidence": 0.5,
                "facts": _facts(aggregation="not_stated")}
    _apply([target], [decision], workflow_for=lambda t: "nf-core/rnaseq")
    assert target.aggregation == "per_sample"
    assert target.bound_key == "total_sequences"
