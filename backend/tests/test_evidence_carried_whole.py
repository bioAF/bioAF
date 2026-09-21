"""plan_8_6 sections 3 and 4: what bioAF holds reaches selection whole, and what cannot is recorded.

Section 3 asked for the silent truncation rules to be REMOVED from the assessment-evidence path, and
for what a packet could not carry to be on the record. The packet's own budget does that: it ranks,
it defers, and its coverage says what it deferred. The step BEFORE it did not.

`extras_for` cut every piece of evidence on its way in, before any obligation had ranked anything:
eight excerpts of each source file, nine hundred characters of each dependency manifest, the first
twenty-four sources, manifests, supplements and sample records, and the first two citing passages of
each supplement. None of it was recorded, so an obligation's coverage read "sufficient" over evidence
bioAF held and never offered.

What that cost on study 65, measured 2026-09-21:

- `scRNA/scanpy_analysis.ipynb` is 9,276 characters and 7,048 of them were carried. The tail holds
  the commented-out `rank_genes_groups` call on `DAZL_plus`, which is the difference between "the
  notebook performs this comparison" and "the notebook has it commented out". M3.B was deducted on
  the first reading.
- `flow/flow_env.yml` is 2,433 characters and 900 were carried, which stops inside the letter `b`
  of an alphabetical dependency list. Every version C5 asks about after that was held and unseen.

So: carry it, rank it in the packet, and where a bound is genuinely reached, say which file it was
reached on and let section 8 refuse an absence finding over the part nobody read.
"""

import pathlib

import pytest

from app.services.validation_code_inspection import inspect_archive
from app.services.validation_documentary_review import (
    carried_evidence,
    extras_for,
    packets_for,
)
from app.services.validation_evidence_index import build_index
from app.services.validation_evidence_packets import packet_for
from app.services.validation_judgment import coverage_supports_absence

_ARCHIVE = (pathlib.Path(__file__).parent / "fixtures" / "granulosa" / "repo_at_cited_revision.tar.gz").read_bytes()


def _granulosa() -> dict:
    return inspect_archive(_ARCHIVE, origin="https://github.com/programmablebio/granulosa")


def _code_evidence() -> dict:
    found = _granulosa()
    return {"code_inspection": {"sources": found["sources"], "manifests": found["manifests"]}}


def _carried(evidence: dict, kind: str) -> str:
    return "\n".join(row["text"] for row in extras_for(evidence=evidence, plan={}) if row["kind"] == kind)


class TestTheSuppliedCodeIsCarriedWhole:
    """Section 4: the obligations that reason about code are shown the code, not its first excerpts."""

    def test_the_commented_out_dazl_comparison_reaches_the_assessor(self):
        """The owner, 2026-09-21: M3.B discussed a comparison the notebook does not execute.

        `#sc.tl.rank_genes_groups(adata, 'DAZL_plus', method='t-test')` sits in the last fifth of the
        notebook, past the eighth excerpt. An assessor that never saw it cannot tell an executed
        cell-level test from a commented-out one.
        """
        carried = _carried(_code_evidence(), "code")
        assert "rank_genes_groups(adata, 'DAZL_plus'" in carried
        assert "#sc.tl.rank_genes_groups(adata, 'DAZL_plus'" in carried

    def test_every_line_of_a_source_file_is_carried_across_its_excerpts(self):
        source = next(s for s in _granulosa()["sources"] if s["path"].endswith("deg_interpretation.py"))
        rows = extras_for(evidence={"code_inspection": {"sources": [source]}}, plan={})
        bodies = [row["text"].partition(":\n")[2] for row in rows if row["kind"] == "code"]
        assert "\n".join(bodies) == "\n".join(source["text"].splitlines())

    def test_each_excerpt_says_where_in_the_file_it_came_from(self):
        rows = [r for r in extras_for(evidence=_code_evidence(), plan={}) if r["kind"] == "code"]
        assert rows
        assert all(r["text"].startswith("lines ") for r in rows)
        assert all(r.get("location") for r in rows)

    def test_the_last_excerpt_of_the_notebook_is_its_last_lines(self):
        source = next(s for s in _granulosa()["sources"] if s["path"].endswith(".ipynb"))
        rows = [
            r for r in extras_for(evidence={"code_inspection": {"sources": [source]}}, plan={}) if r["kind"] == "code"
        ]
        assert source["text"].strip("\n").splitlines()[-1] in rows[-1]["text"]


