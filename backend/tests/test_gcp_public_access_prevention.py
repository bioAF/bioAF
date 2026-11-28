"""Every GCP bucket bioAF defines enforces public-access prevention.

Public-access prevention makes GCS refuse any future allUsers / allAuthenticatedUsers grant, so a
scientific bucket cannot become public through a mistaken IAM binding. Signed URLs still work: they
are authorized by the signing identity, not by a public grant.

Text checks of the modules (the repository's Terraform test style). A plan or apply is not run.
"""

import re
from pathlib import Path

GCP_MODULES = Path(__file__).resolve().parents[1] / "terraform" / "modules"

_RESOURCE_RE = re.compile(r'resource\s+"google_storage_bucket"\s+"([^"]+)"\s*\{')


def _bucket_blocks() -> dict[str, str]:
    blocks: dict[str, str] = {}
    for path in sorted(GCP_MODULES.rglob("*.tf")):
        text = path.read_text()
        for match in _RESOURCE_RE.finditer(text):
            depth, i = 1, match.end()
            while i < len(text) and depth > 0:
                depth += {"{": 1, "}": -1}.get(text[i], 0)
                i += 1
            blocks[f"{path.parent.name}.{match.group(1)}"] = text[match.end() : i]
    return blocks


def test_all_nine_buckets_are_covered():
    assert sorted(_bucket_blocks()) == sorted(
        [
            "foundation.terraform_state",
            "foundation.backups",
            "storage.ingest",
            "storage.raw",
            "storage.working",
            "storage.results",
            "storage.references",
            "storage.literature",
            "storage.config_backups",
        ]
    )


def test_every_bucket_enforces_public_access_prevention():
    missing = [
        name
        for name, block in _bucket_blocks().items()
        if not re.search(r'^\s*public_access_prevention\s*=\s*"enforced"\s*$', block, re.M)
    ]
    assert missing == []


def test_enforcement_keeps_uniform_access_and_versioning():
    for name, block in _bucket_blocks().items():
        assert re.search(r"uniform_bucket_level_access\s*=\s*true", block), name
        assert re.search(r"versioning\s*\{\s*enabled\s*=\s*true", block), name
        assert re.search(r"force_destroy\s*=\s*false", block), name
