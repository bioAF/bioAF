"""plan_8_2 section 3.2 and owner decision 3: counting a published list is not reproducing its selection.

Groff states "We identified 194 significantly differentially expressed genes of which 146 are sex-linked"
and cites Supplemental File S3 in that sentence, and S3 is that list. The claim states no cutoff, so bioAF
cannot reapply the selection, but it can count the list the paper published, and say that is all it did.
A list count needs a table bound to the claim's contrast and evidence that the file is the claim's complete
selected list; it counts distinct identifiers; a subgroup needs a field that defines it; an unfiltered or
partial list never passes as the selected list. It counts under rubric version 2 at consistency depth,
labelled as a list count, never as validation of the selection threshold.
"""

import pytest

from app.services.validation_author_consistency import claim_predicates, supplement_consistency
from tests.test_binding_acceptance import _GROFF, _GROFF_CONTRASTS

_S3_BYTES = (_GROFF / "supplemental_file_3_siggenes.txt").read_bytes()
_SENTENCE = (
    "We identified 194 significantly differentially expressed genes of which 146 are sex-linked "
    "(Supplemental Fig. S2C; Supplemental Files S2, S3)."
)
_PASSAGE = (
    "Finally, we performed differential gene expression analyses between WEs with XX and XY karyotypes using the "
    f"sex chromosome calls ascertained above. {_SENTENCE} We include the results of this analysis as Supplemental "
    "File S3."
)
_S3_TABLE = {"labels": ["Supplemental File S3"], "passages": [{"text": _PASSAGE, "source": "paper_text"}]}


def _claims(*values):
    return [
        {
            "claim_text": "We identified 194 significantly differentially expressed genes of which 146 are sex-linked",
            "claimed_value": value,
            "output_type": "gene_set_size",
            "contrast_index": 1,
            "cutoffs": [],
            "count_relation": "=",
        }
        for value in values
    ]


class _Plan:
    differential_design_json = {"contrasts": _GROFF_CONTRASTS}


def _records(blob=_S3_BYTES, table=_S3_TABLE, values=(194, 146)):
    predicates = claim_predicates(_claims(*values), _Plan())
    return {
        r["claim_index"]: r for r in supplement_consistency(blob, "S3_XX-v-XY_siggenes.txt", predicates, table=table)
    }


class TestTheListTheParagraphCites:
    def test_the_total_is_the_lists_distinct_genes(self):
        record = _records()[0]
        assert (record["method"], record["outcome"], record["rows_passing"]) == ("published_list_count", "agree", 194)
        assert any("does not check the statistical procedure" in a for a in record["assumptions"])
        assert record["list"]["evidence"]["text"] == _SENTENCE

    def test_the_sex_linked_subgroup_is_counted_from_the_tables_own_chromosome_field(self):
        record = _records()[1]
        assert (record["method"], record["outcome"], record["rows_passing"]) == ("published_list_count", "agree", 146)
        assert record["list"]["subgroup"]["definition"] == "located on chromosome X or Y"
        assert record["list"]["subgroup"]["field"] == "chr"
        assert record["list"]["subgroup"]["unmapped"] == 0

    def test_the_total_and_the_subgroup_have_separate_prerequisites(self):
        without_chromosomes = b"\n".join(
            b"\t".join(line.split(b"\t")[:-1]) for line in _S3_BYTES.splitlines() if line.strip()
        )
        records = _records(blob=without_chromosomes)
        assert records[0]["outcome"] == "agree"
        assert records[1]["outcome"] == "unresolved"
        assert "chromosome" in records[1]["reason"]


class TestWhatIsNotTheSelectedList:
    def test_an_unfiltered_table_cannot_pass_as_the_selected_list(self):
        universe = _S3_BYTES.decode().splitlines()
        header = universe[0].split("\t")
        padj = header.index("padj")
        extra = []
        for i in range(20):
            cells = universe[1].split("\t")
            cells[header.index("GENEID")] = f"NOTSIG{i}"
            cells[padj] = "0.93"
            extra.append("\t".join(cells))
        record = _records(blob="\n".join(universe + extra).encode())[0]
        assert record["outcome"] == "unresolved"
        assert "not the claim's selected list" in record["reason"]

    def test_a_partial_list_cannot_pass_as_the_selected_list(self):
        table = {
            "labels": ["Supplemental File S3"],
            "passages": [
                {
                    "text": "Genes differentially expressed between XX and XY WEs; the top 194 are listed in "
                    "Supplemental File S3.",
                    "source": "paper_text",
                }
            ],
        }
        record = _records(table=table, values=(194,))[0]
        assert record["outcome"] in ("unresolved", "not_checkable")
        assert record.get("method") != "published_list_count" or record["outcome"] == "unresolved"

    def test_with_no_evidence_that_the_file_is_the_claims_list_nothing_is_counted(self):
        table = {
            "labels": ["Supplemental File S3"],
            "passages": [{"text": "XX and XY WEs were compared (Supplemental File S3).", "source": "paper_text"}],
        }
        record = _records(table=table, values=(194,))[0]
        assert record["outcome"] == "not_checkable"
        assert record.get("method") != "published_list_count"


