"""change_7.5 section 1.1: significance is recorded as the paper states it, in every prompt.

Study 38 (SAMD1) stated its gene counts at "P < 0.01", a nominal P value. The extraction prompt asked
for "adjusted p / FDR" and said differential expression "is usually reported at a |log2FC| cutoff AND
an adjusted p", so the model wrote a blocker calling the stated cutoff ambiguous. The column resolver
told the model to prefer the adjusted column, and the generated-analysis prompt offered "0.05 was
assumed" as a model answer. Each of those prefers a convention over what the paper said.

These tests make no model call. They assert what each prompt asks for and what the parser keeps.
"""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.comparison_target import ComparisonTarget
from app.services import column_resolution as cr
from app.services import generated_analysis as ga
from app.services import validation_extraction_service as ext
from app.services.validation_extraction_service import (
    ValidationExtractionService,
    build_binding_prompt,
    build_extraction_prompt,
    parse_extraction,
)
from app.services.validation_study_service import ValidationStudyService


def _fenced(obj) -> str:
    return "```json\n" + json.dumps(obj) + "\n```"


# ---- the extraction prompt ----


def test_the_extraction_prompt_asks_for_each_measure_exactly_as_stated():
    system, _ = build_extraction_prompt("body")
    lowered = system.lower()
    # Every kind a paper can state is named, so none is the "normal" one.
    for kind in ("p value", "adjusted p", "fdr", "q-value"):
        assert kind in lowered
    assert "exactly as the paper states" in lowered
    assert "never infer" in lowered


def test_the_extraction_prompt_prefers_no_statistical_convention():
    system, _ = build_extraction_prompt("body")
    lowered = system.lower()
    assert "usually reported" not in lowered
    assert "adjusted p / fdr" not in lowered
    assert "prefer the adjusted" not in lowered


def test_the_contrast_schema_has_no_adjusted_p_only_slot():
    """A contrast's cutoffs come from its claims. The legacy pair allowed only an adjusted P."""
    system, _ = build_extraction_prompt("body")
    assert '"thresholds": {"log2fc": null, "padj": null}' not in system


def test_the_extraction_prompt_forbids_asserting_a_stated_measure_is_missing_or_ambiguous():
    system, _ = build_extraction_prompt("body")
    lowered = system.lower()
    assert "significance_ambiguities" in system
    assert "never write a blocker" in lowered


def test_the_claim_cutoff_vocabulary_names_every_stated_kind():
    system, _ = build_extraction_prompt("body")
    assert "pvalue | padj | fdr | qvalue" in system


# ---- significance_ambiguities: shown, not asserted ----

_TEXT = (
    "Differentially expressed genes were defined at FDR < 0.01 using DESeq2. "
    "In knockout cells, 300 genes were up-regulated (P < 0.01)."
)


def _with_ambiguities(ambiguities) -> str:
    return _fenced(
        {
            "claims": [{"claim_text": "300 genes were up-regulated", "value": 300}],
            "significance_ambiguities": ambiguities,
        }
    )


def test_two_quoted_readings_of_one_claim_are_kept():
    parsed = parse_extraction(
        _with_ambiguities(
            [
                {
                    "claim_index": 0,
                    "readings": [
                        {
                            "kind": "pvalue",
                            "operator": "<",
                            "value": 0.01,
                            "quote": "300 genes were up-regulated (P < 0.01)",
                        },
                        {"kind": "fdr", "operator": "<", "value": 0.01, "quote": "defined at FDR < 0.01 using DESeq2"},
                    ],
                }
            ]
        ),
        full_text=_TEXT,
    )
    [kept] = parsed["significance_ambiguities"]
    assert kept["claim_index"] == 0
    assert [(r["kind"], r["operator"], r["value"]) for r in kept["readings"]] == [
        ("pvalue", "<", 0.01),
        ("padj", "<", 0.01),
    ]
    assert kept["readings"][0]["quote"] == "300 genes were up-regulated (P < 0.01)"


def test_an_entry_with_one_reading_is_dropped():
    parsed = parse_extraction(
        _with_ambiguities(
            [
                {
                    "claim_index": 0,
                    "readings": [{"kind": "pvalue", "operator": "<", "value": 0.01, "quote": "(P < 0.01)"}],
                }
            ]
        ),
        full_text=_TEXT,
    )
    assert parsed["significance_ambiguities"] == []


def test_two_readings_that_say_the_same_thing_are_one_reading():
    """FDR and a q-value are both adjusted P values: the same definition, not two."""
    parsed = parse_extraction(
        _with_ambiguities(
            [
                {
                    "claim_index": 0,
                    "readings": [
                        {"kind": "fdr", "operator": "<", "value": 0.01, "quote": "defined at FDR < 0.01"},
                        {"kind": "padj", "operator": "<", "value": 0.01, "quote": "defined at FDR < 0.01 using DESeq2"},
                    ],
                }
            ]
        ),
        full_text=_TEXT,
    )
    assert parsed["significance_ambiguities"] == []


