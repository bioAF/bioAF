"""How Jupyter authenticates requests from the protected notebook gateway (plan 03.5A).

The gateway reaches Jupyter through the Kubernetes API server's Pod proxy, which strips the
``Authorization`` header after authenticating the gateway itself. Jupyter's own token therefore
travels in a bioAF request header that the proxy forwards, never in a URL (API server audit logs
record URLs). Each launch puts this config in the session's Pod-owned Secret and mounts it on
``JUPYTER_CONFIG_PATH``, which Jupyter merges with the image's own configuration, so any image with
Jupyter Server 2 or later honors the header without being rebuilt.

The header is only a carrier: the value must still equal the session's token, and Jupyter's usual
token sources keep working.
"""

from __future__ import annotations

GATEWAY_TOKEN_HEADER = "X-Bioaf-Notebook-Token"
JUPYTER_CONFIG_DIR = "/etc/bioaf-jupyter"
# The Secret key holding the config; mounted as jupyter_server_config.py.
JUPYTER_CONFIG_KEY = "jupyter-server-config"

JUPYTER_GATEWAY_CONFIG = f'''# bioAF protected notebook gateway (plan 03.5A): accept the session token from the gateway's header.
from jupyter_server.auth.identity import IdentityProvider


class BioafGatewayIdentityProvider(IdentityProvider):
    def get_token(self, handler):
        return handler.request.headers.get("{GATEWAY_TOKEN_HEADER}") or super().get_token(handler)


c.ServerApp.identity_provider_class = BioafGatewayIdentityProvider  # noqa: F821
'''
