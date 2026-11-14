"""Config-backups lifecycle: obsolete export versions expire; nothing live or unrelated does (plan 24 §5)."""

from __future__ import annotations

import re
from pathlib import Path

TERRAFORM = Path(__file__).resolve().parent.parent / "terraform"
GCP = (TERRAFORM / "modules" / "storage" / "main.tf").read_text()
AWS = (TERRAFORM / "aws" / "modules" / "storage" / "main.tf").read_text()


def _block(source: str, header: str) -> str:
    """The brace-balanced body that follows ``header``."""
    start = source.index(header)
    depth = 0
    for i in range(source.index("{", start), len(source)):
        depth += {"{": 1, "}": -1}.get(source[i], 0)
        if depth == 0:
            return source[start : i + 1]
    raise AssertionError(f"unbalanced block after {header!r}")


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"#[^\n]*", "", text))


def test_gcp_expires_only_noncurrent_export_versions_after_a_day():
    bucket = _squash(_block(GCP, 'resource "google_storage_bucket" "config_backups"'))

    rules = re.findall(r"lifecycle_rule \{.*?action \{[^}]*\} \}", bucket)
    assert len(rules) == 1, "the config-backups bucket must have exactly the export rule"
    [rule] = rules
    assert 'matches_prefix = ["exports/"]' in rule
    assert 'with_state = "ARCHIVED"' in rule  # noncurrent versions only; live objects are the app's
    assert "days_since_noncurrent_time = 1" in rule
    assert 'type = "Delete"' in rule
    assert "age" not in rule
    assert "versioning { enabled = true }" in bucket


def test_aws_expires_only_noncurrent_export_versions_and_their_delete_markers():
    config = _squash(_block(AWS, 'resource "aws_s3_bucket_lifecycle_configuration" "config_backups"'))

    assert 'bucket = aws_s3_bucket.store["config_backups"].id' in config
    assert config.count("rule {") == 1
    assert 'filter { prefix = "exports/" }' in config
    assert "noncurrent_version_expiration { noncurrent_days = 1 }" in config
    assert "expiration { expired_object_delete_marker = true }" in config
    assert not re.search(r"expiration \{[^}]*\bdays\b", config.replace("noncurrent_days", ""))


def test_no_other_bucket_gained_an_export_rule():
    assert _squash(GCP).count('matches_prefix = ["exports/"]') == 1
    assert _squash(AWS).count('prefix = "exports/"') == 1
