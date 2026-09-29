"""Which host a URL is for, for the test doubles that dispatch on it.

A fake fetcher routes on the address it was asked for, and it was doing that with ``"host" in url``.
Read as security code that is a host check anyone can spoof, and CodeQL says so
(``py/incomplete-url-substring-sanitization``): ``https://elifesciences.org.example.net/x`` contains
``elifesciences.org`` and ``https://x/?q=github.com`` contains ``github.com``.

Nothing here is reachable from the application, so nothing was exploitable. It is still the wrong
comparison, and a double that routes on a substring is a double that can answer for an address the
real fetcher would treat as somewhere else. This parses the host and compares it.
"""

from __future__ import annotations

from urllib.parse import urlparse


def host_is(url: str, *hosts: str) -> bool:
    """Whether ``url``'s host is exactly one of ``hosts``.

    Exact, not a suffix: these doubles distinguish an article page on
    ``elifesciences.org`` from its members on ``cdn.elifesciences.org``, and a
    subdomain rule would answer the article's page for both. A double that needs a
    subdomain names it.
    """
    host = (urlparse(str(url or "")).hostname or "").lower()
    return host in {expected.lower() for expected in hosts}
