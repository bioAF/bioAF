"""Applier abstraction for the Settings -> Networking page.

bioAF runs on a single GCE VM behind nginx (see docs/deployment-guide.md).
TLS is terminated by nginx in the docker-compose stack using the cert files
at /etc/nginx/certs/tls.crt. HTTPS-on-443 with an HTTP-301-to-HTTPS redirect
on port 80 is set up by install.sh and is the default for every install.

Given that topology, the production applier (:class:`VmNginxApplier`):

- reads the real on-disk cert to report its status,
- raises :class:`ManualActionRequired` for cert issuance, because issuing
  a Let's Encrypt cert requires running certbot on the host (the backend
  container has no path to do that today; see follow-up work for a
  host-side cert agent),
- is a documented no-op for enforce_https and restart_services, since
  HTTPS is already enforced and restarts are an operator action.

:class:`MockNetworkingApplier` remains for tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


CERT_STATUS_NOT_REQUESTED = "not_requested"
CERT_STATUS_PROVISIONING = "provisioning"
CERT_STATUS_ACTIVE = "active"
CERT_STATUS_FAILED = "failed"

# Default location of the nginx cert inside the docker-compose stack.
# install.sh writes the self-signed cert here; certbot --webroot writes the
# Let's Encrypt cert here once an operator runs it on the host.
DEFAULT_CERT_PATH = Path("/etc/nginx/certs/tls.crt")


class NetworkingApplier(Protocol):
    async def request_certificate(self, fqdn: str) -> None:
        pass

    async def get_certificate_status(self, fqdn: str) -> str:
        pass

    async def enforce_https(self, fqdn: str, enabled: bool) -> None:
        pass

    async def restart_services(self) -> None:
        pass


@dataclass
class MockNetworkingApplier:
    """In-memory applier for tests and BIOAF_COMPUTE_MODE=local.

    A test (or local-dev session) can read ``requested_for`` to confirm
    which FQDN was sent to the cluster, and set ``status_to_return`` to
    drive the next ``get_certificate_status`` call.
    """

    requested_for: str | None = None
    status_to_return: str = CERT_STATUS_PROVISIONING
    enforce_calls: list[tuple[str, bool]] = field(default_factory=list)
    restart_count: int = 0

    async def request_certificate(self, fqdn: str) -> None:
        self.requested_for = fqdn

    async def get_certificate_status(self, fqdn: str) -> str:
        return self.status_to_return

    async def enforce_https(self, fqdn: str, enabled: bool) -> None:
        self.enforce_calls.append((fqdn, enabled))

    async def restart_services(self) -> None:
        self.restart_count += 1


class ManualActionRequired(Exception):
    """Signals that the applier cannot complete an action by itself and the
    operator must run a documented command on the host.

    The string form is operator-facing and should describe the exact action
    to take. The API layer translates this into a 501 response carrying the
    same message as the detail.
    """


@dataclass
class VmNginxApplier:
    """Production applier for the VM + nginx topology.

    Cert status is read from the nginx cert file on disk. Cert issuance is
    delegated to the operator (certbot on the host) because the backend
    container has no privileged path to run certbot, modify nginx config,
    or reload nginx by itself. HTTPS enforcement is already in place from
    install.sh's nginx config; there is no toggle to flip.
    """

    cert_path: Path = DEFAULT_CERT_PATH

    async def request_certificate(self, fqdn: str) -> None:
        raise ManualActionRequired(
            f"Automated certificate issuance is not yet available on VM installs. "
            f"On the host, install certbot and run:\n\n"
            f"    sudo certbot certonly --webroot -w /var/www/letsencrypt "
            f"-d {fqdn} --email <your-email> --agree-tos --non-interactive\n\n"
            f"Then copy fullchain.pem to docker/certs/tls.crt and privkey.pem to "
            f"docker/certs/tls.key, and run `./bioaf restart`. Once the new cert "
            f"is in place, click Refresh status on this page to pick it up."
        )

    async def get_certificate_status(self, fqdn: str) -> str:
        """Read the on-disk cert and decide whether it covers *fqdn*.

        Returns CERT_STATUS_ACTIVE only if the cert is currently valid (not
        expired, not yet expired) and either its CN or a SubjectAltName DNS
        entry matches *fqdn*. Anything else (missing file, parse failure,
        self-signed install cert, expired cert, hostname mismatch) returns
        CERT_STATUS_NOT_REQUESTED so the UI prompts the operator to install
        one rather than claiming the cert is fine.
        """
        try:
            data = self.cert_path.read_bytes()
        except OSError:
            return CERT_STATUS_NOT_REQUESTED

        from cryptography import x509
        from cryptography.x509.oid import NameOID

        try:
            cert = x509.load_pem_x509_certificate(data)
        except ValueError:
            return CERT_STATUS_NOT_REQUESTED

        now = datetime.now(timezone.utc)
        not_before = cert.not_valid_before_utc
        not_after = cert.not_valid_after_utc
        if now < not_before or now > not_after:
            return CERT_STATUS_NOT_REQUESTED

        names: set[str] = set()
        for attr in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME):
            names.add(str(attr.value).lower())
        try:
            san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            for dns in san_ext.value.get_values_for_type(x509.DNSName):
                names.add(dns.lower())
        except x509.ExtensionNotFound:
            pass

        return CERT_STATUS_ACTIVE if fqdn.lower() in names else CERT_STATUS_NOT_REQUESTED

    async def enforce_https(self, fqdn: str, enabled: bool) -> None:
        """No-op: install.sh's nginx config already redirects HTTP -> HTTPS.

        There is no in-cluster knob to flip; toggling this flag in the DB is
        a record of intent for future use, not an action.
        """
        return None

    async def restart_services(self) -> None:
        """No-op: the backend container cannot restart docker-compose services.

        Operators restart the stack with `./bioaf restart` after replacing the
        cert files. Documented in the request_certificate instructions.
        """
        return None


# Module-level singleton so the same mock state survives across requests
# within a test. Tests typically override this via FastAPI dependency
# overrides, but local-mode dev wants a stable instance.
_mock_singleton = MockNetworkingApplier()


def get_networking_applier() -> NetworkingApplier:
    """FastAPI dependency: VmNginxApplier in every real mode.

    BIOAF_COMPUTE_MODE used to gate Kubernetes vs local because the older
    design assumed a GKE Ingress topology. bioAF installs are always VM-based,
    so that branch is gone and every non-test caller gets the VM applier.
    Tests override this dependency via app.dependency_overrides.
    """
    return VmNginxApplier()


def reset_mock_applier() -> MockNetworkingApplier:
    """Reset the shared mock state between tests that share the singleton."""
    global _mock_singleton
    _mock_singleton = MockNetworkingApplier()
    return _mock_singleton