def test_an_entry_whose_quote_is_not_in_the_paper_is_dropped():
    parsed = parse_extraction(
        _with_ambiguities(
            [
                {
                    "claim_index": 0,
                    "readings": [
                        {"kind": "pvalue", "operator": "<", "value": 0.01, "quote": "(P < 0.01)"},
                        {"kind": "padj", "operator": "<", "value": 0.05, "quote": "adjusted P below 0.05 throughout"},
                    ],
                }
            ]
        ),
        full_text=_TEXT,
    )
    assert parsed["significance_ambiguities"] == []


def test_a_quote_is_found_across_whitespace_and_case():
    parsed = parse_extraction(
        _with_ambiguities(
            [
                {
                    "claim_index": 0,
                    "readings": [
                        {
                            "kind": "pvalue",
                            "operator": "<",
                            "value": 0.01,
                            "quote": "300 genes  were UP-regulated (P < 0.01)",
                        },
                        {"kind": "fdr", "operator": "<", "value": 0.01, "quote": "Defined at\nFDR < 0.01"},
                    ],
                }
            ]
        ),
        full_text=_TEXT,
    )
    assert len(parsed["significance_ambiguities"]) == 1


def test_with_no_paper_text_nothing_can_be_shown_so_nothing_is_kept():
    parsed = parse_extraction(
        _with_ambiguities(
            [
                {
                    "claim_index": 0,
                    "readings": [
                        {"kind": "pvalue", "operator": "<", "value": 0.01, "quote": "(P < 0.01)"},
                        {"kind": "fdr", "operator": "<", "value": 0.01, "quote": "FDR < 0.01"},
                    ],
                }
            ]
        )
    )
    assert parsed["significance_ambiguities"] == []


# ---- blockers may not assert a stated measure is missing or ambiguous ----


def test_a_blocker_about_a_stated_significance_measure_is_dropped():
    """Study 38's blocker called the stated "P < 0.01" ambiguous. Ambiguity is shown with quotes, or
    it is not recorded."""
    parsed = parse_extraction(
        _fenced(
            {
                "claims": [
                    {"claim_text": "257 genes up", "cutoffs": [{"kind": "pvalue", "operator": "<", "value": 0.01}]}
                ],
                "blockers": [
                    {
                        "text": "thresholds report only P < 0.01 (nominal p-value), not adjusted p; the reported cutoff "
                        "basis for gene counts is ambiguous",
                        "kind": "missing_detail",
                    },
                    {"text": "Library layout not stated", "kind": "missing_detail"},
                ],
            }
        ),
        full_text="257 genes up (P < 0.01)",
    )
    assert parsed["blockers"] == ["Library layout not stated"]
    assert [k["text"] for k in parsed["blocker_kinds"]] == ["Library layout not stated"]


def test_a_blocker_about_significance_stands_when_no_claim_states_a_measure():
    """Where the paper states no significance measure at all, saying so is not contradicting it."""
    parsed = parse_extraction(
        _fenced(
            {
                "claims": [{"claim_text": "257 genes up"}],
                "blockers": [
                    {"text": "The significance threshold for the gene lists is not stated", "kind": "missing_detail"}
                ],
            }
        ),
        full_text="257 genes up",
    )
    assert parsed["blockers"] == ["The significance threshold for the gene lists is not stated"]


# ---- an ambiguity reaches the plan as an unresolved significance ----


_AMBIGUOUS_EXTRACTION = _fenced(
    {
        "accessions": ["GSE1"],
        "method": {"assay": "bulk RNA-seq", "reference_build": "GRCh38"},
        "differential_design": {
            "contrasts": [
                {
                    "name": "KO vs WT",
                    "test_condition": "KO",
                    "reference_condition": "WT",
                    "assay": "bulk RNA-seq",
                    "finding_claim_index": 0,
                }
            ]
        },
        "claims": [
            {
                "metric_key": "",
                "claim_text": "300 genes were up-regulated",
                "value": 300,
                "output_type": "gene_set_size",
                "contrast": "KO vs WT",
                "cutoffs": [{"kind": "pvalue", "operator": "<", "value": 0.01}],
            }
        ],
        "significance_ambiguities": [
            {
                "claim_index": 0,
                "readings": [
                    {
                        "kind": "pvalue",
                        "operator": "<",
                        "value": 0.01,
                        "quote": "300 genes were up-regulated (P < 0.01)",
                    },
                    {"kind": "fdr", "operator": "<", "value": 0.01, "quote": "defined at FDR < 0.01 using DESeq2"},
                ],
            }
        ],
        "data_availability": "deposited",
        "blockers": [],
    }
)


