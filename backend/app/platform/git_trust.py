"""Git server trust for every startup path that clones over SSH (plan 03C).

Work Node startup (GCE and EC2), RStudio session startup and custom-pipeline clone initialization
all verify the Git server against the same material:

- GitHub's SSH host keys, shipped read-only with the release (``git_known_hosts/github.com``),
  checked against GitHub's authenticated metadata when the file is maintained;
- custom hosts an operator approved through ``import_custom_known_hosts`` (the
  ``python -m app.cli.import_git_known_hosts`` command), stored in platform configuration with
  their fingerprints and provenance.

No path scans a host key or trusts a key on first use. A custom host without approved material is
reported (``GitTrust.untrusted_hosts``); Work Nodes keep that one repository's previous behavior
rather than silently skipping it or widening trust for any other host.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import asyncssh

_GITHUB_FILE = Path(__file__).with_name("git_known_hosts") / "github.com"
CUSTOM_CONFIG_KEY = "git_known_hosts"
MAX_ENTRIES = 64
MAX_LINE = 8192
_ALGORITHMS = {
    "ssh-ed25519",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "ssh-rsa",
}
_MIN_RSA_BITS = 2048
_HOSTNAME = re.compile(
    r"(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?)*"
)
_SCP_URL = re.compile(r"(?:[A-Za-z0-9._-]+@)?(?P<host>[A-Za-z0-9.-]+):(?!//)")
_SSH_URL = re.compile(r"ssh://(?:[A-Za-z0-9._-]+@)?(?P<host>[A-Za-z0-9.-]+)(?::(?P<port>[0-9]{1,5}))?/")

# Where each startup path keeps the trust file, and the ssh options every clone uses with it.
STRICT_OPTIONS = "-o StrictHostKeyChecking=yes -o UserKnownHostsFile={path} -o GlobalKnownHostsFile=/dev/null"
# A Work Node repository on a custom host with no approved material keeps its previous behavior.
LEGACY_UNVERIFIED_OPTIONS = "-o StrictHostKeyChecking=no"


class KnownHostsError(ValueError):
    """Known-hosts material that is malformed or not exactly scoped. The message names no key."""


@dataclass(frozen=True)
class HostKeyEntry:
    host: str
    port: int
    line: str
    fingerprint: str


@dataclass(frozen=True)
class GitTrust:
    known_hosts: str
    untrusted_hosts: set[tuple[str, int]] = field(default_factory=set)


def github_known_hosts() -> str:
    lines = [ln.strip() for ln in _GITHUB_FILE.read_text().splitlines()]
    return "\n".join(ln for ln in lines if ln and not ln.startswith("#"))


def ssh_host(url: str) -> tuple[str, int] | None:
    """The (host, port) an SSH Git URL connects to; None for HTTPS or anything unrecognized."""
    match = _SSH_URL.match(url)
    if match:
        return match["host"].lower(), int(match["port"] or 22)
    if "://" in url:
        return None
    match = _SCP_URL.match(url)
    return (match["host"].lower(), 22) if match else None


def _fingerprint(blob: bytes) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def _parse_host(field_text: str) -> tuple[str, int]:
    if field_text.startswith("[") and "]:" in field_text:
        host, _, port = field_text[1:].partition("]:")
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise KnownHostsError("a host entry has an invalid port")
        port_number = int(port)
    else:
        host, port_number = field_text, 22
    if not _HOSTNAME.fullmatch(host):
        raise KnownHostsError("a host entry must name exactly one host (no patterns, lists or hashed names)")
    return host.lower(), port_number


def parse_known_hosts(text: str) -> list[HostKeyEntry]:
    """Validate approved known_hosts material: one exact host, one supported key, nothing else."""
    entries: list[HostKeyEntry] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if len(line) > MAX_LINE:
            raise KnownHostsError("a known_hosts line is too long")
        fields = line.split()
        if len(fields) != 3:
            raise KnownHostsError("each line must be: host key-type base64-key (no markers or options)")
        host_field, algorithm, encoded = fields
        if host_field.startswith("@"):
            raise KnownHostsError("certificate-authority and revoked markers are not accepted")
        host, port = _parse_host(host_field)
        if algorithm not in _ALGORITHMS:
            raise KnownHostsError("unsupported host key type")
        try:
            blob = base64.b64decode(encoded, validate=True)
            key = asyncssh.import_public_key(f"{algorithm} {encoded}")
        except (binascii.Error, ValueError, asyncssh.KeyImportError):
            raise KnownHostsError("a host key could not be read") from None
        if key.algorithm.decode() != algorithm:
            raise KnownHostsError("a host key does not match its declared type")
        if algorithm == "ssh-rsa" and key.pyca_key.key_size < _MIN_RSA_BITS:
            raise KnownHostsError("RSA host keys must be at least 2048 bits")
        canonical_host = host if port == 22 else f"[{host}]:{port}"
        entries.append(
            HostKeyEntry(
                host=host, port=port, line=f"{canonical_host} {algorithm} {encoded}", fingerprint=_fingerprint(blob)
            )
        )
        if len(entries) > MAX_ENTRIES:
            raise KnownHostsError(f"no more than {MAX_ENTRIES} host keys may be approved")
    return entries


def trust_for(repo_urls: list[str], custom: list[HostKeyEntry]) -> GitTrust:
    """The known_hosts content for these repositories and the SSH hosts that have no approved key."""
    covered = {("github.com", 22)} | {(e.host, e.port) for e in custom}
    untrusted = set()
    for url in repo_urls:
        host = ssh_host(url)
        if host and host not in covered:
            untrusted.add(host)
    content = "\n".join([github_known_hosts(), *(e.line for e in custom)])
    return GitTrust(known_hosts=content, untrusted_hosts=untrusted)


async def load_custom_entries(session) -> list[HostKeyEntry]:
    """The operator-approved custom host keys, re-validated on read."""
    from app.platform.platform_config_service import PlatformConfigService

    raw = await PlatformConfigService.get(session, CUSTOM_CONFIG_KEY)
    if not raw:
        return []
    try:
        record = json.loads(raw)
        text = "\n".join(entry["line"] for entry in record.get("entries", []))
    except (ValueError, TypeError, KeyError):
        return []
    return parse_known_hosts(text)


async def import_custom_known_hosts(session, text: str, *, source: str) -> list[dict]:
    """Validate and store approved custom host keys, replacing the previous set. Caller commits.

    Nothing is stored when any line is invalid. ``source`` records where the operator obtained the
    material (its independent authentication is the operator's responsibility, never a first use).
    """
    from app.platform.platform_config_service import PlatformConfigService

    entries = parse_known_hosts(text)
    now = datetime.now(timezone.utc).isoformat()
    stored = [
        {
            "host": e.host,
            "port": e.port,
            "line": e.line,
            "fingerprint": e.fingerprint,
            "source": source,
            "added_at": now,
        }
        for e in entries
    ]
    await PlatformConfigService.set(session, CUSTOM_CONFIG_KEY, json.dumps({"version": 1, "entries": stored}))
    return stored


async def trust_for_repositories(session, repo_urls: list[str]) -> GitTrust:
    return trust_for(repo_urls, await load_custom_entries(session))


def write_known_hosts_lines(path: str, content: str) -> list[str]:
    """Shell lines writing ``content`` to ``path`` through a quoted heredoc (no expansion)."""
    return [
        f"cat > {path} <<'BIOAF_KNOWN_HOSTS_EOF'",
        content.strip(),
        "BIOAF_KNOWN_HOSTS_EOF",
        f"chmod 644 {path}",
    ]
