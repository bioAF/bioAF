"""change_7.1 section 1: a deposit is described by the archive it lives in.

Passing an EGA identifier into the GEO implementation is not a fix, it is a category error: the
answer comes back "no GEO entry" and reads as "this paper deposited nothing". Every archive answers
the SAME questions so the paper-level rows can be an aggregate, and each answers them its own way.

**Existence, access and support are three facts, not one.** EGAD00001005044 exists, is controlled,
and cannot be acquired by bioAF. Collapsing those into a single tri-state loses the two halves a
reader needs: the paper DID deposit its reads, and we still cannot run them.

Every EGA shape here is the live 2026-09-08 response for Groff et al. (`10.1101/gr.252981.119`),
trimmed of prose.
"""

import pytest

from app.services.archive_discovery import (
    ARRAYEXPRESS,
    CONTROLLED,
    EGA,
    GEO,
    OTHER,
    PUBLIC,
    SRA,
    classify_archive,
    describe_ega_deposit,
)
from app.services.validation_capabilities import NO, UNKNOWN, YES

_EGAS = "EGAS00001003667"
_EGAD = "EGAD00001005044"

_DATASETS = (
    '[{"accession_id":"EGAD00001005044","title":"RNA-seq as a tool for evaluating human embryo '
    'competence","dataset_types":["Transcriptome profiling by high-throughput sequencing"],'
    '"technologies":["Illumina HiSeq 2500"],"num_samples":54,"access_type":"controlled",'
    '"is_released":true,"is_deprecated":false,"policy_accession_id":"EGAP00001001194"}]'
)

_FILES = (
    "["
    + ",".join(
        f'{{"accession_id":"EGAF0000246{i:04d}","filesize":442207842,"extension":"fastq.gz"}}' for i in range(108)
    )
    + "]"
)


class _Ega:
    """The two public metadata endpoints, with per-URL control over what fails."""

    def __init__(self, *, datasets=_DATASETS, files=_FILES):
        self.datasets, self.files = datasets, files
        self.urls: list[str] = []

    async def __call__(self, url: str) -> str:
        self.urls.append(url)
        if url.endswith("/files"):
            return self._or_raise(self.files, url)
        if "/datasets" in url:
            return self._or_raise(self.datasets, url)
        raise RuntimeError(f"404 {url}")

    @staticmethod
    def _or_raise(value, url):
        if isinstance(value, Exception):
            raise value
        return value


class TestWhichArchiveAnAccessionNames:
    @pytest.mark.parametrize(
        "accession,expected",
        [
            ("GSE274331", GEO),
            ("GSM123456", GEO),
            ("EGAS00001003667", EGA),
            ("EGAD00001005044", EGA),
            ("PRJNA123456", SRA),
            ("SRP123456", SRA),
            ("ERP123456", SRA),
            ("E-MTAB-1234", ARRAYEXPRESS),
            ("not-an-accession", OTHER),
            ("", OTHER),
        ],
    )
    def test_classification(self, accession, expected):
        assert classify_archive(accession) == expected

    def test_case_and_whitespace_do_not_change_the_archive(self):
        assert classify_archive("  egas00001003667 ") == EGA


