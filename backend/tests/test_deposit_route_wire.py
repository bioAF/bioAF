"""plan_7 step 11: the wire between the inventory, the selection and the download.

Steps 1, 2 and 5 were each built and verified against live GEO, and nothing in the application ever
called the first two. An approved deposit-route study therefore parked at ``acquiring_processed``
forever, because ``_handle_acquiring_processed`` waits for ``evidence["deposit_selection"]`` and no
code path wrote it.

These tests hold the wire itself: on the first visit the driver lists what the study deposited,
decides what to reproduce from (the model in ``autonomous``, a person at the C1 gate in
``assisted``), and only then falls through to the download that step 5 already built.
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.models.validation_study import ValidationStudy
from app.services.validation_driver_service import ValidationDriverService

_FILELIST = "Name\tSize\tType\n"

_DIR_LISTING = """<html><body>
<a href="GSE1_counts.tsv.gz">GSE1_counts.tsv.gz</a>
<a href="GSE1_meta.tsv">GSE1_meta.tsv</a>
<a href="GSE1_RAW.tar">GSE1_RAW.tar</a>
</body></html>"""

_UNFILTERED_LISTING = """<html><body>
<a href="GSM1_raw_feature_bc_matrix.h5">GSM1_raw_feature_bc_matrix.h5</a>
<a href="GSM2_raw_feature_bc_matrix.h5">GSM2_raw_feature_bc_matrix.h5</a>
</body></html>"""

_MATRIX_TSV = b"gene\tWT_1\tWT_2\tKO_1\tKO_2\nTP53\t10\t12\t50\t55\nGAPDH\t100\t110\t105\t99\n"


class _FakeStorage:
    def __init__(self):
        self.written: dict[str, bytes] = {}

    async def write_bytes(self, uri, data, *, content_type="application/octet-stream"):
        self.written[uri] = data

    async def write_text(self, uri, text, *, content_type="text/plain"):
        self.written[uri] = text.encode()


def _listing_fetcher(listing: str):
    """A GEO text fetcher: the supplementary directory answers, filelist.txt does not.

    That is the common real shape. A series with no ``_RAW.tar`` publishes no filelist, and
    ``list_deposit`` merges whichever of the two answered.
    """
    calls: list[str] = []

    async def fetch(url: str) -> str:
        calls.append(url)
        if url.endswith("filelist.txt"):
            raise RuntimeError("404")
        return listing

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def _dead_fetcher():
    async def fetch(url: str) -> str:
        raise RuntimeError("connection reset")

    return fetch


def _bytes_fetcher(pages: dict):
    async def fetch(url: str) -> bytes:
        if url not in pages:
            raise RuntimeError(f"404 {url}")
        return pages[url]

    return fetch


_BASE = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSEnnn/GSE1/suppl/"


def _deposit_pages():
    import gzip

    return {
        _BASE + "GSE1_counts.tsv.gz": gzip.compress(_MATRIX_TSV),
        _BASE + "GSE1_meta.tsv": b"sample\tcondition\nWT_1\tWT\nWT_2\tWT\nKO_1\tKO\nKO_2\tKO\n",
    }


async def _set_autonomy(session, org_id, mode):
    from sqlalchemy import select

    from app.models.organization import Organization

    org = (await session.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
    org.lit_validation_autonomy = mode
    await session.flush()


def _patch_selector(monkeypatch, response, model="claude-opus-4-8"):
    """Point the driver's LLM seam at a canned selection response."""
    from app.services import validation_driver_service as drv

    async def fake_get_for_feature(sess, org_id, feature):
        return SimpleNamespace(provider="anthropic", model=model, api_key=None)

    class _C:
        calls: list[str] = []

        async def submit(self, prompt, payload, model, api_key, attachments=None):
            _C.calls.append(payload)
            return response

    _C.calls = []
    monkeypatch.setattr(drv.llm_provider_config_service, "get_for_feature", fake_get_for_feature)
    monkeypatch.setattr(drv, "get_client", lambda p: _C())
    return _C


def _selection_response(
    primary="GSE1_counts.tsv.gz", metadata="GSE1_meta.tsv", declined=False, reason="a counts matrix"
):
    import json

    return (
        "```json\n"
        + json.dumps(
            {
                "primary_matrix": primary,
                "metadata_file": metadata,
                "value_type": "counts",
                "reason": reason,
                "confidence": 0.9,
                "declined": declined,
            }
        )
        + "\n```"
    )


