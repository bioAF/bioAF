"""The URL-trust boundary shared by Lab Document URL import and the literature
open-access downloader.

Policy (lab_document_upload_service._assert_public_url):

- Only http(s) URLs with a host. A URL or port that cannot be parsed is a
  ValidationError, never an escaping ValueError.
- An IP literal is judged as written and never resolved.
- A hostname must resolve to at least one address, and every answer must pass.
- One address predicate for literals and answers: the address must be global
  and none of private, loopback, link-local, reserved, multicast, unspecified.
- Every request, including each redirect hop, is checked before it is made.

Recorded incident. A resolver answered ``2607:7700:0:50::a00:5`` for the
literal ``10.0.0.5``; the guard resolved the literal, checked only that answer,
found it public, and the fetch went ahead. That mechanism is reproduced here
with a controlled resolver. Why the resolver gave that answer is unproven (NAT64
synthesis is a hypothesis), so these tests do not depend on it: the fix is that
a literal is judged as written.

Known limits, deliberately not closed here: the guard resolves a hostname and
httpx resolves it again when connecting (a resolution/fetch consistency gap,
including DNS rebinding), and a network-specific translation prefix makes a
public-looking IPv6 answer reach a private IPv4 host. The observed address as a
hostname's answer is therefore still accepted; see the explicit test below.

Every test runs on the controlled network from conftest.py's ``url_network``
fixture, so none of them can make a real DNS lookup or outbound request.
"""

import socket

import pytest

from app.exceptions import ValidationError
from app.services.lab_document_upload_service import _assert_public_url

OBSERVED_ANSWER = "2607:7700:0:50::a00:5"


# --- literals --------------------------------------------------------------


def test_the_recorded_private_literal_is_refused_without_consulting_the_resolver(url_network):
    url_network.answers["10.0.0.5"] = [OBSERVED_ANSWER]

    with pytest.raises(ValidationError, match="non-public"):
        _assert_public_url("http://10.0.0.5/internal")

    assert url_network.resolved == []


@pytest.mark.parametrize(
    "literal",
    [
        "10.0.0.5",  # private
        "127.0.0.1",  # loopback
        "[::1]",  # IPv6 loopback
        "169.254.169.254",  # link-local: the cloud metadata endpoint
        "[fe80::1]",  # IPv6 link-local
        "[fd00:ec2::254]",  # IPv6 unique-local: the AWS metadata endpoint
        "0.0.0.0",  # unspecified
        "[::]",  # IPv6 unspecified
        "240.0.0.1",  # reserved
        "224.0.0.1",  # multicast, which is_global alone would accept
        "[ff02::1]",  # IPv6 multicast
        "100.64.0.1",  # shared address space, which the old predicate accepted
        "192.0.2.1",  # documentation
        "198.18.0.1",  # benchmarking
    ],
)
def test_a_non_public_literal_is_refused_without_resolving(url_network, literal):
    with pytest.raises(ValidationError, match="non-public"):
        _assert_public_url(f"http://{literal}/x")

    assert url_network.resolved == []


@pytest.mark.parametrize("literal", ["8.8.8.8", "[2606:4700:4700::1111]"])
def test_a_public_literal_is_allowed_without_resolving(url_network, literal):
    _assert_public_url(f"https://{literal}/x.pdf")

    assert url_network.resolved == []


# --- translation forms -------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "allowed"),
    [
        # The exact observed answer, as a literal: an ordinary public IPv6
        # address. No translation is inferred from its a00:5 suffix.
        (OBSERVED_ANSWER, True),
        # Well-known NAT64 prefix 64:ff9b::/96 is reserved, whichever IPv4
        # address it embeds.
        ("64:ff9b::a00:5", False),
        ("64:ff9b::808:808", False),
        # Local-use NAT64 prefix 64:ff9b:1::/48.
        ("64:ff9b:1::a00:5", False),
        # 6to4 2002::/16, whichever IPv4 address it embeds.
        ("2002:a00:5::", False),
        ("2002:808:808::", False),
        # IPv4-mapped: judged by the IPv4 address it carries.
        ("::ffff:10.0.0.5", False),
        ("::ffff:8.8.8.8", True),
    ],
)
def test_translation_forms_have_explicit_results(url_network, address, allowed):
    url = f"http://[{address}]/x"
    if allowed:
        _assert_public_url(url)
    else:
        with pytest.raises(ValidationError, match="non-public"):
            _assert_public_url(url)
    assert url_network.resolved == []


def test_the_observed_answer_for_a_hostname_is_still_allowed(url_network):
    """The documented limitation, pinned so it is a decision rather than an
    accident. Without knowing the network's translation prefix, this answer is an
    ordinary public IPv6 address, and neither the literal fix nor the predicate
    makes it safe as a hostname's answer."""
    url_network.answers["files.example"] = [OBSERVED_ANSWER]

    _assert_public_url("http://files.example/x.pdf")

    assert url_network.resolved == ["files.example"]


# --- hostnames ---------------------------------------------------------------
# These use a hostname, never a literal, so they reach the resolver: a refusal
# here cannot come from literal validation and hide a resolver-handling bug.

HOSTNAME_URL = "https://files.example/manual.pdf"


@pytest.mark.parametrize(
    "answers",
    [["93.184.216.34"], ["2606:2800:220:1:248:1893:25c8:1946"], ["93.184.216.34", "2606:4700:4700::1111"]],
    ids=["ipv4", "ipv6", "both"],
)
def test_a_hostname_with_only_public_answers_is_allowed(url_network, answers):
    url_network.answers["files.example"] = answers

    _assert_public_url(HOSTNAME_URL)

    assert url_network.resolved == ["files.example"]


@pytest.mark.parametrize(
    "bad",
    ["10.0.0.5", "127.0.0.1", "169.254.169.254", "fd00:ec2::254", "100.64.0.1", "224.0.0.1", "64:ff9b::a00:5"],
)
@pytest.mark.parametrize("position", ["first", "last"])
def test_one_non_public_answer_refuses_the_whole_hostname(url_network, bad, position):
    public = ["93.184.216.34", "2606:4700:4700::1111"]
    url_network.answers["files.example"] = [bad, *public] if position == "first" else [*public, bad]

    with pytest.raises(ValidationError, match="non-public"):
        _assert_public_url(HOSTNAME_URL)

    assert url_network.resolved == ["files.example"]


def test_an_empty_resolver_answer_for_a_hostname_is_refused(url_network):
    """A separate fail-closed case, not the cause of the recorded incident: an
    empty answer proves nothing about the host."""
    url_network.answers["files.example"] = []

    with pytest.raises(ValidationError, match="Could not resolve"):
        _assert_public_url(HOSTNAME_URL)

    assert url_network.resolved == ["files.example"]


@pytest.mark.parametrize(
    "raised",
    [
        socket.gaierror("Name or service not known"),
        OSError("resolver unavailable"),
        UnicodeError("label empty or too long"),
    ],
    ids=["gaierror", "oserror", "unicodeerror"],
)
def test_a_resolver_failure_for_a_hostname_is_refused(url_network, raised):
    url_network.answers["files.example"] = raised

    with pytest.raises(ValidationError, match="Could not resolve"):
        _assert_public_url(HOSTNAME_URL)

    assert url_network.resolved == ["files.example"]
