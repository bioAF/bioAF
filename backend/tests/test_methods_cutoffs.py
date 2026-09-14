"""plan_8_2 section 3.1 and owner decision 2: a claim that states no cutoff inherits one only from a methods
sentence that explicitly covers every differential test in the claim's experiment.

The quote is recorded. A cutoff stated for another experiment, a sentence that does not say which of
several such experiments it covers, a sentence that states an exception, and methods that state two
different cutoffs are never inherited. A paper-level threshold with no such sentence, and a conventional
threshold, are never supplied. Paper-shaped fixtures; nothing here is a rule about any one paper.
"""

import pytest

from app.services.validation_methods_cutoffs import inherited_cutoffs, methods_statements, record
from app.services.validation_predicate import build_predicate

_DEFINED = "Genes with an adjusted P value < 0.05 were considered differentially expressed."
_RNA = {
    "id": "e1",
    "assay": "bulk RNA-seq",
    "description": "knockout versus wild-type ES cells",
    "contrast_indices": [0],
}
_CHIP = {"id": "e2", "assay": "ChIP-seq", "description": "SAMD1 binding in ES cells", "contrast_indices": [1]}
_DIFFERENTIATION = {
    "id": "e3",
    "assay": "bulk RNA-seq",
    "description": "day-7 differentiation of knockout and wild-type cells",
    "contrast_indices": [2],
}
_CONTRASTS = [
    {"name": "KO vs WT ES cells", "reported_experiment_id": "e1"},
    {"name": "KO vs WT binding", "reported_experiment_id": "e2"},
    {"name": "KO vs WT day 7", "reported_experiment_id": "e3"},
]


def _inherit(paragraphs, experiment_id="e1", experiments=(_RNA,)):
    return inherited_cutoffs(
        experiment_id,
        experiments=list(experiments),
        contrasts=_CONTRASTS,
        recorded=record(paragraphs, source="europe_pmc"),
    )


def _claim(**overrides):
    return {
        "claim_text": "We identified 781 differentially expressed genes",
        "claimed_value": 781,
        "output_type": "gene_set_size",
        "contrast_index": 0,
        "reported_experiment_id": "e1",
        **overrides,
    }


class TestWhatAMethodsSentenceStates:
    def test_a_defining_sentence_states_its_cutoff_verbatim(self):
        (statement,) = methods_statements(["RNA was sequenced on a NovaSeq. " + _DEFINED])
        assert statement["quote"] == _DEFINED
        assert statement["cutoffs"] == [{"kind": "padj", "operator": "<", "value": 0.05}]

    @pytest.mark.parametrize(
        "sentence,cutoffs",
        [
            (
                "Differentially expressed genes were defined as those with FDR ≤ 0.1 and |log2 fold change| > 1.",
                [
                    {"kind": "padj", "operator": "<=", "value": 0.1, "adjustment": "FDR unspecified"},
                    {"kind": "abs_log2fc", "operator": ">", "value": 1.0},
                ],
            ),
            (
                "Genes with P < 0.01 and at least twofold change were considered differentially expressed.",
                [
                    {"kind": "pvalue", "operator": "<", "value": 0.01},
                    {"kind": "fold_change", "operator": ">=", "value": 2.0},
                ],
            ),
            (
                "Genes with a Benjamini-Hochberg adjusted P-value below 0.05 were considered differentially expressed.",
                [{"kind": "padj", "operator": "<", "value": 0.05, "adjustment": "BH"}],
            ),
        ],
    )
    def test_each_cutoff_keeps_its_kind_and_operator(self, sentence, cutoffs):
        (statement,) = methods_statements([sentence])
        assert statement["cutoffs"] == cutoffs

    def test_a_sentence_naming_a_method_but_no_cutoff_states_nothing(self):
        assert methods_statements(["Differential expression was performed using DESeq2 (v1.12.4) in R."]) == []

    def test_a_p_value_that_is_not_about_a_differential_test_states_nothing(self):
        assert methods_statements(["P values were calculated by a two-tailed Student's t test (P < 0.05)."]) == []


