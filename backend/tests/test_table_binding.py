"""plan_8_2 section 1.1 and owner decision 1: a table is bound to a claim's contrast before any value in it
is compared.

Groff's XX-versus-XY table was compared with aneuploidy, morphology and morphokinetic counts because it was
the only table, producing invalid disagreements of 194 against 53, 10 and 9. SAMD1's undifferentiated
claims were matched to its differentiation table because the shared gene name looked distinctive when the
differentiation experiment was left out of the comparison. A binding is established by any one of: the
table's own columns name the contrast's arms; a verbatim passage linking the file to the contrast; or a
recorded confirmation. A filename alone keeps the table a candidate. Every accepted binding also resolves
what distinguishes repeated contrasts, and known contradictory metadata rejects the table outright.

The contrasts, filenames and passages below have the shape of the two papers; nothing here is a rule
about either paper.
"""

from app.services import validation_table_binding as binding

_GROFF = [
    {"name": "aneuploid vs euploid WE", "test_condition": "aneuploid WE", "reference_condition": "euploid WE"},
    {"name": "XX vs XY WE", "test_condition": "XX WE", "reference_condition": "XY WE"},
    {
        "name": "good morphology (AA) vs poor morphology (CC) WE",
        "test_condition": "AA morphology WE",
        "reference_condition": "CC morphology WE",
    },
    {
        "name": "high vs low morphokinetic quality",
        "test_condition": "high morphokinetic quality embryos",
        "reference_condition": "low morphokinetic quality embryos",
    },
]
_S3_NAME = "supp_gr.252981.119_Supplemental_File_3_XX-v-XY_siggenes.txt"
_S3_PASSAGE = (
    "Finally, we performed differential gene expression analyses between WEs with XX and XY karyotypes using "
    "the sex chromosome calls ascertained above. We identified 194 significantly differentially expressed genes "
    "of which 146 are sex-linked (Supplemental Fig. S2C; Supplemental Files S2, S3). We include the results of "
    "this analysis as Supplemental File S3."
)
_S3_HEADER = ["info", "baseMean", "log2FoldChange", "lfcSE", "stat", "pvalue", "padj", "genenames", "GENEID", "chr"]


def _s3(**extra):
    return {
        "name": _S3_NAME,
        "source": "supplement",
        "labels": ["Supplemental File S3"],
        "passages": [{"text": _S3_PASSAGE, "source": "paper_text"}],
        **extra,
    }


def _others(contrasts, index):
    return [c for i, c in enumerate(contrasts) if i != index]