@pytest_asyncio.fixture
async def unwired_study(session, admin_user):
    """A study approved onto the deposit route with NOTHING chosen: the shape study 29 was in."""
    study = ValidationStudy(
        organization_id=admin_user.organization_id,
        requested_by_user_id=admin_user.id,
        source_accession="GSE1",
        state="acquiring_processed",
        evidence_json={"route": "deposit"},
    )
    session.add(study)
    await session.flush()
    return study


class TestAutonomousChoosesAndProceeds:
    @pytest.mark.asyncio
    async def test_one_tick_lists_selects_downloads_and_advances(self, session, unwired_study, monkeypatch, admin_user):
        """The whole point of step 11: an approved study moves on its own.

        Before this wire the same study sat at ``acquiring_processed`` on every tick forever.
        """
        await _set_autonomy(session, admin_user.organization_id, "autonomous")
        _patch_selector(monkeypatch, _selection_response())
        storage = _FakeStorage()

        await ValidationDriverService._handle_acquiring_processed(
            session,
            unwired_study,
            fetcher=_bytes_fetcher(_deposit_pages()),
            storage_adapter=storage,
            inventory_fetcher=_listing_fetcher(_DIR_LISTING),
        )

        assert unwired_study.state == "inspecting_deposit"
        assert unwired_study.evidence_json["deposit_selection"]["primary_matrix"] == "GSE1_counts.tsv.gz"
        assert [f["filename"] for f in unwired_study.evidence_json["deposit"]["files"]] == [
            "GSE1_counts.tsv.gz",
            "GSE1_meta.tsv",
        ]

    @pytest.mark.asyncio
    async def test_the_selection_records_who_decided_and_why(self, session, unwired_study, monkeypatch, admin_user):
        """plan_6's rule: the model decides and the decision is stored with its reason, its
        confidence and the model that made it."""
        await _set_autonomy(session, admin_user.organization_id, "autonomous")
        _patch_selector(monkeypatch, _selection_response(reason="the only per-gene matrix deposited"))

        await ValidationDriverService._handle_acquiring_processed(
            session,
            unwired_study,
            fetcher=_bytes_fetcher(_deposit_pages()),
            storage_adapter=_FakeStorage(),
            inventory_fetcher=_listing_fetcher(_DIR_LISTING),
        )

        selection = unwired_study.evidence_json["deposit_selection"]
        assert selection["decided_by"] == "model"
        assert selection["model"] == "claude-opus-4-8"
        assert selection["reason"] == "the only per-gene matrix deposited"
        assert selection["confidence"] == 0.9

    @pytest.mark.asyncio
    async def test_the_inventory_is_kept_beside_the_choice(self, session, unwired_study, monkeypatch, admin_user):
        """What was on offer is part of the record. A choice among three files reads differently
        from a choice among thirty, and the gate renders the list either way."""
        await _set_autonomy(session, admin_user.organization_id, "autonomous")
        _patch_selector(monkeypatch, _selection_response())

        await ValidationDriverService._handle_acquiring_processed(
            session,
            unwired_study,
            fetcher=_bytes_fetcher(_deposit_pages()),
            storage_adapter=_FakeStorage(),
            inventory_fetcher=_listing_fetcher(_DIR_LISTING),
        )

        inventory = unwired_study.evidence_json["deposit_inventory"]
        assert inventory["accession"] == "GSE1"
        assert {e["filename"] for e in inventory["entries"]} == {
            "GSE1_counts.tsv.gz",
            "GSE1_meta.tsv",
            "GSE1_RAW.tar",
        }

    @pytest.mark.asyncio
    async def test_a_second_tick_does_not_list_or_ask_again(self, session, unwired_study, monkeypatch, admin_user):
        """The driver ticks repeatedly. Re-listing would hammer NCBI and re-asking would pay the
        model twice for the same answer."""
        await _set_autonomy(session, admin_user.organization_id, "autonomous")
        client = _patch_selector(monkeypatch, _selection_response())
        listing = _listing_fetcher(_DIR_LISTING)
        storage = _FakeStorage()

        await ValidationDriverService._handle_acquiring_processed(
            session,
            unwired_study,
            fetcher=_bytes_fetcher(_deposit_pages()),
            storage_adapter=storage,
            inventory_fetcher=listing,
        )
        listed, asked = len(listing.calls), len(client.calls)
        unwired_study.state = "acquiring_processed"  # the tick comes round again

        await ValidationDriverService._handle_acquiring_processed(
            session,
            unwired_study,
            fetcher=_bytes_fetcher(_deposit_pages()),
            storage_adapter=storage,
            inventory_fetcher=listing,
        )

        assert len(listing.calls) == listed
        assert len(client.calls) == asked


