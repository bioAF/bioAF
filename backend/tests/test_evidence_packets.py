"""plan_8_6 section 3: one bounded packet per obligation, selected for the obligation being asked.

The passages an obligation is judged on must be relevant TO THAT OBLIGATION. A computational
obligation is not judged on culture protocol, and an experimental one is not judged on a
differential-expression threshold. Study 65's M1.B was verified from Matrigel, mTeSR Plus and EDTA
passaging because the assessor was handed the first forty keyword-matched sentences of the paper and
those were what they were.

What the packet also has to record is its own COVERAGE: which sources and sections were available,
supplied, excluded as irrelevant, unavailable or deferred by budget. Indexing a section is not
evidence that the assessor inspected it, and an absence finding rests on that record (section 8).
"""

from app.services.validation_evidence_index import build_index
from app.services.validation_evidence_packets import PACKET_VERSION, packet_for

_SECTIONS = {
    "index": [
        {
            "title": "Cell culture",
            "kind": "methods",
            "paragraphs": [
                "hiPSCs were maintained on Matrigel-coated plates in mTeSR Plus medium and passaged "
                "with 0.5 mM EDTA every four days. Doxycycline was added at 1 ug/mL."
            ],
        },
        {
            "title": "Bulk RNA sequencing",
            "kind": "methods",
            "paragraphs": [
                "Total RNA was extracted with the RNeasy Mini Kit and libraries were prepared with the "
                "Illumina Stranded mRNA kit. Libraries were sequenced on a NovaSeq 6000."
            ],
        },
        {
            "title": "Bulk RNA-seq analysis",
            "kind": "methods",
            "paragraphs": [
                "Reads were trimmed with Trim Galore 0.6.7, aligned to GRCh38 with STAR 2.7.9a and "
                "quantified with RSEM 1.3.3. Differential expression was tested with DESeq2 1.34.0 and "
                "genes with an absolute log2 fold change greater than 3 and an adjusted P value below "
                "0.05 were used as input to GO enrichment."
            ],
        },
        {
            "title": "Single-cell RNA sequencing",
            "kind": "methods",
            "paragraphs": [
                "Cells with fewer than 500 detected genes or more than 15% mitochondrial reads were "
                "removed, and doublets were called with Scrublet."
            ],
        },
        {
            "title": "figure 4",
            "kind": "legend",
            "paragraphs": ["Figure 4. One sample per clone and condition was analysed."],
        },
        {
            "title": "Discussion",
            "kind": "discussion",
            "paragraphs": [
                "Only selected high-performing clones were carried forward, and a single granulosa-like "
                "line was used for the single-cell experiments."
            ],
        },
        {
            "title": "Introduction",
            "kind": "introduction",
            "paragraphs": ["Granulosa cells are the somatic cells of the ovarian follicle."],
        },
    ]
}

_INDEX = build_index("", sections=_SECTIONS, source="europe_pmc")


def _text_of(packet) -> str:
    return " ".join(p["text"] for p in packet["passages"])


class TestAComputationalObligationIsNotJudgedOnCultureProtocol:
    def test_m1_b_carries_the_preprocessing_parameters(self):
        packet = packet_for("M1.B", index=_INDEX)
        text = _text_of(packet)
        assert "Trim Galore" in text
        assert "mitochondrial" in text

    def test_m1_b_does_not_carry_the_culture_protocol(self):
        """Section 6's acceptance: the study 65 citation set can no longer satisfy M1.B."""
        text = _text_of(packet_for("M1.B", index=_INDEX))
        assert "Matrigel" not in text
        assert "mTeSR" not in text
        assert "EDTA" not in text

    def test_the_culture_paragraph_is_recorded_as_excluded_rather_than_forgotten(self):
        packet = packet_for("M1.B", index=_INDEX)
        assert packet["coverage"]["excluded_irrelevant"] >= 1


class TestAnExperimentalObligationIsNotJudgedOnAnAnalysisThreshold:
    def test_e1_b_carries_the_bench_procedure(self):
        text = _text_of(packet_for("E1.B", index=_INDEX))
        assert "RNeasy" in text or "NovaSeq" in text
        assert "Matrigel" in text

    def test_e1_b_does_not_carry_the_differential_expression_cutoff(self):
        text = _text_of(packet_for("E1.B", index=_INDEX))
        assert "DESeq2" not in text


