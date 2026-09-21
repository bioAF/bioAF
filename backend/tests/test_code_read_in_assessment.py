"""plan_8_6 section 4: the code is fetched and READ on the route the study is actually on.

Study 65's repository was resolved at read time and `code_fetch_service` was only ever called from
`_handle_reproducing`, which is post-approval and which this study never reached. So `retrieval` was
`not_attempted`, no C obligation could be assessed, and M5.B, which asks whether the inspected
methods and the supplied code agree on the consequential parameters, was put to an assessor that had
never been shown the code.

The assessment stage fetches the revision the paper archived, reads its members into source, and
records both. Nothing is installed and nothing is executed: this is an archive, a JSON parse and a
text decode.
"""

import pathlib

import pytest

from app.services.validation_assessment import refresh_code_inspection
from app.services.validation_study_service import ValidationStudyService

_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "granulosa"
_ARCHIVE = (_FIXTURES / "repo_at_cited_revision.tar.gz").read_bytes()
_CITED = "3c650290779db376c4d1f3a14960b08b17ae5561"
_HEAD = "076130744abe793790669094d689bd4440c3cb18"

_AVAILABILITY = (
    "Data analysis code can be found at: https://github.com/programmablebio/granulosa , "
    "(copy archived at swh:1:rev:" + _CITED + " ). Raw and processed sequencing data have been "
    "deposited to GEO under accession GSE213156."
)


class _Fetcher:
    def __init__(self, *, archive=_ARCHIVE, known=(_CITED, _HEAD)):
        self.archive = archive
        self.known = set(known)
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


def _index():
    from app.services.validation_evidence_index import build_index

    return build_index(
        "",
        sections={"index": [{"title": "Data availability", "kind": "availability", "paragraphs": [_AVAILABILITY]}]},
        source="europe_pmc",
    )


async def _study(session, admin_user, *, evidence=None):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_doi="10.7554/elife.83291", intended_route="deposit"
    )
    study.evidence_json = evidence if evidence is not None else {"paper_index": _index()}
    await session.flush()
    return study


_SOURCES = [{"kind": "github", "url": "https://github.com/programmablebio/granulosa"}]


class TestTheCodeIsFetchedWhereTheAssessmentIs:
    @pytest.mark.asyncio
    async def test_the_archived_revision_is_retrieved_and_recorded(self, session, admin_user):
        study = await _study(session, admin_user)
        fetcher = _Fetcher()
        await refresh_code_inspection(session, study, sources=_SOURCES, fetcher=fetcher)
        record = study.evidence_json["code_resolution"]
        assert record["outcome"] == "resolved"
        assert record["commit_sha"] == _CITED
        assert record["is_publication_revision"] is True

    @pytest.mark.asyncio
    async def test_the_members_become_source_a_check_can_read(self, session, admin_user):
        study = await _study(session, admin_user)
        await refresh_code_inspection(session, study, sources=_SOURCES, fetcher=_Fetcher())
        sources = study.evidence_json["code_inspection"]["sources"]
        paths = {s["path"] for s in sources}
        assert "DEG/deg_interpretation.py" in paths
        assert "scRNA/scanpy_analysis.ipynb" in paths
        assert all(s["text"] for s in sources)

    @pytest.mark.asyncio
    async def test_the_environment_specification_is_kept_as_one(self, session, admin_user):
        study = await _study(session, admin_user)
        await refresh_code_inspection(session, study, sources=_SOURCES, fetcher=_Fetcher())
        assert any(m["path"].endswith("flow_env.yml") for m in study.evidence_json["code_inspection"]["manifests"])

    @pytest.mark.asyncio
    async def test_a_repository_that_cannot_be_read_says_bioaf_did_not_read_it(self, session, admin_user):
        study = await _study(session, admin_user)
        await refresh_code_inspection(
            session,
            study,
            sources=_SOURCES,
            fetcher=_Fetcher(known=()),
        )
        record = study.evidence_json["code_resolution"]
        assert record["outcome"] != "resolved"
        assert _CITED in record["reason"]
        assert study.evidence_json["code_inspection"]["sources"] == []

    @pytest.mark.asyncio
    async def test_a_paper_that_names_no_code_costs_no_fetch(self, session, admin_user):
        study = await _study(session, admin_user)
        fetcher = _Fetcher()
        await refresh_code_inspection(session, study, sources=[], fetcher=fetcher)
        assert fetcher.asked == []

    @pytest.mark.asyncio
    async def test_an_unchanged_study_is_not_fetched_again(self, session, admin_user):
        study = await _study(session, admin_user)
        fetcher = _Fetcher()
        await refresh_code_inspection(session, study, sources=_SOURCES, fetcher=fetcher)
        first = len(fetcher.asked)
        await refresh_code_inspection(session, study, sources=_SOURCES, fetcher=fetcher)
        assert len(fetcher.asked) == first