class TestTheDepositCannotServe:
    @pytest.mark.asyncio
    async def test_an_unlistable_deposit_holds_with_the_reason(self, session, unwired_study, monkeypatch, admin_user):
        """GEO being unreachable is a reason to take the pipeline route, not an error on the study,
        and never a silent park."""
        await _set_autonomy(session, admin_user.organization_id, "autonomous")
        _patch_selector(monkeypatch, _selection_response())

        await ValidationDriverService._handle_acquiring_processed(
            session,
            unwired_study,
            fetcher=_bytes_fetcher({}),
            storage_adapter=_FakeStorage(),
            inventory_fetcher=_dead_fetcher(),
        )

        assert unwired_study.state == "acquiring_processed"
        assert "GSE1" in unwired_study.evidence_json["deposit_failed"]["reason"]

    @pytest.mark.asyncio
    async def test_a_deposit_of_only_unfiltered_matrices_holds_naming_what_is_there(
        self, session, unwired_study, monkeypatch, admin_user
    ):
        """GSE312719's shape, reached through the wire rather than hand-planted on the evidence."""
        await _set_autonomy(session, admin_user.organization_id, "autonomous")
        _patch_selector(monkeypatch, _selection_response())

        await ValidationDriverService._handle_acquiring_processed(
            session,
            unwired_study,
            fetcher=_bytes_fetcher({}),
            storage_adapter=_FakeStorage(),
            inventory_fetcher=_listing_fetcher(_UNFILTERED_LISTING),
        )

        assert unwired_study.state == "acquiring_processed"
        reason = unwired_study.evidence_json["deposit_failed"]["reason"]
        assert "cell-calling" in reason
        assert unwired_study.evidence_json["deposit_unusable"]

    @pytest.mark.asyncio
    async def test_a_model_that_declines_holds_with_its_reasoning(
        self, session, unwired_study, monkeypatch, admin_user
    ):
        """Looked and said no is a finding. It is not the same as never having looked, and it must
        not read as a study that is still working."""
        await _set_autonomy(session, admin_user.organization_id, "autonomous")
        _patch_selector(
            monkeypatch,
            _selection_response(primary=None, declined=True, reason="every file deposited is a coverage track"),
        )

        await ValidationDriverService._handle_acquiring_processed(
            session,
            unwired_study,
            fetcher=_bytes_fetcher({}),
            storage_adapter=_FakeStorage(),
            inventory_fetcher=_listing_fetcher(_DIR_LISTING),
        )

        assert unwired_study.state == "acquiring_processed"
        assert "coverage track" in unwired_study.evidence_json["deposit_failed"]["reason"]


class TestAssistedHandsItToAPerson:
    @pytest.mark.asyncio
    async def test_the_inventory_is_stored_and_no_model_is_asked(self, session, unwired_study, monkeypatch, admin_user):
        """``assisted`` means a person picks at the C1 gate. The listing still happens, because the
        gate has nothing to show without it."""
        await _set_autonomy(session, admin_user.organization_id, "assisted")
        client = _patch_selector(monkeypatch, _selection_response())

        await ValidationDriverService._handle_acquiring_processed(
            session,
            unwired_study,
            fetcher=_bytes_fetcher({}),
            storage_adapter=_FakeStorage(),
            inventory_fetcher=_listing_fetcher(_DIR_LISTING),
        )

        assert unwired_study.state == "acquiring_processed"
        assert unwired_study.evidence_json["deposit_inventory"]["entries"]
        assert unwired_study.evidence_json.get("deposit_selection") is None
        assert client.calls == []

    @pytest.mark.asyncio
    async def test_a_person_s_pick_is_taken_on_the_next_tick(self, session, unwired_study, monkeypatch, admin_user):
        """The gate writes a selection; the driver then behaves exactly as it did for the model's."""
        await _set_autonomy(session, admin_user.organization_id, "assisted")
        _patch_selector(monkeypatch, _selection_response())
        listing = _listing_fetcher(_DIR_LISTING)

        await ValidationDriverService._handle_acquiring_processed(
            session,
            unwired_study,
            fetcher=_bytes_fetcher({}),
            storage_adapter=_FakeStorage(),
            inventory_fetcher=listing,
        )
        evidence = dict(unwired_study.evidence_json)
        evidence["deposit_selection"] = {
            "primary_matrix": "GSE1_counts.tsv.gz",
            "matrix_files": ["GSE1_counts.tsv.gz"],
            "metadata_file": "GSE1_meta.tsv",
            "value_type": "counts",
            "decided_by": "human",
        }
        unwired_study.evidence_json = evidence
        await session.flush()

        await ValidationDriverService._handle_acquiring_processed(
            session,
            unwired_study,
            fetcher=_bytes_fetcher(_deposit_pages()),
            storage_adapter=_FakeStorage(),
            inventory_fetcher=listing,
        )

        assert unwired_study.state == "inspecting_deposit"


