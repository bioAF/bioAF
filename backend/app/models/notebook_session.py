from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.types import EncryptedString


class ComputeSession(Base):
    __tablename__ = "compute_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    session_type: Mapped[str] = mapped_column(String(20), nullable=False)
    experiment_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)
    slurm_job_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resource_profile: Mapped[str] = mapped_column(String(50), nullable=False)
    cpu_cores: Mapped[int] = mapped_column(Integer, nullable=False)
    memory_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    idle_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    proxy_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    k8s_pod_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    k8s_namespace: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Backend-neutral compute handle + provider-specific detail (BAL rework,
    # Phase 4); mirrors pipeline_run. compute_job_ref is the opaque session
    # handle; provider_metadata holds backend specifics for disclosure.
    compute_job_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    access_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    gcs_home_prefix: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # New columns for work node support (ADR-034)
    environment_version_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("environment_versions.id"), nullable=True
    )
    machine_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    data_mount_paths: Mapped[list | None] = mapped_column(JSON, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_token: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    git_branch_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    git_commit_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gcs_output_prefix: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # GCE VM columns (ADR-043)
    gce_instance_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gce_zone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    gce_project_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    github_repo_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)

    failure_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String, nullable=True)
    requested_disk_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Work node trust (plan 03A). The backend sync key is generated and committed before the
    # provider launch call so any worker can save outputs after a restart; the host public key is
    # the only server identity the backend accepts for this node. Both are bound to the immutable
    # provider instance id and the launch attempt label written on the instance.
    # node_trust_version is NULL for nodes launched before durable credentials existed.
    sync_private_key: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    host_public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    launch_attempt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    node_trust_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Truthful Stop: a bounded reason code when saving or deletion is still owed
    # (save_failed | delete_unconfirmed | state_unknown), the attempts made, and when the current
    # stop claimed the node so a concurrent Stop or cleanup does not run twice.
    stop_failure: Mapped[str | None] = mapped_column(String(30), nullable=True)
    stop_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    stop_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Work Node login (plan 03B): the user's key fingerprint installed at launch (None: no key, so
    # password login only), and the Work Node SSH policy revision the node was launched with. Running
    # nodes keep their launch-time policy; a later range change applies to later launches.
    login_key_fingerprint: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ssh_policy_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The backend sync route this node was provisioned for ("public" or "private-v1").
    sync_route: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Work Node reports (plan 03.5D). heartbeat_at is the last successful report; these record the
    # latest attempt (also the per-node claim) and why it failed (node_ssh.REPORT_FAILURES), if it did.
    report_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    report_failure: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Protected notebook access (plan 03.5A): the IDE's own per-session secret (Jupyter token), kept
    # encrypted here and in the session's Kubernetes Secret; added upstream by the gateway only.
    notebook_access_secret: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)

    user = relationship("User")
    organization = relationship("Organization")
    experiment = relationship("Experiment")
    project = relationship("Project")
    environment_version = relationship("EnvironmentVersion")
    accessed_files = relationship("NotebookSessionFile", foreign_keys="NotebookSessionFile.session_id")

    @property
    def provider_namespace(self) -> str | None:
        """Backend namespace from provider_metadata (BAL Phase 4), or None.

        Callers apply their own default (e.g. ``or "bioaf-notebooks"``)."""
        return (self.provider_metadata or {}).get("namespace")


# Backwards-compatible alias for existing imports
NotebookSession = ComputeSession