class TestHowAListCountIsShown:
    def test_the_report_labels_it_a_list_count(self):
        from app.services.validation_report_summary import _consistency_row

        row = _consistency_row(_records()[0])
        assert row["label"] == "List count agrees with the authors' published list"
        assert row["method"] == "published_list_count"

    @pytest.mark.parametrize("value", [194])
    def test_under_version_two_it_counts_at_consistency_depth_labelled_as_a_list_count(self, value):
        from app.services.validation_rubric_v2 import govern_claim

        record = {**_records(values=(value,))[0], "label": "List count agrees with the authors' published list"}
        governed = govern_claim(0, [], [record])
        assert governed["status"] == "supported" and governed["depth"] == "consistency"


def test_the_markdown_export_says_what_was_counted_and_never_rows_passing():
    from app.services.provenance.markdown_renderer import _append_each_claim
    from app.services.validation_report_summary import summarize

    record = _records()[1]
    claim = {**_claims(146)[0], "id": 1, "checks": {"author_results": {"status": "available", "reason": None}}}
    evidence = {
        "supplements": [
            {
                "filename": "S3_XX-v-XY_siggenes.txt",
                "role": "results_table",
                "resolved": True,
                "consistency": [{**record, "claim_index": 0}],
            }
        ]
    }
    plan = {"differential_design": {"contrasts": _GROFF_CONTRASTS}}
    summary = summarize(study={"state": "classified"}, evidence=evidence, plan=plan, targets=[claim], issues=[])
    parts: list[str] = []
    _append_each_claim(parts, summary)
    text = "\n".join(parts)
    assert "List count agrees with the authors' published list (S3_XX-v-XY_siggenes.txt)" in text
    assert "146 distinct identifiers located on chromosome X or Y (field chr) among 194 rows" in text
    assert f'The paper names this file as the list: "{_SENTENCE}"' in text
    assert "does not check the statistical procedure" in text
    assert "rows pass" not in text


class TestThroughRetrievalAndTheQueue:
    """The real paper's passage, recorded at retrieval, carries the evidence to both check paths."""

    async def _supplements(self, claims):
        from app.services.supplement_inventory import parse_jats_supplements, resolve_supplements
        from tests.test_binding_acceptance import _bundle

        async def fetch(url):
            return _bundle()

        return await resolve_supplements(
            "PMC0000001",
            parse_jats_supplements((_GROFF / "fulltext_jats.xml").read_text()),
            fetcher=fetch,
            predicates=claim_predicates([dict(c) for c in claims], _Plan()),
        )

    @pytest.mark.asyncio
    async def test_at_retrieval_the_cited_list_is_counted(self):
        from tests.test_binding_acceptance import _s3_row

        records = {r["claim_index"]: r for r in _s3_row(await self._supplements(_claims(194, 146)))["consistency"]}
        assert [(records[i]["method"], records[i]["outcome"]) for i in (0, 1)] == [
            ("published_list_count", "agree"),
            ("published_list_count", "agree"),
        ]

    @pytest.mark.asyncio
    async def test_the_queued_check_counts_it_too(self, session, admin_user):
        from app.services import validation_check_queue as queue
        from app.services import validation_consistency_checks as consistency
        from app.services.reproduction_plan_service import ReproductionPlanService
        from app.services.validation_study_service import ValidationStudyService
        from tests.test_binding_acceptance import _s3_row

        claims = _claims(194, 146)
        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        study.state = "classified"
        plan = await ReproductionPlanService.create_plan(
            session,
            study,
            admin_user.id,
            accessions=["EGAS00000000001"],
            differential_design={"contrasts": [{**c, "reported_experiment_id": "e1"} for c in _GROFF_CONTRASTS]},
            reported_experiments=[{"id": "e1", "assay": "bulk RNA-seq", "claim_indices": [0, 1]}],
        )
        targets = await ReproductionPlanService.add_comparison_targets(
            session, plan, [{**c, "metric_key": "", "reported_experiment_id": "e1"} for c in claims]
        )
        # Retrieval held no comparison, so the queue reads the table itself, from its own copy.
        supplements = await self._supplements(claims)
        for row in supplements:
            row.pop("consistency", None)
        _s3_row(supplements)["url"] = "https://example.org/S3.txt"
        study.evidence_json = {"supplements": supplements}
        await session.flush()

        async def fetch(url):
            assert url == "https://example.org/S3.txt"
            return _S3_BYTES

        await consistency.enqueue(session, study, plan)
        await consistency.run_pending(session, study, plan, fetcher=fetch)
        records = {r.comparison_target_id: r for r in await queue.records_for(session, study.id)}
        for target, value in zip(targets, (194, 146)):
            outcome = records[target.id].outcome_json
            assert (outcome["method"], outcome["outcome"], outcome["rows_passing"]) == (
                "published_list_count",
                "agree",
                value,
            )
