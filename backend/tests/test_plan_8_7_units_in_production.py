"""plan_8_7 stage 1: the per-unit allocation reaches production, with per-unit outcomes behind it.

"Per-analysis allocation exists but production does not use it" was the owner's finding. The repair
has two halves and both are asserted here: `build_assessment` allocates over the units it established,
AND each unit's outcome is produced from that unit's own evidence. A paper-wide verdict copied into
every unit would satisfy the first half and none of the point.
"""

import pytest

from app.services.validation_documentary_review import packets_for
from app.services.validation_judgment import build_request
from app.services.validation_rubric_assessment import build_assessment
from app.services.validation_rubric_v3 import FAILED, UNDETERMINED, VERIFIED

_RNA = {"id": "e1", "assay": "bulk RNA-seq", "description": "differential expression", "tools": ["DESeq2"]}
_CHIP = {"id": "e2", "assay": "ChIP-seq", "description": "FOXL2 occupancy", "tools": ["MACS2"]}

_BROKEN = {"path": "broken.py", "language": "python", "text": "def f(:\n"}
_FINE = {"path": "fine.py", "language": "python", "text": "import os\n\n\nif __name__ == '__main__':\n    print(1)\n"}


class TestTheAllocationIsOverTheUnits:
    def test_two_experiments_give_each_arm_its_own_leaf_and_half_the_weight(self):
        found = build_assessment(plan={"reported_experiments": [_RNA, _CHIP]}, evidence={}, claims=[], inventory=None)
        ids = [leaf["id"] for leaf in found["leaves"]]
        assert "M3.B#exp:e1" in ids and "M3.B#exp:e2" in ids
        weights = {leaf["id"]: leaf["weight"] for leaf in found["leaves"]}
        assert weights["M3.B#exp:e1"] == weights["M3.B#exp:e2"]

    def test_one_experiment_keeps_the_paper_wide_leaf(self):
        found = build_assessment(plan={"reported_experiments": [_RNA]}, evidence={}, claims=[], inventory=None)
        assert "M3.B" in [leaf["id"] for leaf in found["leaves"]]

    def test_the_scope_travels_with_the_assessment(self):
        found = build_assessment(plan={"reported_experiments": [_RNA, _CHIP]}, evidence={}, claims=[], inventory=None)
        assert found["units"]["definitions"]["exp:e1"]["assay"] == "bulk RNA-seq"
        assert found["units"]["revision"]

    def test_a_scope_change_invalidates_the_held_assessment(self):
        from app.services.validation_rubric_assessment import assessment_inputs, reusable

        one = build_assessment(plan={"reported_experiments": [_RNA]}, evidence={}, claims=[], inventory=None)
        inputs = assessment_inputs(plan={"reported_experiments": [_RNA, _CHIP]}, evidence={}, claims=[], inventory=None)
        assert reusable(one, inputs) is False


class TestEachUnitsOutcomeComesFromItsOwnEvidence:
    def test_one_script_that_will_not_parse_does_not_settle_the_other(self):
        evidence = {"code_inspection": {"sources": [_BROKEN, _FINE]}}
        found = build_assessment(plan={}, evidence=evidence, claims=[], inventory=None)
        outcomes = found["outcomes"]
        assert outcomes["C1.A#code:broken.py"]["outcome"] == FAILED
        assert outcomes["C1.A#code:fine.py"]["outcome"] == VERIFIED

    def test_the_failing_unit_names_its_own_file(self):
        evidence = {"code_inspection": {"sources": [_BROKEN, _FINE]}}
        found = build_assessment(plan={}, evidence=evidence, claims=[], inventory=None)
        assert "broken.py" in found["outcomes"]["C1.A#code:broken.py"]["scope"]
        assert "broken.py" not in found["outcomes"]["C1.A#code:fine.py"]["scope"]

    def test_a_single_script_is_still_assessed_paper_wide(self):
        found = build_assessment(
            plan={}, evidence={"code_inspection": {"sources": [_BROKEN]}}, claims=[], inventory=None
        )
        assert found["outcomes"]["C1.A"]["outcome"] == FAILED

    def test_the_score_counts_the_units_separately(self):
        from app.services.validation_rubric_assessment import card_from

        evidence = {"code_inspection": {"sources": [_BROKEN, _FINE]}}
        card = card_from(build_assessment(plan={}, evidence=evidence, claims=[], inventory=None))
        section = next(row for row in card["sections"] if row["section"] == "C")
        assert section["verified"] > 0 and section["failed"] > 0


