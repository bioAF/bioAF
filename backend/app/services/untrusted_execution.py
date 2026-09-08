"""plan_7 step 16a: the identity code fetched from a paper's authors runs as.

``notebook_execution_service`` carries an explicit trust boundary, in its docstring and enforced in
code: only curated, platform-owned templates run headless, because the pod executes as the
``notebook_runner`` service account. That account holds project-wide ``roles/storage.objectAdmin``,
so anything in that pod can read, overwrite and DELETE any object in any bucket in the project,
including ``backups``, ``config-backups`` and ``tfstate``.

**The exposure is the credential, not the node.** The node is ephemeral and that is fine; Workload
Identity means the pod does not use the node's service account at all.

So untrusted execution gets its own identity: no project-level role, one bucket-level binding on one
dedicated bucket, in its own namespace. The ``is_builtin`` gate is then relaxed **only** for this
identity. The boundary is preserved, not deleted.

**There is no fallback.** When the untrusted identity is not configured, step 17 refuses to run
rather than borrowing the notebook runner's, because borrowing it is precisely the thing this exists
to prevent. Terraform provisions it (``backend/terraform/modules/compute``), and the installers do
not: ``grep -ci notebook`` is 0 in both.

**The honest gap, stated rather than implied.** ``objectAdmin`` on a shared untrusted bucket means
study A's code can reach study B's prefix. For a single-tenant lab instance that is likely
acceptable; for a shared deployment it is not, and that is the point at which Tier 3 (no cloud
identity in the pod at all, driven by pre-signed URLs) stops being optional.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.platform_config_service import PlatformConfigService

logger = logging.getLogger("bioaf.untrusted_execution")

# Its own namespace, so a pod running a stranger's code cannot be scheduled beside one running ours
# and cannot pick up the other namespace's service account by default.
UNTRUSTED_NAMESPACE = "bioaf-untrusted"
UNTRUSTED_KSA = "bioaf-untrusted-runner"

# What terraform writes and this reads. Both are required: a bucket with no service account launches
# a pod that cannot write anywhere, and a service account with no bucket launches one whose only
# grant points at nothing.
BUCKET_KEY = "untrusted_bucket_name"
SA_EMAIL_KEY = "untrusted_runner_sa_email"


@dataclass(frozen=True)
class UntrustedIdentity:
    """Where untrusted code runs, and the only storage it can reach."""

    namespace: str
    ksa: str
    sa_email: str
    bucket: str

    def prefix_for(self, study_id: int) -> str:
        """The study's own prefix in the untrusted bucket. Deleted after the run."""
        return f"study-{study_id}"


async def untrusted_identity(session: AsyncSession) -> UntrustedIdentity | None:
    """The configured untrusted-execution identity, or None when this install has none.

    None means step 17 cannot run here. It deliberately does NOT fall back to the notebook runner:
    a fallback would hand a stranger's code the credential that can delete this project's backups,
    which is the entire reason this identity exists.
    """
    config = await PlatformConfigService.get_many(session, [BUCKET_KEY, SA_EMAIL_KEY])
    bucket = (config.get(BUCKET_KEY) or "").strip()
    sa_email = (config.get(SA_EMAIL_KEY) or "").strip()
    if not bucket or bucket == "null" or not sa_email or sa_email == "null":
        logger.info("no untrusted-execution identity is configured on this install")
        return None
    return UntrustedIdentity(namespace=UNTRUSTED_NAMESPACE, ksa=UNTRUSTED_KSA, sa_email=sa_email, bucket=bucket)


UNCONFIGURED_MESSAGE = (
    "This bioAF install has no isolated identity for running code fetched from a paper, so the "
    "authors' own code cannot be executed here. An administrator can provision it by applying the "
    "compute infrastructure update."
)
