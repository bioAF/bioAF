"""plan_8_6 sections 5 and 11: one transfer allowance, counted as bytes stream, shared by every fetch.

The owner's review of the deployed code, 2026-09-21:

    "The aggregate download budget is not enforced as specified. Member retrieval checks its budget
    between batches and counts successful response sizes afterward. Three simultaneous requests can
    exceed the remaining allowance. Failed transfers count as zero, and the member budget is separate
    from repository and bundle transfers. A shared transfer allowance enforced during downloads,
    with retained successful artifacts and accurate accounting."

Three defects in one number:

- a request is admitted before its bytes are counted, so three in flight can each spend what the
  allowance had left for one;
- a transfer that FAILS carries bytes over the wire and was recorded as zero, and the plan counts
  "failed/bundle transfers" against the budget for exactly that reason;
- the bundle's own 243 MiB and the repository's archive were not counted at all, so an assessment
  could transfer far more than its budget by spending it in three places.

What must not change: a file already in hand is kept. Budget exhaustion defers the files it did not
reach, and is never an absence finding about the paper.
"""

import pytest

from app.services.supplement_inventory import BUDGET_EXHAUSTED, TransferAllowance


class TestTheAllowanceIsSpentBeforeTheBytesArrive:
    def test_a_request_reserves_what_it_may_transfer(self):
        allowance = TransferAllowance(1000)
        assert allowance.reserve(600) == 600
        assert allowance.reserve(600) == 400, "the second request may only have what is left"
        assert allowance.reserve(600) == 0

    def test_three_concurrent_reservations_cannot_exceed_the_allowance(self):
        allowance = TransferAllowance(900)
        granted = [allowance.reserve(500) for _ in range(3)]
        assert sum(granted) <= 900

    def test_what_a_transfer_did_not_use_comes_back(self):
        allowance = TransferAllowance(1000)
        granted = allowance.reserve(800)
        allowance.settle(granted, transferred=100)
        assert allowance.remaining == 900

    def test_a_failed_transfer_still_costs_what_it_moved(self):
        allowance = TransferAllowance(1000)
        granted = allowance.reserve(500)
        allowance.settle(granted, transferred=500, ok=False)
        assert allowance.remaining == 500
        assert allowance.spent == 500

    def test_a_refusal_that_moved_nothing_costs_nothing(self):
        """What bioAF can observe is what the transport reports. A 404 that returned a short error
        page moved a short error page; charging it a whole 200 MiB reservation would exhaust the
        paper's allowance on three refusals."""
        from app.services.supplement_inventory import _transferred_by

        assert _transferred_by(RuntimeError("404 not found")) == 0

    def test_a_failure_that_reports_its_body_is_charged_for_it(self):
        from app.services.supplement_inventory import _transferred_by

        class _Response:
            content = b"x" * 4096

        class _Error(RuntimeError):
            response = _Response()

        assert _transferred_by(_Error("500")) == 4096

    def test_it_records_what_was_spent_and_on_what(self):
        allowance = TransferAllowance(1000)
        allowance.settle(allowance.reserve(300), transferred=300)
        allowance.settle(allowance.reserve(200), transferred=200, ok=False)
        assert allowance.record() == {"budget": 1000, "spent": 500, "transferred_ok": 300, "failed": 200}

    def test_an_exhausted_allowance_grants_nothing(self):
        allowance = TransferAllowance(100)
        allowance.settle(allowance.reserve(100), transferred=100)
        assert allowance.exhausted
        assert allowance.reserve(1) == 0


class TestOneAllowanceCoversEveryFetchOfOneAttempt:
    @pytest.mark.asyncio
    async def test_the_bundle_attempt_spends_the_same_allowance_the_members_do(self):
        from app.services import supplement_inventory

        allowance = TransferAllowance(supplement_inventory.AGGREGATE_TRANSFER_BYTES)
        moved = []

        async def fetcher(url, max_bytes=None):
            moved.append((url, max_bytes))
            if url.endswith("supplementaryFiles"):
                # Over the per-file cap: refused, and its bytes are still bytes that moved.
                return b"x" * (supplement_inventory._MAX_BUNDLE_BYTES + 1)
            raise RuntimeError("404 no members")

        await supplement_inventory.resolve_supplements(
            "PMC1",
            [{"label": "Supplementary File 1", "filename": "s1.txt"}],
            fetcher=fetcher,
            allowance=allowance,
        )
        assert allowance.spent > 0, "the bundle transfer was not counted against the allowance"
        assert allowance.record()["failed"] > 0

    @pytest.mark.asyncio
    async def test_a_member_is_not_started_once_the_allowance_is_gone(self):
        from app.services import supplement_inventory

        allowance = TransferAllowance(10)
        allowance.settle(allowance.reserve(10), transferred=10)
        started = []

        async def fetcher(url, max_bytes=None):
            started.append(url)
            raise RuntimeError("404")

        ledger: list[dict] = []
        contents, _ = await supplement_inventory._retrieve_members(
            [{"identity": "s1.txt", "filename": "s1.txt", "kind": "attachment"}],
            fetcher=fetcher,
            article_urls=["https://example.org/article"],
            ledger=ledger,
            allowance=allowance,
        )
        assert contents == {}
        assert any(e.get("outcome") == BUDGET_EXHAUSTED for e in ledger)
        assert started == [], "a transfer was started with nothing left to spend"
