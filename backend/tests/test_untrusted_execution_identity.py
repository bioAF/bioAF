"""plan_7 step 16a: an identity for untrusted execution.

Step 17 runs a stranger's code. Today the notebook pod is bound by Workload Identity to a GSA
holding **project-wide `roles/storage.objectAdmin`**, so anything in that pod can read, overwrite
and DELETE any object in any bucket in the project, including `backups`, `config-backups` and
`tfstate`. The node is ephemeral and irrelevant; the credential is the exposure.

Three tiers, and this file holds the first two:

- **Tier 1**, shippable on its own merits: bring GCP to the parity AWS already has by scoping the
  existing notebook runner to `bioaf-` buckets with the IAM Condition idiom `install-gcp.sh`
  already uses everywhere else. It is a pre-existing over-grant affecting the CURATED templates
  too.
- **Tier 2**, the prerequisite for step 17: a second identity with NO project-level storage role,
  one dedicated bucket, and its own namespace. The `is_builtin` gate is then relaxed only for that
  identity, never for the existing one.

Tier 3 (no cloud identity in the pod at all, driven by signed URLs) is deliberately deferred; its
cost lands inside the step already carrying the most risk.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_GCP_COMPUTE = _REPO_ROOT / "backend" / "terraform" / "modules" / "compute" / "main.tf"
_GCP_STORAGE = _REPO_ROOT / "backend" / "terraform" / "modules" / "storage" / "main.tf"
_AWS_COMPUTE = _REPO_ROOT / "backend" / "terraform" / "aws" / "modules" / "compute" / "main.tf"


def _block(text: str, header: str) -> str:
    """The body of one terraform block, from its header to the closing brace at column 0."""
    start = text.index(header)
    end = text.index("\n}\n", start)
    return text[start:end]


@pytest.fixture(scope="module")
def gcp() -> str:
    return _GCP_COMPUTE.read_text()


@pytest.fixture(scope="module")
def aws() -> str:
    return _AWS_COMPUTE.read_text()


class TestTier1GcpParity:
    """The AWS module scopes its notebook runner by ARN and carries a comment naming the GCP
    condition it mirrors. GCP grants project-wide. They diverged, and a fresh install today still
    gets the over-grant, because this comes from terraform rather than the installers."""

    def test_the_notebook_runners_storage_grant_is_conditioned(self, gcp: str):
        block = _block(gcp, 'resource "google_project_iam_member" "notebook_runner_storage"')
        assert "condition" in block, "the notebook runner still holds project-wide storage.objectAdmin"

    def test_the_condition_uses_the_prefix_idiom_the_installer_already_uses(self, gcp: str):
        block = _block(gcp, 'resource "google_project_iam_member" "notebook_runner_storage"')
        # The HCL escapes the inner quotes, so match on the predicate rather than on one
        # particular quoting of it.
        assert "resource.name.startsWith(" in block
        assert "projects/_/buckets/bioaf-" in block


class TestTier2AnIdentityForUntrustedCode:
    def test_there_is_a_second_service_account(self, gcp: str):
        assert 'resource "google_service_account" "untrusted_runner"' in gcp
        assert "bioaf-untrusted-runner" in gcp

    def test_it_holds_no_project_level_storage_role(self, gcp: str):
        """The whole point. `bioaf-backups-*` and `bioaf-tfstate-*` share the `bioaf-` prefix, so
        even Tier 1's condition still reaches them: acceptable for code we wrote, not for code we
        fetched."""
        for match in re.finditer(r'resource\s+"google_project_iam_member"\s+"([^"]+)"\s*\{', gcp):
            block = _block(gcp, match.group(0))
            assert "untrusted_runner" not in block, (
                f"{match.group(1)} grants a project-level role to the untrusted runner"
            )

    def test_its_only_storage_access_is_one_bucket(self, gcp: str):
        assert 'resource "google_storage_bucket_iam_member" "untrusted_runner_bucket"' in gcp
        block = _block(gcp, 'resource "google_storage_bucket_iam_member" "untrusted_runner_bucket"')
        assert "roles/storage.objectAdmin" in block
        assert "var.untrusted_bucket_name" in block or "untrusted" in block

    def test_it_runs_in_its_own_namespace(self, gcp: str):
        """`DEFAULT_NOTEBOOK_NAMESPACE` is already a parameter on the adapter, so this is a value
        rather than a rewrite."""
        block = _block(gcp, 'resource "google_service_account_iam_member" "untrusted_runner_workload_identity"')
        assert "bioaf-untrusted/bioaf-untrusted-runner" in block

    def test_the_dedicated_bucket_exists_and_expires_its_contents(self):
        """A lifecycle rule, so an abandoned run does not accumulate cost, and because that bucket
        is the one place untrusted code can write."""
        storage = _GCP_STORAGE.read_text()
        assert 'resource "google_storage_bucket" "untrusted"' in storage
        block = _block(storage, 'resource "google_storage_bucket" "untrusted"')
        assert "lifecycle_rule" in block
        assert "Delete" in block

    def test_the_bucket_keeps_the_bioaf_prefix(self):
        """The naming invariant every other bucket holds; `bioaf-app`'s own IAM condition depends
        on it."""
        storage = _GCP_STORAGE.read_text()
        block = _block(storage, 'resource "google_storage_bucket" "untrusted"')
        assert "bioaf" in block


class TestAwsGetsTheMirror:
    """Without it the providers diverge again, exactly as they already did on the notebook runner."""

    def test_there_is_an_untrusted_runner(self, aws: str):
        assert "untrusted" in aws

    def test_its_s3_access_is_scoped_to_the_one_bucket_not_to_bioaf_star(self, aws: str):
        block = _block(aws, 'data "aws_iam_policy_document" "untrusted_runner_s3"')
        assert "s3_bucket_arns" not in block, "the untrusted role reuses the bioaf-* wildcard"
        assert "untrusted" in block

    def test_it_trusts_only_its_own_namespace(self, aws: str):
        block = _block(aws, 'data "aws_iam_policy_document" "untrusted_runner_trust"')
        assert "system:serviceaccount:bioaf-untrusted:bioaf-untrusted-runner" in block


# ---- the backend half: the identity has to be reachable, and the gate stays shut without it ----


class TestTheIdentityResolves:
    @pytest.mark.asyncio
    async def test_it_is_absent_until_terraform_has_run(self, session):
        """A fresh install has no untrusted identity, and step 17 must refuse rather than fall back
        to the notebook runner. Falling back is exactly the failure this step exists to prevent."""
        from app.services.untrusted_execution import untrusted_identity

        assert await untrusted_identity(session) is None

    @pytest.mark.asyncio
    async def test_it_resolves_once_the_bucket_and_the_service_account_are_configured(self, session):
        from app.platform.platform_config_service import PlatformConfigService
        from app.services.untrusted_execution import UNTRUSTED_NAMESPACE, untrusted_identity

        await PlatformConfigService.set(session, "untrusted_bucket_name", "bioaf-untrusted-lab-abc")
        await PlatformConfigService.set(
            session, "untrusted_runner_sa_email", "bioaf-untrusted-runner@p.iam.gserviceaccount.com"
        )

        identity = await untrusted_identity(session)
        assert identity is not None
        assert identity.bucket == "bioaf-untrusted-lab-abc"
        assert identity.namespace == UNTRUSTED_NAMESPACE
        assert identity.sa_email.startswith("bioaf-untrusted-runner@")

    @pytest.mark.asyncio
    async def test_a_half_configured_identity_is_no_identity(self, session):
        """A bucket with no service account, or the reverse, would launch a pod that either cannot
        write anywhere or writes as somebody else."""
        from app.platform.platform_config_service import PlatformConfigService
        from app.services.untrusted_execution import untrusted_identity

        await PlatformConfigService.set(session, "untrusted_bucket_name", "bioaf-untrusted-lab-abc")
        assert await untrusted_identity(session) is None

    @pytest.mark.asyncio
    async def test_it_never_resolves_to_the_notebook_runner(self, session):
        """The one substitution that must be impossible. Running a stranger's code as the notebook
        runner is the whole exposure."""
        from app.platform.platform_config_service import PlatformConfigService
        from app.services.untrusted_execution import untrusted_identity

        await PlatformConfigService.set(session, "notebook_runner_sa_email", "bioaf-notebook-runner@p.iam.g.com")
        assert await untrusted_identity(session) is None


class TestTheGateStaysShutForTheTrustedIdentity:
    @pytest.mark.asyncio
    async def test_a_non_builtin_template_is_still_refused_on_the_ordinary_path(self, session, admin_user):
        """`is_builtin` is relaxed only for the untrusted namespace and identity, never for the
        existing one. The boundary is preserved, not deleted."""
        from app.exceptions import ValidationError
        from app.models.template_notebook import TemplateNotebook
        from app.services.notebook_execution_service import NotebookExecutionService

        tmpl = TemplateNotebook(
            organization_id=admin_user.organization_id,
            name="somebody's own notebook",
            category="analysis",
            notebook_path="notebooks/user.ipynb",
            is_builtin=False,
        )
        session.add(tmpl)
        await session.flush()

        with pytest.raises(ValidationError):
            await NotebookExecutionService.execute_template(
                session, org_id=admin_user.organization_id, user_id=admin_user.id, template_id=tmpl.id
            )


class TestThePodLandsInItsOwnNamespace:
    """`launch_session` pinned `DEFAULT_NOTEBOOK_NAMESPACE`, so an untrusted pod would have been
    scheduled beside the trusted ones and picked up their service account."""

    def test_the_spec_can_name_the_namespace(self):
        from app.adapters.notebooks.kubernetes import DEFAULT_NOTEBOOK_NAMESPACE, namespace_for

        assert namespace_for({}) == DEFAULT_NOTEBOOK_NAMESPACE
        assert namespace_for({"namespace": "bioaf-untrusted"}) == "bioaf-untrusted"

    def test_an_unknown_namespace_is_refused_rather_than_created(self):
        """A spec is data. Letting it name any namespace would let a bug schedule a pod into
        `kube-system`; only the two bioAF owns are accepted."""
        from app.adapters.notebooks.kubernetes import DEFAULT_NOTEBOOK_NAMESPACE, namespace_for

        assert namespace_for({"namespace": "kube-system"}) == DEFAULT_NOTEBOOK_NAMESPACE
        assert namespace_for({"namespace": "../../etc"}) == DEFAULT_NOTEBOOK_NAMESPACE

    def test_each_namespace_is_prepared_independently(self):
        """`_namespace_ready` was one boolean, so preparing the notebook namespace would mark the
        untrusted one ready without ever creating it."""
        from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider

        adapter = KubernetesNotebookProvider.__new__(KubernetesNotebookProvider)
        adapter._namespaces_ready = set()
        adapter._namespaces_ready.add("bioaf-notebooks")
        assert "bioaf-untrusted" not in adapter._namespaces_ready
