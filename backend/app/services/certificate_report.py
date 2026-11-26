"""The host's report on the installed HTTPS certificate (plan 03.5A).

``installer/tls.sh`` writes ``status.json`` in the report directory after every issue, renewal and
upload; the backend reads it through a read-only mount so Settings can show an installation without
a hostname (public IP-only, or private with an organization certificate) its certificate's expiry,
whether bioAF renews it, and the last renewal attempt. A missing, unreadable or malformed report
claims nothing.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone

REPORT_FILE = "status.json"
MAX_REPORT_BYTES = 16 * 1024
RENEWAL_RESULTS = ("succeeded", "failed")


@dataclass(frozen=True)
class CertificateReport:
    not_after: datetime
    subject: str
    issuer: str
    renewed_by_bioaf: bool
    last_renewal_at: datetime | None
    last_renewal_result: str | None


def _openssl_time(value: str) -> datetime | None:
    # openssl x509 -enddate: "Dec  2 12:00:00 2026 GMT"
    try:
        return datetime.strptime(" ".join(value.split()), "%b %d %H:%M:%S %Y GMT").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _iso_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def read_report(directory: str) -> CertificateReport | None:
    path = os.path.join(directory, REPORT_FILE)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_REPORT_BYTES:
            return None
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = json.loads(handle.read(MAX_REPORT_BYTES).decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    finally:
        os.close(fd)
    if not isinstance(data, dict) or not isinstance(data.get("certificate"), dict):
        return None
    certificate = data["certificate"]
    not_after = _openssl_time(certificate.get("not_after"))
    if not_after is None:
        return None
    renewal = data.get("last_renewal") if isinstance(data.get("last_renewal"), dict) else {}
    result = renewal.get("result") if renewal.get("result") in RENEWAL_RESULTS else None
    return CertificateReport(
        not_after=not_after,
        subject=str(certificate.get("subject") or ""),
        issuer=str(certificate.get("issuer") or ""),
        renewed_by_bioaf=data.get("renewed_by_bioaf") is True,
        last_renewal_at=_iso_time(renewal.get("at")) if result else None,
        last_renewal_result=result,
    )