class TestASingleTableIsStillOnlyACandidate:
    def test_the_sex_comparison_table_cannot_assess_the_aneuploidy_contrast(self):
        result = binding.bind(_s3(), _GROFF[0], competitors=_others(_GROFF, 0), header=_S3_HEADER)
        assert result["status"] == binding.REJECTED
        assert "XX vs XY WE" in result["reason"]

    def test_nor_the_morphology_or_morphokinetic_contrasts(self):
        for index in (2, 3):
            result = binding.bind(_s3(), _GROFF[index], competitors=_others(_GROFF, index), header=_S3_HEADER)
            assert result["status"] == binding.REJECTED, _GROFF[index]["name"]

    def test_the_passage_that_cites_it_binds_it_to_the_sex_contrast(self):
        result = binding.bind(_s3(), _GROFF[1], competitors=_others(_GROFF, 1), header=_S3_HEADER)
        assert result["status"] == binding.ESTABLISHED
        assert [e["kind"] for e in result["evidence"]] == ["passage"]
        assert result["evidence"][0]["text"] == _S3_PASSAGE
        assert result["version"] == binding.BINDING_VERSION

    def test_its_name_alone_keeps_it_a_candidate(self):
        table = {"name": _S3_NAME, "source": "supplement", "labels": ["Supplemental File S3"], "passages": []}
        result = binding.bind(table, _GROFF[1], competitors=_others(_GROFF, 1), header=_S3_HEADER)
        assert result["status"] == binding.CANDIDATE
        assert "name" in result["reason"]

    def test_a_single_unnamed_table_is_a_candidate_never_the_table(self):
        table = {"name": "results.txt", "source": "deposit"}
        header = ["gene", "log2FoldChange", "pvalue", "padj"]
        result = binding.bind(table, _GROFF[0], competitors=_others(_GROFF, 0), header=header)
        assert result["status"] == binding.CANDIDATE

    def test_a_table_whose_columns_name_the_arms_is_bound(self):
        table = {"name": "results.txt", "source": "deposit"}
        header = ["gene", "log2FoldChange(aneuploid/euploid)", "pvalue", "padj"]
        result = binding.bind(table, _GROFF[0], competitors=_others(_GROFF, 0), header=header)
        assert result["status"] == binding.ESTABLISHED
        assert result["evidence"][0]["kind"] == "columns"

    def test_columns_naming_another_contrast_reject_the_table(self):
        table = {"name": "results.txt", "source": "deposit"}
        header = ["gene", "log2FoldChange(XX/XY)", "pvalue", "padj"]
        result = binding.bind(table, _GROFF[0], competitors=_others(_GROFF, 0), header=header)
        assert result["status"] == binding.REJECTED

    def test_a_recorded_confirmation_binds_what_nothing_else_does(self):
        table = {"name": "results.txt", "source": "deposit"}
        confirmation = {"confirmed_by": "a person", "at": "2026-09-14", "contrast": _GROFF[0]["name"]}
        result = binding.bind(
            table, _GROFF[0], competitors=_others(_GROFF, 0), header=["gene", "x"], confirmation=confirmation
        )
        assert result["status"] == binding.ESTABLISHED
        assert result["evidence"][0]["kind"] == "confirmation"

    def test_a_confirmation_does_not_override_known_contradictory_metadata(self):
        confirmation = {"confirmed_by": "a person", "at": "2026-09-14", "contrast": _GROFF[0]["name"]}
        result = binding.bind(_s3(), _GROFF[0], competitors=_others(_GROFF, 0), confirmation=confirmation)
        assert result["status"] == binding.REJECTED


_SAMD1 = [
    {
        "name": "SAMD1 KO vs WT (undifferentiated ES cells)",
        "test_condition": "SAMD1 KO mouse ES cells",
        "reference_condition": "WT mouse ES cells",
        "reported_experiment_id": "e2",
    },
    {
        "name": "SAMD1 KO vs WT (day 7 differentiation)",
        "test_condition": "SAMD1 KO mouse ES cells day 7",
        "reference_condition": "WT mouse ES cells day 7",
        "reported_experiment_id": "e3",
    },
]
_DIFFERENTIATION = "GSE144396_DeSeq2-Differentiation-SAMD1KOvsWT.txt.gz"
_UNDIFFERENTIATED = "GSE144396_RNA-Seq_DeSeq2.txt.gz"
_OTHER_KNOCKOUT = "GSE144396_DeSeq2-KDM1AKOvsWT.txt.gz"


class TestRepeatedContrastsAcrossExperiments:
    def test_a_shared_gene_name_never_makes_the_other_experiments_table_this_ones(self):
        table = {"name": _DIFFERENTIATION, "source": "deposit"}
        result = binding.bind(table, _SAMD1[0], competitors=[_SAMD1[1]])
        assert result["status"] == binding.REJECTED
        assert "differentiation" in result["reason"].lower()

    def test_the_differentiation_table_is_only_a_candidate_for_its_own_contrast_by_name(self):
        table = {"name": _DIFFERENTIATION, "source": "deposit"}
        result = binding.bind(table, _SAMD1[1], competitors=[_SAMD1[0]])
        assert result["status"] == binding.CANDIDATE

    def test_columns_naming_arms_two_experiments_share_do_not_say_which_experiment(self):
        table = {"name": "combined.txt", "source": "deposit"}
        header = ["gene", "log2FC_SAMD1KO_vs_WT", "padj"]
        result = binding.bind(table, _SAMD1[0], competitors=[_SAMD1[1]], header=header)
        assert result["status"] == binding.UNRESOLVED
        assert "SAMD1 KO vs WT (day 7 differentiation)" in result["reason"]

    def test_columns_that_name_the_arms_and_the_distinguishing_condition_bind(self):
        table = {"name": "combined.txt", "source": "deposit"}
        header = ["gene", "log2FC_SAMD1KO_vs_WT_day7", "padj"]
        result = binding.bind(table, _SAMD1[1], competitors=[_SAMD1[0]], header=header)
        assert result["status"] == binding.ESTABLISHED

    def test_a_table_naming_an_unrelated_knockout_is_not_evidence_for_this_one(self):
        table = {"name": _OTHER_KNOCKOUT, "source": "deposit"}
        result = binding.bind(table, _SAMD1[0], competitors=[_SAMD1[1]])
        assert result["status"] in (binding.CANDIDATE, binding.REJECTED)
        assert result["status"] != binding.ESTABLISHED


