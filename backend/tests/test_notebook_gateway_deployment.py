"""Plan 03.5A: how the notebook gateway is deployed and how it reaches a Pod.

The gateway is its own process (no bioAF API route, no database upgrade) behind its own nginx TLS
server on 8443; the application's pages may post the Open form to it; both installers open 8443 like
443. The Kubernetes transport addresses only the granted Pod through the API server.
"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.notebook_gateway.transport import KubernetesPodProxyTransport
from app.services.notebook_access_grant import AccessGrant

ROOT = Path(__file__).resolve().parents[2]


def _block(conf: str, start_at: int) -> str:
    depth, i = 0, conf.index("{", start_at)
    for j in range(i, len(conf)):
        depth += {"{": 1, "}": -1}.get(conf[j], 0)
        if depth == 0:
            return conf[i : j + 1]
    raise AssertionError("unbalanced")


def _server(conf: str, listen: str) -> str:
    for match in re.finditer(r"\bserver\s*\{", conf):
        block = _block(conf, match.start())
        if re.search(rf"listen\s+{listen}\b", block):
            return block
    raise AssertionError(f"no server listening on {listen}")


@pytest.fixture(scope="module")
def nginx_conf() -> str:
    return (ROOT / "docker" / "nginx.conf").read_text()


def test_the_gateway_has_its_own_tls_server_that_proxies_only_to_the_gateway(nginx_conf):
    server = _server(nginx_conf, "8443 ssl")
    assert "ssl_certificate     /etc/nginx/certs/tls.crt;" in server
    assert re.findall(r"proxy_pass\s+(\S+);", server) == ["http://notebook_gateway"]
    assert "location /api/" not in server and "http://backend" not in server and "http://frontend" not in server
    # WebSockets (kernels, RStudio) and the browser's own host:port for Origin checks.
    for line in (
        "proxy_http_version 1.1;",
        "proxy_set_header Upgrade $http_upgrade;",
        "proxy_set_header Host $http_host;",
    ):
        assert line in server
    assert re.search(r"upstream notebook_gateway\s*\{\s*server notebook-gateway:8001;", nginx_conf)


def test_the_application_pages_may_post_the_open_form_to_the_gateway(nginx_conf):
    app_server = _server(nginx_conf, "443 ssl")
    csp = re.search(r'add_header Content-Security-Policy "([^"]+)"', app_server).group(1)
    form_action = next(d for d in csp.split(";") if d.strip().startswith("form-action"))
    assert form_action.split()[1:] == ["'self'", "https://$host:8443"]


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load((ROOT / "docker" / "docker-compose.yml").read_text())


def test_the_gateway_runs_as_its_own_process_without_the_backend_entrypoint(compose):
    gateway = compose["services"]["notebook-gateway"]
    backend = compose["services"]["backend"]
    assert gateway["image"] == backend["image"]
    command = " ".join(gateway["entrypoint"] + gateway.get("command", []))
    assert "uvicorn app.notebook_gateway.app:create_app --factory" in command
    assert "--port 8001" in command
    assert "schema_upgrade" not in command and "entrypoint.sh" not in command
    # Same database, signing and encryption settings as the backend.
    for key in ("BIOAF_DATABASE_URL", "BIOAF_JWT_SECRET_KEY", "BIOAF_ENCRYPTION_KEYS", "BIOAF_COMPUTE_MODE"):
        assert gateway["environment"][key] == backend["environment"][key]
    assert "ports" not in gateway
    assert "443:443" in compose["services"]["nginx"]["ports"] and "8443:8443" in compose["services"]["nginx"]["ports"]


def test_both_installers_open_the_gateway_port_like_https():
    gcp = (ROOT / "install-gcp.sh").read_text()
    aws = (ROOT / "install-aws.sh").read_text()
    assert "--allow=tcp:80,tcp:443,tcp:8443" in gcp
    assert 'authorize_ingress tcp 8443 "0.0.0.0/0"' in aws


# -- Kubernetes transport -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_kubernetes_transport_addresses_only_the_granted_pod(tmp_path):
    ca = tmp_path / "ca.crt"
    import subprocess

    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-nodes",
            "-keyout",
            str(tmp_path / "k"),
            "-out",
            str(ca),
            "-days",
            "1",
            "-subj",
            "/CN=test-ca",
        ],
        check=True,
        capture_output=True,
    )
    config = SimpleNamespace(
        host="https://10.0.0.1/", ssl_ca_cert=str(ca), api_key={"authorization": "Bearer cluster-token"}
    )
    adapter = SimpleNamespace()

    async def client():
        return SimpleNamespace(configuration=config)

    adapter._get_api_client_async = client
    transport = KubernetesPodProxyTransport(lambda: adapter)
    grant = AccessGrant(5, 3, 41, "uid-1", "bioaf-notebook-41", "bioaf-notebooks")

    jupyter = await transport.upstream(grant, "jupyter")
    rstudio = await transport.upstream(grant, "rstudio")
    prefix = "/api/v1/namespaces/bioaf-notebooks/pods/bioaf-notebook-41:8888/proxy"
    assert jupyter.http_base == "https://10.0.0.1" + prefix
    assert jupyter.ws_base == "wss://10.0.0.1" + prefix
    assert jupyter.rewrite_prefix == prefix
    assert rstudio.http_base.endswith("/pods/bioaf-notebook-41:8787/proxy")
    assert jupyter.headers == {"Authorization": "Bearer cluster-token"}
    # The cluster CA is verified; never an unverified connection.
    assert jupyter.verify is not True and jupyter.verify is not False
    assert jupyter.verify.verify_mode.name == "CERT_REQUIRED"


def test_the_backend_stages_uploaded_certificates_in_a_private_host_directory(compose):
    assert "../tls-upload:/app/tls-upload" in compose["services"]["backend"]["volumes"]
    bioaf = (ROOT / "bioaf").read_text()
    assert 'mkdir -m 700 "$SCRIPT_DIR/tls-upload"' in bioaf
    assert "/tls-upload/" in (ROOT / ".gitignore").read_text()
