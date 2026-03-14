"""Phase 17 Terraform Executor Service.

Provides real-world Terraform plan, apply, and bootstrap operations with:
- GCP credential injection from platform_config
- Real-time progress streaming via async generators
- Concurrency lock with stale run recovery
- Foundation bootstrap that creates the GCS state bucket
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.component import TerraformRun
from app.services.activity_feed_service import ActivityFeedService
from app.services.audit_service import log_action
from app.services.credential_injector import GCPCredentialInjector
from app.services.plan_parser import TerraformPlanParser

logger = logging.getLogger("bioaf.terraform_executor")

# Path inside the container / local dev where Terraform modules live
MODULES_DIR = Path("/app/terraform/modules")
STALE_RUN_THRESHOLD_MINUTES = 30

# Global asyncio lock - one Terraform operation at a time
_tf_lock = asyncio.Lock()


@dataclass
class TerraformProgressEvent:
    """A single SSE event emitted during terraform apply."""

    event_type: str  # "resource_complete" | "apply_complete" | "apply_error" | "progress"
    message: str = ""
    resource_address: str = ""
    resources_completed: int = 0
    resources_total: int = 0
    log_line: str = ""
    extra: dict = field(default_factory=dict)


class TerraformExecutor:
    """Execute Terraform operations against real GCP infrastructure."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    async def run_plan(
        session: AsyncSession,
        user_id: int,
        module_name: str,
    ) -> TerraformRun:
        """Run `terraform plan` for the given module and return the run record.

        Steps:
        1. Recover stale runs then check concurrency lock
        2. Create a TerraformRun record
        3. Read GCP config from platform_config
        4. Copy module to a temp working directory
        5. Run `terraform init` and `terraform plan -out=tfplan -json`
        6. Run `terraform show -json tfplan` to get structured plan output
        7. Parse output and update the run record
        """
        await TerraformExecutor._recover_stale_runs(session)
        await TerraformExecutor._check_no_active_run(session)

        run = TerraformRun(
            triggered_by_user_id=user_id,
            action="plan",
            module_name=module_name,
            status="planning",
        )
        session.add(run)
        await session.flush()

        config = await TerraformExecutor._read_gcp_config(session)

        try:
            work_dir = await asyncio.to_thread(TerraformExecutor._prepare_work_dir, module_name)
            TerraformExecutor._write_tfvars(work_dir, module_name, config)

            env, cleanup = await GCPCredentialInjector.build_env(config)
            try:
                await TerraformExecutor._run_init(work_dir, env, config, module_name=module_name)
                plan_json = await TerraformExecutor._run_plan_capture(work_dir, env)
                parsed = TerraformPlanParser.parse(plan_json)

                run.plan_json = parsed
                run.resources_planned = parsed["total"]
                run.status = "awaiting_confirmation"
            finally:
                await cleanup()

        except Exception as exc:
            logger.error("Terraform plan failed: %s", exc)
            run.status = "failed"
            run.error_message = str(exc)
            run.completed_at = datetime.now(timezone.utc)

        await session.flush()
        await log_action(
            session,
            user_id=user_id,
            entity_type="terraform",
            entity_id=run.id,
            action="plan",
            details={"module_name": module_name, "status": run.status},
        )
        return run

    @staticmethod
    async def run_apply(
        session: AsyncSession,
        run_id: int,
        user_id: int,
    ) -> AsyncIterator[TerraformProgressEvent]:
        """Apply an approved plan, yielding progress events.

        Yields TerraformProgressEvent objects as Terraform processes each resource.
        Updates resources_completed in the DB as resources complete.
        """
        result = await session.execute(select(TerraformRun).where(TerraformRun.id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            raise ValueError(f"Run {run_id} not found")
        if run.status != "awaiting_confirmation":
            raise ValueError(f"Run {run_id} is not awaiting confirmation (status: {run.status})")

        run.action = "apply"
        run.status = "applying"
        await session.flush()

        config = await TerraformExecutor._read_gcp_config(session)
        module_name = run.module_name or "foundation"
        resources_total = run.resources_planned or 0
        resources_completed = 0

        env, cleanup = await GCPCredentialInjector.build_env(config)
        log_lines: list[str] = []
        process = None
        try:
            work_dir = await asyncio.to_thread(TerraformExecutor._prepare_work_dir, module_name)
            TerraformExecutor._write_tfvars(work_dir, module_name, config)
            await TerraformExecutor._run_init(work_dir, env, config, module_name=module_name)

            process = await asyncio.create_subprocess_exec(
                "terraform",
                "apply",
                "-auto-approve",
                "-json",
                "-no-color",
                cwd=str(work_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**TerraformExecutor._base_env(), **env},
            )

            while True:
                try:
                    line_bytes = await asyncio.wait_for(process.stdout.readline(), timeout=15)
                except asyncio.TimeoutError:
                    yield TerraformProgressEvent(
                        event_type="heartbeat",
                        message="Terraform operation in progress...",
                        resources_completed=resources_completed,
                        resources_total=resources_total,
                    )
                    continue

                if not line_bytes:
                    break

                line = line_bytes.decode().rstrip()
                if not line:
                    continue
                log_lines.append(line)

                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type", "")

                if entry_type == "apply_complete" and "hook" in entry:
                    hook = entry["hook"]
                    resource = hook.get("resource", {})
                    addr = resource.get("addr", "")

                    # Data source reads emit apply_complete but are not
                    # counted in the plan total.  Show them as progress
                    # events instead of inflating resources_completed.
                    if addr.startswith("data."):
                        yield TerraformProgressEvent(
                            event_type="progress",
                            message=f"Read: {addr}",
                            resources_completed=resources_completed,
                            resources_total=resources_total,
                            log_line=line,
                        )
                        continue

                    resources_completed += 1
                    run.resources_completed = resources_completed
                    await session.flush()
                    yield TerraformProgressEvent(
                        event_type="resource_complete",
                        resource_address=addr,
                        message=f"Applied: {addr}",
                        resources_completed=resources_completed,
                        resources_total=resources_total,
                        log_line=line,
                    )

                elif entry_type == "apply_start" and "hook" in entry:
                    hook = entry["hook"]
                    resource = hook.get("resource", {})
                    addr = resource.get("addr", "")
                    if not addr.startswith("data."):
                        yield TerraformProgressEvent(
                            event_type="progress",
                            resource_address=addr,
                            message=entry.get("@message", f"Creating: {addr}"),
                            resources_completed=resources_completed,
                            resources_total=resources_total,
                            log_line=line,
                        )

                elif "@message" in entry:
                    yield TerraformProgressEvent(
                        event_type="progress",
                        message=entry["@message"],
                        resources_completed=resources_completed,
                        resources_total=resources_total,
                        log_line=line,
                    )

            stderr_output = ""
            if process.stderr:
                stderr_output = (await process.stderr.read()).decode()
            return_code = await process.wait()

            run.apply_log = "\n".join(log_lines)

            if return_code == 0:
                run.status = "completed"
                run.completed_at = datetime.now(timezone.utc)
                yield TerraformProgressEvent(
                    event_type="apply_complete",
                    message="Apply complete",
                    resources_completed=resources_completed,
                    resources_total=resources_total,
                )
            else:
                run.status = "failed"
                run.error_message = stderr_output or "Terraform apply failed"
                run.completed_at = datetime.now(timezone.utc)
                yield TerraformProgressEvent(
                    event_type="apply_error",
                    message=run.error_message,
                    resources_completed=resources_completed,
                    resources_total=resources_total,
                )

        except asyncio.CancelledError:
            logger.warning("Terraform apply cancelled for run %s", run_id)
            if process and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    process.kill()
            run.status = "failed"
            run.error_message = "Operation cancelled (client disconnected)"
            run.completed_at = datetime.now(timezone.utc)
            run.apply_log = "\n".join(log_lines) if log_lines else None
            await session.flush()
            return
        except Exception as exc:
            logger.error("Terraform apply failed: %s", exc)
            run.status = "failed"
            run.error_message = str(exc)
            run.completed_at = datetime.now(timezone.utc)
            yield TerraformProgressEvent(
                event_type="apply_error",
                message=str(exc),
                resources_completed=resources_completed,
                resources_total=resources_total,
            )
        finally:
            await cleanup()
            await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="terraform",
            entity_id=run.id,
            action="apply",
            details={"status": run.status, "module_name": module_name},
        )

    @staticmethod
    async def bootstrap_foundation(
        session: AsyncSession,
        user_id: int,
        org_id: int | None = None,
    ) -> AsyncIterator[TerraformProgressEvent]:
        """Bootstrap GCS state bucket via the foundation module.

        Steps:
        1. Validate GCP is configured and not already initialized
        2. Plan + apply the foundation module with local backend
        3. Run `terraform output -json` to get the state bucket name
        4. Update platform_config: terraform_state_bucket, terraform_initialized
        """
        config = await TerraformExecutor._read_gcp_config(session)

        if config.get("gcp_credentials_configured", "false") != "true":
            raise ValueError("GCP credentials are not configured. Configure GCP settings before bootstrapping.")

        if config.get("terraform_initialized", "false") == "true":
            raise ValueError("Infrastructure is already initialized. terraform_initialized = true.")

        yield TerraformProgressEvent(
            event_type="progress",
            message="Starting foundation bootstrap...",
        )

        env, cleanup = await GCPCredentialInjector.build_env(config)
        work_dir = None

        try:
            work_dir = await asyncio.to_thread(TerraformExecutor._prepare_work_dir, "foundation")
            TerraformExecutor._write_tfvars(work_dir, "foundation", config)

            yield TerraformProgressEvent(event_type="progress", message="Running terraform init...")
            await TerraformExecutor._run_init(work_dir, env, config, local_backend=True)

            yield TerraformProgressEvent(event_type="progress", message="Running terraform plan...")
            plan_json = await TerraformExecutor._run_plan_capture(work_dir, env)
            parsed = TerraformPlanParser.parse(plan_json)
            resources_total = parsed["total"]

            # Create a run record for audit trail
            run = TerraformRun(
                triggered_by_user_id=user_id,
                action="bootstrap",
                module_name="foundation",
                status="applying",
                plan_json=parsed,
                resources_planned=resources_total,
            )
            session.add(run)
            await session.flush()

            yield TerraformProgressEvent(
                event_type="progress",
                message=f"Applying {resources_total} resource(s)...",
                resources_total=resources_total,
            )

            bootstrap_process = await asyncio.create_subprocess_exec(
                "terraform",
                "apply",
                "-auto-approve",
                "-json",
                "-no-color",
                "tfplan",
                cwd=str(work_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**TerraformExecutor._base_env(), **env},
            )

            resources_completed = 0
            bootstrap_log_lines: list[str] = []
            while True:
                try:
                    line_bytes = await asyncio.wait_for(bootstrap_process.stdout.readline(), timeout=15)
                except asyncio.TimeoutError:
                    yield TerraformProgressEvent(
                        event_type="heartbeat",
                        message="Bootstrap in progress...",
                        resources_completed=resources_completed,
                        resources_total=resources_total,
                    )
                    continue

                if not line_bytes:
                    break

                line = line_bytes.decode().rstrip()
                if not line:
                    continue
                bootstrap_log_lines.append(line)

                try:
                    entry = json.loads(line)
                    if entry.get("type") == "apply_complete" and "hook" in entry:
                        resources_completed += 1
                        hook = entry["hook"]
                        resource = hook.get("resource", {})
                        run.resources_completed = resources_completed
                        yield TerraformProgressEvent(
                            event_type="resource_complete",
                            resource_address=resource.get("addr", ""),
                            message=f"Applied: {resource.get('addr', '')}",
                            resources_completed=resources_completed,
                            resources_total=resources_total,
                        )
                except json.JSONDecodeError:
                    pass

            bootstrap_stderr = ""
            if bootstrap_process.stderr:
                bootstrap_stderr = (await bootstrap_process.stderr.read()).decode()
            bootstrap_rc = await bootstrap_process.wait()

            if bootstrap_rc != 0:
                run.status = "failed"
                run.error_message = bootstrap_stderr or "Apply failed"
                run.completed_at = datetime.now(timezone.utc)
                await session.flush()
                yield TerraformProgressEvent(
                    event_type="apply_error",
                    message=run.error_message,
                )
                return

            # Get bucket name from terraform output
            output_result = await asyncio.to_thread(
                subprocess.run,
                ["terraform", "output", "-json"],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=60,
                env={**TerraformExecutor._base_env(), **env},
            )

            bucket_name = ""
            if output_result.returncode == 0:
                try:
                    outputs = json.loads(output_result.stdout)
                    bucket_name = outputs.get("state_bucket_name", {}).get("value", "")
                except (json.JSONDecodeError, AttributeError):
                    pass

            # Update platform_config
            for key, value in [
                ("terraform_initialized", "true"),
                ("terraform_state_bucket", bucket_name),
            ]:
                await session.execute(
                    text(
                        "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
                    ).bindparams(k=key, v=value)
                )

            run.status = "completed"
            run.terraform_state_url = f"gs://{bucket_name}" if bucket_name else ""
            run.completed_at = datetime.now(timezone.utc)
            await session.flush()

            await log_action(
                session,
                user_id=user_id,
                entity_type="terraform",
                entity_id=run.id,
                action="bootstrap",
                details={"status": "completed", "module_name": "foundation", "state_bucket": bucket_name},
            )

            if org_id is not None:
                await ActivityFeedService.add_event(
                    session,
                    org_id=org_id,
                    user_id=user_id,
                    event_type="infrastructure.bootstrap_completed",
                    summary=f"Terraform state bucket created: {bucket_name}",
                    entity_type="terraform",
                    entity_id=run.id,
                    metadata={"state_bucket": bucket_name},
                )

            await session.flush()

            yield TerraformProgressEvent(
                event_type="apply_complete",
                message=f"Bootstrap complete. State bucket: {bucket_name}",
                resources_completed=resources_completed,
                resources_total=resources_total,
            )

        except Exception as exc:
            logger.error("Bootstrap failed: %s", exc)
            yield TerraformProgressEvent(event_type="apply_error", message=str(exc))
        finally:
            await cleanup()
            if work_dir and work_dir.exists():
                try:
                    shutil.rmtree(str(work_dir))
                except Exception:
                    pass

    @staticmethod
    async def run_destroy(
        session: AsyncSession,
        user_id: int,
        module_name: str,
    ) -> AsyncIterator[TerraformProgressEvent]:
        """Destroy all resources for a module via `terraform destroy`.

        Creates a TerraformRun record, runs `terraform destroy -auto-approve -json`,
        and yields progress events as resources are destroyed.
        """
        await TerraformExecutor._recover_stale_runs(session)

        run = TerraformRun(
            triggered_by_user_id=user_id,
            action="destroy",
            module_name=module_name,
            status="applying",
        )
        session.add(run)
        await session.flush()

        config = await TerraformExecutor._read_gcp_config(session)
        resources_completed = 0
        resources_total = 0

        env, cleanup = await GCPCredentialInjector.build_env(config)
        log_lines: list[str] = []
        process = None
        try:
            work_dir = await asyncio.to_thread(TerraformExecutor._prepare_work_dir, module_name)
            TerraformExecutor._write_tfvars(work_dir, module_name, config)
            await TerraformExecutor._run_init(work_dir, env, config, module_name=module_name)

            process = await asyncio.create_subprocess_exec(
                "terraform",
                "destroy",
                "-auto-approve",
                "-json",
                "-no-color",
                cwd=str(work_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**TerraformExecutor._base_env(), **env},
            )

            while True:
                try:
                    line_bytes = await asyncio.wait_for(process.stdout.readline(), timeout=15)
                except asyncio.TimeoutError:
                    yield TerraformProgressEvent(
                        event_type="heartbeat",
                        message="Destroy operation in progress...",
                        resources_completed=resources_completed,
                        resources_total=resources_total,
                    )
                    continue

                if not line_bytes:
                    break

                line = line_bytes.decode().rstrip()
                if not line:
                    continue
                log_lines.append(line)

                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type", "")

                if entry_type == "planned_change":
                    resources_total += 1

                if entry_type == "apply_complete" and "hook" in entry:
                    hook = entry["hook"]
                    resource = hook.get("resource", {})
                    addr = resource.get("addr", "")

                    if addr.startswith("data."):
                        continue

                    resources_completed += 1
                    run.resources_completed = resources_completed
                    await session.flush()
                    yield TerraformProgressEvent(
                        event_type="resource_complete",
                        resource_address=addr,
                        message=f"Destroyed: {addr}",
                        resources_completed=resources_completed,
                        resources_total=resources_total,
                        log_line=line,
                    )

                elif "@message" in entry:
                    yield TerraformProgressEvent(
                        event_type="progress",
                        message=entry["@message"],
                        resources_completed=resources_completed,
                        resources_total=resources_total,
                        log_line=line,
                    )

            stderr_output = ""
            if process.stderr:
                stderr_output = (await process.stderr.read()).decode()
            return_code = await process.wait()

            run.apply_log = "\n".join(log_lines)

            if return_code == 0:
                run.status = "completed"
                run.completed_at = datetime.now(timezone.utc)
                yield TerraformProgressEvent(
                    event_type="apply_complete",
                    message="Destroy complete",
                    resources_completed=resources_completed,
                    resources_total=resources_total,
                )
            else:
                run.status = "failed"
                run.error_message = stderr_output or "Terraform destroy failed"
                run.completed_at = datetime.now(timezone.utc)
                yield TerraformProgressEvent(
                    event_type="apply_error",
                    message=run.error_message,
                    resources_completed=resources_completed,
                    resources_total=resources_total,
                )

        except asyncio.CancelledError:
            logger.warning("Terraform destroy cancelled for run %s", run.id)
            if process and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    process.kill()
            run.status = "failed"
            run.error_message = "Operation cancelled (client disconnected)"
            run.completed_at = datetime.now(timezone.utc)
            run.apply_log = "\n".join(log_lines) if log_lines else None
            await session.flush()
            return
        except Exception as exc:
            logger.error("Terraform destroy failed: %s", exc)
            run.status = "failed"
            run.error_message = str(exc)
            run.completed_at = datetime.now(timezone.utc)
            yield TerraformProgressEvent(
                event_type="apply_error",
                message=str(exc),
                resources_completed=resources_completed,
                resources_total=resources_total,
            )
        finally:
            await cleanup()
            await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="terraform",
            entity_id=run.id,
            action="destroy",
            details={"status": run.status, "module_name": module_name},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _base_env() -> dict:
        """Return a minimal safe environment for subprocesses."""
        import os

        base = {
            k: v
            for k, v in os.environ.items()
            if k
            in (
                "PATH",
                "HOME",
                "USER",
                "TMPDIR",
                "TEMP",
                "TMP",
                "SSL_CERT_FILE",
                "CURL_CA_BUNDLE",
            )
        }
        base["TF_IN_AUTOMATION"] = "1"
        return base

    @staticmethod
    def _prepare_work_dir(module_name: str) -> Path:
        """Copy module files to a fresh temp directory and return its Path."""
        src = MODULES_DIR / module_name
        tmp = Path(tempfile.mkdtemp(prefix=f"bioaf_tf_{module_name}_"))
        if src.exists():
            shutil.copytree(str(src), str(tmp), dirs_exist_ok=True)
        return tmp

    @staticmethod
    def _write_tfvars(work_dir: Path, module_name: str, config: dict) -> None:
        """Write terraform.tfvars.json into work_dir from platform_config values."""
        project_id = config.get("gcp_project_id") or ""
        region = config.get("gcp_region") or "us-central1"
        zone = config.get("gcp_zone") or f"{region}-a"
        org_slug = config.get("org_slug") or "bioaf"
        stack_uid = config.get("stack_uid") or ""
        state_bucket = config.get("terraform_state_bucket") or f"bioaf-tfstate-{project_id}"

        # Common variables shared by all modules
        tfvars: dict = {
            "project_id": project_id,
            "region": region,
        }

        if module_name == "foundation":
            tfvars["state_bucket_name"] = state_bucket
        elif module_name == "storage":
            tfvars["org_slug"] = org_slug
            tfvars["stack_uid"] = stack_uid
        elif module_name == "compute":
            tfvars["zone"] = zone
            tfvars["org_slug"] = org_slug
            tfvars["stack_uid"] = stack_uid

        tfvars_path = work_dir / "terraform.tfvars.json"
        tfvars_path.write_text(json.dumps(tfvars, indent=2))

    @staticmethod
    async def _run_init(
        work_dir: Path,
        env: dict,
        config: dict,
        local_backend: bool = False,
        module_name: str | None = None,
    ) -> None:
        """Run `terraform init` in work_dir."""
        cmd = ["terraform", "init", "-no-color", "-input=false"]
        if local_backend:
            cmd += ["-backend=false"]
        else:
            bucket = config.get("terraform_state_bucket")
            if bucket:
                cmd += [f"-backend-config=bucket={bucket}"]
            # Each module gets its own state prefix so they don't
            # clobber each other's terraform.tfstate in the bucket.
            if module_name:
                cmd += [f"-backend-config=prefix={module_name}"]

        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=300,
            env={**TerraformExecutor._base_env(), **env},
        )
        if result.returncode != 0:
            raise RuntimeError(f"terraform init failed: {result.stderr}")

    @staticmethod
    async def _run_plan_capture(work_dir: Path, env: dict) -> dict:
        """Run `terraform plan -out=tfplan` and `terraform show -json`, return parsed dict."""
        plan_result = await asyncio.to_thread(
            subprocess.run,
            ["terraform", "plan", "-out=tfplan", "-no-color", "-input=false"],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=600,
            env={**TerraformExecutor._base_env(), **env},
        )
        if plan_result.returncode != 0:
            raise RuntimeError(f"terraform plan failed: {plan_result.stderr}")

        show_result = await asyncio.to_thread(
            subprocess.run,
            ["terraform", "show", "-json", "tfplan"],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=60,
            env={**TerraformExecutor._base_env(), **env},
        )
        if show_result.returncode != 0:
            raise RuntimeError(f"terraform show failed: {show_result.stderr}")

        try:
            return json.loads(show_result.stdout)
        except json.JSONDecodeError:
            return {}

    @staticmethod
    async def _read_gcp_config(session: AsyncSession) -> dict:
        """Read GCP and terraform keys from platform_config."""
        keys = [
            "gcp_credentials_configured",
            "gcp_credential_source",
            "gcp_project_id",
            "gcp_region",
            "gcp_zone",
            "gcp_service_account_key",
            "org_slug",
            "stack_uid",
            "terraform_initialized",
            "terraform_state_bucket",
        ]
        rows = (
            await session.execute(
                text("SELECT key, value FROM platform_config WHERE key = ANY(:keys)").bindparams(keys=keys)
            )
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    @staticmethod
    async def _recover_stale_runs(session: AsyncSession) -> None:
        """Mark runs stuck in planning/applying/awaiting_confirmation for >30 min as failed."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_RUN_THRESHOLD_MINUTES)
        await session.execute(
            text("""
            UPDATE terraform_runs
            SET status = 'failed',
                error_message = 'Run timed out and was recovered',
                completed_at = now()
            WHERE status IN ('planning', 'applying', 'awaiting_confirmation')
              AND started_at < :cutoff
            """).bindparams(cutoff=cutoff)
        )
        await session.flush()

    @staticmethod
    async def _check_no_active_run(session: AsyncSession) -> None:
        """Raise ValueError if any run is currently in progress."""
        result = await session.execute(select(TerraformRun).where(TerraformRun.status.in_(["planning", "applying"])))
        active = result.scalar_one_or_none()
        if active:
            raise ValueError(f"Another Terraform operation is in progress (run {active.id})")
