"""plan_7 step 13: what this paper actually has, established in one pass at read time.

Capability discovery was scattered: code availability at read time, deposit inventory only after
approval and only on the GEO route, and three of the seven questions never answered at all. The
consequence shipped in `7d36acad`: the route modal offers all three routes blind, so a person can
choose GEO on a paper with no deposited matrix and find out only after approving.

**Every answer is YES, NO or UNKNOWN.** A boolean has no room for "we could not find out", so every
transport failure would render as an established absence. "We could not find out" and "it is not
there" are different statements about a paper and the report must not merge them.

**Existence and accessibility are separate.** A repository can be known to exist and be private.
Existence is answered here; accessibility is answered later, by whatever tries to fetch the thing.

**Nothing here rules a paper in or out**, and UNKNOWN least of all. It decides which route is best
and how high a validation level is reachable.
"""

import pytest

from app.services.validation_capabilities import (
    NO,
    UNKNOWN,
    YES,
    discover_capabilities,
)

_GSE = "GSE274331"

_SERIES_MATRIX = "\n".join(
    [
        '!Series_title\t"A paper"',
        '!Sample_title\t"Control 1"\t"KD 1"',
        '!Sample_geo_accession\t"GSM1"\t"GSM2"',
        '!Sample_relation\t"SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX1"\t"SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX2"',
        '!Sample_characteristics_ch1\t"condition: control"\t"condition: KD"',
        '!Series_relation\t"SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRP1"',
    ]
)

_SUPPL_LISTING = f"""<html><body>
<a href="{_GSE}_counts.tsv.gz">{_GSE}_counts.tsv.gz</a>
<a href="{_GSE}_RAW.tar">{_GSE}_RAW.tar</a>
</body></html>"""

_ENA_WITH_FASTQ = (
    "run_accession\texperiment_accession\tsample_accession\tsample_title\tlibrary_strategy\tfastq_bytes\n"
    "SRR1\tSRX1\tSAMN1\tControl 1\tRNA-Seq\t1234567;2345678\n"
    "SRR2\tSRX2\tSAMN2\tKD 1\tRNA-Seq\t1234567;2345678\n"
)

# GSE96583's shape, measured live 2026-09-07: runs are registered and carry a read count, and ZERO
# fastq bytes, because the study deposits 10x BAMs rather than FASTQ.
_ENA_REGISTERED_BUT_NO_FASTQ = (
    "run_accession\texperiment_accession\tsample_accession\tsample_title\tlibrary_strategy\tfastq_bytes\n"
    "SRR1\tSRX1\tSAMN1\tControl 1\tRNA-Seq\t\n"
)


class _Geo:
    """Every URL this discovery touches, with per-URL control over what fails."""

    def __init__(self, *, matrix=_SERIES_MATRIX, suppl=_SUPPL_LISTING, ena=_ENA_WITH_FASTQ):
        self.matrix, self.suppl, self.ena = matrix, suppl, ena
        self.urls: list[str] = []

    async def __call__(self, url: str) -> str:
        self.urls.append(url)
        if "filereport" in url:
            return self._or_raise(self.ena, url)
        if url.endswith("filelist.txt"):
            raise RuntimeError("404: no _RAW.tar manifest")
        if url.endswith("/suppl/"):
            return self._or_raise(self.suppl, url)
        if "series_matrix" in url or url.endswith("/matrix/"):
            return self._or_raise(self.matrix, url)
        raise RuntimeError(f"404 {url}")

    @staticmethod
    def _or_raise(value, url):
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise RuntimeError(f"404 {url}")
        return value


async def _discover(**kw):
    return await discover_capabilities(
        accession=kw.pop("accession", _GSE),
        has_full_text=kw.pop("has_full_text", True),
        code_availability=kw.pop("code_availability", []),
        fetcher=kw.pop("fetcher", _Geo()),
        **kw,
    )


class TestThePaperItself:
    @pytest.mark.asyncio
    async def test_full_text_in_hand_is_yes(self):
        caps = await _discover(has_full_text=True)
        assert caps["paper_readable"]["value"] == YES

    @pytest.mark.asyncio
    async def test_no_full_text_is_no_not_unknown(self):
        """Whether we hold the text is a fact about our own state, not something we could fail to
        find out."""
        caps = await _discover(has_full_text=False)
        assert caps["paper_readable"]["value"] == NO


