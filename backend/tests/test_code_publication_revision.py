"""plan_8_6 section 4: a pinned revision that is never retrieved is a location, not evidence.

Study 65 names `github.com/programmablebio/granulosa` AND the archived revision
`swh:1:rev:3c650290779db376c4d1f3a14960b08b17ae5561`, and recorded `retrieval: not_attempted`.
Nothing fetched it, so no C obligation could be assessed and the disagreement between the analysis
settings the paper states and those in the published script could not be detected.

`code_fetch_service` resolved HEAD. On this repository HEAD is a LATER commit than the one the paper
archived, so fetching it and calling it the published code would attribute to the paper whatever the
authors pushed afterwards. The cited revision is resolved to verified bytes, the requested and the
retrieved identities are both recorded, and where no publication revision exists the snapshot says
it is a snapshot of current code.
"""

import pytest

from app.services.code_fetch_service import RESOLVED, cited_revisions, resolve_code

_AVAILABILITY = (
    "All data needed to evaluate the conclusions in the paper are present in the paper and "
    "supplementary tables and figures. Data analysis code can be found at: "
    "https://github.com/programmablebio/granulosa , (copy archived at "
    "swh:1:rev:3c650290779db376c4d1f3a14960b08b17ae5561 ). Raw and processed sequencing data have "
    "been deposited to GEO under accession GSE213156 ."
)

_CITED = "3c650290779db376c4d1f3a14960b08b17ae5561"
_HEAD = "076130744abe793790669094d689bd4440c3cb18"


class TestWhatThePaperArchived:
    def test_a_software_heritage_revision_is_read_as_a_revision(self):
        found = cited_revisions(_AVAILABILITY)
        assert [r["value"] for r in found] == [_CITED]
        assert found[0]["kind"] == "swh_revision"
        assert found[0]["raw"].startswith("swh:1:rev:")

    def test_the_repository_it_belongs_to_is_in_its_context(self):
        assert "programmablebio/granulosa" in cited_revisions(_AVAILABILITY)[0]["context"]

    def test_a_bare_commit_is_read_as_one(self):
        found = cited_revisions("Code at https://github.com/a/b at commit 0123456789abcdef0123456789abcdef01234567.")
        assert found and found[0]["kind"] == "commit"

    def test_a_forty_character_word_that_is_not_a_hash_is_not_a_revision(self):
        assert cited_revisions("The differentiation protocol lasted for many days in culture medium.") == []

    def test_a_paper_that_archives_nothing_cites_nothing(self):
        assert cited_revisions("Code is available at https://github.com/a/b.") == []


class _Fetcher:
    """GitHub at its boundary: HEAD is a later commit than the one the paper archived."""

    def __init__(self, *, known=(_CITED, _HEAD), archive=b"\x1f\x8bfake"):
        self.known = set(known)
        self.archive = archive
        self.asked: list[str] = []

    async def __call__(self, url: str) -> bytes:
        self.asked.append(url)
        if "/commits/HEAD" in url:
            return f'{{"sha": "{_HEAD}"}}'.encode()
        if "/commits/" in url:
            sha = url.rsplit("/", 1)[-1]
            if sha not in self.known:
                raise RuntimeError("404 not found")
            return f'{{"sha": "{sha}"}}'.encode()
        if "/tarball/" in url:
            return self.archive
        raise RuntimeError("404 not found")


def _tarball() -> bytes:
    import pathlib

    return (pathlib.Path(__file__).parent / "fixtures" / "granulosa" / "repo_at_cited_revision.tar.gz").read_bytes()


_SOURCES = [{"kind": "github", "url": "https://github.com/programmablebio/granulosa"}]


class TestTheCitedRevisionIsWhatIsFetched:
    @pytest.mark.asyncio
    async def test_the_paper_s_own_revision_is_used_rather_than_head(self):
        fetcher = _Fetcher(archive=_tarball())
        found = await resolve_code(sources=_SOURCES, fetcher=fetcher, revisions=cited_revisions(_AVAILABILITY))
        assert found.outcome == RESOLVED
        assert found.commit_sha == _CITED
        assert any(f"/tarball/{_CITED}" in url for url in fetcher.asked)

    @pytest.mark.asyncio
    async def test_both_identities_are_recorded(self):
        found = await resolve_code(
            sources=_SOURCES, fetcher=_Fetcher(archive=_tarball()), revisions=cited_revisions(_AVAILABILITY)
        )
        assert found.requested_revision == _CITED
        assert found.commit_sha == _CITED
        assert found.is_publication_revision is True

    @pytest.mark.asyncio
    async def test_an_unavailable_cited_revision_is_reported_and_head_is_not_used_silently(self):
        fetcher = _Fetcher(known=(_HEAD,), archive=_tarball())
        found = await resolve_code(sources=_SOURCES, fetcher=fetcher, revisions=cited_revisions(_AVAILABILITY))
        assert found.outcome != RESOLVED
        assert _CITED in found.reason
        assert not any(f"/tarball/{_HEAD}" in url for url in fetcher.asked)

    @pytest.mark.asyncio
    async def test_with_no_cited_revision_the_snapshot_says_it_is_one(self):
        found = await resolve_code(sources=_SOURCES, fetcher=_Fetcher(archive=_tarball()), revisions=[])
        assert found.outcome == RESOLVED
        assert found.commit_sha == _HEAD
        assert found.is_publication_revision is False
        assert found.requested_revision is None
        assert "snapshot" in found.reason.lower()

    @pytest.mark.asyncio
    async def test_a_revision_cited_beside_a_different_repository_is_not_applied(self):
        text = "Code for the aligner is at https://github.com/other/tool (archived at swh:1:rev:" + _CITED + ")."
        found = await resolve_code(
            sources=_SOURCES, fetcher=_Fetcher(archive=_tarball()), revisions=cited_revisions(text)
        )
        assert found.commit_sha == _HEAD
        assert found.is_publication_revision is False


class TestTheFetchedBytesBecomeSource:
    @pytest.mark.asyncio
    async def test_the_archive_members_are_listed_with_their_sizes(self):
        found = await resolve_code(
            sources=_SOURCES, fetcher=_Fetcher(archive=_tarball()), revisions=cited_revisions(_AVAILABILITY)
        )
        paths = {f["path"] for f in found.files}
        assert "DEG/deg_interpretation.py" in paths
        assert "scRNA/scanpy_analysis.ipynb" in paths
        assert all(f["size_bytes"] > 0 for f in found.files)
