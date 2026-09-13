"""plan_8_1 stage 4: weighted rubric version 2.

What counts in the numbers: an independent assessment, as in version 1, and author-result consistency (the
claim checked against the authors' own table at its predicate), which is never presented as independent.
Resource statements are verified and shown, never scored. Controlled access never lowers a score. Depth is
shown beside the scope on every surface.

Authority is declared before results: a valid independent assessment outranks consistency; among the
authors' sources, a documented correction governs its superseded version, and conflicting authoritative
sources are inconclusive until reconciled. Every outcome is kept; a lower-authority outcome that disagrees
with the governing one is shown as a concern.
"""

from app.services.validation_rubric_v2 import (
    CONSISTENCY,
    INDEPENDENT,
    combine_finding,
    govern_claim,
    resource_statements,
)
from app.services.validation_scorecard import (
    BLOCKED,
    DISCREPANCY,
    INCONCLUSIVE,
    SUPPORTED,
    UNRESOLVED,
    build_scorecard,
    compact_scorecard,
)


def _independent(status, method="processed_reanalysis", reason="the reanalysis"):
    return {
        "claim_index": 0,
        "status": status,
        "method": method,
        "reason": reason,
        "evidence": ["level3_result"],
        "criteria": "c",
    }


def _source(outcome, table="t.txt", version_status=None, **extra):
    return {
        "outcome": outcome,
        "table": table,
        "source": "deposit",
        "version_status": version_status,
        "reason": f"{table} {outcome}",
        **extra,
    }


class TestEachClaimsStatus:
    def test_an_independent_discrepancy_governs_over_an_agreeing_table_and_the_agreement_is_a_concern(self):
        governed = govern_claim(0, [_independent(DISCREPANCY)], [_source("agree")])
        assert (governed["status"], governed["depth"]) == (DISCREPANCY, INDEPENDENT)
        assert any("the authors' table agrees with the paper" in c["text"] for c in governed["concerns"])

    def test_an_independent_success_with_a_failed_consistency_check_stays_supported_with_the_concern(self):
        governed = govern_claim(0, [_independent(SUPPORTED)], [_source("disagree")])
        assert (governed["status"], governed["depth"]) == (SUPPORTED, INDEPENDENT)
        assert any(c["text"] == "the paper's text disagrees with its own table" for c in governed["concerns"])

    def test_conflicting_comparable_independent_assessments_leave_the_claim_inconclusive(self):
        governed = govern_claim(0, [_independent(SUPPORTED), _independent(DISCREPANCY)], [])
        assert governed["status"] == INCONCLUSIVE
        assert governed["reason"] == "independent assessments disagree; not reconciled"

    def test_conflicting_authoritative_sources_leave_the_claim_inconclusive_whatever_their_order(self):
        for sources in (
            [_source("agree", "a.txt"), _source("disagree", "b.txt")],
            [_source("disagree", "b.txt"), _source("agree", "a.txt")],
        ):
            governed = govern_claim(0, [], sources)
            assert governed["status"] == INCONCLUSIVE
            assert governed["reason"] == "author-result sources disagree; not reconciled"
            assert governed["depth"] is None
            assert sorted(s["table"] for s in governed["authority"]["sources"]) == ["a.txt", "b.txt"]

    def test_a_documented_correction_governs_over_its_superseded_version_and_both_stay_recorded(self):
        governed = govern_claim(
            0, [], [_source("disagree", "original.txt", "superseded"), _source("agree", "corrected.txt", "corrected")]
        )
        assert (governed["status"], governed["depth"]) == (SUPPORTED, CONSISTENCY)
        authority = governed["authority"]
        assert authority["governing"] == ["corrected.txt"]
        assert authority["excluded"] == [{"table": "original.txt", "why": "superseded by a documented correction"}]
        assert {s["table"] for s in authority["sources"]} == {"original.txt", "corrected.txt"}

    def test_an_independent_assessment_governs_despite_conflicting_sources_and_the_conflict_is_a_concern(self):
        governed = govern_claim(0, [_independent(SUPPORTED)], [_source("agree", "a.txt"), _source("disagree", "b.txt")])
        assert (governed["status"], governed["depth"]) == (SUPPORTED, INDEPENDENT)
        assert any(c["text"] == "author-result sources disagree; not reconciled" for c in governed["concerns"])

    def test_consistency_governs_at_consistency_depth_without_an_independent_assessment(self):
        governed = govern_claim(0, [], [_source("agree")])
        assert (governed["status"], governed["depth"], governed["method"]) == (SUPPORTED, CONSISTENCY, "author_results")

    def test_an_unresolved_source_is_not_an_assessment(self):
        assert govern_claim(0, [], [_source("unresolved")]) is None


