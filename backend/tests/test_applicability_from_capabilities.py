"""plan_8_3 section 1.3: what an experiment is SUPPORTED by, stated per experiment and per check.

Study 48's report called all five of its experiment types supported although its available assays
establish no eligible score-producing path under bioAF's current methods. Two things did it: a claim
whose check was unresolved counted as eligible whatever left it unresolved, so a binding that failed
promoted its assay to supported; and a workflow mapped from a word that merely describes an assay's
family counted the same as one the paper named outright.

The states are distinct now, because they have different remedies:

- ``supported``: a workflow the paper's own assay names, or a claim with a check bioAF can make;
- ``awaiting_input``: supported, and waiting on something the run itself establishes;
- ``unresolved_interpretation``: the only evidence for its workflow describes a family, not an assay;
- ``failed_decision``: its checks are open only because a model decision failed, which is bioAF's
  failure and never support;
- ``unsupported``: none of those.
"""

import pytest

from app.services.validation_applicability import (
    AWAITING_INPUT,
    FAILED_DECISION,
    NOT_APPLICABLE,
    PARTIAL,
    SUPPORTED,
    UNRESOLVED_INTERPRETATION,
    UNSUPPORTED,
    applicability,
)


def _plan(experiments, blockers=()):
    return {"reported_experiments": experiments, "blockers": list(blockers)}


def _target(experiment_id, checks):
    return {"reported_experiment_id": experiment_id, "checks": checks}


def _found(plan, targets):
    return applicability(plan, targets)


def _support(found, experiment_id):
    return next(r["support"] for r in found["experiments"] if r["id"] == experiment_id)


class TestAFailedDecisionIsNeverSupport:
    def test_a_claim_whose_binding_failed_does_not_make_its_assay_supported(self):
        found = _found(
            _plan([{"id": "e1", "assay": "atomic force microscopy", "workflow": None}]),
            [
                _target(
                    "e1",
                    {
                        "processed_reanalysis": {
                            "status": "unresolved",
                            "requirement": "binding",
                            "reason": "the binding call did not return a readable decision for this claim",
                        }
                    },
                )
            ],
        )
        assert _support(found, "e1") == FAILED_DECISION
        assert found["status"] == NOT_APPLICABLE

    def test_a_claim_waiting_on_the_sample_mapping_is_supported_and_awaiting_input(self):
        found = _found(
            _plan([{"id": "e1", "assay": "bulk RNA-seq", "workflow": "nf-core/rnaseq"}]),
            [_target("e1", {"processed_reanalysis": {"status": "unresolved", "requirement": "sample_mapping"}})],
        )
        assert _support(found, "e1") == AWAITING_INPUT
        assert found["status"] == "applicable"

    def test_a_claim_with_an_available_check_is_supported(self):
        found = _found(
            _plan([{"id": "e1", "assay": "bulk RNA-seq", "workflow": "nf-core/rnaseq"}]),
            [_target("e1", {"author_results": {"status": "available"}})],
        )
        assert _support(found, "e1") == SUPPORTED