class TestTheGeoEntry:
    @pytest.mark.asyncio
    async def test_a_parsed_series_record_proves_the_entry_exists(self):
        caps = await _discover()
        assert caps["geo_entry"]["value"] == YES

    @pytest.mark.asyncio
    async def test_an_unlistable_supplementary_directory_does_not_deny_the_entry(self):
        """The load-bearing correction. A failed listing of `.../suppl/` says nothing about whether
        the GEO record exists, and deriving existence from it read "GEO entry: NO" for a study that
        is plainly there."""
        caps = await _discover(fetcher=_Geo(suppl=RuntimeError("connection reset")))
        assert caps["geo_entry"]["value"] == YES
        assert caps["preprocessed_data"]["value"] == UNKNOWN

    @pytest.mark.asyncio
    async def test_a_series_matrix_failure_is_unknown_not_absent(self):
        caps = await _discover(fetcher=_Geo(matrix=RuntimeError("502 Bad Gateway")))
        assert caps["geo_entry"]["value"] == UNKNOWN
        assert caps["geo_entry"]["failure_reason"]

    @pytest.mark.asyncio
    async def test_a_study_with_no_accession_is_a_no(self):
        """Not UNKNOWN: there is nothing to look up, which is an established absence."""
        caps = await _discover(accession="")
        assert caps["geo_entry"]["value"] == NO


class TestRawSampleData:
    @pytest.mark.asyncio
    async def test_runs_with_fastq_bytes_are_yes(self):
        caps = await _discover()
        assert caps["raw_data"]["value"] == YES

    @pytest.mark.asyncio
    async def test_registered_runs_with_no_fastq_bytes_are_no(self):
        """GSE96583's shape. Run rows and a read count prove the runs are REGISTERED; without fastq
        bytes there is nothing fetchngs could fetch, and answering YES would promise a route that
        cannot run."""
        caps = await _discover(fetcher=_Geo(ena=_ENA_REGISTERED_BUT_NO_FASTQ))
        assert caps["raw_data"]["value"] == NO

    @pytest.mark.asyncio
    async def test_an_ena_outage_is_unknown(self):
        caps = await _discover(fetcher=_Geo(ena=RuntimeError("timed out")))
        assert caps["raw_data"]["value"] == UNKNOWN
        assert caps["raw_data"]["failure_reason"]


class TestPreprocessedData:
    @pytest.mark.asyncio
    async def test_a_selectable_deposit_is_yes(self):
        caps = await _discover()
        assert caps["preprocessed_data"]["value"] == YES

    @pytest.mark.asyncio
    async def test_a_deposit_of_only_raw_archives_is_no(self):
        """GEO answered and there is nothing reproducible in it. An established absence."""
        listing = f'<html><a href="{_GSE}_RAW.tar">{_GSE}_RAW.tar</a></html>'
        caps = await _discover(fetcher=_Geo(suppl=listing))
        assert caps["preprocessed_data"]["value"] == NO

    @pytest.mark.asyncio
    async def test_a_listing_failure_is_unknown(self):
        caps = await _discover(fetcher=_Geo(suppl=RuntimeError("503")))
        assert caps["preprocessed_data"]["value"] == UNKNOWN


class TestSampleMetadata:
    @pytest.mark.asyncio
    async def test_samples_in_the_series_matrix_are_yes(self):
        caps = await _discover()
        assert caps["sample_metadata"]["value"] == YES

    @pytest.mark.asyncio
    async def test_the_same_fetch_failure_leaves_it_unknown(self):
        caps = await _discover(fetcher=_Geo(matrix=RuntimeError("502")))
        assert caps["sample_metadata"]["value"] == UNKNOWN


