"""Evidence for the two scanner findings plan 04D closes without new controls (R04, R22).

- S3 public access: every bucket the AWS modules create has a public access block with all four
  flags on, attached to the same bucket set, so a bucket policy or ACL cannot make data public.
- EKS secrets encryption: the cluster runs a Kubernetes version (1.28 or later) on which AWS applies
  default envelope encryption to all Kubernetes API data with an AWS-owned KMS key. No
  customer-managed key is added here; that would be separate scope. A live cluster has not been
  inspected (see the plan's status record).

Text checks of the modules. They guard against a regression; they are not a live observation.
"""

import re
from pathlib import Path

AWS_MODULES = Path(__file__).resolve().parents[1] / "terraform" / "aws" / "modules"
FLAGS = ("block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets")


def _blocks(text: str, resource_type: str) -> dict[str, str]:
    out = {}
    for match in re.finditer(rf'resource "{resource_type}" "([^"]+)" \{{', text):
        depth, i = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        out[match.group(1)] = text[match.end() : i]
    return out


def test_every_s3_bucket_has_all_four_public_access_blocks():
    checked = 0
    for main in sorted(AWS_MODULES.glob("*/main.tf")):
        text = main.read_text()
        buckets = _blocks(text, "aws_s3_bucket")
        blocks = _blocks(text, "aws_s3_bucket_public_access_block")
        for name, bucket in buckets.items():
            fan_out = re.search(r"for_each\s*=\s*(\S+)", bucket)
            matching = [
                b
                for b in blocks.values()
                if f"aws_s3_bucket.{name}" in b
                and (not fan_out or f"for_each                = {fan_out.group(1)}" in b)
            ]
            assert matching, f"{main.parent.name}: aws_s3_bucket.{name} has no public access block"
            for flag in FLAGS:
                assert re.search(rf"{flag}\s*=\s*true", matching[0]), f"{main.parent.name}.{name}: {flag}"
            checked += 1
    assert checked >= 2


def test_the_cluster_version_receives_default_envelope_encryption():
    variables = (AWS_MODULES / "compute" / "variables.tf").read_text()
    default = re.search(r'variable "kubernetes_version" \{[^}]*default\s*=\s*"(\d+)\.(\d+)"', variables, re.S)
    major, minor = int(default.group(1)), int(default.group(2))
    assert (major, minor) >= (1, 28)