class TestDesignEvidenceReachesTheDesignObligation:
    def test_e2_b_carries_the_replication_legend_and_the_clone_selection(self):
        text = _text_of(packet_for("E2.B", index=_INDEX))
        assert "One sample per clone" in text
        assert "high-performing clones" in text


class TestAnIrrelevantOnlyPacketIsUntestedRatherThanAnswered:
    def test_a_paper_with_nothing_for_this_obligation_yields_no_passages(self):
        index = build_index(
            "",
            sections={"index": [{"title": "Introduction", "kind": "introduction", "paragraphs": ["Cells matter."]}]},
            source="pasted",
        )
        packet = packet_for("M1.B", index=index)
        assert packet["passages"] == []
        assert packet["coverage"]["sufficient"] is False
        assert packet["coverage"]["reason"]


class TestTheOrderOfTheDocumentIsNotTheOrderOfRelevance:
    def test_a_reordered_paper_keeps_the_same_evidence(self):
        reordered = {"index": list(reversed(_SECTIONS["index"]))}
        packet = packet_for("M1.B", index=build_index("", sections=reordered, source="europe_pmc"))
        text = _text_of(packet)
        assert "Trim Galore" in text
        assert "mitochondrial" in text

    def test_a_long_irrelevant_introduction_does_not_crowd_out_the_methods(self):
        padded = {
            "index": [
                {
                    "title": "Introduction",
                    "kind": "introduction",
                    "paragraphs": [f"Prior work established point {i} about follicles." for i in range(400)],
                },
                *_SECTIONS["index"],
            ]
        }
        packet = packet_for("M1.B", index=build_index("", sections=padded, source="europe_pmc"))
        assert "Trim Galore" in _text_of(packet)


class TestAValidMethodWithoutTheOldKeywordsIsStillAssessable:
    def test_a_methods_paragraph_that_matches_no_ranking_term_can_still_be_supplied(self):
        index = build_index(
            "",
            sections={
                "index": [
                    {
                        "title": "Processing",
                        "kind": "methods",
                        "paragraphs": ["Ribosomal sequences were set aside before the matrix was assembled."],
                    }
                ]
            },
            source="europe_pmc",
        )
        packet = packet_for("M1.A", index=index)
        assert "Ribosomal sequences" in _text_of(packet)


class TestThePacketIsBounded:
    def test_it_does_not_exceed_its_character_budget(self):
        from app.services.validation_evidence_packets import MAX_PACKET_CHARS

        padded = {
            "index": [
                {
                    "title": "Bulk RNA-seq analysis",
                    "kind": "methods",
                    "paragraphs": [
                        f"Reads were trimmed with Trim Galore and aligned with STAR for sample {i}." for i in range(900)
                    ],
                }
            ]
        }
        packet = packet_for("M1.B", index=build_index("", sections=padded, source="europe_pmc"))
        assert sum(len(p["text"]) for p in packet["passages"]) <= MAX_PACKET_CHARS

    def test_what_did_not_fit_is_offered_as_one_targeted_expansion(self):
        padded = {
            "index": [
                {
                    "title": "Bulk RNA-seq analysis",
                    "kind": "methods",
                    "paragraphs": [
                        f"Reads were trimmed with Trim Galore and aligned with STAR for sample {i}." for i in range(900)
                    ],
                }
            ]
        }
        packet = packet_for("M1.B", index=build_index("", sections=padded, source="europe_pmc"))
        assert packet["expansion"], "the next-ranked passages are kept for one bounded second ask"
        assert packet["coverage"]["deferred"]
        assert packet["coverage"]["truncated"] is True