class TestCodeSources:
    @pytest.mark.asyncio
    async def test_a_github_link_is_its_own_source_row(self):
        caps = await _discover(
            code_availability=[{"kind": "github", "url": "https://github.com/lab/paper", "identifier": None}]
        )
        sources = caps["code_sources"]
        assert len(sources) == 1
        assert sources[0]["kind"] == "github"
        assert sources[0]["exists"] == YES

    @pytest.mark.asyncio
    async def test_existence_is_answered_here_and_accessibility_is_not(self):
        """A repository can be known to exist and be private. Only an attempted fetch settles the
        second, and that happens in step 16."""
        caps = await _discover(code_availability=[{"kind": "github", "url": "https://github.com/lab/p"}])
        assert caps["code_sources"][0]["accessible"] == "not_attempted"

    @pytest.mark.asyncio
    async def test_a_paper_with_two_code_sources_keeps_both(self):
        """A dead Zenodo DOI and a live GitHub mirror is usable code. Collapsing them to one would
        lose the working half."""
        caps = await _discover(
            code_availability=[
                {"kind": "zenodo", "url": "https://zenodo.org/record/1"},
                {"kind": "github", "url": "https://github.com/lab/p"},
            ]
        )
        assert [s["kind"] for s in caps["code_sources"]] == ["zenodo", "github"]

    @pytest.mark.asyncio
    async def test_a_paper_naming_no_code_is_a_no_with_no_sources(self):
        caps = await _discover(code_availability=[])
        assert caps["code_sources"] == []
        assert caps["code_artifact"]["value"] == NO
        assert caps["code_repository"]["value"] == NO

    @pytest.mark.asyncio
    async def test_the_extraction_never_ran_is_unknown_not_no(self):
        """`None` is "we never got to ask"; `[]` is "we looked and the paper named none"."""
        caps = await _discover(code_availability=None)
        assert caps["code_artifact"]["value"] == UNKNOWN
        assert caps["code_repository"]["value"] == UNKNOWN

    @pytest.mark.asyncio
    async def test_an_attachment_and_a_repo_are_reported_separately(self):
        caps = await _discover(
            code_availability=[
                {"kind": "supplementary", "url": "https://journal.org/supp/code.zip"},
                {"kind": "github", "url": "https://github.com/lab/p"},
            ]
        )
        assert caps["code_artifact"]["value"] == YES
        assert caps["code_repository"]["value"] == YES


class TestOneFailureDoesNotAbandonTheRest:
    @pytest.mark.asyncio
    async def test_a_geo_outage_leaves_the_code_rows_answered(self):
        """Each question is answered independently. A GEO outage blanking the code rows would make
        the checklist say something false about the paper."""
        caps = await _discover(
            fetcher=_Geo(matrix=RuntimeError("down"), suppl=RuntimeError("down"), ena=RuntimeError("down")),
            code_availability=[{"kind": "github", "url": "https://github.com/lab/p"}],
        )
        assert caps["geo_entry"]["value"] == UNKNOWN
        assert caps["raw_data"]["value"] == UNKNOWN
        assert caps["preprocessed_data"]["value"] == UNKNOWN
        assert caps["code_repository"]["value"] == YES
        assert caps["paper_readable"]["value"] == YES

    @pytest.mark.asyncio
    async def test_every_unknown_carries_a_reason_for_the_issues_section(self):
        """A discovery failure has to be visible as a limitation of the run rather than as a fact
        about the paper."""
        caps = await _discover(fetcher=_Geo(matrix=RuntimeError("502 Bad Gateway")))
        for key in ("geo_entry", "sample_metadata"):
            assert caps[key]["value"] == UNKNOWN
            assert caps[key]["failure_reason"]


class TestItIsCheap:
    @pytest.mark.asyncio
    async def test_it_asks_no_model_anything(self):
        """Two HTTP calls beyond what read already does. No LLM, no compute: it runs on every paper
        read, including the ones nobody approves."""
        geo = _Geo()
        await _discover(fetcher=geo)
        assert geo.urls, "discovery made no requests at all"

    @pytest.mark.asyncio
    async def test_it_never_raises_however_badly_the_network_behaves(self):
        async def _explode(url):
            raise RuntimeError("everything is on fire")

        caps = await _discover(fetcher=_explode)
        assert set(caps) >= {
            "paper_readable",
            "geo_entry",
            "raw_data",
            "preprocessed_data",
            "sample_metadata",
            "code_artifact",
            "code_repository",
            "code_sources",
        }


