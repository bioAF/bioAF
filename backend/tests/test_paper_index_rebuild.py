"""plan_8_6 section 3: a study recorded before the index is rebuilt rather than left unassessed.

"A study whose held evidence predates this record is rebuilt from held source text first. Retrieve
missing public text through existing services if necessary; do not automatically repeat the full LLM
paper extraction."

Study 65 on the deployed demo holds 40 methods sentences and no index, because the index is built at
read time and the study was read before it existed. Its 17 methods subsections, its legends and its
availability statement are all still free to fetch from the source the read recorded, and none of
that is an extraction.
"""

import pathlib

import pytest

from app.services.validation_assessment import refresh_paper_index
from app.services.validation_study_service import ValidationStudyService

_JATS = (pathlib.Path(__file__).parent / "fixtures" / "granulosa" / "fulltext_jats.xml").read_text()


def _text():
    from app.services.literature.fulltext_service import _jats_sections, _jats_to_text
    from app.services.validation_paper_text import EUROPE_PMC, PaperText

    return PaperText(
        text=_jats_to_text(_JATS), source=EUROPE_PMC, supplements=[], pmcid="PMC9943069", sections=_jats_sections(_JATS)
    )


async def _study(session, admin_user, evidence):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_doi="10.7554/elife.83291", intended_route="deposit"
    )
    study.evidence_json = evidence
    await session.flush()
    return study


# What study 65 actually holds on the demo: the capped sentences, no index.
_HELD = {
    "pmcid": "PMC9943069",
    "paper_passages": {
        "methods": ["hiPSCs were maintained on Matrigel in mTeSR Plus."],
        "statements": [],
        "claims": [],
    },
    "paper_text_acquisition": {"source": "europe_pmc"},
}


class TestAStudyRecordedBeforeTheIndex:
    @pytest.mark.asyncio
    async def test_the_index_is_rebuilt_from_the_source_the_read_recorded(self, session, admin_user):
        study = await _study(session, admin_user, dict(_HELD))
        calls = []

        async def _again(_session, _study, _source, pasted=None):
            calls.append(_source)
            return _text()

        await refresh_paper_index(session, study, fetch_text=_again)
        index = study.evidence_json["paper_index"]
        carried = " ".join(p["text"] for p in index["passages"])
        assert "kallisto" in carried
        assert "log 2 fc >3" in carried
        assert calls == ["europe_pmc"], "the source the read recorded, not a new choice"

    @pytest.mark.asyncio
    async def test_the_obligations_are_then_given_the_methods_they_are_about(self, session, admin_user):
        from app.services.validation_documentary_review import packets_for

        study = await _study(session, admin_user, dict(_HELD))
        await refresh_paper_index(session, study, fetch_text=lambda *a, **k: _answer())
        packets = packets_for(evidence=study.evidence_json, plan={}, leaves=("M1.B",))
        carried = " ".join(p["text"] for p in packets["M1.B"]["passages"])
        assert "kallisto" in carried or "Scanpy" in carried
        assert "mTeSR" not in carried

    @pytest.mark.asyncio
    async def test_a_study_that_already_holds_an_index_is_not_fetched_again(self, session, admin_user):
        from app.services.validation_evidence_index import build_index

        held = {**_HELD, "paper_index": build_index("Methods Reads were aligned with STAR.", sections=None, source="x")}
        study = await _study(session, admin_user, held)
        calls = []

        async def _again(*args, **kwargs):
            calls.append(1)
            return _text()

        await refresh_paper_index(session, study, fetch_text=_again)
        assert calls == []

    @pytest.mark.asyncio
    async def test_a_source_that_cannot_be_reached_leaves_the_held_passages_as_they_were(self, session, admin_user):
        study = await _study(session, admin_user, dict(_HELD))

        async def _again(*args, **kwargs):
            return None

        await refresh_paper_index(session, study, fetch_text=_again)
        assert "paper_index" not in study.evidence_json
        assert study.evidence_json["paper_passages"]["methods"], "what was held is untouched"

    @pytest.mark.asyncio
    async def test_a_failure_to_fetch_never_fails_the_stage(self, session, admin_user):
        study = await _study(session, admin_user, dict(_HELD))

        async def _boom(*args, **kwargs):
            raise RuntimeError("the endpoint is down")

        await refresh_paper_index(session, study, fetch_text=_boom)
        assert "paper_index" not in study.evidence_json

    @pytest.mark.asyncio
    async def test_the_cutoff_reader_is_refreshed_with_it(self, session, admin_user):
        """Section 3 item 4: M2 and M4 read the methods this rebuild recovers."""
        study = await _study(session, admin_user, dict(_HELD))

        async def _again(*args, **kwargs):
            return _text()

        await refresh_paper_index(session, study, fetch_text=_again)
        record = study.evidence_json["methods_cutoffs"]
        assert record["paragraphs"] > 1


async def _answer():
    return _text()
