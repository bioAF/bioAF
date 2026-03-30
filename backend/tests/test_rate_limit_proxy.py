from app.middleware.rate_limit import get_client_ip


class FakeClient:
    def __init__(self, host: str):
        self.host = host


class FakeRequest:
    def __init__(self, client_host: str, headers: dict[str, str] | None = None):
        self.client = FakeClient(client_host)
        self.headers = headers or {}


class TestGetClientIp:
    """get_client_ip should resolve the real client IP behind trusted proxies."""

    def test_direct_connection_no_forwarded_header(self):
        """Without X-Forwarded-For, return the socket address."""
        request = FakeRequest("203.0.113.50")
        assert get_client_ip(request) == "203.0.113.50"

    def test_untrusted_proxy_ignores_forwarded_header(self):
        """When the direct connection is not a trusted proxy, ignore the header."""
        request = FakeRequest(
            "198.51.100.1",
            {"x-forwarded-for": "203.0.113.50"},
        )
        assert get_client_ip(request) == "198.51.100.1"

    def test_trusted_proxy_uses_forwarded_for(self):
        """When the direct connection is a trusted proxy, use X-Forwarded-For."""
        request = FakeRequest(
            "172.18.0.2",  # Docker bridge network, trusted by default
            {"x-forwarded-for": "203.0.113.50"},
        )
        assert get_client_ip(request) == "203.0.113.50"

    def test_trusted_proxy_chained_forwarded_for(self):
        """With multiple proxies, use the rightmost untrusted IP."""
        request = FakeRequest(
            "172.18.0.2",
            {"x-forwarded-for": "203.0.113.50, 10.0.0.1, 172.18.0.3"},
        )
        # 172.18.0.3 and 10.0.0.1 are trusted; 203.0.113.50 is the real client
        assert get_client_ip(request) == "203.0.113.50"

    def test_localhost_is_trusted(self):
        """127.0.0.1 connections should use X-Forwarded-For."""
        request = FakeRequest(
            "127.0.0.1",
            {"x-forwarded-for": "198.51.100.99"},
        )
        assert get_client_ip(request) == "198.51.100.99"

    def test_trusted_proxy_no_forwarded_header_falls_back(self):
        """Trusted proxy without X-Forwarded-For falls back to socket address."""
        request = FakeRequest("172.18.0.2")
        assert get_client_ip(request) == "172.18.0.2"

    def test_all_forwarded_ips_trusted_returns_leftmost(self):
        """If every IP in the chain is trusted, return the leftmost."""
        request = FakeRequest(
            "172.18.0.2",
            {"x-forwarded-for": "10.0.0.1, 172.16.0.5"},
        )
        assert get_client_ip(request) == "10.0.0.1"

    def test_no_client_returns_unknown(self):
        """When request.client is None, return 'unknown'."""
        request = FakeRequest("0.0.0.0")
        request.client = None  # type: ignore[assignment]
        assert get_client_ip(request) == "unknown"

    def test_custom_trusted_cidrs(self):
        """Caller can supply custom trusted CIDRs."""
        request = FakeRequest(
            "198.51.100.1",
            {"x-forwarded-for": "203.0.113.50"},
        )
        # 198.51.100.1 is not trusted by default, so header is ignored
        assert get_client_ip(request) == "198.51.100.1"
        # But it is if we pass a custom list that includes it
        assert get_client_ip(request, trusted_cidrs=["198.51.100.0/24"]) == "203.0.113.50"