_POOLED = [
    {"name": "KO vs WT", "test_condition": "KO", "reference_condition": "WT"},
    {"name": "DKO vs WT", "test_condition": "DKO", "reference_condition": "WT"},
]


class TestAPooledTable:
    def test_a_multi_contrast_table_serves_a_contrast_through_its_own_columns(self):
        table = {"name": "all_contrasts.txt", "source": "deposit"}
        header = ["gene", "KO_vs_WT_log2FC", "KO_vs_WT_padj", "DKO_vs_WT_log2FC", "DKO_vs_WT_padj"]
        first = binding.bind(table, _POOLED[0], competitors=[_POOLED[1]], header=header)
        second = binding.bind(table, _POOLED[1], competitors=[_POOLED[0]], header=header)
        assert first["status"] == second["status"] == binding.ESTABLISHED
        assert first["selector"] == {"lfc": 1, "padj": 2}
        assert second["selector"] == {"lfc": 3, "padj": 4}

    def test_a_passage_naming_several_contrasts_needs_a_selector_in_the_table(self):
        table = {
            "name": "Table S2.txt",
            "source": "supplement",
            "labels": ["Supplementary Table S2"],
            "passages": [
                {"text": "KO vs WT and DKO vs WT results are in Supplementary Table S2.", "source": "paper_text"}
            ],
        }
        result = binding.bind(table, _POOLED[0], competitors=[_POOLED[1]], header=["gene", "log2FoldChange", "padj"])
        assert result["status"] == binding.UNRESOLVED
        assert "selector" in result["reason"] or "column" in result["reason"]


class TestAModelsProposal:
    def test_a_proposal_counts_only_when_its_quote_is_found_in_the_source(self):
        table = {"name": "results.txt", "source": "supplement", "labels": ["Supplementary Table S5"], "passages": []}
        sources = {
            "paper_text": "Genes changed between aneuploid and euploid WEs are listed in Supplementary Table S5."
        }
        verified = binding.bind(
            table,
            _GROFF[0],
            competitors=_others(_GROFF, 0),
            proposal={
                "quote": "Genes changed between aneuploid and euploid WEs are listed in Supplementary Table S5.",
                "source": "paper_text",
            },
            sources=sources,
        )
        invented = binding.bind(
            table,
            _GROFF[0],
            competitors=_others(_GROFF, 0),
            proposal={"quote": "Table S5 lists the aneuploid versus euploid genes.", "source": "paper_text"},
            sources=sources,
        )
        assert verified["status"] == binding.ESTABLISHED
        assert verified["evidence"][0]["kind"] == "passage"
        assert invented["status"] == binding.CANDIDATE


class TestTheBindingIsAContract:
    def test_the_binding_names_its_source_experiment_contrast_and_revision(self):
        result = binding.bind(
            _s3(checksum="abc"), {**_GROFF[1], "reported_experiment_id": "e1"}, competitors=_others(_GROFF, 1)
        )
        assert result["source"] == {
            "name": _S3_NAME,
            "source": "supplement",
            "accession": None,
            "supplement_index": None,
            "checksum": "abc",
        }
        assert result["experiment"] == "e1"
        assert result["contrast"]["name"] == "XX vs XY WE"
        assert result["version"] == binding.BINDING_VERSION
        assert binding.established(result)
        assert not binding.established({**result, "version": binding.BINDING_VERSION - 1})
        assert not binding.established(None)
