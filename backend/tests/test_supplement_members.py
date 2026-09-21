"""plan_8_6 section 5: a bundle that is too large is a reason to fetch its members, not to hold nothing.

Europe PMC serves an article's attachments as one archive. Study 65's is 243 MiB, over bioAF's
200 MiB cap, and the ledger records `too_large` twice and stops. Every attachment the paper
published was lost to one refusal, including the roughly 2.6 MB differential-expression supplement
that is 1% of the bundle.

The limit stays. What changes is what bioAF does when it is hit: it resolves each member to the
publisher's own copy and fetches the relevant ones individually, under the same per-file limit, the
same ledger and a recorded aggregate budget. A file that is itself too large costs the others
nothing, and the ledger says which of three things happened: the bundle was too large and the
members were fetched, the bundle was too large and the members could not be listed, or this one
member was too large.
"""

import pytest

from app.services.supplement_inventory import (
    BUDGET_EXHAUSTED,
    MEMBERS_UNRESOLVED,
    TOO_LARGE,
    article_links,
    member_locations,
    resolve_supplements,
)

_MANIFEST = [
    {"label": "Supplementary file 4", "filename": "elife-83291-supp4.zip", "source": "attached", "kind": "attachment"},
    {"label": "Supplementary file 2", "filename": "elife-83291-supp2.xlsx", "source": "attached", "kind": "attachment"},
    {
        "label": "Figure 2 source data 1",
        "filename": "elife-83291-fig2-data1.zip",
        "source": "attached",
        "kind": "attachment",
    },
]

# What the publisher's own article page links. It names the figure data and not the supplements,
# which is exactly what eLife's page does for study 65.
_PAGE = """
<html><body>
<a href="https://cdn.elifesciences.org/articles/83291/elife-83291-fig2-data1-v2.zip">Figure 2 source data 1</a>
<a href="/articles/83291/figures#supp4">Supplementary file 4</a>
</body></html>
"""


class TestWhereAMemberLives:
    def test_a_link_the_publisher_gives_is_used_as_it_is(self):
        links = article_links(_PAGE, "https://elifesciences.org/articles/83291")
        found = member_locations(_MANIFEST, links)
        assert found["elife-83291-fig2-data1.zip"].endswith("elife-83291-fig2-data1-v2.zip")

    def test_the_rest_are_resolved_against_the_base_the_publisher_revealed(self):
        """One linked attachment names the directory the others sit in and the suffix the publisher
        adds. That is learned from the publisher's own link, not hard-coded per journal."""
        found = member_locations(_MANIFEST, article_links(_PAGE, "https://elifesciences.org/articles/83291"))
        assert found["elife-83291-supp4.zip"] == (
            "https://cdn.elifesciences.org/articles/83291/elife-83291-supp4-v2.zip"
        )
        assert found["elife-83291-supp2.xlsx"].endswith("elife-83291-supp2-v2.xlsx")

    def test_an_absolute_href_in_the_manifest_needs_no_resolution(self):
        manifest = [
            {
                "label": "S1",
                "filename": "https://example.org/files/s1.csv",
                "source": "attached",
                "kind": "attachment",
            }
        ]
        assert member_locations(manifest, [])["https://example.org/files/s1.csv"] == (
            "https://example.org/files/s1.csv"
        )

    def test_a_page_that_links_nothing_resolves_nothing(self):
        assert member_locations(_MANIFEST, []) == {}

    def test_a_prose_alias_is_not_a_member_of_its_own(self):
        """Section 5: 23 unresolved records are 19 attachments and four aliases, not 23 files."""
        manifest = [
            *_MANIFEST,
            {"label": "Supplementary file 4", "filename": None, "source": "named_in_text", "kind": "reference"},
        ]
        found = member_locations(manifest, article_links(_PAGE, "https://elifesciences.org/articles/83291"))
        assert len(found) == 3


