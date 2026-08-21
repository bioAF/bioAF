"""The URL-import SSRF guard, at the point where it decides to fetch or refuse.

The guard is an ALLOWLIST, not a denylist: it must prove every resolved address
is public before it returns, and any path that returns without having proved
that is a hole. It raised inside its loop over ``getaddrinfo`` results, so an
empty result skipped the body entirely and the URL was treated as safe. An empty
result means "I learned nothing about this host", which is not "this host is
fine".

That is not hypothetical. ``test_import_from_url_blocks_ssrf_to_internal_hosts``
failed on the full suite with a 202 and a background task that went on to make a
real outbound request to the address that should have been refused. Probing
every failure mode of the resolver found exactly one that produces a 202:

    getaddrinfo behaviour   guard result before this file existed
    normal resolution       blocked, 400
    EMPTY RESULT LIST       ACCEPTED, no exception
    socket.gaierror         blocked, 400
    OSError (not gaierror)  uncaught, escaped as a 500
    UnicodeError            uncaught, escaped as a 500

So these drive the resolver rather than the network. The four internal hosts in
``test_lab_documents_api.py`` stay where they are: they cover the addresses, and
these cover the ways the guard can fail to learn one.
"""

import socket
from unittest.mock import AsyncMock, patch

import pytest

from app.exceptions import ValidationError
from app.services.lab_document_upload_service import LabDocumentUploadService, _assert_public_url

INTERNAL = "http://10.0.0.5/internal"
PUBLIC = "http://8.8.8.8/policy.pdf"


def _resolver(behaviour):
    """A ``getaddrinfo`` that behaves one way for the host under test and
    normally for everything else, so patching it cannot disturb the ASGI test
    client."""
    real = socket.getaddrinfo

    def fake(host, port, *args, **kwargs):
        if str(host) == "10.0.0.5":
            if isinstance(behaviour, BaseException):
                raise behaviour
            return behaviour
        return real(host, port, *args, **kwargs)

    return fake


def test_an_empty_resolver_result_is_refused():
    """The regression that matters most. Nothing was learned about the host, so
    the guard has proved nothing and must not let the fetch happen."""
    with patch("socket.getaddrinfo", _resolver([])):
        with pytest.raises(ValidationError):
            _assert_public_url(INTERNAL)


@pytest.mark.parametrize(
    "raised",
    [
        socket.gaierror("Name or service not known"),
        # gaierror subclasses OSError; a plain OSError is the resolver failing
        # some other way, descriptor exhaustion among them.
        OSError("resolver unavailable"),
        # What getaddrinfo raises for an over-long IDNA label.
        UnicodeError("label empty or too long"),
    ],
    ids=["gaierror", "oserror", "unicodeerror"],
)
def test_a_resolver_that_raises_is_refused_rather_than_escaping(raised):
    """Each of these left the guard's own exception type behind: two escaped as
    500s, which is a server error standing in for a refusal the guard had
    already decided on."""
    with patch("socket.getaddrinfo", _resolver(raised)):
        with pytest.raises(ValidationError):
            _assert_public_url(INTERNAL)


def test_a_public_address_still_passes():
    """The guard failing closed must not mean it refuses everything: a URL that
    resolves to a public address is still fetched."""
    _assert_public_url(PUBLIC)


@pytest.mark.asyncio
async def test_import_url_returns_400_when_the_host_cannot_be_resolved(client, admin_token):
    """End to end, because the hole was visible only as a status code: the
    endpoint answered 202, wrote the import row, and queued the background task
    that made the outbound request."""
    with (
        patch("socket.getaddrinfo", _resolver([])),
        patch(
            "app.services.lab_document_upload_service.LabDocumentUploadService.run_url_import",
            new_callable=AsyncMock,
        ) as queued,
    ):
        resp = await client.post(
            "/api/lab-knowledge/documents/import-url",
            json={"url": INTERNAL, "tag_ids": []},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 400, resp.text
    # No row to poll and nothing queued: a refused URL leaves no job behind.
    queued.assert_not_called()
    status = await client.get(
        "/api/lab-knowledge/documents/url-imports/1",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status.status_code == 404, status.text


class _FakeRedirect:
    """A 302 to an internal address, which is how an external host bounces the
    fetch somewhere the first check would have refused."""

    is_redirect = True
    headers = {"location": INTERNAL}


class _FakeStream:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url):
        return _FakeStream(_FakeRedirect())


@pytest.mark.asyncio
async def test_a_redirect_onto_an_internal_address_is_refused():
    """The guard's second call site. Redirects are followed by hand so every hop
    is checked, and the hop inherits whatever the guard does: an empty resolver
    result let a public first host bounce the fetch inward."""
    with patch("httpx.AsyncClient", _FakeClient):
        with pytest.raises(ValidationError):
            await LabDocumentUploadService._fetch_url(PUBLIC)