class TestWhenACutoffIsInherited:
    def test_the_only_experiment_of_its_assay_inherits_a_defining_sentence_with_its_quote(self):
        inherited = _inherit([_DEFINED])
        assert inherited["cutoffs"] == [{"kind": "padj", "operator": "<", "value": 0.05}]
        assert inherited["quote"] == _DEFINED

    def test_a_cutoff_stated_for_another_experiment_is_not_inherited(self):
        bound = "Regions with FDR < 0.05 were considered differentially bound."
        inherited = _inherit([bound], experiments=(_RNA, _CHIP))
        assert inherited["cutoffs"] is None
        assert "covers this experiment" in inherited["reason"]
        assert _inherit([bound], experiment_id="e2", experiments=(_RNA, _CHIP))["cutoffs"][0]["value"] == 0.05

    def test_a_sentence_that_does_not_say_which_of_several_experiments_it_covers_is_not_inherited(self):
        inherited = _inherit([_DEFINED], experiments=(_RNA, _DIFFERENTIATION))
        assert inherited["cutoffs"] is None
        assert "which of the paper's 2" in inherited["reason"]

    def test_a_sentence_naming_one_of_several_experiments_covers_that_experiment_only(self):
        sentence = "For the differentiation time course, genes with P < 0.01 were considered differentially expressed."
        experiments = (_RNA, _DIFFERENTIATION)
        assert _inherit([sentence], experiment_id="e3", experiments=experiments)["cutoffs"][0]["kind"] == "pvalue"
        assert _inherit([sentence], experiment_id="e1", experiments=experiments)["cutoffs"] is None

    def test_an_exception_is_preserved_by_inheriting_nothing(self):
        sentence = (
            "Genes with an adjusted P value < 0.05 were considered differentially expressed, except in the "
            "rescue comparison, where P < 0.01 was used."
        )
        inherited = _inherit([sentence])
        assert inherited["cutoffs"] is None
        assert "exception" in inherited["reason"]

    def test_two_different_cutoffs_for_the_same_experiment_are_not_resolved_by_picking_one(self):
        other = "Differentially expressed genes were defined by FDR < 0.1."
        inherited = _inherit([_DEFINED, other])
        assert inherited["cutoffs"] is None
        assert _DEFINED in inherited["reason"] and other in inherited["reason"]

    def test_no_sentence_means_nothing_is_inherited(self):
        inherited = _inherit(["Differential expression was performed using DESeq2."])
        assert inherited["cutoffs"] is None

    def test_methods_bioaf_never_read_are_not_a_reason_to_supply_one(self):
        inherited = inherited_cutoffs("e1", experiments=[_RNA], contrasts=_CONTRASTS, recorded=None)
        assert inherited["cutoffs"] is None


class TestThePredicate:
    def test_an_inherited_cutoff_is_applied_with_its_quote_recorded(self):
        predicate = build_predicate(_claim(cutoffs=[]), contrast=_CONTRASTS[0], inherited=_inherit([_DEFINED]))
        assert predicate["status"] == "resolved"
        assert predicate["significance"]["kind"] == "padj" and predicate["significance"]["value"] == 0.05
        assert predicate["cutoff_source"] == {"kind": "methods", "quote": _DEFINED}
        assert any(_DEFINED in a for a in predicate["assumptions"])

    def test_the_claims_own_cutoff_always_wins(self):
        own = [{"kind": "pvalue", "operator": "<", "value": 0.01}]
        predicate = build_predicate(_claim(cutoffs=own), contrast=_CONTRASTS[0], inherited=_inherit([_DEFINED]))
        assert predicate["significance"]["kind"] == "pvalue"
        assert predicate["cutoff_source"] == {"kind": "claim"}

    def test_a_paper_level_threshold_with_no_covering_sentence_is_never_substituted(self):
        predicate = build_predicate(
            _claim(cutoffs=[]),
            contrast=_CONTRASTS[0],
            design={"thresholds": {"padj": 0.05, "log2fc": 1.0}},
            inherited=_inherit(["Differential expression was performed using DESeq2."]),
        )
        assert predicate["status"] == "not_checkable"
        assert predicate["significance"] is None
        assert "paper-level" in predicate["reason"]

    def test_why_nothing_was_inherited_is_the_reason(self):
        predicate = build_predicate(
            _claim(cutoffs=[]),
            contrast=_CONTRASTS[0],
            inherited=_inherit([_DEFINED], experiments=(_RNA, _DIFFERENTIATION)),
        )
        assert predicate["status"] == "not_checkable"
        assert "which of the paper's 2" in predicate["reason"]


