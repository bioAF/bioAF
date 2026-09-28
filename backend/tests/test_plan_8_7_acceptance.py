"""plan_8_7 stage 4: a material error blocks acceptance, and three runs have to agree.

    "Use three independent runs of the selected model on the frozen evidence, without cache
    substitution, to expose outcome variability. Every run must satisfy the case's required/forbidden
    material conclusions and allowed uncertainty. One observed material error keeps the affected
    capability and full completion gate open, even if the other two runs succeed. Record and diagnose
    it, correct the implementation/prompt/evidence handling, and repeat the affected three-run
    evaluation plus relevant regression checks. Merely explaining the failure, averaging answers,
    replacing the selected model silently or making all difficult outcomes untested does not close the
    gate. Preserve failed runs as evidence."

The frozen cases live under `tests/data/plan_8_7/`. Each records who established the expectation and
from what source, and each is labelled a control: none of them is a defect found in a published paper.
"""

import pytest

from app.services.validation_acceptance import (
    MATERIAL_ERROR,
    PASSED,
    evaluate_case,
    evaluate_runs,
    frozen_case,
    frozen_cases,
)


def _answer(outcome, rationale, **extra):
    return {"outcome": outcome, "rationale": rationale, **extra}


_CAUGHT = _answer(
    "failed",
    "the methods state a Benjamini-Hochberg adjusted threshold and the script selects on the uncorrected "
    "p value, so which genes are called significant does not follow the stated criterion",
    impact="more genes are reported as significant than the stated criterion selects",
)


class TestTheCorpusShipsWithTheApplication:
    def test_it_is_not_under_the_tests_directory(self):
        """Found by verifying the deployed container: the backend image carries no `tests/`, so a
        corpus path under it resolved to nothing and every gate would have passed on zero cases."""
        from app.services.validation_acceptance import CASES

        assert "tests" not in CASES.parts
        assert CASES.is_dir()

    def test_an_empty_corpus_is_an_error_rather_than_an_empty_result(self, tmp_path, monkeypatch):
        from app.services import validation_acceptance

        validation_acceptance.frozen_cases.cache_clear()
        monkeypatch.setattr(validation_acceptance, "CASES", tmp_path)
        with pytest.raises(FileNotFoundError):
            validation_acceptance.frozen_cases()
        validation_acceptance.frozen_cases.cache_clear()


class TestTheCorpusIsFrozenAndSourceBacked:
    def test_every_case_records_who_established_it_and_from_what(self):
        for case in frozen_cases():
            assert case["established_by"], case["case"]
            assert case["source_basis"], case["case"]

    def test_every_case_is_labelled_a_control(self):
        """ "Label edited fixtures as controls, not defects discovered in the original paper.\""""
        assert all(case["control"] is True for case in frozen_cases())

    def test_every_case_states_what_is_required_and_what_is_forbidden(self):
        for case in frozen_cases():
            assert case["required"] or case["allowed_unresolved"], case["case"]
            assert "forbidden" in case, case["case"]

    def test_the_multiple_comparison_trio_is_present(self):
        names = {case["case"] for case in frozen_cases()}
        assert {"multiple_comparison", "multiple_comparison_corrected", "multiple_comparison_no_code"} <= names


