"""Archive member names and attachment headers for data exports."""

from __future__ import annotations

import uuid
from urllib.parse import unquote

import pytest

from app.api.streaming_responses import attachment_disposition
from app.services.data_export import naming

U1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
U2 = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("reads.fastq.gz", "reads.fastq.gz"),
        ("a b/c\\d", "a_b_c_d"),
        ("../../etc/passwd", "etc_passwd"),
        ("C:evil.txt", "C_evil.txt"),
        ("..", "fallback"),
        (".", "fallback"),
        ("", "fallback"),
        ("///", "fallback"),
        ("\x00\x1f", "fallback"),
        ('quote"semi;comma,', "quote_semi_comma"),
        ("实验", "实验"),
        (".hidden", "hidden"),
    ],
)
def test_safe_component_never_yields_a_separator_traversal_or_empty_name(raw, expected):
    assert naming.safe_component(raw, fallback="fallback") == expected


def test_a_long_name_is_shortened_to_the_byte_bound_and_keeps_its_extension():
    name = "x" * 400 + ".fastq.gz"

    out = naming.safe_component(name, fallback="file")

    assert len(out.encode("utf-8")) <= naming.MAX_COMPONENT_BYTES
    assert out.endswith(".fastq.gz")


def test_shortening_multibyte_text_never_splits_a_character():
    out = naming.safe_component("实" * 200 + ".txt", fallback="file")

    out.encode("utf-8").decode("utf-8")
    assert len(out.encode("utf-8")) <= naming.MAX_COMPONENT_BYTES
    assert out.endswith(".txt")


def test_roots_carry_the_full_uuid_and_a_readable_name():
    assert naming.experiment_root(U1, "My Exp") == f"experiment_{U1}_My_Exp"
    assert naming.project_root(U2, "实验") == f"project_{U2}_实验"


def test_a_root_with_an_unusable_name_is_still_unique_by_uuid():
    assert naming.experiment_root(U1, "///") == f"experiment_{U1}"


def test_a_long_root_name_keeps_the_uuid():
    root = naming.experiment_root(U1, "n" * 500)

    assert root.startswith(f"experiment_{U1}_")
    assert len(root.encode("utf-8")) <= naming.MAX_COMPONENT_BYTES


def test_data_members_are_unique_per_file_even_for_the_same_basename():
    a = naming.data_member("results", U1, "counts.csv")
    b = naming.data_member("results", U2, "counts.csv")

    assert a == f"results/{U1}/counts.csv"
    assert b == f"results/{U2}/counts.csv"


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a\\b", "C:", "x\x00", "x\n", "y" * 129])
def test_join_rejects_an_unsafe_component(bad):
    with pytest.raises(naming.UnsafeMemberName):
        naming.join("root", bad)


def test_join_rejects_a_full_name_over_the_byte_bound():
    parts = ["a" * 128] * 4 + ["b"]

    with pytest.raises(naming.UnsafeMemberName):
        naming.join(*parts)


def test_member_registry_refuses_a_duplicate_including_by_case():
    names = naming.MemberNames()
    names.claim("root/README.txt")

    with pytest.raises(naming.UnsafeMemberName):
        names.claim("root/README.txt")
    with pytest.raises(naming.UnsafeMemberName):
        names.claim("ROOT/readme.TXT")


def test_member_registry_refuses_a_file_where_a_directory_already_is():
    names = naming.MemberNames()
    names.claim("root/results/x")

    with pytest.raises(naming.UnsafeMemberName):
        names.claim("root/results")


# -- Content-Disposition ------------------------------------------------------------------------


def _params(header: str) -> dict[str, str]:
    kind, *rest = [p.strip() for p in header.split(";")]
    assert kind == "attachment"
    return dict(p.split("=", 1) for p in rest)


@pytest.mark.parametrize(
    "name",
    ["export.zip", "实验_export.zip", 'a"b\\c.zip', "line\r\nbreak.zip", "semi;colon,comma.zip", "a/b.zip", ""],
)
def test_attachment_header_is_latin1_safe_with_a_quoted_fallback_and_utf8_name(name):
    header = attachment_disposition(name)

    header.encode("latin-1")
    params = _params(header)
    fallback = params["filename"]
    assert fallback.startswith('"') and fallback.endswith('"')
    inner = fallback[1:-1]
    assert inner and all(32 < ord(c) < 127 for c in inner)
    assert not set(inner) & set('"\\/;,')
    assert params["filename*"].startswith("UTF-8''")
    decoded = unquote(params["filename*"][len("UTF-8''") :])
    assert decoded
    assert not any(c in decoded for c in '/\\"\r\n\x00')


def test_attachment_header_keeps_a_unicode_name_in_the_extended_parameter():
    params = _params(attachment_disposition("实验_export.zip"))

    assert unquote(params["filename*"][len("UTF-8''") :]) == "实验_export.zip"
    assert params["filename"].endswith('.zip"')