class TestTheRealPapersMethods:
    """Neither fixture paper states a methods cutoff for its differential tests, so neither inherits one."""

    def test_groffs_methods_state_no_cutoff(self):
        methods = [
            "Differential expression analysis by sex chromosome content was conducted in R 3.5.1 using DESeq2 1.2.0.",
            "Differential expression was performed on RSEM read count data using the DESeq2 (v1.12.4) (Love et al. "
            "2014) package in R 3.3.3 on sample partitions described in the text.",
        ]
        assert methods_statements(methods) == []


# ---- through the product: the read records the sentences, and only dependent checks follow them ----

from tests.test_failed_read import _complete, llm, quiet_discovery  # noqa: E402,F401  (fixtures)

_EXTRACTION = dict(
    reported_experiments=[{"id": "e1", "assay": "bulk RNA-seq", "claim_indices": [0, 1], "contrast_indices": [0]}],
    differential_design={"contrasts": [{"name": "KO vs WT", "test_condition": "KO", "reference_condition": "WT"}]},
    claims=[
        {
            "metric_key": "",
            "claim_text": "We identified 781 differentially expressed genes",
            "value": 781,
            "output_type": "gene_set_size",
            "contrast": "KO vs WT",
            "cutoffs": [],
        },
        {
            "metric_key": "",
            "claim_text": "257 genes were up-regulated at P < 0.01",
            "value": 257,
            "output_type": "gene_set_size",
            "contrast": "KO vs WT",
            "direction": "up",
            "cutoffs": [{"kind": "pvalue", "operator": "<", "value": 0.01}],
        },
    ],
)


@pytest.fixture
def europe_pmc_text(monkeypatch):
    """The paper as Europe PMC serves it: its text, and its methods paragraphs addressable."""
    from app.services import validation_paper_text as paper_text

    paragraphs = ["RNA-seq reads were aligned with STAR. Differential expression was tested with DESeq2. " + _DEFINED]

    async def acquire(session, study, *, pasted=None):
        return paper_text.PaperText(
            text="We deposited the data under GSE000001. " + " ".join(paragraphs),
            source=paper_text.EUROPE_PMC,
            supplements=[],
            sections={"methods": paragraphs, "captions": {}},
        )

    monkeypatch.setattr(paper_text, "acquire", acquire)


