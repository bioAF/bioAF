"""plan_8_7 stage 1: the analyses a paper reports are assessed one by one, or not split at all.

The owner's September 21 assessment:

    "Per-analysis allocation exists but production does not use it."

`allocate` has taken a `units` input since plan_8_4 and `build_assessment` never passed one, so every
obligation carried one paper-wide verdict: a defect in the ChIP-seq arm settled the RNA-seq arm and a
script that would not parse settled every other script.

plan_8_7 is explicit that splitting weights is not the repair: "Splitting weights while copying the
same paper-wide verdict into every unit is not implementation." A unit exists only where bioAF holds
separable evidence for it, its outcome is produced from that evidence, and a genuinely paper-wide
obligation keeps its explicit paper-wide scope.
"""

from app.services.validation_analysis_units import (
    MAX_UNITS_PER_OBLIGATION,
    PAPER_WIDE,
    analysis_units,
    unit_of,
)

_RNA = {
    "id": "e1",
    "assay": "bulk RNA-seq",
    "description": "differential expression after induction",
    "tools": ["DESeq2"],
}
_CHIP = {"id": "e2", "assay": "ChIP-seq", "description": "FOXL2 occupancy", "tools": ["MACS2"]}


def _source(path, text="import os\nprint(os.getcwd())\n"):
    return {"path": path, "language": "python", "text": text}


class TestUnitsExistOnlyWhereTheEvidenceSeparates:
    def test_one_experiment_and_one_script_produce_no_units(self):
        found = analysis_units(
            plan={"reported_experiments": [_RNA]}, evidence={"code_inspection": {"sources": [_source("a.py")]}}
        )
        assert found["units"] == {}
        assert found["definitions"] == {}

    def test_two_experiments_split_the_obligations_about_experiments(self):
        found = analysis_units(plan={"reported_experiments": [_RNA, _CHIP]}, evidence={})
        assert found["units"]["M3.B"] == ["exp:e1", "exp:e2"]
        assert found["units"]["E1.A"] == ["exp:e1", "exp:e2"]

    def test_two_scripts_split_the_obligations_about_the_code(self):
        evidence = {"code_inspection": {"sources": [_source("one.py"), _source("two.py", "print(2)\n")]}}
        found = analysis_units(plan={"reported_experiments": [_RNA]}, evidence=evidence)
        assert found["units"]["C1.A"] == ["code:one.py", "code:two.py"]

    def test_an_experiment_split_never_reaches_a_code_obligation(self):
        found = analysis_units(plan={"reported_experiments": [_RNA, _CHIP]}, evidence={})
        assert "C1.A" not in found["units"]

    def test_the_same_file_supplied_twice_creates_no_second_unit(self):
        """plan_8_7: "Duplicate files or repeated mentions create no units or points.\""""
        evidence = {"code_inspection": {"sources": [_source("a.py"), _source("copy/a.py")]}}
        found = analysis_units(plan={}, evidence=evidence)
        assert found["units"] == {}
        assert found["duplicates"] == ["copy/a.py"]

    def test_a_paper_wide_obligation_keeps_its_explicit_scope(self):
        found = analysis_units(plan={"reported_experiments": [_RNA, _CHIP]}, evidence={})
        assert "M5.A" not in found["units"]
        assert found["paper_wide"]["M5.A"] == PAPER_WIDE


class TestTheSplitIsBoundedAndRecorded:
    def test_more_arms_than_the_ceiling_stays_paper_wide_and_says_why(self):
        many = [{**_RNA, "id": f"e{i}", "assay": f"assay {i}"} for i in range(MAX_UNITS_PER_OBLIGATION + 2)]
        found = analysis_units(plan={"reported_experiments": many}, evidence={})
        assert "M3.B" not in found["units"]
        assert found["declined"]["experiment"]

    def test_an_experiment_with_no_identity_holds_its_own_allocation_only(self):
        """Unresolved unit identity holds the affected allocation undetermined; it does not erase the rest."""
        found = analysis_units(plan={"reported_experiments": [_RNA, {"id": "", "assay": ""}]}, evidence={})
        assert found["units"]["M3.B"] == ["exp:e1", "exp:unidentified-2"]
        assert found["unresolved"] == ["exp:unidentified-2"]

    def test_the_revision_moves_when_the_scope_changes(self):
        one = analysis_units(plan={"reported_experiments": [_RNA]}, evidence={})
        two = analysis_units(plan={"reported_experiments": [_RNA, _CHIP]}, evidence={})
        assert one["revision"] != two["revision"]

    def test_the_revision_is_stable_for_the_same_scope(self):
        plan = {"reported_experiments": [_RNA, _CHIP]}
        assert analysis_units(plan=plan, evidence={})["revision"] == analysis_units(plan=plan, evidence={})["revision"]


class TestAUnitCarriesWhatItIsAbout:
    def test_an_experiment_unit_names_its_assay_and_description(self):
        found = analysis_units(plan={"reported_experiments": [_RNA, _CHIP]}, evidence={})
        unit = found["definitions"]["exp:e1"]
        assert unit["kind"] == "experiment"
        assert "bulk RNA-seq" in unit["label"]
        assert "DESeq2" in unit["terms"]

    def test_a_code_unit_names_its_file(self):
        evidence = {"code_inspection": {"sources": [_source("one.py"), _source("two.py", "print(2)\n")]}}
        found = analysis_units(plan={}, evidence=evidence)
        assert found["definitions"]["code:one.py"]["kind"] == "implementation"
        assert found["definitions"]["code:one.py"]["paths"] == ["one.py"]

    def test_the_leaf_id_of_a_unit_is_recoverable(self):
        assert unit_of("C1.A#code:one.py") == ("C1.A", "code:one.py")
        assert unit_of("C1.A") == ("C1.A", None)