class _Fetcher:
    """The network at its boundary. The bundle is oversized; the members are not."""

    def __init__(self, *, bundle_bytes=243 * 1024 * 1024, files=None, fail=(), page=_PAGE):
        self.bundle_bytes = bundle_bytes
        self.files = files or {}
        self.fail = set(fail)
        self.page = page
        self.asked: list[str] = []
        self.inflight = 0
        self.peak = 0

    async def __call__(self, url: str, max_bytes: int | None = None) -> bytes:
        self.asked.append(url)
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        try:
            return await self._answer(url)
        finally:
            self.inflight -= 1

    async def _answer(self, url: str) -> bytes:
        if url in self.fail:
            raise RuntimeError("404 not found")
        if "supplementaryFiles" in url:
            return b"x" * self.bundle_bytes
        if url.startswith("https://elifesciences.org") or url.startswith("https://doi.org"):
            return self.page.encode()
        name = url.rsplit("/", 1)[-1]
        for filename, blob in self.files.items():
            stem = filename.rpartition(".")[0]
            if name.startswith(stem):
                return blob
        raise RuntimeError("404 not found")


def _table(rows=3) -> bytes:
    header = "gene\tlog2FoldChange\tpadj\n"
    return (header + "".join(f"G{i}\t2.5\t0.001\n" for i in range(rows))).encode()


_ARTICLE_URLS = ["https://elifesciences.org/articles/83291"]


class TestTheBundleBeingTooLargeDoesNotLoseTheFiles:
    @pytest.mark.asyncio
    async def test_a_small_relevant_file_is_retrieved_beside_an_oversized_bundle(self):
        fetcher = _Fetcher(files={"elife-83291-supp4.zip": _table(), "elife-83291-supp2.xlsx": _table()})
        ledger: list[dict] = []
        rows = await resolve_supplements(
            "PMC9943069", _MANIFEST, fetcher=fetcher, ledger=ledger, article_urls=_ARTICLE_URLS
        )
        supp4 = next(r for r in rows if r["identity"] == "elife-83291-supp4.zip")
        assert supp4["resolved"] is True
        assert supp4["inspected"] is True

    @pytest.mark.asyncio
    async def test_the_ledger_says_the_bundle_was_too_large_and_the_members_were_fetched(self):
        fetcher = _Fetcher(files={"elife-83291-supp4.zip": _table()})
        ledger: list[dict] = []
        await resolve_supplements("PMC9943069", _MANIFEST, fetcher=fetcher, ledger=ledger, article_urls=_ARTICLE_URLS)
        assert any(e["outcome"] == TOO_LARGE and "supplementaryFiles" in (e["url"] or "") for e in ledger)
        assert any(e["outcome"] == "retrieved" and "supp4" in (e["url"] or "") for e in ledger)

    @pytest.mark.asyncio
    async def test_one_member_that_is_itself_too_large_costs_the_others_nothing(self):
        fetcher = _Fetcher(
            files={
                "elife-83291-fig2-data1.zip": b"y" * (201 * 1024 * 1024),
                "elife-83291-supp4.zip": _table(),
            }
        )
        ledger: list[dict] = []
        rows = await resolve_supplements(
            "PMC9943069", _MANIFEST, fetcher=fetcher, ledger=ledger, article_urls=_ARTICLE_URLS
        )
        assert next(r for r in rows if r["identity"] == "elife-83291-supp4.zip")["resolved"] is True
        oversized = next(e for e in ledger if "fig2-data1" in (e["url"] or ""))
        assert oversized["outcome"] == TOO_LARGE
        assert oversized["artifacts"] == ["elife-83291-fig2-data1.zip"], "this one file's limitation, not the set's"

    @pytest.mark.asyncio
    async def test_a_member_that_cannot_be_reached_leaves_the_others_resolved(self):
        fetcher = _Fetcher(
            files={"elife-83291-supp4.zip": _table()},
            fail={"https://cdn.elifesciences.org/articles/83291/elife-83291-supp2-v2.xlsx"},
        )
        ledger: list[dict] = []
        rows = await resolve_supplements(
            "PMC9943069", _MANIFEST, fetcher=fetcher, ledger=ledger, article_urls=_ARTICLE_URLS
        )
        assert next(r for r in rows if r["identity"] == "elife-83291-supp4.zip")["resolved"] is True
        unreachable = next(r for r in rows if r["identity"] == "elife-83291-supp2.xlsx")
        assert not unreachable.get("resolved")
        assert unreachable["retrieval"]["status"] == "failed", "this file was not reached, not established absent"

    @pytest.mark.asyncio
    async def test_members_that_cannot_be_listed_are_recorded_as_that_and_not_as_absent(self):
        fetcher = _Fetcher(page="<html><body>nothing here</body></html>")
        ledger: list[dict] = []
        rows = await resolve_supplements(
            "PMC9943069", _MANIFEST, fetcher=fetcher, ledger=ledger, article_urls=_ARTICLE_URLS
        )
        assert any(e["outcome"] == MEMBERS_UNRESOLVED for e in ledger)
        assert all(not r.get("resolved") for r in rows)
        assert all(r["retrieval"]["status"] != "not_in_bundle" for r in rows), "never an established absence"