def _patch_llm(monkeypatch, response):
    async def fake_get_active(sess, org_id):
        return SimpleNamespace(provider="anthropic", model="claude-opus-4-8", api_key=None)

    class _C:
        async def submit(self, prompt, payload, model, api_key, attachments=None):
            return response

    monkeypatch.setattr(ext.llm_provider_config_service, "get_active", fake_get_active)
    monkeypatch.setattr(ext, "get_client", lambda p: _C())

    async def _bind(claims, *, client, model, api_key, on_issue=None, **_):
        return [
            {"claim_index": i, "bound_key": None, "reason": "declined", "confidence": 0.9, "declined": True}
            for i in range(len(claims))
        ]

    monkeypatch.setattr(ext, "bind_claims", _bind)


@pytest.mark.asyncio
async def test_a_kept_ambiguity_leaves_the_claim_and_its_contrast_unresolved(session, admin_user, monkeypatch):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, _AMBIGUOUS_EXTRACTION)

    plan = await ValidationExtractionService.extract(session, study, _TEXT, admin_user.organization_id, admin_user.id)
    await session.flush()

    [target] = (
        (await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id)))
        .scalars()
        .all()
    )
    # Both readings are on the record, each with its own words.
    assert "P < 0.01" in target.unresolved_reason
    assert "adjusted P < 0.01" in target.unresolved_reason
    assert "300 genes were up-regulated (P < 0.01)" in target.unresolved_reason
    assert "defined at FDR < 0.01 using DESeq2" in target.unresolved_reason
    # The cutoffs stay as the claim stated them; neither reading overwrites them.
    assert target.cutoffs == [{"kind": "pvalue", "operator": "<", "value": 0.01}]

    contrast = plan.differential_design_json["contrasts"][0]
    assert contrast["thresholds_unresolved"]


@pytest.mark.asyncio
async def test_an_unshown_ambiguity_leaves_the_stated_reading_standing(session, admin_user, monkeypatch):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, _AMBIGUOUS_EXTRACTION)

    # The paper text holds neither quote, so the entry is dropped and nothing is unresolved.
    plan = await ValidationExtractionService.extract(
        session, study, "unrelated text", admin_user.organization_id, admin_user.id
    )
    await session.flush()

    [target] = (
        (await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id)))
        .scalars()
        .all()
    )
    assert target.unresolved_reason is None
    contrast = plan.differential_design_json["contrasts"][0]
    assert "thresholds_unresolved" not in contrast
    assert contrast["cutoffs"] == [{"kind": "pvalue", "operator": "<", "value": 0.01}]


# ---- the binding prompt sees the claim's cutoffs ----


def test_the_binding_payload_carries_each_claims_cutoffs():
    _, payload = build_binding_prompt(
        [
            {
                "metric_key": "",
                "claim_text": "257 genes were up",
                "value": 257,
                "cutoffs": [{"kind": "pvalue", "operator": "<", "value": 0.01}],
            }
        ]
    )
    assert "stated cutoffs: P < 0.01" in payload


@pytest.mark.asyncio
async def test_extraction_hands_the_binding_call_the_claims_cutoffs(session, admin_user, monkeypatch):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, _AMBIGUOUS_EXTRACTION)
    seen = {}

    async def _bind(claims, *, client, model, api_key, on_issue=None, **_):
        seen["claims"] = claims
        return [{"claim_index": 0, "bound_key": None, "reason": "declined", "confidence": 0.9, "declined": True}]

    monkeypatch.setattr(ext, "bind_claims", _bind)
    await ValidationExtractionService.extract(session, study, _TEXT, admin_user.organization_id, admin_user.id)
    assert seen["claims"][0]["cutoffs"] == [{"kind": "pvalue", "operator": "<", "value": 0.01}]


# ---- column resolution prefers neither measure ----


def test_column_resolution_prefers_neither_significance_column():
    system, _ = cr.build_column_prompt(["gene", "log2FoldChange", "pvalue", "padj"], kind="gene")
    lowered = system.lower()
    assert "prefer the adjusted" not in lowered
    assert "only if there is no adjusted" not in lowered
    # Both measures are asked for, each in its own role.
    assert "padj" in system and "pval" in system


# ---- the generated analysis assumes no threshold ----


def test_the_generated_analysis_prompt_assumes_no_threshold():
    lowered = ga._SYSTEM.lower()
    assert "0.05 was assumed" not in lowered
    assert "never assume a threshold" in lowered
    # The results carry both measures, so the paper's own one can be applied to them.
    assert "raw p-value" in lowered and "adjusted p-value" in lowered