class TestWhatTheCodeThenAnswers:
    @pytest.mark.asyncio
    async def test_the_code_reaches_the_obligation_that_reasons_about_it(self, session, admin_user):
        from app.services.validation_documentary_review import packets_for

        study = await _study(session, admin_user)
        await refresh_code_inspection(session, study, sources=_SOURCES, fetcher=_Fetcher())
        packet = packets_for(evidence=study.evidence_json, plan={}, leaves=("M5.B",))["M5.B"]
        carried = " ".join(p["text"] for p in packet["passages"])
        assert "fc_thresh = 1" in carried

    @pytest.mark.asyncio
    async def test_the_paper_s_own_threshold_is_beside_it(self, session, admin_user):
        """Section 4's acceptance: the stated cutoff and the script's cutoff are supplied together,
        so the disagreement is something an assessor can actually see."""
        from app.services.validation_documentary_review import packets_for
        from app.services.validation_evidence_index import build_index

        methods = (
            "PantherDB was used to calculate gene ontology enrichment for significantly upregulated "
            "(log 2 fc >3, p adj < 0.05) and downregulated (log 2 fc <-3, p adj < 0.05) genes."
        )
        index = build_index(
            "",
            sections={
                "index": [
                    {"title": "Data availability", "kind": "availability", "paragraphs": [_AVAILABILITY]},
                    {"title": "RNA-seq", "kind": "methods", "paragraphs": [methods]},
                ]
            },
            source="europe_pmc",
        )
        study = await _study(session, admin_user, evidence={"paper_index": index})
        await refresh_code_inspection(session, study, sources=_SOURCES, fetcher=_Fetcher())
        packet = packets_for(evidence=study.evidence_json, plan={}, leaves=("M5.B",))["M5.B"]
        carried = " ".join(p["text"] for p in packet["passages"])
        assert "log 2 fc >3" in carried
        assert "fc_thresh = 1" in carried

    @pytest.mark.asyncio
    async def test_a_study_with_code_no_longer_records_it_as_a_missing_source(self, session, admin_user):
        from app.services.validation_documentary_review import limitations_for

        study = await _study(session, admin_user)
        await refresh_code_inspection(session, study, sources=_SOURCES, fetcher=_Fetcher())
        assert not any(limit["needs"] == "code" for limit in limitations_for(study.evidence_json))


class TestTheRepositorySourceSurvivesASupplementReRead:
    """plan_8_6 section 4, found on the demo: re-reading the attachments replaced
    `code_inspection` wholesale, so a study whose repository bioAF had fetched and parsed came out
    of the assessment stage holding no source at all and `limitations_for` reported the code as
    missing beside the reason it had been found."""

    @pytest.mark.asyncio
    async def test_a_supplement_inspection_does_not_wipe_what_the_repository_gave(self, session, admin_user):
        from app.services.validation_documentary_review import limitations_for

        study = await _study(session, admin_user)
        await refresh_code_inspection(session, study, sources=_SOURCES, fetcher=_Fetcher())
        assert study.evidence_json["code_inspection"]["sources"]

        # What `resolve_study_supplements` does at the end of its own pass.
        from app.services.validation_code_inspection import inspect_code

        evidence = dict(study.evidence_json)
        inspected = inspect_code([{"filename": "x.R", "role": "code"}], bytes_for={"x.R": b"library(DESeq2)\n"})
        held = dict(evidence["code_inspection"])
        repository = [s for s in held["sources"] if (s.get("provenance") or {}).get("from") == "repository"]
        evidence["code_inspection"] = {**inspected, "sources": [*repository, *inspected["sources"]]}
        study.evidence_json = evidence
        await session.flush()

        paths = {s["path"] for s in study.evidence_json["code_inspection"]["sources"]}
        assert "DEG/deg_interpretation.py" in paths
        assert "x.R" in paths
        assert not any(limit["needs"] == "code" for limit in limitations_for(study.evidence_json))