_MANY = [
    {
        "label": f"Supplementary file {i}",
        "filename": f"elife-83291-supp{i}.zip",
        "source": "attached",
        "kind": "attachment",
    }
    for i in range(1, 8)
] + [_MANIFEST[2]]


class TestTheBudget:
    @pytest.mark.asyncio
    async def test_the_aggregate_transfer_budget_stops_further_members(self):
        fetcher = _Fetcher(files={f"elife-83291-supp{i}.zip": b"a" * (2 * 1024 * 1024) for i in range(1, 8)})
        ledger: list[dict] = []
        await resolve_supplements(
            "PMC9943069",
            _MANY,
            fetcher=fetcher,
            ledger=ledger,
            article_urls=_ARTICLE_URLS,
            transfer_budget=3 * 1024 * 1024,
        )
        exhausted = next(e for e in ledger if e["outcome"] == BUDGET_EXHAUSTED)
        assert exhausted["artifacts"], "the files it did not reach are named, not silently dropped"

    @pytest.mark.asyncio
    async def test_budget_exhaustion_does_not_start_further_requests(self):
        fetcher = _Fetcher(files={f"elife-83291-supp{i}.zip": b"a" * (2 * 1024 * 1024) for i in range(1, 8)})
        ledger: list[dict] = []
        await resolve_supplements(
            "PMC9943069",
            _MANY,
            fetcher=fetcher,
            ledger=ledger,
            article_urls=_ARTICLE_URLS,
            transfer_budget=1024,
        )
        member_calls = [u for u in fetcher.asked if "cdn.elifesciences.org" in u]
        assert len(member_calls) <= 3, "at most the requests already in flight when the budget ran out"

    @pytest.mark.asyncio
    async def test_at_most_three_member_requests_are_in_flight(self):
        fetcher = _Fetcher(files={f"elife-83291-supp{i}.zip": b"a" for i in range(1, 8)})
        fetcher.peak = 0
        await resolve_supplements("PMC9943069", _MANY, fetcher=fetcher, ledger=[], article_urls=_ARTICLE_URLS)
        assert fetcher.peak <= 3


class TestWhatIsFetchedFirst:
    @pytest.mark.asyncio
    async def test_the_named_supplements_are_fetched_before_the_figure_archives(self):
        fetcher = _Fetcher(
            files={
                "elife-83291-supp4.zip": _table(),
                "elife-83291-supp2.xlsx": _table(),
                "elife-83291-fig2-data1.zip": _table(),
            }
        )
        await resolve_supplements("PMC9943069", _MANIFEST, fetcher=fetcher, ledger=[], article_urls=_ARTICLE_URLS)
        members = [u for u in fetcher.asked if "cdn.elifesciences.org" in u]
        assert members.index(next(u for u in members if "supp4" in u)) < members.index(
            next(u for u in members if "fig2-data1" in u)
        )


class TestNothingChangesWhenTheBundleArrives:
    @pytest.mark.asyncio
    async def test_a_bundle_within_the_limit_is_still_used(self):
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("elife-83291-supp4.zip", _table())
        fetcher = _Fetcher(bundle_bytes=0)
        fetcher.__call__ = None  # replaced below

        class _Bundle:
            def __init__(self, blob):
                self.blob = blob
                self.asked = []

            async def __call__(self, url, max_bytes=None):
                self.asked.append(url)
                return self.blob

        bundle = _Bundle(buffer.getvalue())
        ledger: list[dict] = []
        rows = await resolve_supplements(
            "PMC9943069", _MANIFEST, fetcher=bundle, ledger=ledger, article_urls=_ARTICLE_URLS
        )
        assert next(r for r in rows if r["identity"] == "elife-83291-supp4.zip")["resolved"] is True
        assert not any("cdn.elifesciences.org" in u for u in bundle.asked), "no member fetch when the bundle arrives"


