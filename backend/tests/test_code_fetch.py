"""plan_7 step 16: fetch and pin the authors' code.

Resolution order is the owner's: **the code ARTIFACT first, then the repository.** DESIRED STATE
phase 3 reads "run the code artifact if one exists, else the GitHub repo", and phase 4's checklist
has a `Download` value that nothing could act on while this handled repos only.

**A dead, private or unpinnable link is an OUTCOME, not a failure of this step.** It is recorded as
`code_unreachable` and carried on with; it is a true statement about the paper.

**This step is where accessibility gets answered.** Step 13 established that a source EXISTS; only
an attempted fetch establishes whether it can be reached, and the checklist carries both so it can
say "GitHub, exists, not accessible: repository is private" instead of flattening the two.

**Try every recorded source before concluding `code_absent`.** A paper with a dead Zenodo DOI and a
live GitHub mirror has usable code.
"""

import io
import tarfile

import pytest

from app.services.code_fetch_service import (
    CODE_ABSENT,
    CODE_UNREACHABLE,
    RESOLVED,
    resolve_code,
    safe_member_path,
)


def _tar_gz(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


_REPO_TAR = _tar_gz(
    {
        "lab-paper-abc1234/README.md": b"# the paper's analysis\n",
        "lab-paper-abc1234/analysis.R": b"library(DESeq2)\n",
        "lab-paper-abc1234/renv.lock": b"{}\n",
    }
)


class _Github:
    """GitHub's two calls: resolve the default branch to a SHA, then download the tarball."""

    def __init__(self, *, sha="abc1234def5678", tar=_REPO_TAR, fail_ref=None, fail_tar=None):
        self.sha, self.tar = sha, tar
        self.fail_ref, self.fail_tar = fail_ref, fail_tar
        self.urls: list[str] = []

    async def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        if "/commits/" in url or "/git/ref" in url:
            if self.fail_ref:
                raise self.fail_ref
            import json

            return json.dumps({"sha": self.sha}).encode()
        if "tarball" in url or url.endswith(".tar.gz"):
            if self.fail_tar:
                raise self.fail_tar
            return self.tar
        raise RuntimeError(f"404 {url}")


def _source(kind="github", url="https://github.com/lab/paper", identifier=None):
    return {"kind": kind, "url": url, "identifier": identifier}


async def _resolve(**kw):
    return await resolve_code(
        sources=kw.pop("sources", [_source()]),
        deposit_entries=kw.pop("deposit_entries", []),
        fetcher=kw.pop("fetcher", _Github()),
        **kw,
    )


class TestNoCodeAtAll:
    @pytest.mark.asyncio
    async def test_a_paper_naming_no_code_is_code_absent(self):
        result = await _resolve(sources=[])
        assert result.outcome == CODE_ABSENT
        assert result.reason

    @pytest.mark.asyncio
    async def test_it_is_not_reported_as_unreachable(self):
        """`code_absent` says the paper published nothing; `code_unreachable` says it published
        something we could not get. Two different findings about a paper."""
        assert (await _resolve(sources=[])).outcome != CODE_UNREACHABLE


class TestTheArtifactComesFirst:
    @pytest.mark.asyncio
    async def test_a_deposited_code_archive_is_taken_before_the_repo(self):
        """The owner's stated order. A supplementary archive in the GEO deposit is already listed by
        step 1, so no new fetcher is needed for it."""
        from app.services.literature.deposit_inventory_service import DepositEntry

        entry = DepositEntry(
            filename="GSE1_analysis_code.tar.gz",
            url="https://ftp.ncbi.nlm.nih.gov/x/GSE1_analysis_code.tar.gz",
            classification="code",
            level="series",
        )

        async def fetch(url):
            if url == entry.url:
                return _tar_gz({"analysis.R": b"library(DESeq2)\n"})
            raise AssertionError("the repository was fetched before the artifact")

        result = await _resolve(
            sources=[_source(kind="supplementary", url=entry.url), _source()],
            deposit_entries=[entry],
            fetcher=fetch,
        )
        assert result.outcome == RESOLVED
        assert result.kind == "supplementary"

    @pytest.mark.asyncio
    async def test_a_journal_hosted_artifact_that_404s_is_code_unreachable_not_a_crash(self):
        async def fetch(url):
            raise RuntimeError("404 Not Found")

        result = await _resolve(
            sources=[_source(kind="supplementary", url="https://journal.org/supp/code.zip")], fetcher=fetch
        )
        assert result.outcome == CODE_UNREACHABLE
        assert "journal.org" in result.reason or "code.zip" in result.reason


class TestTheRepository:
    @pytest.mark.asyncio
    async def test_it_resolves_and_pins_a_commit(self):
        """A repo that moved is not the one the paper used, and the provenance report has to name
        what actually ran."""
        result = await _resolve()
        assert result.outcome == RESOLVED
        assert result.commit_sha == "abc1234def5678"
        assert result.url == "https://github.com/lab/paper"

    @pytest.mark.asyncio
    async def test_it_keeps_the_files_it_fetched(self):
        result = await _resolve()
        assert {f["path"] for f in result.files} == {"README.md", "analysis.R", "renv.lock"}

    @pytest.mark.asyncio
    async def test_the_repos_own_top_level_directory_is_stripped(self):
        """GitHub's tarball wraps everything in `<repo>-<sha>/`. Keeping it would make every entry
        point path start with a directory name nothing else knows."""
        result = await _resolve()
        assert all(not f["path"].startswith("lab-paper-") for f in result.files)

    @pytest.mark.asyncio
    async def test_a_private_repo_is_code_unreachable_with_the_reason(self):
        result = await _resolve(fetcher=_Github(fail_ref=RuntimeError("404 Not Found")))
        assert result.outcome == CODE_UNREACHABLE
        assert result.reason

    @pytest.mark.asyncio
    async def test_a_repo_that_cannot_be_pinned_is_refused_by_name(self):
        """Same contract as the .xls refusal: say what is wrong rather than proceed and be
        confidently wrong about which code ran."""
        result = await _resolve(fetcher=_Github(sha=""))
        assert result.outcome == CODE_UNREACHABLE
        assert "commit" in result.reason.lower()


class TestEverySourceIsTried:
    @pytest.mark.asyncio
    async def test_a_dead_zenodo_doi_does_not_stop_a_live_github_mirror(self):
        github = _Github()

        async def fetch(url):
            if "zenodo" in url:
                raise RuntimeError("410 Gone")
            return await github(url)

        result = await _resolve(
            sources=[_source(kind="zenodo", url="https://zenodo.org/record/1"), _source()], fetcher=fetch
        )
        assert result.outcome == RESOLVED
        assert result.kind == "github"

    @pytest.mark.asyncio
    async def test_the_failures_of_the_others_are_recorded_not_discarded(self):
        github = _Github()

        async def fetch(url):
            if "zenodo" in url:
                raise RuntimeError("410 Gone")
            return await github(url)

        result = await _resolve(
            sources=[_source(kind="zenodo", url="https://zenodo.org/record/1"), _source()], fetcher=fetch
        )
        assert any(a["kind"] == "zenodo" and not a["ok"] for a in result.attempts)
        assert any("410" in (a["reason"] or "") for a in result.attempts)

    @pytest.mark.asyncio
    async def test_every_source_failing_is_unreachable_not_absent(self):
        async def fetch(url):
            raise RuntimeError("503")

        result = await _resolve(sources=[_source(kind="zenodo", url="https://zenodo.org/1"), _source()], fetcher=fetch)
        assert result.outcome == CODE_UNREACHABLE
        assert len(result.attempts) == 2


class TestAccessibilityIsWrittenBack:
    @pytest.mark.asyncio
    async def test_a_reachable_source_is_marked_accessible(self):
        result = await _resolve()
        assert result.accessibility["https://github.com/lab/paper"]["accessible"] == "yes"

    @pytest.mark.asyncio
    async def test_a_dead_link_records_no_with_its_reason_rather_than_unknown(self):
        """`unknown` here would be a transport failure; a 404 is an established `no`."""
        result = await _resolve(fetcher=_Github(fail_ref=RuntimeError("404 Not Found")))
        entry = result.accessibility["https://github.com/lab/paper"]
        assert entry["accessible"] == "no"
        assert entry["reason"]

    @pytest.mark.asyncio
    async def test_a_transport_failure_records_unknown(self):
        result = await _resolve(fetcher=_Github(fail_ref=RuntimeError("connection reset by peer")))
        assert result.accessibility["https://github.com/lab/paper"]["accessible"] == "unknown"


class TestTheCaps:
    @pytest.mark.asyncio
    async def test_an_archive_over_the_size_cap_is_refused_by_name(self):
        big = _tar_gz({"big.bin": b"x" * 2048})
        result = await _resolve(fetcher=_Github(tar=big), max_bytes=1024)
        assert result.outcome == CODE_UNREACHABLE
        assert "cap" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_an_archive_over_the_file_count_cap_is_refused(self):
        many = _tar_gz({f"f{i}.R": b"x" for i in range(50)})
        result = await _resolve(fetcher=_Github(tar=many), max_files=10)
        assert result.outcome == CODE_UNREACHABLE
        assert "files" in result.reason.lower()


class TestTraversal:
    """`reference_importer`'s tar reader streams members and writes to storage keys, which sidesteps
    filesystem traversal but does NOT sanitize `../`. This is the first path that unpacks a
    third-party archive, so the guard is explicit."""

    @pytest.mark.parametrize(
        "name", ["../../etc/passwd", "/etc/passwd", "a/../../b", "./../x", "..", "C:\\windows\\system32"]
    )
    def test_a_traversing_member_name_is_refused(self, name):
        assert safe_member_path(name) is None

    @pytest.mark.parametrize("name", ["analysis.R", "src/analysis.R", "./src/a.R", "a/b/c.py"])
    def test_an_ordinary_member_name_is_kept(self, name):
        assert safe_member_path(name)

    @pytest.mark.asyncio
    async def test_an_archive_carrying_a_traversal_is_refused_rather_than_unpacked(self):
        evil = _tar_gz({"lab-paper-abc/../../../etc/passwd": b"root::0:0\n"})
        result = await _resolve(fetcher=_Github(tar=evil))
        assert result.outcome == CODE_UNREACHABLE


class TestSearchingIsOutOfScope:
    @pytest.mark.asyncio
    async def test_it_never_goes_hunting_for_an_unlinked_repo(self):
        """Capture what the paper states; do not go looking. A repository we found by searching is
        not the one the paper published, and attributing a result to it would be a lie."""
        calls: list[str] = []

        async def fetch(url):
            calls.append(url)
            raise RuntimeError("404")

        await _resolve(sources=[])
        assert calls == []