class TestThroughTheRead:
    @pytest.mark.asyncio
    async def test_the_read_records_the_defining_sentence_and_the_claim_inherits_it(
        self,
        session,
        admin_user,
        llm,
        quiet_discovery,
        europe_pmc_text,  # noqa: F811
    ):
        from app.services.validation_assessment import claimed_predicates
        from app.services.validation_study_service import ValidationStudyService
        from app.services.validation_driver_service import ValidationDriverService

        llm(_complete(**_EXTRACTION))
        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        await session.flush()
        study = await ValidationDriverService.read_and_plan(
            session, study, None, admin_user.organization_id, admin_user.id
        )
        recorded = study.evidence_json["methods_cutoffs"]
        assert [s["quote"] for s in recorded["statements"]] == [_DEFINED]
        assert recorded["source"] == "europe_pmc" and recorded["version"] == 1
        predicates = {p["claim_index"]: p["predicate"] for p in await claimed_predicates(session, study)}
        assert predicates[0]["cutoff_source"] == {"kind": "methods", "quote": _DEFINED}
        assert predicates[1]["cutoff_source"] == {"kind": "claim"}
        assert predicates[1]["significance"]["kind"] == "pvalue"

    @pytest.mark.asyncio
    async def test_the_report_names_the_quote_an_inherited_cutoff_came_from(
        self,
        session,
        admin_user,
        llm,
        quiet_discovery,
        europe_pmc_text,  # noqa: F811
    ):
        from app.services.validation_report_summary import report_summary_for
        from app.services.validation_study_service import ValidationStudyService
        from app.services.validation_driver_service import ValidationDriverService

        llm(_complete(**_EXTRACTION))
        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        await session.flush()
        study = await ValidationDriverService.read_and_plan(
            session, study, None, admin_user.organization_id, admin_user.id
        )
        summary = await report_summary_for(session, study, admin_user.organization_id)
        claim = summary["claims"][0]
        assert claim["cutoff_source"] == {"kind": "methods", "quote": _DEFINED}
        assert "adjusted P < 0.05" in claim["predicate"]


class TestOnlyDependentChecksFollow:
    @pytest.mark.asyncio
    async def test_a_newly_recorded_sentence_re_evaluates_only_the_claims_that_inherit_it(self, session, admin_user):
        from app.services import validation_check_queue as queue
        from app.services import validation_consistency_checks as consistency
        from app.services.reproduction_plan_service import ReproductionPlanService
        from app.services.validation_study_service import ValidationStudyService

        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        study.state = "classified"
        plan = await ReproductionPlanService.create_plan(
            session,
            study,
            admin_user.id,
            accessions=["GSE000001"],
            differential_design={
                "contrasts": [
                    {
                        "name": "KO vs WT",
                        "test_condition": "KO",
                        "reference_condition": "WT",
                        "reported_experiment_id": "e1",
                    }
                ]
            },
            reported_experiments=[
                {"id": "e1", "assay": "bulk RNA-seq", "claim_indices": [0, 1], "contrast_indices": [0]}
            ],
        )
        own = [{"kind": "pvalue", "operator": "<", "value": 0.01}]
        targets = await ReproductionPlanService.add_comparison_targets(
            session,
            plan,
            [
                {**_claim(cutoffs=[]), "metric_key": "", "contrast_index": 0},
                {**_claim(cutoffs=own, claimed_value=257), "metric_key": "", "contrast_index": 0},
            ],
        )
        study.evidence_json = {
            "supplements": [
                {
                    "filename": "S1_KO_vs_WT.txt",
                    "label": "Table S1",
                    "role": "results_table",
                    "resolved": True,
                    "kind": "attachment",
                    "url": "https://example.org/S1_KO_vs_WT.txt",
                }
            ]
        }
        await session.flush()
        await consistency.enqueue(session, study, plan)
        before = {r.comparison_target_id: r.revision for r in await queue.records_for(session, study.id)}

        study.evidence_json = {**study.evidence_json, "methods_cutoffs": record([_DEFINED], source="europe_pmc")}
        await session.flush()
        await consistency.enqueue(session, study, plan, reason="the methods were recorded")
        after = {r.comparison_target_id: r for r in await queue.records_for(session, study.id)}
        assert after[targets[0].id].revision == before[targets[0].id] + 1
        assert after[targets[0].id].history_json[-1]["superseded_because"] == "the methods were recorded"
        assert after[targets[1].id].revision == before[targets[1].id]