class TestItLandsAtReadTime:
    """The timing is the point: the C1 gate is pre-approval, so an answer produced after approval
    cannot inform the decision to approve."""

    @staticmethod
    def _patch_llm(monkeypatch, response):
        from types import SimpleNamespace

        from app.services import validation_extraction_service as ext

        async def _cfg(sess, org_id):
            return SimpleNamespace(provider="anthropic", model="claude-opus-4-8", api_key=None)

        class _C:
            async def submit(self, prompt, payload, model, api_key, attachments=None):
                return response

        monkeypatch.setattr(ext.llm_provider_config_service, "get_active", _cfg)
        monkeypatch.setattr(ext, "get_client", lambda p: _C())

    _EXTRACTION = (
        '```json\n{"accessions": ["GSE274331"], "sample_structure": {"organism": "Homo sapiens"}, '
        '"method": {"assay": "bulk RNA-seq"}, "claims": [], "data_availability": "deposited", '
        '"code_availability": [{"kind": "github", "url": "https://github.com/lab/paper"}], "blockers": []}\n```'
    )

    @pytest.mark.asyncio
    async def test_reading_a_paper_leaves_the_capabilities_on_the_study(self, session, admin_user, monkeypatch):
        from app.services.validation_driver_service import ValidationDriverService
        from app.services.validation_study_service import ValidationStudyService

        self._patch_llm(monkeypatch, self._EXTRACTION)
        geo = _Geo()
        monkeypatch.setattr("app.services.literature.accession_manifest_service._http_fetch_text", geo)
        monkeypatch.setattr("app.services.literature.deposit_inventory_service._http_fetch_text", geo)

        study = await ValidationStudyService.create_study(
            session, admin_user.organization_id, admin_user.id, source_accession=_GSE
        )
        await ValidationDriverService.read_and_plan(
            session, study, "the paper body", admin_user.organization_id, admin_user.id
        )

        caps = study.evidence_json["capabilities"]
        assert caps["geo_entry"]["value"] == YES
        assert caps["preprocessed_data"]["value"] == YES
        assert caps["code_repository"]["value"] == YES
        assert study.state == "plan_ready"

    @pytest.mark.asyncio
    async def test_a_discovery_failure_does_not_stop_the_read(self, session, admin_user, monkeypatch):
        """Nothing here rules a paper in or out, and a GEO outage least of all."""
        from app.services.validation_driver_service import ValidationDriverService
        from app.services.validation_study_service import ValidationStudyService

        self._patch_llm(monkeypatch, self._EXTRACTION)

        async def _explode(url):
            raise RuntimeError("GEO is down")

        monkeypatch.setattr("app.services.literature.accession_manifest_service._http_fetch_text", _explode)
        monkeypatch.setattr("app.services.literature.deposit_inventory_service._http_fetch_text", _explode)

        study = await ValidationStudyService.create_study(
            session, admin_user.organization_id, admin_user.id, source_accession=_GSE
        )
        await ValidationDriverService.read_and_plan(
            session, study, "the paper body", admin_user.organization_id, admin_user.id
        )

        assert study.state == "plan_ready"
        assert study.evidence_json["capabilities"]["geo_entry"]["value"] == UNKNOWN

    @pytest.mark.asyncio
    async def test_each_unknown_reaches_the_issues_section_with_its_reason(self, session, admin_user, monkeypatch):
        """A discovery failure is a limitation of the run, so it belongs beside every other step
        that could not get an answer, not only in the checklist's UNKNOWN cells."""
        from app.services.validation_driver_service import ValidationDriverService
        from app.services.validation_issue_service import ValidationIssueService
        from app.services.validation_study_service import ValidationStudyService

        self._patch_llm(monkeypatch, self._EXTRACTION)

        async def _explode(url):
            raise RuntimeError("GEO is down")

        monkeypatch.setattr("app.services.literature.accession_manifest_service._http_fetch_text", _explode)
        monkeypatch.setattr("app.services.literature.deposit_inventory_service._http_fetch_text", _explode)

        study = await ValidationStudyService.create_study(
            session, admin_user.organization_id, admin_user.id, source_accession=_GSE
        )
        await ValidationDriverService.read_and_plan(
            session, study, "the paper body", admin_user.organization_id, admin_user.id
        )

        issues = await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id)
        steps = [i["step"] for i in issues]
        assert "checking whether this paper has a GEO entry" in steps
        assert all(i["impact"] == "degraded" for i in issues)