class TestReuse:
    @pytest.mark.asyncio
    async def test_a_bundle_already_refused_for_its_size_is_not_downloaded_again(self):
        """Section 5: remember `too_large` against the source, so an unchanged retry goes straight
        to the member path instead of transferring 243 MiB a second time."""
        fetcher = _Fetcher(files={"elife-83291-supp4.zip": _table()})
        ledger: list[dict] = []
        await resolve_supplements("PMC9943069", _MANIFEST, fetcher=fetcher, ledger=ledger, article_urls=_ARTICLE_URLS)
        first = len([u for u in fetcher.asked if "supplementaryFiles" in u])
        await resolve_supplements("PMC9943069", _MANIFEST, fetcher=fetcher, ledger=ledger, article_urls=_ARTICLE_URLS)
        assert len([u for u in fetcher.asked if "supplementaryFiles" in u]) == first

    @pytest.mark.asyncio
    async def test_a_member_already_held_is_not_fetched_again(self):
        fetcher = _Fetcher(files={"elife-83291-supp4.zip": _table(), "elife-83291-supp2.xlsx": _table()})
        ledger: list[dict] = []
        rows = await resolve_supplements(
            "PMC9943069", _MANIFEST, fetcher=fetcher, ledger=ledger, article_urls=_ARTICLE_URLS
        )
        before = len([u for u in fetcher.asked if "supp4" in u])
        await resolve_supplements("PMC9943069", rows, fetcher=fetcher, ledger=ledger, article_urls=_ARTICLE_URLS)
        assert next(r for r in rows if r["identity"] == "elife-83291-supp4.zip")["resolved"] is True
        assert before >= 1


class TestWhatTheLiveRunOnStudy65Found:
    """plan_8_6 section 5, three defects the deployed run surfaced that no fixture had."""

    def test_an_elife_supplementary_file_merges_with_the_prose_that_cites_it(self):
        """eLife deposits "Supplementary file 4" as `elife-83291-supp4.zip`. The citation and the
        attachment never merged, so four aliases were reported as four files bioAF failed to get."""
        from app.services.supplement_inventory import establish_identity

        rows = establish_identity(
            [
                {
                    "label": "Supplementary file 4",
                    "filename": "elife-83291-supp4.zip",
                    "source": "attached",
                    "kind": "attachment",
                },
                {
                    "label": "Supplemental File 4",
                    "filename": None,
                    "source": "named_in_text",
                    "kind": "reference",
                    "identity": "reference:file:4",
                },
            ]
        )
        assert len(rows) == 1
        assert rows[0]["identity"] == "elife-83291-supp4.zip"
        assert "Supplemental File 4" in rows[0]["references"]

    def test_a_table_citation_still_never_takes_a_file(self):
        from app.services.supplement_inventory import establish_identity

        rows = establish_identity(
            [
                {
                    "label": "Supplementary file 1",
                    "filename": "elife-1-supp1.xlsx",
                    "source": "attached",
                    "kind": "attachment",
                },
                {
                    "label": "Supplementary Table S1",
                    "filename": None,
                    "source": "named_in_text",
                    "kind": "reference",
                    "identity": "reference:table:1",
                },
            ]
        )
        assert len(rows) == 2, "a table is not a file, whatever the numbers"

    def test_an_unresolved_prose_alias_is_not_a_missing_attachment(self):
        from app.services.validation_documentary_review import limitations_for

        evidence = {
            "paper_index": {"passages": [{"text": "x", "kind": "methods"}]},
            "supplements": [
                {"identity": "a.zip", "kind": "attachment", "resolved": True},
                {"identity": "reference:file:9", "kind": "reference", "resolved": False},
            ],
            "code_inspection": {"sources": [{"path": "a.py"}]},
            "sample_records": {"deposits": [{"accession": "GSE1"}]},
        }
        assert not any(limit["needs"] == "supplements" for limit in limitations_for(evidence))

    def test_an_attachment_bioaf_did_not_retrieve_still_is_one(self):
        from app.services.validation_documentary_review import limitations_for

        evidence = {
            "paper_index": {"passages": [{"text": "x", "kind": "methods"}]},
            "supplements": [{"identity": "a.zip", "kind": "attachment", "resolved": False}],
            "code_inspection": {"sources": [{"path": "a.py"}]},
            "sample_records": {"deposits": [{"accession": "GSE1"}]},
        }
        assert any(limit["needs"] == "supplements" for limit in limitations_for(evidence))