class TestADependencyManifestIsNotCutMidList:
    """Section 4: dependency manifests stay linked to their scripts, and C5 asks about versions."""

    def test_a_version_pinned_past_the_first_nine_hundred_characters_is_carried(self):
        carried = _carried(_code_evidence(), "code")
        assert "pandas=0.23.4" in carried
        assert "numpy=1.15.4" in carried

    def test_the_whole_manifest_is_carried(self):
        manifest = _granulosa()["manifests"][0]
        rows = extras_for(evidence={"code_inspection": {"manifests": [manifest]}}, plan={})
        bodies = [row["text"].partition(":\n")[2] for row in rows if row["kind"] == "code"]
        assert "\n".join(bodies) == "\n".join(manifest["text"].splitlines())


class TestEveryRecordBioafHoldsReachesSelection:
    """Section 3: selection decides what an obligation is judged on. A cap before it is not selection."""

    def _deposit(self, samples: int) -> dict:
        return {
            "sample_records": {
                "deposits": [
                    {
                        "accession": "GSE9",
                        "samples": [
                            {
                                "accession": f"GSM{i}",
                                "title": f"granulosa-like ovaroid replicate {i}",
                                "organism": "Homo sapiens",
                                "source_name": "ovaroid",
                                "characteristics": {"cell type": "granulosa-like"},
                            }
                            for i in range(samples)
                        ],
                    }
                ]
            }
        }

    def test_the_thirtieth_deposited_sample_record_is_offered(self):
        rows = extras_for(evidence=self._deposit(30), plan={})
        assert any(row["id"] == "GSE9/GSM29" for row in rows)

    def test_a_sample_record_longer_than_one_passage_is_carried_in_parts_rather_than_cut(self):
        record = {
            "sample_records": {
                "deposits": [
                    {
                        "accession": "GSE9",
                        "samples": [
                            {
                                "accession": "GSM1",
                                "title": "ovaroid",
                                "organism": "Homo sapiens",
                                "characteristics": {f"field {i}": f"value {i} " + "x" * 60 for i in range(30)},
                            }
                        ],
                    }
                ]
            }
        }
        carried = " ".join(row["text"] for row in extras_for(evidence=record, plan={}) if row["kind"] == "deposit")
        assert "field 29" in carried

    def test_the_thirtieth_supplement_is_offered_with_all_of_its_citing_passages(self):
        evidence = {
            "supplements": [
                {
                    "identity": f"supp-{i}",
                    "label": f"Supplementary File {i}",
                    "kind": "attachment",
                    "resolved": True,
                    "citing_passages": [{"text": f"Supplementary File {i} lists the {w} genes."} for w in "abcd"],
                }
                for i in range(30)
            ]
        }
        carried = " ".join(row["text"] for row in extras_for(evidence=evidence, plan={}) if row["kind"] == "supplement")
        assert "Supplementary File 29" in carried
        assert "the d genes" in carried

    def test_the_thirtieth_source_file_is_offered(self):
        evidence = {
            "code_inspection": {
                "sources": [
                    {"path": f"analysis/step_{i}.py", "language": "python", "text": f"threshold = {i}\n"}
                    for i in range(30)
                ]
            }
        }
        carried = " ".join(row["source"] for row in extras_for(evidence=evidence, plan={}) if row["kind"] == "code")
        assert "analysis/step_29.py" in carried