class TestAWorkflowIsNotSupportOnItsOwn:
    def test_an_assay_the_paper_names_outright_is_supported(self):
        found = _found(_plan([{"id": "e1", "assay": "ChIP-seq", "workflow": "nf-core/chipseq"}]), [])
        assert _support(found, "e1") == SUPPORTED

    def test_an_assay_matched_only_by_a_word_describing_its_family_is_unresolved_not_supported(self):
        """"RNA-seq" names a family; nf-core/rnaseq is its answer of last resort, not an established
        measurement type and analysis contract."""
        found = _found(_plan([{"id": "e1", "assay": "RNA-seq", "workflow": "nf-core/rnaseq"}]), [])
        assert _support(found, "e1") == UNRESOLVED_INTERPRETATION

    def test_a_declared_library_strategy_settles_a_contextual_match(self):
        found = _found(
            _plan([{"id": "e1", "assay": "RNA-seq", "workflow": "nf-core/rnaseq", "library_strategy": "RNA-Seq"}]),
            [],
        )
        assert _support(found, "e1") == SUPPORTED

    def test_a_strategy_nobody_has_reasoned_about_settles_nothing(self):
        """plan_8_4 section 6.1: ANY library-strategy string counted as proof of the measurement type.
        `pipeline_mapper`'s own rule is that an undeclared strategy has NO OPINION, and this bypassed
        it: a depositor's free text promoted a contextual match to an established contract."""
        found = _found(
            _plan(
                [
                    {
                        "id": "e1",
                        "assay": "RNA-seq",
                        "workflow": "nf-core/rnaseq",
                        "library_strategy": "OTHER",
                    }
                ]
            ),
            [],
        )
        assert _support(found, "e1") == UNRESOLVED_INTERPRETATION

    def test_a_declared_strategy_the_workflow_does_not_consume_settles_nothing_either(self):
        """A deposit that declares itself Bisulfite-Seq does not establish an RNA-seq contract. It
        contradicts one, and the deposit guard is what refuses it: this must not read as support."""
        found = _found(
            _plan(
                [
                    {
                        "id": "e1",
                        "assay": "RNA-seq",
                        "workflow": "nf-core/rnaseq",
                        "library_strategy": "Bisulfite-Seq",
                    }
                ]
            ),
            [],
        )
        assert _support(found, "e1") != SUPPORTED

    def test_a_claim_bioaf_can_check_settles_it_too(self):
        found = _found(
            _plan([{"id": "e1", "assay": "RNA-seq", "workflow": "nf-core/rnaseq"}]),
            [_target("e1", {"author_results": {"status": "available"}})],
        )
        assert _support(found, "e1") == SUPPORTED

    def test_an_assay_with_no_workflow_and_no_eligible_claim_is_unsupported(self):
        found = _found(_plan([{"id": "e1", "assay": "western blot", "workflow": None}]), [])
        assert _support(found, "e1") == UNSUPPORTED


class TestTheWholePaper:
    def test_five_experiments_none_of_which_bioaf_can_check_is_not_applicable(self):
        experiments = [
            {"id": "e1", "assay": "atomic force microscopy", "workflow": None},
            {"id": "e2", "assay": "immunofluorescence", "workflow": None},
            {"id": "e3", "assay": "western blot", "workflow": None},
            {"id": "e4", "assay": "qRT-PCR", "workflow": None},
            {"id": "e5", "assay": "traction force microscopy", "workflow": None},
        ]
        found = _found(_plan(experiments), [])
        assert found["status"] == NOT_APPLICABLE
        assert all(r["support"] == UNSUPPORTED for r in found["experiments"])
        assert "atomic force microscopy" in found["limitation"]
        assert "does not establish" in found["statement"]

    def test_a_mixed_paper_keeps_its_eligible_experiment_and_names_the_rest(self):
        found = _found(
            _plan(
                [
                    {"id": "e1", "assay": "bulk RNA-seq", "workflow": "nf-core/rnaseq"},
                    {"id": "e2", "assay": "western blot", "workflow": None},
                ]
            ),
            [_target("e1", {"processed_reanalysis": {"status": "available"}})],
        )
        assert found["status"] == PARTIAL
        assert "western blot" in found["limitation"]
        assert "bulk RNA-seq" not in found["limitation"]

    def test_every_experiment_carries_the_evidence_for_its_support_label(self):
        found = _found(
            _plan(
                [
                    {"id": "e1", "assay": "bulk RNA-seq", "workflow": "nf-core/rnaseq"},
                    {"id": "e2", "assay": "western blot", "workflow": None},
                ]
            ),
            [_target("e1", {"processed_reanalysis": {"status": "available"}})],
        )
        for row in found["experiments"]:
            assert row["support"] in (
                SUPPORTED,
                AWAITING_INPUT,
                UNRESOLVED_INTERPRETATION,
                FAILED_DECISION,
                UNSUPPORTED,
            )
            assert row["support_reason"]


class TestSequencingRequirementsAreSuppressedWhereTheyDoNotApply:
    @pytest.mark.parametrize("support", [UNSUPPORTED, FAILED_DECISION])
    def test_a_paper_outside_the_methods_states_its_limit_without_claiming_no_data_exists(self, support):
        found = _found(_plan([{"id": "e1", "assay": "western blot", "workflow": None}]), [])
        assert found["status"] == NOT_APPLICABLE
        assert "no quantitative analysis" in found["statement"]
        assert "no artifact" not in found["statement"]
