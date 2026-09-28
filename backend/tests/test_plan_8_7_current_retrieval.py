"""plan_8_7 stage 2: a resource bioAF has now is not a resource it could not get.

The owner's September 21 assessment found a stale retrieval failure on the report beside the resource
that had, in a later attempt, been retrieved:

    "Project current retrieval success separately from historical failures."

The ledger is append-only and right to be. What was wrong is reading a failed entry as the current
state of a source that a later entry retrieved. Both facts are kept: the current state leads, and the
attempts that failed are history a reader can open.
"""

from app.services.validation_report_summary import current_retrieval


def _ledger(*entries):
    return [{"id": f"R{i}", **e} for i, e in enumerate(entries, start=1)]


class TestTheCurrentStateLeads:
    def test_a_source_retrieved_after_a_failure_is_not_a_current_failure(self):
        found = current_retrieval(
            {
                "retrieval_ledger": _ledger(
                    {"url": "https://x/supp.zip", "outcome": "too_large", "at": "2026-09-01T00:00:00Z"},
                    {"url": "https://x/supp.zip", "outcome": "retrieved", "at": "2026-09-02T00:00:00Z"},
                )
            }
        )
        assert found["failures"] == []
        assert found["retrieved"] == ["https://x/supp.zip"]
        assert found["historical_failures"] == 1

    def test_a_source_that_never_arrived_is_still_a_current_failure(self):
        found = current_retrieval(
            {
                "retrieval_ledger": _ledger(
                    {"url": "https://x/supp.zip", "outcome": "too_large", "at": "2026-09-01T00:00:00Z"},
                    {"url": "https://x/supp.zip", "outcome": "too_large", "at": "2026-09-02T00:00:00Z"},
                )
            }
        )
        assert [f["url"] for f in found["failures"]] == ["https://x/supp.zip"]
        assert found["failures"][0]["attempts"] == 2

    def test_two_different_sources_keep_their_own_current_state(self):
        found = current_retrieval(
            {
                "retrieval_ledger": _ledger(
                    {"url": "https://x/a.zip", "outcome": "retrieved", "at": "2026-09-01T00:00:00Z"},
                    {"url": "https://x/b.zip", "outcome": "not_found", "at": "2026-09-01T00:00:00Z"},
                )
            }
        )
        assert found["retrieved"] == ["https://x/a.zip"]
        assert [f["url"] for f in found["failures"]] == ["https://x/b.zip"]

    def test_the_history_is_kept_rather_than_discarded(self):
        found = current_retrieval(
            {
                "retrieval_ledger": _ledger(
                    {"url": "https://x/a.zip", "outcome": "too_large", "at": "2026-09-01T00:00:00Z"},
                    {"url": "https://x/a.zip", "outcome": "retrieved", "at": "2026-09-02T00:00:00Z"},
                )
            }
        )
        assert found["history"][0]["outcome"] == "too_large"

    def test_no_ledger_at_all_reports_nothing_either_way(self):
        found = current_retrieval({})
        assert found["failures"] == [] and found["retrieved"] == [] and found["historical_failures"] == 0


class TestTheNextActionAddressesTheRecordedCause:
    def test_a_resolved_failure_recommends_nothing(self):
        found = current_retrieval(
            {
                "retrieval_ledger": _ledger(
                    {"url": "https://x/a.zip", "outcome": "too_large", "at": "2026-09-01T00:00:00Z"},
                    {"url": "https://x/a.zip", "outcome": "retrieved", "at": "2026-09-02T00:00:00Z"},
                )
            }
        )
        assert found["next_actions"] == []

    def test_an_unauthorized_source_asks_for_authorization_not_a_retry(self):
        found = current_retrieval(
            {
                "retrieval_ledger": _ledger(
                    {"url": "https://ega/x", "outcome": "unauthorized", "at": "2026-09-01T00:00:00Z"}
                )
            }
        )
        assert any(
            "authoris" in a["action"] or "authoriz" in a["action"] or "credential" in a["action"]
            for a in found["next_actions"]
        )

    def test_a_source_that_does_not_exist_is_not_a_credentials_problem(self):
        """plan_8_7 section 6: "credentials cannot supply a missing assay implementation.\""""
        found = current_retrieval(
            {
                "retrieval_ledger": _ledger(
                    {"url": "https://x/gone", "outcome": "not_found", "at": "2026-09-01T00:00:00Z"}
                )
            }
        )
        assert found["next_actions"]
        assert not any("credential" in a["action"] for a in found["next_actions"])
