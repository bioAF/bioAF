"""plan_8_6 section 11: what an assessment actually cost, recorded rather than estimated.

"Accuracy gates above must pass before a cheaper configuration is accepted; more tokens alone do
not prove better coverage." Neither half of that can be argued from numbers nobody kept.
"""

import pytest

from app.services.validation_assessment import _measured


def _reviewed():
    return {
        "judgments": {
            "M1.B": {"outcome": "verified"},
            "E1.A": {"outcome": "undetermined"},
            "M5.B": {"outcome": "failed"},
        },
        "asked": {"requests": 4, "expansions": 1, "skipped_without_evidence": 2},
        "evidence_chars": 18000,
    }


_EVIDENCE = {
    "retrieval_ledger": [
        {"id": "R1", "outcome": "too_large", "bytes_transferred": 0},
        {"id": "R2", "outcome": "retrieved", "bytes_transferred": 2759815},
    ],
    "code_resolution": {"files": [{"path": "a.py", "size_bytes": 5081}]},
    "code_inspection": {"sources": [{"path": "a.py", "text": "x"}]},
}


class TestWhatIsRecorded:
    def test_the_bytes_moved_for_supplements_and_code(self):
        found = _measured(_EVIDENCE, _reviewed(), seconds=1.5)
        assert found["supplement_bytes"] == 2759815
        assert found["code_bytes"] == 5081

    def test_the_model_calls_including_the_expansions_and_the_skips(self):
        found = _measured(_EVIDENCE, _reviewed(), seconds=1.5)
        assert found["model_requests"] == 4
        assert found["model_expansions"] == 1
        assert found["skipped_without_evidence"] == 2

    def test_the_obligations_judged_and_the_ones_actually_settled(self):
        found = _measured(_EVIDENCE, _reviewed(), seconds=1.5)
        assert found["obligations_judged"] == 3
        assert found["obligations_settled"] == 2

    def test_the_elapsed_time_and_the_evidence_carried(self):
        found = _measured(_EVIDENCE, _reviewed(), seconds=1.5)
        assert found["seconds"] == 1.5
        assert found["evidence_chars"] == 18000

    def test_a_study_that_moved_nothing_records_zeros_rather_than_nothing(self):
        found = _measured({}, None, seconds=0.1)
        assert found["supplement_bytes"] == 0
        assert found["model_requests"] == 0
        assert found["obligations_judged"] == 0


class TestItReachesTheStoredAssessment:
    @pytest.mark.asyncio
    async def test_the_assessment_record_carries_what_the_stage_cost(self, session, admin_user):
        from app.services.validation_assessment import run_assessment
        from app.services.validation_study_service import ValidationStudyService

        study = await ValidationStudyService.create_study(
            session, admin_user.organization_id, admin_user.id, source_doi="10.1/cost", intended_route="deposit"
        )
        study.evidence_json = {}
        await session.flush()
        record = await run_assessment(session, study)
        assert record["measured"]["seconds"] >= 0
        assert "model_requests" in record["measured"]