class TestNoProviderIsNotAFailure:
    @pytest.mark.asyncio
    async def test_an_autonomous_org_with_no_provider_falls_back_to_the_person(
        self, session, unwired_study, monkeypatch, admin_user
    ):
        """Same direction the ratifier takes: hold where the assisted policy holds, rather than
        finalising something nobody decided."""
        from app.services import validation_driver_service as drv

        await _set_autonomy(session, admin_user.organization_id, "autonomous")

        async def no_config(sess, org_id, feature):
            return None

        monkeypatch.setattr(drv.llm_provider_config_service, "get_for_feature", no_config)

        await ValidationDriverService._handle_acquiring_processed(
            session,
            unwired_study,
            fetcher=_bytes_fetcher({}),
            storage_adapter=_FakeStorage(),
            inventory_fetcher=_listing_fetcher(_DIR_LISTING),
        )

        assert unwired_study.state == "acquiring_processed"
        assert unwired_study.evidence_json["deposit_inventory"]["entries"]
        assert unwired_study.evidence_json.get("deposit_selection") is None


class TestAPersonCanPickAtTheGate:
    """plan_7 step 15: `assisted` is only a working mode if a person has a control to answer with.

    The driver lists the deposit and holds; without this endpoint it holds forever.
    """

    @pytest_asyncio.fixture(autouse=True)
    async def _enable(self, session):
        from app.services import beta_features_service

        await beta_features_service.set_flag(session, "lit_validation", True)
        await session.commit()

    @pytest.mark.asyncio
    async def test_a_posted_pick_is_stored_as_the_selection(
        self, session, unwired_study, admin_user, admin_token, client
    ):
        evidence = dict(unwired_study.evidence_json)
        evidence["deposit_inventory"] = {
            "accession": "GSE1",
            "source": "directory",
            "listed_at": "2026-09-07T00:00:00Z",
            "entries": [
                {
                    "filename": "GSE1_counts.tsv.gz",
                    "url": "https://x/GSE1_counts.tsv.gz",
                    "classification": "matrix_counts",
                    "level": "series",
                    "gsm": None,
                    "size_bytes": 1024,
                    "deposited_type": None,
                }
            ],
            "triplets": [],
        }
        unwired_study.evidence_json = evidence
        await session.commit()

        r = await client.post(
            f"/api/validation-studies/{unwired_study.id}/deposit-selection",
            json={
                "primary_matrix": "GSE1_counts.tsv.gz",
                "matrix_files": ["GSE1_counts.tsv.gz"],
                "metadata_file": None,
                "value_type": "unknown",
                "reason": "chosen at the approval gate",
                "confidence": 1.0,
                "declined": False,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert r.status_code == 200
        selection = r.json()["evidence"]["deposit_selection"]
        assert selection["primary_matrix"] == "GSE1_counts.tsv.gz"
        # Recorded as a person's, so the report never credits a model for a human choice.
        assert selection["decided_by"] == "human"
        assert selection["model"] is None

    @pytest.mark.asyncio
    async def test_a_file_the_deposit_does_not_hold_is_refused(self, session, unwired_study, admin_token, client):
        """The same guard the model's pick gets. A filename that is not in the inventory would send
        the download at a 404."""
        evidence = dict(unwired_study.evidence_json)
        evidence["deposit_inventory"] = {
            "accession": "GSE1",
            "source": "directory",
            "listed_at": "x",
            "entries": [],
            "triplets": [],
        }
        unwired_study.evidence_json = evidence
        await session.commit()

        r = await client.post(
            f"/api/validation-studies/{unwired_study.id}/deposit-selection",
            json={"primary_matrix": "invented.tsv", "matrix_files": ["invented.tsv"]},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 400
        assert "invented.tsv" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_it_only_applies_while_the_study_is_waiting_for_one(
        self, session, unwired_study, admin_token, client
    ):
        """After acquisition the choice is made, and re-applying it would suggest a decision that is
        already spent can still be changed."""
        unwired_study.state = "reproducing"
        await session.commit()

        r = await client.post(
            f"/api/validation-studies/{unwired_study.id}/deposit-selection",
            json={"primary_matrix": "x.tsv", "matrix_files": ["x.tsv"]},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 400
