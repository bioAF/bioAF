"""Plan 03.5A: certificate health for an installation without a hostname (public IP-only or private).

The host writes ``tls-report/status.json`` (installer/tls.sh) after every issue, renewal and upload;
the backend reads it through a read-only mount. Settings then shows the installed certificate's
expiry, whether bioAF renews it, and the last renewal attempt, instead of "not requested".
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.api.networking import get_networking_applier
from app.services import certificate_report
from tests.test_networking_api import MockNetworkingApplier


def _openssl_date(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%b %e %H:%M:%S %Y GMT")


@pytest_asyncio.fixture
async def report_dir(tmp_path, monkeypatch, client):
    from app import main as main_module
    from app.config import settings

    monkeypatch.setattr(settings, "tls_report_dir", str(tmp_path))
    main_module.app.dependency_overrides[get_networking_applier] = lambda: MockNetworkingApplier()
    try:
        yield tmp_path
    finally:
        main_module.app.dependency_overrides.pop(get_networking_applier, None)


def _write(directory, *, not_after, renewed=True, last=("2026-11-26T03:00:00Z", "succeeded")):
    (directory / "status.json").write_text(
        json.dumps(
            {
                "certificate": {
                    "not_after": _openssl_date(not_after),
                    "subject": "CN=203.0.113.7",
                    "issuer": "C=US, O=Let's Encrypt, CN=YE1",
                },
                "renewed_by_bioaf": renewed,
                "last_renewal": {"at": last[0], "result": last[1]} if last else None,
            }
        )
    )


async def _status(client, token):
    response = await client.get(
        "/api/v1/settings/networking/certificate/status", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_an_ip_only_installation_reports_its_certificate_and_renewal(client, admin_token, report_dir):
    expires = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=5)
    _write(report_dir, not_after=expires)
    body = await _status(client, admin_token)
    assert body["status"] == "active"
    assert datetime.fromisoformat(body["not_after"]) == expires
    assert body["issuer"] == "C=US, O=Let's Encrypt, CN=YE1"
    assert body["renewed_by_bioaf"] is True
    assert body["last_renewal_result"] == "succeeded"
    assert datetime.fromisoformat(body["last_renewal_at"]) == datetime(2026, 11, 26, 3, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_a_failed_renewal_is_reported_while_the_certificate_still_works(client, admin_token, report_dir):
    _write(
        report_dir, not_after=datetime.now(timezone.utc) + timedelta(days=2), last=("2026-11-26T09:00:00Z", "failed")
    )
    body = await _status(client, admin_token)
    assert body["status"] == "active"
    assert body["last_renewal_result"] == "failed"


@pytest.mark.asyncio
async def test_an_expired_certificate_is_reported_as_failed(client, admin_token, report_dir):
    _write(report_dir, not_after=datetime.now(timezone.utc) - timedelta(hours=1))
    assert (await _status(client, admin_token))["status"] == "failed"


@pytest.mark.asyncio
async def test_an_uploaded_certificate_has_no_renewal(client, admin_token, report_dir):
    _write(report_dir, not_after=datetime.now(timezone.utc) + timedelta(days=300), renewed=False, last=None)
    body = await _status(client, admin_token)
    assert body["renewed_by_bioaf"] is False
    assert body["last_renewal_result"] is None and body["last_renewal_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, "not json", "[]", '{"certificate": {"not_after": "soon"}}'])
async def test_without_a_readable_report_nothing_is_claimed(client, admin_token, report_dir, content):
    if content is not None:
        (report_dir / "status.json").write_text(content)
    body = await _status(client, admin_token)
    assert body["status"] == "not_requested"
    assert body.get("not_after") is None


def test_a_symlinked_report_is_not_followed(tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text("{}")
    (tmp_path / "status.json").symlink_to(target)
    assert certificate_report.read_report(str(tmp_path)) is None