class TestWhatCouldNotBeCarriedIsRecorded:
    """Section 3: record what was deferred. Section 8: an absence cannot be reported over it."""

    def _huge(self) -> dict:
        body = "\n".join(f"value_{i} = {i}" for i in range(60_000))
        return {"code_inspection": {"sources": [{"path": "big/generated.py", "language": "python", "text": body}]}}

    def test_a_source_bioaf_could_not_carry_whole_is_named_in_its_omissions(self):
        found = carried_evidence(evidence=self._huge(), plan={})
        omissions = [o for o in found["omissions"] if o["needs"] == "code"]
        assert omissions
        assert any("big/generated.py" in o["reason"] for o in omissions)

    def test_a_code_obligation_cannot_report_an_absence_over_the_part_nobody_read(self):
        packet = packets_for(evidence=self._huge(), plan={}, leaves=("M5.B",))["M5.B"]
        supported, why = coverage_supports_absence(packet["coverage"])
        assert supported is False
        assert why

    def test_code_that_fits_records_no_omission_and_leaves_the_obligation_answerable(self):
        found = carried_evidence(evidence=_code_evidence(), plan={})
        assert [o for o in found["omissions"] if o["needs"] == "code"] == []
        packet = packets_for(evidence=_code_evidence(), plan={}, leaves=("M5.B",))["M5.B"]
        assert not any("characters" in reason for reason in packet["coverage"]["unavailable"])

    def test_a_study_with_no_code_at_all_is_still_reported_as_holding_none(self):
        packet = packets_for(evidence={}, plan={}, leaves=("M5.B",))["M5.B"]
        supported, _ = coverage_supports_absence(packet["coverage"])
        assert supported is False


class TestThePacketsDeferralAccountingStaysHonest:
    """Section 8: `truncated` is what refuses an absence finding, so it has to mean something.

    Carrying whole files multiplies the rows an obligation may be offered, and most of them say
    nothing about it. Counting every deferred row as relevant evidence bioAF failed to carry would
    leave every obligation on every real paper permanently unable to report an absence, which is the
    failure the character budget was raised to avoid.
    """

    _INDEX = build_index(
        "",
        sections={
            "index": [
                {
                    "title": "Single-cell RNA sequencing",
                    "kind": "methods",
                    "paragraphs": [
                        "Cells with fewer than 500 detected genes or more than 15% mitochondrial reads "
                        "were removed, and doublets were called with Scrublet."
                    ],
                }
            ]
        },
        source="europe_pmc",
    )

    def _code(self, text: str, count: int) -> list[dict]:
        return [
            {"id": f"code:plots.py#{i}", "kind": "code", "source": "the supplied plots.py", "text": text}
            for i in range(count)
        ]

    def test_deferred_code_that_says_nothing_about_this_obligation_does_not_truncate_the_packet(self):
        extras = self._code("lines 1-8:\nplt.savefig('figure_one.svg')\nsns.set_theme()", 40)
        packet = packet_for("M1.B", index=self._INDEX, extras=extras, budget_chars=600)
        assert packet["coverage"]["deferred"]
        assert packet["coverage"]["truncated"] is False
        assert packet["coverage"]["sufficient"] is True

    def test_deferred_code_that_answers_this_obligation_does_truncate_the_packet(self):
        extras = self._code("lines 1-8:\nadata = adata[adata.obs.pct_counts_mt < 15]  # filter cells", 40)
        packet = packet_for("M1.B", index=self._INDEX, extras=extras, budget_chars=600)
        assert packet["coverage"]["truncated"] is True
        supported, _ = coverage_supports_absence(packet["coverage"])
        assert supported is False

    @pytest.mark.parametrize("leaf", ["S2.B", "M5.A", "E2.B"])
    def test_an_obligations_own_kind_of_record_still_reaches_it(self, leaf):
        """The kinds that are eligible BECAUSE of what they are keep reaching their obligations."""
        extras = [
            {"id": "GSE9/GSM1", "kind": "deposit", "source": "the deposited record of GSE9", "text": "HepG2 cells"},
            {"id": "t1", "kind": "tools", "source": "the tools this analysis declares", "text": "STAR, DESeq2"},
            {
                "id": "d1",
                "kind": "design",
                "source": "the paper on figure 4",
                "text": "One sample per clone and condition was analysed.",
                "carries": "design",
            },
        ]
        packet = packet_for(leaf, index=self._INDEX, extras=extras)
        assert packet["passages"]