class TestThroughTheRecovery:
    """A recovery reads the article again for its passages; the same read records the methods sentences, and
    a check whose cutoff then changes is re-evaluated with the others. A check it does not change is not."""

    @pytest.mark.asyncio
    async def test_the_recovery_records_the_methods_and_re_evaluates_only_the_check_they_change(
        self, session, admin_user, monkeypatch
    ):
        from app.services import validation_check_queue as queue
        from app.services import validation_consistency_checks as consistency
        from app.services.literature.fulltext_service import FullTextFetchService, FullTextResult
        from app.services.reproduction_plan_service import ReproductionPlanService
        from app.services.validation_recovery import preview_recovery, run_recovery
        from app.services.validation_study_service import ValidationStudyService
        from app.services.validation_table_binding import BINDING_VERSION

        async def fetch(*, doi=None, pmid=None, pmcid=None):
            return FullTextResult(
                text="text",
                source="europepmc",
                external_id="PMC0000002",
                supplements=[],
                sections={"methods": [_DEFINED], "captions": {}},
            )

        monkeypatch.setattr(FullTextFetchService, "fetch", staticmethod(fetch))
        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        study.state = "classified"
        plan = await ReproductionPlanService.create_plan(
            session,
            study,
            admin_user.id,
            accessions=["GSE000001"],
            differential_design={
                "contrasts": [
                    {
                        "name": "KO vs WT",
                        "test_condition": "KO",
                        "reference_condition": "WT",
                        "reported_experiment_id": "e1",
                    }
                ]
            },
            reported_experiments=[
                {"id": "e1", "assay": "bulk RNA-seq", "claim_indices": [0, 1], "contrast_indices": [0]}
            ],
        )
        own = [{"kind": "pvalue", "operator": "<", "value": 0.01}]
        targets = await ReproductionPlanService.add_comparison_targets(
            session,
            plan,
            [
                {**_claim(cutoffs=[]), "metric_key": "", "contrast_index": 0},
                {**_claim(cutoffs=own, claimed_value=257), "metric_key": "", "contrast_index": 0},
            ],
        )
        study.evidence_json = {
            "pmcid": "PMC0000002",
            "supplements": [
                {
                    "filename": "S1_KO_vs_WT.txt",
                    "label": "Table S1",
                    "role": "results_table",
                    "resolved": True,
                    "kind": "attachment",
                    "url": "https://example.org/S1_KO_vs_WT.txt",
                }
            ],
        }
        await session.flush()
        # Both checks were decided under the current binding rules, so neither is affected on its own.
        for record in await consistency.enqueue(session, study, plan):
            await queue.finish(
                session,
                record,
                state=queue.UNRESOLVED,
                outcome={"outcome": "unresolved", "binding": {"version": BINDING_VERSION, "status": "candidate"}},
                terminal_reason=queue.BINDING,
            )
        await session.commit()
        before = {r.comparison_target_id: r.revision for r in await queue.records_for(session, study.id)}

        preview = await preview_recovery(session, study)
        assert "methods" in next(a for a in preview["actions"] if a["kind"] == "fetch_passages")["detail"]
        result = await run_recovery(session, study, user_id=admin_user.id, preview_fingerprint=preview["fingerprint"])
        await session.commit()

        recorded = study.evidence_json["methods_cutoffs"]
        assert [s["quote"] for s in recorded["statements"]] == [_DEFINED]
        assert study.evidence_json["recovery_history"][-1]["prior"]["methods_cutoffs"] is None
        assert result["cutoffs_changed"] == 1
        after = {r.comparison_target_id: r for r in await queue.records_for(session, study.id)}
        assert after[targets[0].id].revision == before[targets[0].id] + 1
        assert after[targets[0].id].history_json[-1]["superseded_because"].startswith("recovery")
        assert after[targets[1].id].revision == before[targets[1].id]


def test_the_markdown_export_quotes_the_methods_sentence():
    from app.services.provenance.markdown_renderer import _append_each_claim

    summary = {
        "claims": [
            {
                "description": "We identified 781 differentially expressed genes",
                "checks": [{"label": "Consistency with the authors' results", "status": "available"}],
                "predicate": "KO versus WT, adjusted P < 0.05, either direction, no fold-change requirement",
                "cutoff_source": {"kind": "methods", "quote": _DEFINED},
            }
        ]
    }
    parts: list[str] = []
    _append_each_claim(parts, summary)
    assert f'- Cutoff from the methods: "{_DEFINED}"' in "\n".join(parts)
