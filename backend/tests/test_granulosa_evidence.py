"""plan_8_6 section 10: the defects, accepted on the evidence they were found in.

Study 65 is eLife 83291, the owner's negative control. It scored 27.5/100 positive and 0/100
negative on the deployed demo on 2026-09-20. `tests/fixtures/granulosa/fulltext_jats.xml` is the
Europe PMC document bioAF read for it, and these tests are run against that document rather than
against a paraphrase of it.

What the assessment missed, and what must now reach the obligation that needs it:

- the detailed RNA-seq methods, which state `log 2 fc >3, p adj < 0.05` for the GO-enrichment input;
- the single-cell methods, which state `scanpy ingest`, `Scanpy (version 1.8.2)`, doublet filtering
  and the Parse pipeline version;
- the replication facts in the legends and the methods ("Two biological replicates were collected
  for each sample", "6 ovaroids per sample, 2 samples per time point");
- the clone selection and the single line, which E2.B is about;
- the cell-culture protocol, which is what M1.B was wrongly verified from and must no longer reach it.
"""

import pathlib

import pytest

from app.services.literature.fulltext_service import _jats_sections, _jats_to_text
from app.services.validation_evidence_index import build_index
from app.services.validation_evidence_packets import packet_for

_JATS = (pathlib.Path(__file__).parent / "fixtures" / "granulosa" / "fulltext_jats.xml").read_text()


@pytest.fixture(scope="module")
def index():
    return build_index(_jats_to_text(_JATS), sections=_jats_sections(_JATS), source="europe_pmc")


def _text(packet) -> str:
    return " ".join(p["text"] for p in packet["passages"])


class TestTheMethodsThatWereCutOffNowReachTheIndex:
    def test_the_whole_methods_section_is_indexed(self, index):
        methods = [p for p in index["passages"] if p["kind"] == "methods"]
        carried = " ".join(p["text"] for p in methods)
        assert "kallisto" in carried
        assert "Ensembl GRCh38 v96" in carried
        assert "DESeq2" in carried
        assert "scanpy ingest" in carried
        assert "Scanpy (version 1.8.2)" in carried

    def test_the_go_enrichment_threshold_the_paper_states_is_in_the_index(self, index):
        carried = " ".join(p["text"] for p in index["passages"])
        assert "log 2 fc >3" in carried

    def test_the_methods_subsections_keep_their_own_titles(self, index):
        titles = {p["section"] for p in index["passages"] if p["kind"] == "methods"}
        assert "RNA-seq" in titles
        assert "Single-cell RNA sequencing" in titles
        assert "Cell culture" in titles

    def test_the_repository_and_its_cited_revision_are_in_the_index(self, index):
        carried = " ".join(p["text"] for p in index["passages"] if p["kind"] == "availability")
        assert "github.com/programmablebio/granulosa" in carried
        assert "swh:1:rev:3c650290779db376c4d1f3a14960b08b17ae5561" in carried


class TestEachObligationIsGivenTheEvidenceThatAnswersIt:
    def test_m1_carries_the_preprocessing_the_paper_actually_describes(self, index):
        carried = _text(packet_for("M1.A", index=index)) + _text(packet_for("M1.B", index=index))
        assert "kallisto" in carried
        assert "doublet filtering" in carried
        assert "Scanpy (version 1.8.2)" in carried

    def test_m1_b_is_not_given_the_culture_protocol_it_was_verified_from(self, index):
        """Section 6's acceptance: the study 65 citation set can no longer satisfy M1.B.

        The cited evidence was the key resources table, mTeSR Plus on Matrigel and EDTA passaging,
        which is the paper's `Cell culture`, `Electroporations` and `Flow cytometry/cell sorting`
        methods. None of those sections may reach a computational preprocessing obligation.
        """
        sections = {p["section"] for p in packet_for("M1.B", index=index)["passages"]}
        assert "Cell culture" not in sections
        assert "Electroporations" not in sections
        assert "Flow cytometry/cell sorting" not in sections
        assert "Immunofluorescence" not in sections
        assert "Reporter construction" not in sections
        assert "Materials availability" not in sections

    def test_e1_still_gets_the_culture_protocol_that_m1_b_must_not(self, index):
        """The same paragraphs are the right evidence for the row that is about the bench."""
        sections = {p["section"] for p in packet_for("E1.B", index=index)["passages"]}
        assert "Cell culture" in sections

    def test_m2_is_given_the_reference_build(self, index):
        assert "Ensembl GRCh38 v96" in _text(packet_for("M2.B", index=index))

    def test_m4_is_given_the_thresholds_the_paper_states(self, index):
        carried = _text(packet_for("M4.A", index=index))
        assert "log 2 fc >3" in carried
        assert "p adj < 0.05" in carried

    def test_e1_is_given_the_bench_procedure(self, index):
        carried = _text(packet_for("E1.A", index=index)) + _text(packet_for("E1.B", index=index))
        assert "NEBNext Ultra II Directional" in carried or "PicoPure" in carried
        assert "Illumina NextSeq 500" in carried or "NovaSeq" in carried

    def test_e2_is_given_the_replication_and_the_selection(self, index):
        carried = _text(packet_for("E2.A", index=index)) + _text(packet_for("E2.B", index=index))
        assert "Two biological replicates" in carried
        assert "2 samples per time point" in carried

    def test_m5_is_given_the_code_location_the_paper_states(self, index):
        assert "github.com/programmablebio/granulosa" in _text(packet_for("M5.A", index=index))


class TestCoverageIsHonestAboutWhatWasNotRetrieved:
    def test_the_supplements_this_study_could_not_fetch_leave_absence_findings_unsupported(self, index):
        """The bundle was `too_large` twice, so nothing bioAF says about an omission is established."""
        limitation = [{"needs": "supplements", "reason": "the supplementary bundle was too large to retrieve"}]
        packet = packet_for("M1.B", index=index, limitations=limitation)
        assert packet["coverage"]["sufficient"] is False
        assert packet["passages"], "the evidence in hand still reaches the assessor"

    def test_a_missing_repository_leaves_the_code_comparison_unsupported(self, index):
        packet = packet_for(
            "M5.B", index=index, limitations=[{"needs": "code", "reason": "the repository was never fetched"}]
        )
        assert packet["coverage"]["sufficient"] is False


class TestWhatTheOldSelectorDidWithTheSamePaper:
    def test_the_old_forty_sentence_budget_stopped_before_the_rna_seq_methods(self):
        """The defect, reproduced: the cap fell in the cell-culture methods, and the sequencing and
        computational procedures never reached the assessor."""
        from app.services.validation_passages import method_statements

        kept = " ".join(method_statements(_jats_to_text(_JATS)))
        assert "kallisto" not in kept
        assert "scanpy ingest" not in kept
        assert "log 2 fc >3" not in kept
