"""plan_8_7 section 3: published code creates required follow-up work, per implementation.

    "For each relevant published implementation, record its immutable source revision, claimed
    analysis and inputs, and the attempt or specific reason it cannot yet be attempted. When
    compatible data, runtime and authorization exist, the normal Validate workflow schedules an
    attempt using the authors' code and compares its outputs with the paper... If full reproduction is
    blocked, attempt a supported bounded build/load and representative invocation of the actual author
    entry point or relevant functions where authorization and inputs permit... If even this check is
    blocked, name the missing access, runtime, capability, input or authorization and what would
    resolve it."

"A user must not need to discover an advanced toggle to trigger work already covered by their
authorization." That is the other half: the follow-up is produced by the assessment, not by a control
somebody has to find.
"""

import pytest

from app.services.validation_code_followup import (
    ATTEMPT_BOUNDED,
    ATTEMPT_REPRODUCTION,
    BLOCKED,
    NEEDS_AUTHORIZATION,
    code_followup,
)

_PY = {"path": "analysis.py", "language": "python", "text": "import numpy\nprint(numpy.mean([1]))\n"}
_R = {"path": "deg.R", "language": "r", "text": "library(DESeq2)\nprint(1)\n"}
_JULIA = {"path": "sim.jl", "language": "julia", "text": "println(1)"}


def _evidence(sources=(_PY,), **kw):
    base = {
        "code_inspection": {
            "sources": list(sources),
            "manifests": [{"path": "requirements.txt", "text": "numpy==1.26.4\n"}],
        },
        "code_resolution": {"outcome": "resolved", "url": "https://github.com/x/y", "commit_sha": "abc123"},
        "capabilities": {"preprocessed_data": {"value": "yes"}},
    }
    return {**base, **kw}


class TestOnePieceOfFollowUpPerImplementation:
    def test_each_supplied_implementation_gets_its_own_row(self):
        found = code_followup(evidence=_evidence(sources=[_PY, _R]), plan={}, route="deposit")
        assert [row["unit"] for row in found["followups"]] == ["code:analysis.py", "code:deg.R"]

    def test_each_row_names_the_immutable_revision_it_is_about(self):
        row = code_followup(evidence=_evidence(), plan={}, route="deposit")["followups"][0]
        assert row["source"]["commit_sha"] == "abc123"
        assert row["source"]["repo_url"] == "https://github.com/x/y"
        assert row["source"]["digest"]

    def test_each_row_names_the_analysis_it_claims_and_the_inputs_it_would_need(self):
        plan = {
            "reported_experiments": [{"id": "e1", "assay": "bulk RNA-seq", "description": "differential expression"}]
        }
        row = code_followup(evidence=_evidence(), plan=plan, route="deposit")["followups"][0]
        assert "not yet established" in row["claimed_analysis"]
        assert row["inputs"]["processed_available"] is False

    def test_a_paper_that_published_no_code_has_no_follow_up_and_says_so(self):
        found = code_followup(evidence={"code_resolution": {"outcome": "code_absent"}}, plan={}, route="deposit")
        assert found["followups"] == []
        assert "no source code" in found["reason"].lower()


class TestWhatTheFollowUpAsksFor:
    def test_paper_wide_availability_without_a_binding_requests_a_bounded_invocation(self):
        row = code_followup(evidence=_evidence(), plan={}, route="deposit")["followups"][0]
        assert row["action"] == ATTEMPT_BOUNDED

    def test_no_compatible_inputs_still_attempts_the_bounded_run(self):
        """ "If full reproduction is blocked, attempt a supported bounded build/load and representative
        invocation of the actual author entry point.\""""
        evidence = _evidence(capabilities={"preprocessed_data": {"value": "no"}})
        row = code_followup(evidence=evidence, plan={}, route="deposit")["followups"][0]
        assert row["action"] == ATTEMPT_BOUNDED
        assert "no pre-processed" in row["reason"] or "processed" in row["reason"]

    def test_an_unsupported_runtime_is_blocked_and_names_the_capability(self):
        row = code_followup(evidence=_evidence(sources=[_JULIA]), plan={}, route="deposit")["followups"][0]
        assert row["action"] == BLOCKED
        assert "julia" in row["reason"].lower()
        assert row["missing"] == "runtime"

    def test_an_assessment_only_study_names_the_authorization_rather_than_spending(self):
        """An assessment authorizes no execution, which is what makes it the default. The follow-up is
        still recorded, with what would authorize it."""
        row = code_followup(evidence=_evidence(), plan={}, route="assessment")["followups"][0]
        assert row["action"] == NEEDS_AUTHORIZATION
        assert row["missing"] == "authorization"
        assert "reproduce" in row["next_action"].lower()

    def test_generated_code_is_never_the_authors_implementation(self):
        evidence = _evidence(sources=[{**_PY, "generated": True}])
        found = code_followup(evidence=evidence, plan={}, route="deposit")
        assert found["followups"] == []
        assert "generated" in found["reason"].lower()


class TestOneScriptDoesNotSpeakForTheOthers:
    def test_a_supported_and_an_unsupported_implementation_reach_different_actions(self):
        found = code_followup(evidence=_evidence(sources=[_PY, _JULIA]), plan={}, route="deposit")
        actions = {row["unit"]: row["action"] for row in found["followups"]}
        assert actions["code:analysis.py"] == ATTEMPT_BOUNDED
        assert actions["code:sim.jl"] == BLOCKED

    def test_a_completed_attempt_on_one_unit_leaves_the_other_outstanding(self):
        evidence = _evidence(sources=[_PY, _R])
        evidence["code_inspection"]["execution_by_unit"] = {
            "code:analysis.py": {"invocation": {"status": "succeeded", "invoked": True}}
        }
        found = code_followup(evidence=evidence, plan={}, route="deposit")
        done = {row["unit"]: row["attempted"] for row in found["followups"]}
        assert done["code:analysis.py"] is True
        assert done["code:deg.R"] is False


@pytest.mark.parametrize("route", ["deposit", "pipeline", "both"])
def test_every_executing_route_covers_the_attempt_without_another_question(route):
    row = code_followup(evidence=_evidence(), plan={}, route=route)["followups"][0]
    assert row["action"] in (ATTEMPT_REPRODUCTION, ATTEMPT_BOUNDED)