class TestAnEgaStudy:
    @pytest.mark.asyncio
    async def test_a_controlled_dataset_exists_and_says_so(self):
        """The load-bearing correction. The paper DID deposit its reads; we cannot fetch them.
        Both halves have to survive into the answer."""
        deposit = await describe_ega_deposit(_EGAS, fetcher=_Ega())
        assert deposit["archive"] == EGA
        assert deposit["exists"] == YES
        assert deposit["access"] == CONTROLLED
        assert deposit["supported"] == NO

    @pytest.mark.asyncio
    async def test_raw_reads_are_established_from_the_public_file_listing(self):
        deposit = await describe_ega_deposit(_EGAS, fetcher=_Ega())
        assert deposit["raw_data"] == YES
        assert "108" in deposit["evidence"]

    @pytest.mark.asyncio
    async def test_a_fastq_only_dataset_holds_no_processed_matrix(self):
        deposit = await describe_ega_deposit(_EGAS, fetcher=_Ega())
        assert deposit["preprocessed_data"] == NO

    @pytest.mark.asyncio
    async def test_registered_samples_are_sample_metadata(self):
        deposit = await describe_ega_deposit(_EGAS, fetcher=_Ega())
        assert deposit["sample_metadata"] == YES
        assert "54" in deposit["evidence"]

    @pytest.mark.asyncio
    async def test_a_dataset_accession_is_looked_up_directly(self):
        """EGAD is a dataset, not a study, so it does not go through the study endpoint."""
        ega = _Ega(datasets=_DATASETS.strip("[]"))
        deposit = await describe_ega_deposit(_EGAD, fetcher=ega)
        assert deposit["exists"] == YES
        assert deposit["access"] == CONTROLLED
        assert all("/studies/" not in url for url in ega.urls)


class TestWhenTheLookupCannotAnswer:
    @pytest.mark.asyncio
    async def test_an_empty_listing_is_an_absence_not_a_failure(self):
        """EGA answers 200 with `[]` for an accession it does not hold, so absence cannot be read
        off the status code."""
        deposit = await describe_ega_deposit(_EGAS, fetcher=_Ega(datasets="[]"))
        assert deposit["exists"] == NO
        assert deposit["failure_reason"] is None

    @pytest.mark.asyncio
    async def test_a_transport_failure_is_unknown_never_absent(self):
        deposit = await describe_ega_deposit(_EGAS, fetcher=_Ega(datasets=RuntimeError("connection reset")))
        assert deposit["exists"] == UNKNOWN
        assert deposit["failure_reason"]

    @pytest.mark.asyncio
    async def test_unreadable_json_is_unknown_never_absent(self):
        deposit = await describe_ega_deposit(_EGAS, fetcher=_Ega(datasets="<html>maintenance</html>"))
        assert deposit["exists"] == UNKNOWN

    @pytest.mark.asyncio
    async def test_a_file_listing_failure_leaves_the_dataset_established(self):
        """One check failing does not abandon the others: the dataset is known to exist and to be
        controlled even when its file listing times out."""
        deposit = await describe_ega_deposit(_EGAS, fetcher=_Ega(files=RuntimeError("504")))
        assert deposit["exists"] == YES
        assert deposit["access"] == CONTROLLED
        assert deposit["raw_data"] == UNKNOWN

    @pytest.mark.asyncio
    async def test_it_never_raises(self):
        deposit = await describe_ega_deposit(
            _EGAS, fetcher=_Ega(datasets=RuntimeError("boom"), files=RuntimeError("boom"))
        )
        assert deposit["accession"] == _EGAS


class TestAPublicDeposit:
    @pytest.mark.asyncio
    async def test_a_public_dataset_is_public(self):
        deposit = await describe_ega_deposit(
            _EGAS, fetcher=_Ega(datasets=_DATASETS.replace('"controlled"', '"public"'))
        )
        assert deposit["access"] == PUBLIC

    @pytest.mark.asyncio
    async def test_public_ega_data_is_still_not_something_bioaf_can_fetch(self):
        """Access and support are different facts. bioAF has no EGA download client at all, so
        `supported` is NO even when the archive would let anyone in."""
        deposit = await describe_ega_deposit(
            _EGAS, fetcher=_Ega(datasets=_DATASETS.replace('"controlled"', '"public"'))
        )
        assert deposit["supported"] == NO


class TestItIsCheap:
    @pytest.mark.asyncio
    async def test_two_calls_per_deposit(self):
        """This runs on every paper read, including the ones nobody approves."""
        ega = _Ega()
        await describe_ega_deposit(_EGAS, fetcher=ega)
        assert len(ega.urls) == 2