class TestOneRunAgainstOneCase:
    def test_the_expected_conclusion_passes(self):
        found = evaluate_case(frozen_case("multiple_comparison"), _CAUGHT)
        assert found["verdict"] == PASSED

    def test_missing_the_established_defect_is_a_material_error(self):
        found = evaluate_case(
            frozen_case("multiple_comparison"),
            _answer("verified", "the decision criteria are specified and unambiguous"),
        )
        assert found["verdict"] == MATERIAL_ERROR
        assert "establish failed" in found["reason"]

    def test_a_forbidden_conclusion_is_a_material_error_even_with_the_right_outcome(self):
        found = evaluate_case(
            frozen_case("multiple_comparison"),
            _answer(
                "failed",
                "the correction is missing from the methods, so the entire paper is invalid",
                impact="nothing in it can be relied on",
            ),
        )
        assert found["verdict"] == MATERIAL_ERROR
        assert "forbids" in found["reason"]

    def test_the_right_outcome_without_the_required_statement_is_a_material_error(self):
        found = evaluate_case(
            frozen_case("multiple_comparison"), _answer("failed", "something about this analysis is wrong")
        )
        assert found["verdict"] == MATERIAL_ERROR
        assert "requires" in found["reason"]

    def test_the_matched_repair_withdraws_the_criticism(self):
        found = evaluate_case(
            frozen_case("multiple_comparison_corrected"),
            _answer("verified", "the script selects on the adjusted p value the methods state"),
        )
        assert found["verdict"] == PASSED

    def test_keeping_the_criticism_against_the_repair_is_a_material_error(self):
        found = evaluate_case(
            frozen_case("multiple_comparison_corrected"),
            _answer("failed", "the script selects on the uncorrected p value", impact="x"),
        )
        assert found["verdict"] == MATERIAL_ERROR

    def test_with_the_code_inaccessible_untested_is_the_right_answer(self):
        found = evaluate_case(
            frozen_case("multiple_comparison_no_code"),
            _answer("undetermined", "whether the correction is implemented was not tested: bioAF holds no code"),
        )
        assert found["verdict"] == PASSED

    def test_inferring_compliance_from_the_methods_alone_is_a_material_error(self):
        found = evaluate_case(
            frozen_case("multiple_comparison_no_code"),
            _answer("verified", "the correction was applied as the methods state"),
        )
        assert found["verdict"] == MATERIAL_ERROR

    def test_a_demonstrated_defect_in_code_bioaf_does_not_hold_is_a_material_error(self):
        found = evaluate_case(
            frozen_case("multiple_comparison_no_code"),
            _answer("failed", "the analysis code does not apply the correction", impact="x"),
        )
        assert found["verdict"] == MATERIAL_ERROR


class TestThreeRunsAndWhatOneErrorDoes:
    def test_three_clean_runs_accept_the_case(self):
        found = evaluate_runs(frozen_case("multiple_comparison"), [_CAUGHT, _CAUGHT, _CAUGHT])
        assert found["accepted"] is True
        assert found["runs"] == 3

    def test_two_of_three_does_not_accept_it(self):
        missed = _answer("verified", "the decision criteria are specified and unambiguous")
        found = evaluate_runs(frozen_case("multiple_comparison"), [_CAUGHT, _CAUGHT, missed])
        assert found["accepted"] is False
        assert found["material_errors"] == 1

    def test_the_failed_run_is_preserved_as_evidence_rather_than_averaged_away(self):
        missed = _answer("verified", "the decision criteria are specified and unambiguous")
        found = evaluate_runs(frozen_case("multiple_comparison"), [_CAUGHT, missed, _CAUGHT])
        assert [row["verdict"] for row in found["evaluations"]] == [PASSED, MATERIAL_ERROR, PASSED]
        assert found["evaluations"][1]["answer"]["rationale"]

    def test_fewer_than_three_runs_is_not_an_evaluation(self):
        found = evaluate_runs(frozen_case("multiple_comparison"), [_CAUGHT, _CAUGHT])
        assert found["accepted"] is False
        assert "three" in found["reason"]

    def test_making_every_difficult_outcome_untested_does_not_close_the_gate(self):
        grey = _answer("undetermined", "the evidence supplied does not settle it")
        found = evaluate_runs(frozen_case("multiple_comparison"), [grey, grey, grey])
        assert found["accepted"] is False
        assert found["material_errors"] == 3


class TestCrossModelBehaviourIsNotAssumed:
    def test_one_model_accepted_reports_the_others_as_unverified(self):
        found = evaluate_runs(frozen_case("multiple_comparison"), [_CAUGHT, _CAUGHT, _CAUGHT], model="model-a")
        assert found["model"] == "model-a"
        assert found["cross_model"] == "unverified"

    def test_two_models_each_need_their_own_three_runs(self):
        from app.services.validation_acceptance import accepted_models

        runs = {
            "model-a": evaluate_runs(frozen_case("multiple_comparison"), [_CAUGHT] * 3, model="model-a"),
            "model-b": evaluate_runs(
                frozen_case("multiple_comparison"),
                [_CAUGHT, _CAUGHT, _answer("verified", "the criteria are specified")],
                model="model-b",
            ),
        }
        assert accepted_models(runs) == ["model-a"]


@pytest.mark.parametrize(
    "name", ["multiple_comparison", "multiple_comparison_corrected", "multiple_comparison_no_code"]
)
def test_each_frozen_case_carries_the_sources_its_expectation_rests_on(name):
    case = frozen_case(name)
    assert case["sources"].get("methods")
    assert case["expected_outcome"] in ("verified", "failed", "undetermined")
