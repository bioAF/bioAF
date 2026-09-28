"""plan_8_7 stage 3: the four controls, so a real failure is told apart from bioAF's own limitation.

    "Also exercise a controlled author-code defect, a matched repair and a blocked-input case. Label
    edited fixtures as controls, not defects discovered in the original paper. Prove a meaningful
    partial invocation when full reproduction cannot run, and show exactly what remains untested."

The fixtures here are CONTROLS. `defective.py` and `repaired.py` are written for this test; neither is
a defect anybody found in a published paper, and the plan is explicit that labelling them otherwise is
itself a failure.
"""

from app.services.validation_code_followup import ATTEMPT_BOUNDED, BLOCKED, code_followup
from app.services.validation_environment_check import (
    INSTALL_MARKER,
    INTERFACE_MARKER,
    LOAD_MARKER,
    RESOLVE_MARKER,
    outcome_from_run,
)

# A control: it imports a module its own manifest does not declare, so the declared environment
# cannot load it. Not a paper's defect; a fixture written to have one.
_DEFECTIVE = {
    "path": "defective.py",
    "language": "python",
    "text": "import numpy\nimport tensorflow\n\nprint(tensorflow.__version__, numpy.mean([1]))\n",
}
# The matched repair: the same analysis, declaring what it uses.
_REPAIRED = {
    "path": "repaired.py",
    "language": "python",
    "text": "import numpy\n\nprint(numpy.mean([1]))\n",
}
_MANIFEST = {"path": "requirements.txt", "text": "numpy==1.26.4\n"}


def _evidence(sources, processed="yes"):
    return {
        "code_inspection": {"sources": list(sources), "manifests": [_MANIFEST]},
        "code_resolution": {"outcome": "resolved", "url": "https://example.test/control", "commit_sha": "c0ffee"},
        "capabilities": {"preprocessed_data": {"value": processed}},
    }


class TestTheControlsAreLabelledAsControls:
    def test_the_fixtures_are_named_for_what_they_are(self):
        assert "defective" in _DEFECTIVE["path"] and "repaired" in _REPAIRED["path"]

    def test_the_defect_and_its_repair_are_separate_implementations(self):
        found = code_followup(evidence=_evidence([_DEFECTIVE, _REPAIRED]), plan={}, route="deposit")
        assert sorted(row["unit"] for row in found["followups"]) == ["code:defective.py", "code:repaired.py"]
        assert len({row["source"]["digest"] for row in found["followups"]}) == 2


class TestADefectiveControlFailsAndItsRepairDoesNot:
    def test_the_defective_control_fails_its_load_in_the_built_environment(self):
        found = outcome_from_run(
            exit_code=1,
            transcript="\n".join(
                [
                    f"{INSTALL_MARKER} requirements.txt ok",
                    f"{LOAD_MARKER} numpy ok",
                    f"{LOAD_MARKER} tensorflow failed: No module named 'tensorflow'",
                    f"{RESOLVE_MARKER} failed: tensorflow",
                ]
            ),
            environment="python:3.12-slim",
            ref="control-defective",
        )
        assert found["load"]["status"] == "failed"
        assert "tensorflow" in found["load"]["reason"]
        assert found["dependency_resolution"]["status"] == "failed"

    def test_the_matched_repair_establishes_both_obligations(self):
        found = outcome_from_run(
            exit_code=0,
            transcript="\n".join(
                [
                    f"{INSTALL_MARKER} requirements.txt ok",
                    f"{LOAD_MARKER} numpy ok",
                    f"{INTERFACE_MARKER} numpy.mean ok",
                    f"{RESOLVE_MARKER} ok",
                ]
            ),
            environment="python:3.12-slim",
            ref="control-repaired",
        )
        assert found["load"]["status"] == "succeeded"
        assert found["dependency_resolution"]["status"] == "succeeded"

    def test_the_repair_is_a_different_source_revision_and_says_so(self):
        rows = {
            row["unit"]: row
            for row in code_followup(evidence=_evidence([_DEFECTIVE, _REPAIRED]), plan={}, route="deposit")["followups"]
        }
        assert rows["code:defective.py"]["source"]["digest"] != rows["code:repaired.py"]["source"]["digest"]


class TestAPartiallyRunnableControlEstablishesOnlyItsTestedScope:
    def test_a_load_that_worked_with_an_interface_that_did_not_is_partial(self):
        found = outcome_from_run(
            exit_code=1,
            transcript="\n".join(
                [
                    f"{INSTALL_MARKER} requirements.txt ok",
                    f"{LOAD_MARKER} numpy ok",
                    f"{INTERFACE_MARKER} numpy.nonexistent missing",
                    f"{RESOLVE_MARKER} ok",
                ]
            ),
            environment="python:3.12-slim",
            ref="control-partial",
        )
        assert found["load"]["status"] == "succeeded", "the tested scope"
        assert found["interfaces"]["status"] == "missing", "and what it did not establish"
        assert found["dependency_resolution"]["status"] == "failed"

    def test_an_interface_nobody_reached_leaves_that_scope_open(self):
        found = outcome_from_run(
            exit_code=1,
            transcript="\n".join(
                [
                    f"{INSTALL_MARKER} requirements.txt ok",
                    f"{LOAD_MARKER} tensorflow failed: No module named 'tensorflow'",
                    f"{INTERFACE_MARKER} tensorflow.keras unchecked",
                ]
            ),
            environment="python:3.12-slim",
            ref="control-partial-2",
        )
        assert found["interfaces"]["status"] == "unchecked"
        assert "not loaded" in found["interfaces"]["reason"]


class TestABlockedInputControlIsNotAFailure:
    def test_no_processed_data_asks_for_the_bounded_check_rather_than_failing(self):
        row = code_followup(evidence=_evidence([_REPAIRED], processed="no"), plan={}, route="deposit")["followups"][0]
        assert row["action"] == ATTEMPT_BOUNDED
        assert row["missing"] == "input"

    def test_an_unsupported_runtime_is_bioafs_limitation_and_names_it(self):
        julia = {"path": "control.jl", "language": "julia", "text": "println(1)"}
        row = code_followup(evidence=_evidence([julia]), plan={}, route="deposit")["followups"][0]
        assert row["action"] == BLOCKED
        assert row["missing"] == "runtime"
        assert "bioAF supplies no runtime" in row["reason"]
