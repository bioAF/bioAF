"""Webhook destination policy (01A): URL shape, address classification, operator exceptions.

The policy decides, for one organization's subscription URL and one set of resolver answers,
whether bioAF may connect. These tests exercise the pure policy; the enforcing transport is
covered in test_webhook_transport.py.
"""

from __future__ import annotations

import ipaddress
import socket

import pytest

from app.services import webhook_transport
from app.services.outbound_destination import is_public_address
from app.services.webhook_transport import (
    DestinationRefused,
    InternalDestination,
    authorize_webhook_destination,
    parse_internal_destinations,
    parse_webhook_url,
)

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"

pytestmark = pytest.mark.asyncio


# --- URL shape and canonicalization -----------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "host", "port"),
    [
        ("https://hooks.example/path?q=1", "hooks.example", 443),
        ("https://HOOKS.Example./x", "hooks.example", 443),
        ("https://hooks.example:443/x", "hooks.example", 443),
        ("https://hooks.example:8443/x", "hooks.example", 8443),
        ("https://bücher.example/x", "xn--bcher-kva.example", 443),
        ("https://[2606:2800:0220:0001::1]/x", "2606:2800:220:1::1", 443),
    ],
)
async def test_urls_are_canonicalized_once(url, host, port):
    parsed = parse_webhook_url(url)
    assert (parsed.host, parsed.port) == (host, port)
    assert parsed.origin == f"https://{parsed.host_for_origin}:{port}"


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example/x",  # plain HTTP
        "ftp://hooks.example/x",
        "hooks.example/x",  # not absolute
        "https:///x",  # no host
        "https://user:pw@hooks.example/x",  # userinfo
        "https://user@hooks.example/x",
        "https://hooks.example/x#frag",  # fragment
        "https://hooks.example:0/x",  # invalid port
        "https://hooks.example:99999/x",
        "https://hooks.example:abc/x",
        "https://[::1/x",  # malformed IPv6
        "https://hooks..example/x",  # empty label
        "https://hooks example/x",  # space in host
        "",
    ],
)
async def test_malformed_or_unsupported_urls_are_invalid(url):
    with pytest.raises(DestinationRefused) as err:
        parse_webhook_url(url)
    assert err.value.code == "invalid_destination"


# --- the shared predicate is the URL-import predicate -----------------------------------------


async def test_the_shared_predicate_is_what_url_import_uses():
    from app.services import lab_document_upload_service

    assert lab_document_upload_service._is_public_address is is_public_address


# --- default policy ---------------------------------------------------------------------------


def _resolver(answers: dict[str, list[str] | BaseException], calls: list[str] | None = None):
    async def resolve(host: str, port: int) -> list[str]:
        if calls is not None:
            calls.append(host)
        answer = answers.get(host)
        if answer is None:
            raise socket.gaierror("unknown")
        if isinstance(answer, BaseException):
            raise answer
        return list(answer)

    return resolve


async def test_public_hostname_answers_are_allowed():
    dest = await authorize_webhook_destination(
        "https://hooks.example/x",
        1,
        policy=(),
        resolver=_resolver({"hooks.example": [PUBLIC_V4, PUBLIC_V6]}),
    )
    assert [str(a) for a in dest.addresses] == [PUBLIC_V4, PUBLIC_V6]


@pytest.mark.parametrize("literal", [f"[{PUBLIC_V6}]", PUBLIC_V4])
async def test_public_literals_are_allowed_without_dns(literal):
    calls: list[str] = []
    dest = await authorize_webhook_destination(f"https://{literal}/x", 1, policy=(), resolver=_resolver({}, calls))
    assert calls == []
    assert len(dest.addresses) == 1


@pytest.mark.parametrize(
    "literal",
    [
        "10.0.0.5",
        "127.0.0.1",
        "[::1]",
        "169.254.169.254",
        "[fd00:ec2::254]",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "100.64.0.1",
        "[64:ff9b::a00:5]",
        "[::ffff:10.0.0.5]",
        "[2002:a00:5::]",
    ],
)
async def test_non_public_literals_are_denied_without_dns(literal):
    calls: list[str] = []
    with pytest.raises(DestinationRefused) as err:
        await authorize_webhook_destination(f"https://{literal}/x", 1, policy=(), resolver=_resolver({}, calls))
    assert err.value.code == "destination_denied"
    assert calls == []


