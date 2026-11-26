"""Plan 03.5A (owner decision 2026-11-25): private installations upload a certificate from their own CA.

The backend checks the pair before anything is staged: the key matches, the certificate is a server
certificate valid for at least an hour, and it names the address the operator is using. Only then is
it staged (private file) and the host's fixed install operation requested. No key material is logged,
audited or returned.
"""

import datetime as dt
import json
import os

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from sqlalchemy import select

from app.config import settings

URL = "/api/v1/settings/networking/certificate/upload"


def _pair(names=("bioaf.lab.internal",), ips=(), days=30, ca=False, not_before_days=-1):
    key = ec.generate_private_key(ec.SECP256R1())
    import ipaddress

    sans = [x509.DNSName(n) for n in names] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    now = dt.datetime.now(dt.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, (names or ips)[0])]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Lab Root CA")]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now + dt.timedelta(days=not_before_days))
        .not_valid_after(now + dt.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if sans:
        builder = builder.add_extension(x509.SubjectAlternativeName(sans), critical=False)
    cert = builder.sign(key, hashes.SHA256())
    return (
        cert.public_bytes(serialization.Encoding.PEM).decode(),
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode(),
    )


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    upload = tmp_path / "tls-upload"
    requests = tmp_path / "update-requests"
    upload.mkdir(mode=0o700)
    requests.mkdir()
    monkeypatch.setattr(settings, "tls_upload_dir", str(upload))
    monkeypatch.setattr(settings, "update_requests_dir", str(requests))
    return upload, requests


def _post(client, token, cert, key, host="bioaf.lab.internal"):
    return client.post(
        URL,
        json={"certificate_pem": cert, "private_key_pem": key},
        headers={"Authorization": f"Bearer {token}", "Host": host},
    )


@pytest.mark.asyncio
async def test_a_valid_pair_for_this_address_is_staged_and_installed_by_the_host(client, admin_token, dirs, session):
    from app.models.audit_log import AuditLog

    upload, requests = dirs
    cert, key = _pair()
    response = await _post(client, admin_token, cert, key)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "installing"
    assert body["names"] == ["bioaf.lab.internal"]
    assert "PRIVATE" not in response.text
    assert (upload / "tls.crt").read_text() == cert
    assert (upload / "tls.key").read_text() == key
    assert oct(os.stat(upload / "tls.key").st_mode & 0o777) == "0o600"
    triggers = [json.loads(p.read_text()) for p in requests.iterdir()]
    assert triggers == [{"operation": "tls-install-uploaded"}]
    audit = (await session.execute(select(AuditLog).where(AuditLog.action == "upload_certificate"))).scalars().all()
    assert len(audit) == 1 and "PRIVATE" not in json.dumps(audit[0].details_json)


@pytest.mark.asyncio
async def test_an_ip_address_installation_needs_the_address_in_the_certificate(client, admin_token, dirs):
    cert, key = _pair(names=(), ips=("10.20.0.5",))
    assert (await _post(client, admin_token, cert, key, host="10.20.0.5")).status_code == 202
    other_cert, other_key = _pair(names=(), ips=("10.20.0.6",))
    response = await _post(client, admin_token, other_cert, other_key, host="10.20.0.5")
    assert response.status_code == 422
    assert "10.20.0.5" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case, expected",
    [
        ("mismatched_key", "does not match"),
        ("expired", "expired"),
        ("not_yet_valid", "not valid yet"),
        ("ca_certificate", "server certificate"),
        ("wrong_name", "bioaf.lab.internal"),
        ("garbage", "could not be read"),
    ],
)
async def test_a_pair_that_would_not_work_is_refused_and_nothing_is_staged(client, admin_token, dirs, case, expected):
    upload, requests = dirs
    cert, key = _pair()
    if case == "mismatched_key":
        _, key = _pair()
    elif case == "expired":
        cert, key = _pair(days=-1, not_before_days=-10)
    elif case == "not_yet_valid":
        cert, key = _pair(not_before_days=2, days=30)
    elif case == "ca_certificate":
        cert, key = _pair(ca=True)
    elif case == "wrong_name":
        cert, key = _pair(names=("other.lab.internal",))
    elif case == "garbage":
        cert = "-----BEGIN CERTIFICATE-----\nnope\n-----END CERTIFICATE-----\n"
    response = await _post(client, admin_token, cert, key)
    assert response.status_code == 422
    assert expected in response.json()["detail"]
    assert "PRIVATE" not in response.text
    assert list(upload.iterdir()) == [] and list(requests.iterdir()) == []


@pytest.mark.asyncio
async def test_oversized_input_is_refused(client, admin_token, dirs):
    cert, key = _pair()
    response = await _post(client, admin_token, cert + "x" * 70000, key)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_uploading_needs_permission_to_configure_infrastructure(client, comp_bio_token, dirs):
    cert, key = _pair()
    assert (await _post(client, comp_bio_token, cert, key)).status_code == 403