class TestEachFindingsStatusAndDepth:
    def test_the_depth_is_the_shallowest_required_claims(self):
        claims = [govern_claim(0, [_independent(SUPPORTED)], []), govern_claim(1, [], [_source("agree")])]
        outcome = combine_finding({"id": "F1"}, claims, [0, 1])
        assert (outcome["status"], outcome["depth"]) == (SUPPORTED, CONSISTENCY)

    def test_a_finding_is_independently_assessed_only_when_every_required_claim_is(self):
        claims = [govern_claim(0, [_independent(SUPPORTED)], []), govern_claim(1, [_independent(SUPPORTED)], [])]
        assert combine_finding({"id": "F1"}, claims, [0, 1])["depth"] == INDEPENDENT


def _inventory(*findings, version=2):
    return {
        "rubric_version": version,
        "revision": 1,
        "status": "established",
        "findings": [
            {
                "id": fid,
                "description": f"finding {fid}",
                "claim_indices": [i],
                "importance": {
                    "category": category,
                    "weight": {"primary": 2, "supporting": 1, "technical": 0}[category],
                    "status": "validated",
                },
            }
            for i, (fid, category) in enumerate(findings)
        ],
    }


def _outcome(status, depth=None, **extra):
    return {"status": status, "depth": depth, **extra}


class TestVersionTwoInTheNumbers:
    def test_version_one_records_render_unchanged_with_their_label(self):
        card = build_scorecard(_inventory(("F1", "primary"), version=1), {"F1": _outcome(SUPPORTED)})
        assert card["rubric_label"] == "weighted rubric version 1"
        assert card["depth_label"] is None
        assert card["score_label"] == "100 / 100"

    def test_every_consistency_depth_finding_passing_gives_100_and_says_none_was_independent(self):
        card = build_scorecard(
            _inventory(("F1", "primary"), ("F2", "supporting")),
            {"F1": _outcome(SUPPORTED, CONSISTENCY), "F2": _outcome(SUPPORTED, CONSISTENCY)},
        )
        assert card["rubric_label"] == "weighted rubric version 2"
        assert card["score_label"] == "100 / 100"
        assert card["scope_label"] == "2 / 2 assessed"
        assert card["depth_label"] == "2 consistency only; 0 independently assessed"

    def test_a_conclusive_discrepancy_can_give_zero(self):
        card = build_scorecard(_inventory(("F1", "primary")), {"F1": _outcome(DISCREPANCY, CONSISTENCY)})
        assert card["score_label"] == "0 / 100"

    def test_unassessed_checks_add_neither_earned_nor_assessed_weight(self):
        card = build_scorecard(
            _inventory(("F1", "primary"), ("F2", "supporting")),
            {"F1": _outcome(SUPPORTED, INDEPENDENT), "F2": _outcome(UNRESOLVED)},
        )
        assert (card["supported_weight"], card["assessed_weight"]) == (2, 2)
        assert card["depth_label"] == "0 consistency only; 1 independently assessed"

    def test_access_blocked_checks_never_lower_the_score(self):
        groff = _inventory(("F1", "primary"), ("F2", "supporting"), ("F3", "supporting"))
        blocked = {"status": BLOCKED, "cause": "access", "cause_label": "Access required"}
        alone = build_scorecard(groff, {"F1": blocked, "F2": blocked, "F3": blocked})
        assert alone["score_label"] is None and alone["scope_label"] == "0 / 3 assessed"
        with_one = build_scorecard(groff, {"F1": blocked, "F2": _outcome(SUPPORTED, CONSISTENCY), "F3": blocked})
        assert with_one["score_label"] == "100 / 100"

    def test_every_item_carries_its_depth_and_governing_evidence(self):
        card = build_scorecard(
            _inventory(("F1", "primary")),
            {"F1": _outcome(SUPPORTED, CONSISTENCY, governing={"method": "author_results", "evidence": ["check:1"]})},
        )
        (item,) = card["assessed_items"]
        assert (item["depth"], item["depth_label"]) == (CONSISTENCY, "Consistency only")
        assert item["governing"]["method"] == "author_results"

    def test_a_primary_findings_concern_appears_in_the_messages(self):
        card = build_scorecard(
            _inventory(("F1", "primary")),
            {
                "F1": _outcome(
                    SUPPORTED,
                    INDEPENDENT,
                    concerns=[{"kind": "author_disagrees", "text": "the paper's text disagrees with its own table"}],
                )
            },
        )
        assert any(m["kind"] == "concern" and "disagrees with its own table" in m["text"] for m in card["messages"])

    def test_the_list_carries_the_depth(self):
        card = build_scorecard(
            _inventory(("F1", "primary"), ("F2", "supporting")),
            {"F1": _outcome(SUPPORTED, INDEPENDENT), "F2": _outcome(SUPPORTED, CONSISTENCY)},
        )
        compact = compact_scorecard(card)
        assert (compact["independent_count"], compact["consistency_count"]) == (1, 1)
        assert compact["depth_label"] == card["depth_label"] == "1 consistency only; 1 independently assessed"