class TestCoverageSaysWhatWasAndWasNotInspected:
    def test_it_names_the_sections_it_supplied(self):
        coverage = packet_for("M1.B", index=_INDEX)["coverage"]
        assert any("RNA-seq analysis" in s for s in coverage["supplied"])
        assert coverage["packet_version"] == PACKET_VERSION

    def test_a_complete_methods_packet_is_sufficient_for_an_absence_finding(self):
        assert packet_for("M1.B", index=_INDEX)["coverage"]["sufficient"] is True

    def test_a_source_this_obligation_needs_and_bioaf_did_not_get_makes_it_insufficient(self):
        packet = packet_for(
            "M5.B",
            index=_INDEX,
            limitations=[{"needs": "code", "reason": "the repository was never fetched"}],
        )
        assert packet["coverage"]["sufficient"] is False
        assert any("repository" in u for u in packet["coverage"]["unavailable"])

    def test_a_limitation_about_a_source_this_obligation_does_not_need_does_not_block_it(self):
        packet = packet_for(
            "E1.B",
            index=_INDEX,
            limitations=[{"needs": "code", "reason": "the repository was never fetched"}],
        )
        assert packet["coverage"]["sufficient"] is True

    def test_an_unretrieved_supplement_blocks_every_documentary_absence_finding(self):
        """A supplementary methods document bioAF never got is a source an absence would be about."""
        limitation = [{"needs": "supplements", "reason": "the supplementary bundle was too large to retrieve"}]
        for leaf in ("M1.B", "E1.B", "E2.B", "M4.A"):
            packet = packet_for(leaf, index=_INDEX, limitations=limitation)
            assert packet["coverage"]["sufficient"] is False, leaf
            assert any("too large" in u for u in packet["coverage"]["unavailable"]), leaf

    def test_the_passages_still_reach_the_assessor_when_coverage_is_short(self):
        """Section 3: incomplete coverage restricts what an ABSENCE can establish; it does not make
        the obligation unanswerable from the evidence that is in hand."""
        packet = packet_for(
            "M1.B",
            index=_INDEX,
            limitations=[{"needs": "supplements", "reason": "the supplementary bundle was too large"}],
        )
        assert packet["passages"]


class TestEvidenceThatIsNotTheArticlesText:
    def test_a_deposit_record_reaches_the_sample_obligation(self):
        extras = [
            {
                "id": "GSE1/GSM1",
                "kind": "deposit",
                "source": "the deposited record of GSE1",
                "text": "granulosa-like cells; Homo sapiens; day 5",
            }
        ]
        packet = packet_for("S2.B", index=_INDEX, extras=extras)
        assert any(p["id"] == "GSE1/GSM1" for p in packet["passages"])

    def test_the_supplied_code_reaches_the_traceability_obligation(self):
        extras = [
            {
                "id": "code:deg_interpretation.py",
                "kind": "code",
                "source": "the supplied deg_interpretation.py",
                "text": "GO_LOG2FC_THRESHOLD = 1",
            }
        ]
        packet = packet_for("M5.B", index=_INDEX, extras=extras)
        assert any(p["id"] == "code:deg_interpretation.py" for p in packet["passages"])

    def test_the_code_does_not_crowd_into_an_obligation_about_the_bench(self):
        extras = [
            {
                "id": "code:deg_interpretation.py",
                "kind": "code",
                "source": "the supplied deg_interpretation.py",
                "text": "GO_LOG2FC_THRESHOLD = 1",
            }
        ]
        packet = packet_for("E1.B", index=_INDEX, extras=extras)
        assert not any(p["id"].startswith("code:") for p in packet["passages"])


class TestEveryJudgedObligationHasASelector:
    def test_no_judged_leaf_falls_through_to_nothing(self):
        from app.services.validation_documentary_review import JUDGED_LEAVES
        from app.services.validation_evidence_packets import selector_for

        for leaf in JUDGED_LEAVES:
            assert selector_for(leaf) is not None, leaf


