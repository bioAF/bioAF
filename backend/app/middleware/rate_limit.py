import ipaddress
import time
from collections import defaultdict

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import settings

# Per-path rate limit configuration (requests per minute)
RATE_LIMITS: dict[str, int] = {
    "/api/auth/login": settings.rate_limit_login,
    "/api/auth/verify-email": settings.rate_limit_verify,
    "/api/auth/request-reset": settings.rate_limit_reset,
    "/api/auth/reset-password": settings.rate_limit_verify,
}

# Default trusted proxy networks (private ranges + loopback)
_DEFAULT_TRUSTED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network(cidr.strip()) for cidr in settings.trusted_proxy_cidrs.split(",") if cidr.strip()
]


def _parse_trusted_networks(
    cidrs: list[str] | None,
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    if cidrs is None:
        return _DEFAULT_TRUSTED_NETWORKS
    return [ipaddress.ip_network(c.strip()) for c in cidrs if c.strip()]


def _is_trusted(ip_str: str, networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str.strip())
    except ValueError:
        return False
    return any(addr in net for net in networks)


def get_client_ip(request: Request, *, trusted_cidrs: list[str] | None = None) -> str:
    """Resolve the real client IP, respecting X-Forwarded-For from trusted proxies.

    Walks the X-Forwarded-For chain from right to left, skipping trusted proxy
    IPs, and returns the first untrusted address (the real client). If the
    direct connection is not from a trusted proxy, the header is ignored to
    prevent spoofing.
    """
    if request.client is None:
        return "unknown"

    direct_ip = request.client.host
    networks = _parse_trusted_networks(trusted_cidrs)

    if not _is_trusted(direct_ip, networks):
        return direct_ip

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return direct_ip

    # Walk right-to-left, skipping trusted proxies
    ips = [ip.strip() for ip in forwarded.split(",")]
    for ip in reversed(ips):
        if not _is_trusted(ip, networks):
            return ip

    # Every IP in the chain is trusted; return the leftmost
    return ips[0]


# Module-level storage so tests can call rate_limit_requests.clear()
rate_limit_requests: dict[tuple[str, str], list[float]] = defaultdict(list)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        limit = RATE_LIMITS.get(path)

        if limit is not None and request.method == "POST":
            client_ip = get_client_ip(request)
            key = (path, client_ip)
            now = time.time()
            window_start = now - 60  # 1-minute sliding window

            # Clean old entries
            rate_limit_requests[key] = [t for t in rate_limit_requests[key] if t > window_start]

            if len(rate_limit_requests[key]) >= limit:
                return JSONResponse(
                    status_code=429,
                    content={"detail": f"Too many requests. Limit: {limit} per minute."},
                )

            rate_limit_requests[key].append(now)

        return await call_next(request)
