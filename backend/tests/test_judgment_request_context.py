"""plan_8_6 section 6: the question keeps the context that says which evidence answers it.

M1.B reads "Material settings and the relevant quality and filtering decisions are specified". M1 is
a **Computational methods** row about preprocessing. On study 65 the assessor read "material" as
*materials* and returned verified, citing the key resources table, mTeSR Plus on Matrigel and EDTA
passaging. M1.A, asked about the same paper, correctly returned undetermined and said the evidence
was wet-lab steps.

`build_request` sent the obligation text and the criterion identifier and omitted the section name,
the criterion title, the other half of the criterion, and what kind of evidence can answer it. The
title lived in the decision's logging metadata, which never reaches the model.

Every obligation in rubric v3 is read for the same ambiguity, and the context each row carries is
declared beside the row rather than invented per request. The criterion meanings and weights are
rubric v3's and are not changed here.
"""

import pytest

from app.services.validation_judgment import build_request
from app.services.validation_rubric_v3 import CRITERIA, SECTION_TITLES

_PASSAGES = [
    {"id": "m1", "source": "the paper's methods", "text": "Cells were cultured on Matrigel in mTeSR Plus."},
    {"id": "m2", "source": "the paper's methods", "text": "Reads were trimmed with Trim Galore 0.6.7."},
]


class TestTheRequestCarriesItsSectionAndCriterion:
    def test_the_payload_names_the_section_the_obligation_belongs_to(self):
        request = build_request("M1.B", passages=_PASSAGES)
        assert SECTION_TITLES["M"] in request["payload"]

    def test_the_payload_names_the_criterion_title(self):
        request = build_request("M1.B", passages=_PASSAGES)
        assert "Preprocessing" in request["payload"]

    def test_the_payload_carries_the_other_half_of_the_criterion(self):
        """A is context for B: an assessor that cannot see both halves cannot tell them apart."""
        request = build_request("M1.B", passages=_PASSAGES)
        assert "Preprocessing steps and their order are identifiable" in request["payload"]

    def test_the_payload_names_which_half_is_being_asked(self):
        request = build_request("M1.B", passages=_PASSAGES)
        assert "M1.B" in request["payload"] or "M1 B" in request["payload"]


class TestTheRequestSaysWhatEvidenceCanAnswerIt:
    def test_a_computational_row_says_it_is_about_the_analysis_not_the_bench(self):
        payload = build_request("M1.B", passages=_PASSAGES)["payload"].lower()
        assert "preprocessing" in payload
        assert "culture" in payload or "reagent" in payload or "wet-lab" in payload

    def test_an_experimental_row_says_it_is_about_the_bench_not_the_analysis(self):
        payload = build_request("E1.B", passages=_PASSAGES)["payload"].lower()
        assert "wet-lab" in payload or "bench" in payload or "measurement" in payload

    def test_an_independent_records_row_says_the_paper_is_not_the_independent_record(self):
        payload = build_request("S2.B", passages=_PASSAGES)["payload"].lower()
        assert "deposit" in payload or "independent" in payload

    def test_every_judged_obligation_declares_its_scope_and_acceptable_evidence(self):
        from app.services.validation_documentary_review import JUDGED_LEAVES
        from app.services.validation_rubric_v3 import CRITERION_EVIDENCE

        for leaf in JUDGED_LEAVES:
            criterion = leaf.partition(".")[0]
            context = CRITERION_EVIDENCE.get(criterion)
            assert context, f"{criterion} declares no evidence context"
            assert context["scope"].strip(), f"{criterion} declares an empty scope"
            assert context["accepts"], f"{criterion} names no evidence that can answer it"

    def test_every_rubric_criterion_was_read_for_the_same_ambiguity(self):
        """Section 6: every row is read once, not only the one that failed."""
        from app.services.validation_rubric_v3 import CRITERION_EVIDENCE

        assert {c.id for c in CRITERIA} == set(CRITERION_EVIDENCE)


class TestTheContextIsNotAChangeToTheRubric:
    def test_the_obligation_text_is_still_rubric_v3s_own_words(self):
        from app.services.validation_rubric_v3 import CRITERIA_BY_ID

        request = build_request("M1.B", passages=_PASSAGES)
        assert request["obligation"] == CRITERIA_BY_ID["M1"].b

    def test_the_contract_version_moved_so_cached_judgments_are_asked_again(self):
        from app.services.validation_judgment import CONTRACT_VERSION

        assert CONTRACT_VERSION >= 2


class TestTheRequestStillRefusesWhatItAlwaysRefused:
    def test_an_obligation_that_is_not_a_rubric_leaf_is_refused(self):
        from app.services.validation_judgment import JudgmentRefused

        with pytest.raises(JudgmentRefused):
            build_request("M9.Z", passages=_PASSAGES)

    def test_no_evidence_is_still_refused(self):
        from app.services.validation_judgment import JudgmentRefused

        with pytest.raises(JudgmentRefused):
            build_request("M1.B", passages=[])

    def test_it_still_never_asks_for_a_score(self):
        request = build_request("M1.B", passages=_PASSAGES)
        blob = (request["system"] + request["payload"]).lower()
        assert "0-100" not in blob and "0 to 100" not in blob