class TestEachUnitIsAskedAboutItself:
    def test_a_unit_leaf_carries_its_own_packet(self):
        evidence = {
            "paper_index": {
                "passages": [
                    {"id": "p1", "kind": "methods", "section": "Methods", "text": "DESeq2 was used with a Wald test."},
                    {
                        "id": "p2",
                        "kind": "methods",
                        "section": "Methods",
                        "text": "MACS2 called peaks with default settings.",
                    },
                ]
            }
        }
        units = {"exp:e1": {"terms": ["bulk RNA-seq", "DESeq2"], "label": "bulk RNA-seq"}}
        packets = packets_for(
            evidence=evidence,
            plan={},
            leaves=("M3.B#exp:e1",),
            units=units,
            budget_chars=len("DESeq2 was used with a Wald test.") + 5,
        )
        assert [p["id"] for p in packets["M3.B#exp:e1"]["passages"]] == ["p1"]

    def test_the_request_tells_the_assessor_which_unit_it_is_about(self):
        request = build_request(
            "M3.B#exp:e1",
            passages=[{"id": "p1", "text": "DESeq2 was used.", "source": "the paper"}],
            unit={"label": "bulk RNA-seq - differential expression"},
        )
        assert "bulk RNA-seq - differential expression" in request["payload"]
        assert request["leaf"] == "M3.B#exp:e1"
        assert request["unit"] == "bulk RNA-seq - differential expression"

    def test_a_code_unit_is_shown_only_its_own_source(self):
        evidence = {
            "paper_index": {
                "passages": [{"id": "p1", "kind": "methods", "section": "Methods", "text": "Counts were filtered."}]
            },
            "code_inspection": {"sources": [_BROKEN, _FINE]},
        }
        from app.services.validation_analysis_units import analysis_units

        units = analysis_units(plan={}, evidence=evidence)["definitions"]
        packet = packets_for(evidence=evidence, plan={}, leaves=("C4.A#code:fine.py",), units=units)[
            "C4.A#code:fine.py"
        ]
        ids = [str(p["id"]) for p in packet["passages"]]
        assert any(i.startswith("code:fine.py") for i in ids)
        assert not any(i.startswith("code:broken.py") for i in ids)

    def test_a_code_unit_is_still_shown_the_shared_manifest(self):
        """A manifest is not another unit's implementation: it is the environment every unit ran in."""
        from app.services.validation_analysis_units import analysis_units

        evidence = {
            "paper_index": {
                "passages": [{"id": "p1", "kind": "methods", "section": "Methods", "text": "Counts were filtered."}]
            },
            "code_inspection": {
                "sources": [_BROKEN, _FINE],
                "manifests": [{"path": "requirements.txt", "text": "numpy==1.26.4\n"}],
            },
        }
        units = analysis_units(plan={}, evidence=evidence)["definitions"]
        packet = packets_for(evidence=evidence, plan={}, leaves=("C3.B#code:fine.py",), units=units)[
            "C3.B#code:fine.py"
        ]
        ids = [str(p["id"]) for p in packet["passages"]]
        assert any(i.startswith("code:requirements.txt") for i in ids)


class TestAnUnidentifiedArmHoldsOnlyItsOwnAllocation:
    def test_it_is_undetermined_and_the_identified_arm_is_not(self):
        plan = {"reported_experiments": [_RNA, {"id": "", "assay": ""}]}
        found = build_assessment(plan=plan, evidence={}, claims=[], inventory=None)
        ids = [leaf["id"] for leaf in found["leaves"]]
        assert "M3.B#exp:unidentified-2" in ids
        assert found["outcomes"]["M3.B#exp:unidentified-2"]["outcome"] == UNDETERMINED
        assert found["outcomes"]["M3.B#exp:unidentified-2"]["next_action"]


@pytest.mark.parametrize("leaf", ["M5.A", "M5.B"])
def test_a_paper_wide_obligation_is_never_split(leaf):
    found = build_assessment(plan={"reported_experiments": [_RNA, _CHIP]}, evidence={}, claims=[], inventory=None)
    ids = [row["id"] for row in found["leaves"]]
    assert leaf in ids
    assert not any(row.startswith(f"{leaf}#") for row in ids)