@pytest.mark.parametrize(
    "answers",
    [
        [PUBLIC_V4, "10.0.0.5"],  # mixed: every answer must pass
        ["127.0.0.1"],
        ["169.254.169.254"],
        ["fd00:ec2::254"],
        ["::ffff:127.0.0.1"],
    ],
)
async def test_any_non_public_answer_denies_the_hostname(answers):
    with pytest.raises(DestinationRefused) as err:
        await authorize_webhook_destination(
            "https://hooks.example/x", 1, policy=(), resolver=_resolver({"hooks.example": answers})
        )
    assert err.value.code == "destination_denied"


@pytest.mark.parametrize("answer", [[], socket.gaierror("nope"), OSError("boom"), UnicodeError("idna")])
async def test_empty_or_failed_resolution_permits_nothing(answer):
    with pytest.raises(DestinationRefused) as err:
        await authorize_webhook_destination(
            "https://hooks.example/x", 1, policy=(), resolver=_resolver({"hooks.example": answer})
        )
    assert err.value.code == "dns_failed"


# --- operator exceptions ----------------------------------------------------------------------


EXCEPTION = [{"organization_id": 1, "origin": "https://lims.lab.example:8443", "cidrs": ["10.24.8.19/32"]}]


async def test_exception_allows_its_exact_internal_receiver():
    policy = parse_internal_destinations(EXCEPTION)
    dest = await authorize_webhook_destination(
        "https://LIMS.lab.example.:8443/hook?x=1",
        1,
        policy=policy,
        resolver=_resolver({"lims.lab.example": ["10.24.8.19"]}),
    )
    assert [str(a) for a in dest.addresses] == ["10.24.8.19"]


async def test_exception_still_allows_public_answers_under_the_default_rule():
    policy = parse_internal_destinations(EXCEPTION)
    dest = await authorize_webhook_destination(
        "https://lims.lab.example:8443/hook",
        1,
        policy=policy,
        resolver=_resolver({"lims.lab.example": [PUBLIC_V4]}),
    )
    assert [str(a) for a in dest.addresses] == [PUBLIC_V4]


@pytest.mark.parametrize(
    ("url", "org", "answers"),
    [
        ("https://lims.lab.example:8443/hook", 2, ["10.24.8.19"]),  # wrong organization
        ("https://lims.lab.example/hook", 1, ["10.24.8.19"]),  # wrong port
        ("https://other.lab.example:8443/hook", 1, ["10.24.8.19"]),  # wrong host
        ("https://lims.lab.example:8443/hook", 1, ["10.24.8.20"]),  # outside the CIDR
        ("https://lims.lab.example:8443/hook", 1, ["10.24.8.19", "127.0.0.1"]),  # mixed
    ],
)
async def test_exception_requires_org_origin_and_cidr(url, org, answers):
    policy = parse_internal_destinations(EXCEPTION)
    host = url.split("//")[1].split(":")[0].split("/")[0]
    with pytest.raises(DestinationRefused) as err:
        await authorize_webhook_destination(url, org, policy=policy, resolver=_resolver({host: answers}))
    assert err.value.code == "destination_denied"


async def test_exception_never_authorizes_plain_http():
    policy = parse_internal_destinations(EXCEPTION)
    with pytest.raises(DestinationRefused) as err:
        await authorize_webhook_destination(
            "http://lims.lab.example:8443/hook",
            1,
            policy=policy,
            resolver=_resolver({"lims.lab.example": ["10.24.8.19"]}),
        )
    assert err.value.code == "invalid_destination"


async def test_ula_host_exception_is_allowed():
    policy = parse_internal_destinations(
        [{"organization_id": 3, "origin": "https://lims6.example", "cidrs": ["fd12:3456:789a::19/128"]}]
    )
    dest = await authorize_webhook_destination(
        "https://lims6.example/x", 3, policy=policy, resolver=_resolver({"lims6.example": ["fd12:3456:789a::19"]})
    )
    assert dest.addresses == (ipaddress.ip_address("fd12:3456:789a::19"),)