class TestThePacketIsSizedAgainstADeclaredBudget:
    """plan_8_6 section 11: the input budget is declared where every other budget is, and the
    packet is sized against it rather than against a row count.

    Measured on study 65 at the 8,192 tokens section 11 said to start from: all seventeen judged
    obligations deferred relevant evidence, so no absence finding could be reported on a real paper
    at all. The cap moved to 16,384 with that measurement recorded beside it.
    """

    def test_the_budget_is_declared_in_the_decision_audit(self):
        from app.services.validation_decision_budgets import DOCUMENTARY_JUDGMENT, policy_for

        declared = policy_for(DOCUMENTARY_JUDGMENT)
        assert declared.max_input_tokens >= 8192
        assert declared.provenance()["max_input_tokens"] == declared.max_input_tokens

    def test_the_packet_fits_inside_it(self):
        from app.services.validation_decision_budgets import DOCUMENTARY_JUDGMENT, policy_for
        from app.services.validation_evidence_packets import MAX_PACKET_CHARS

        declared = policy_for(DOCUMENTARY_JUDGMENT).max_input_tokens
        # Four characters to a token for prose, with room left for the instructions and the question.
        assert MAX_PACKET_CHARS / 4 < declared * 0.85

    def test_the_character_budget_binds_before_the_row_count(self):
        """A row count deciding a token question is what cut packets sitting at 21,000 characters."""
        from app.services.validation_evidence_packets import MAX_PACKET_CHARS, MAX_PACKET_PASSAGES

        # A packet of typical paragraphs reaches the character budget before the passage count.
        assert MAX_PACKET_CHARS / MAX_PACKET_PASSAGES <= 700


class TestAPaperWhoseCodeIsCarriedWholeStillFitsItsPacket:
    """Section 11: where the acceptance evidence cannot fit, adjust the cap on a measurement.

    Measured on the demo against study 65's own held evidence, 2026-09-21, once the supplied code
    reached selection whole: M1.B's relevant evidence is 51,946 characters (37,954 of code across
    nine sources, 9,077 of the paper's methods, the rest deposit and design facts) and C5's is
    47,632. At 48,000 both deferred a relevant excerpt, which under section 8 leaves the obligation
    unable to report an absence the owner's review says it should still be able to report.

    The declared input budget does not move: 54,000 characters is about 13,500 tokens against the
    16,384 the decision audit already declares.
    """

    def _relevant(self, chars: int) -> list[dict]:
        row = (
            "lines 1-12:\n"
            "sc.pp.filter_cells(adata, min_genes=1000)\n"
            "sc.pp.filter_genes(adata, min_cells=5)\n"
            "adata = adata[adata.obs.pct_counts_mt < 10]\n"
            "sc.pp.normalize_total(adata, target_sum=1e6)\n"
        )
        row += "x = 1\n" * ((900 - len(row)) // 6)
        return [
            {"id": f"code:pipeline.py#{i}", "kind": "code", "source": "the supplied pipeline.py", "text": row}
            for i in range(chars // len(row))
        ]

    def test_fifty_two_thousand_characters_of_relevant_evidence_are_carried_whole(self):
        packet = packet_for("M1.B", index=_INDEX, extras=self._relevant(52_000))
        assert packet["coverage"]["deferred_relevant"] == []
        assert packet["coverage"]["truncated"] is False

    def test_an_absence_can_still_be_reported_over_that_packet(self):
        from app.services.validation_judgment import coverage_supports_absence

        packet = packet_for("M1.B", index=_INDEX, extras=self._relevant(52_000))
        supported, why = coverage_supports_absence(packet["coverage"])
        assert supported is True, why


class TestTheRowCountIsAGuardAndNotASecondBudget:
    """Measured on the demo, 2026-09-21: S2.B carried 80 deposit records at 25,624 characters and
    deferred eighteen more, so an obligation about the deposited records could not report an
    absence on evidence that was half of its character budget. A record is a short row; a packet of
    them reaches the characters that map to tokens long after it reaches eighty rows."""

    def _records(self, count: int) -> list[dict]:
        return [
            {
                "id": f"GSE9/GSM{i}",
                "kind": "deposit",
                "source": "the deposited record of GSE9",
                "text": (
                    f"granulosa-like ovaroid replicate {i}; Homo sapiens; ovaroid; cell type: "
                    f"granulosa-like; treatment: doxycycline; time point: day 4; library: 10x 3' v3"
                ),
            }
            for i in range(count)
        ]

    def test_a_hundred_and_fifty_deposited_records_are_all_carried(self):
        packet = packet_for("S2.B", index=_INDEX, extras=self._records(150))
        carried = [p for p in packet["passages"] if p["id"].startswith("GSE9/")]
        assert len(carried) == 150
        assert packet["coverage"]["truncated"] is False