class TestResourceStatements:
    _PLAN = {
        "reported_experiments": [{"id": "e1", "assay": "bulk RNA-seq", "resources": ["GSE1", "EGAS1", "GSE9"]}],
        "sample_sheet": {"organism": "Homo sapiens"},
    }

    def _deposits(self):
        return [
            {
                "accession": "GSE1",
                "archive": "geo",
                "exists": "yes",
                "access": "public",
                "organisms": ["Homo sapiens"],
                "listing": {"library_strategies": ["RNA-Seq"]},
            },
            {
                "accession": "EGAS1",
                "archive": "ega",
                "exists": "yes",
                "access": "controlled",
                "listing": {"library_strategies": ["RNA-Seq"]},
            },
            {"accession": "GSE9", "archive": "geo", "exists": "no", "access": "unknown"},
        ]

    def test_verified_contradicted_and_not_established_each_render(self):
        rows = {
            r["identifier"]: r
            for r in resource_statements(self._PLAN, {"capabilities": {"deposits": self._deposits()}})
        }
        assert rows["GSE1"]["outcome"] == "verified"
        # Access is context: a controlled deposit whose public record matches is verified.
        assert rows["EGAS1"]["outcome"] == "verified"
        assert rows["GSE9"]["outcome"] == "contradicted"
        assert {r["outcome_label"] for r in rows.values()} == {"Verified", "Contradicted"}

    def test_a_failed_retrieval_is_not_established_never_contradicted(self):
        deposits = [
            {"accession": "GSE1", "archive": "geo", "exists": "unknown", "failure_reason": "GEO could not be reached"}
        ]
        (row,) = resource_statements(self._PLAN, {"capabilities": {"deposits": deposits}})
        assert (row["outcome"], row["outcome_label"]) == ("not_established", "Not established")

    def test_statements_never_change_either_metric(self):
        inventory = _inventory(("F1", "primary"))
        outcomes = {"F1": _outcome(SUPPORTED, CONSISTENCY)}
        plain = build_scorecard(inventory, outcomes)
        with_statements = build_scorecard(
            inventory,
            outcomes,
            resource_statements=resource_statements(self._PLAN, {"capabilities": {"deposits": self._deposits()}}),
        )
        for key in ("score", "display_score", "scope_label", "assessed_weight", "total_count"):
            assert plain[key] == with_statements[key]
        assert with_statements["resource_statements"]
        assert any(m["kind"] == "resource_contradicted" for m in with_statements["messages"])