@pytest.mark.parametrize(
    "entry",
    [
        {"organization_id": 1, "origin": "https://lims.example", "cidrs": ["10.0.0.0/8"]},  # subnet
        {"organization_id": 1, "origin": "https://lims.example", "cidrs": ["0.0.0.0/0"]},  # everything
        {"organization_id": 1, "origin": "https://lims.example", "cidrs": ["8.8.8.8/32"]},  # public
        {"organization_id": 1, "origin": "https://lims.example", "cidrs": ["127.0.0.1/32"]},  # loopback
        {"organization_id": 1, "origin": "https://lims.example", "cidrs": ["169.254.169.254/32"]},  # metadata
        {"organization_id": 1, "origin": "https://lims.example", "cidrs": ["fd00:ec2::254/128"]},  # AWS metadata
        {"organization_id": 1, "origin": "https://lims.example", "cidrs": ["fd00::/64"]},  # ULA subnet
        {"organization_id": 1, "origin": "https://lims.example", "cidrs": ["100.64.0.1/32"]},  # shared
        {"organization_id": 1, "origin": "https://lims.example", "cidrs": []},  # nothing
        {"organization_id": 1, "origin": "https://*.example", "cidrs": ["10.0.0.1/32"]},  # wildcard
        {"organization_id": 1, "origin": "https://lims.example/path", "cidrs": ["10.0.0.1/32"]},  # path
        {"organization_id": 1, "origin": "http://lims.example", "cidrs": ["10.0.0.1/32"]},  # http
        {"organization_id": 1, "origin": "https://lims.example", "cidrs": ["not-a-cidr"]},
        {"origin": "https://lims.example", "cidrs": ["10.0.0.1/32"]},  # no organization
        {"organization_id": 1, "origin": "https://lims.example", "cidrs": ["10.0.0.1/32"], "extra": 1},
    ],
)
async def test_overly_broad_or_malformed_exceptions_are_rejected(entry):
    with pytest.raises(ValueError):
        parse_internal_destinations([entry])


async def test_exception_policy_parses_from_json_text():
    import json

    policy = parse_internal_destinations(json.dumps(EXCEPTION))
    assert policy == (
        InternalDestination(
            organization_id=1,
            origin="https://lims.lab.example:8443",
            hosts=(ipaddress.ip_network("10.24.8.19/32"),),
        ),
    )


async def test_settings_load_the_operator_policy_and_refuse_bad_values_without_echoing_them(monkeypatch):
    import json

    from app.config import load_settings
    from app.settings_contract import SettingsError

    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    monkeypatch.setenv("BIOAF_WEBHOOK_INTERNAL_DESTINATIONS", json.dumps(EXCEPTION))
    loaded = load_settings()
    assert webhook_transport.internal_destination_policy(loaded) == parse_internal_destinations(EXCEPTION)

    secretish = "https://s3cr3t-host.example"
    monkeypatch.setenv(
        "BIOAF_WEBHOOK_INTERNAL_DESTINATIONS",
        json.dumps([{"organization_id": 1, "origin": secretish, "cidrs": ["0.0.0.0/0"]}]),
    )
    with pytest.raises(SettingsError) as err:
        load_settings()
    assert "BIOAF_WEBHOOK_INTERNAL_DESTINATIONS" in str(err.value)
    assert "s3cr3t" not in str(err.value)


async def test_default_policy_is_empty():
    from app.config import Settings

    assert Settings(compute_mode="local").webhook_internal_destinations == ()


async def test_exception_does_not_change_url_import_policy(url_network, monkeypatch):
    """An internal LIMS exception is for webhooks only; URL imports keep the public-only rule."""
    from app.config import settings
    from app.exceptions import ValidationError
    from app.services.lab_document_upload_service import _assert_public_url

    monkeypatch.setattr(settings, "webhook_internal_destinations", parse_internal_destinations(EXCEPTION))
    url_network.answers["lims.lab.example"] = ["10.24.8.19"]
    with pytest.raises(ValidationError):
        _assert_public_url("https://lims.lab.example:8443/doc.pdf")
