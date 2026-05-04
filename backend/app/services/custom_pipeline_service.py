import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.registry import get_compute_adapter
from app.models.custom_pipeline import CustomPipeline
from app.models.custom_pipeline_variable import CustomPipelineVariable
from app.models.custom_pipeline_version import CustomPipelineVersion
from app.models.environment_version import EnvironmentVersion
from app.models.experiment import Experiment
from app.models.file import File
from app.models.github_repo import GitHubRepo
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.pipeline_run import PipelineRun
from app.models.pipeline_run_input_file import PipelineRunInputFile
from app.models.project import Project
from app.schemas.custom_pipeline import (
    CustomPipelineCreateRequest,
    CustomPipelineLaunchRequest,
    CustomPipelineUpdateRequest,
    CustomPipelineVersionCreateRequest,
)
from app.services.audit_service import log_action
from app.services.event_bus import event_bus
from app.services.event_types import PIPELINE_STARTED
from app.services.quota_service import QuotaService

logger = logging.getLogger("bioaf.custom_pipeline")


VALID_CODE_SOURCE_TYPES = ("github_repo", "code_blob", "inline")
CPU_PATTERN = re.compile(r"^\d+(\.\d+)?m?$")
MEMORY_PATTERN = re.compile(r"^\d+(Ki|Mi|Gi|Ti|Pi|Ei|K|M|G|T|P|E)?$")
PARAM_NAME_SANITIZER = re.compile(r"[^A-Z0-9]")


def _slugify(name: str) -> str:
    """Lowercase, replace spaces with hyphens, strip special chars, truncate to 100."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "-", slug)
    slug = slug.strip("-")
    return slug[:100]


def _validate_resource_strings(cpu_request: str, memory_request: str) -> None:
    if not CPU_PATTERN.match(cpu_request):
        raise ValueError(f"Invalid cpu_request: {cpu_request}")
    if not MEMORY_PATTERN.match(memory_request):
        raise ValueError(f"Invalid memory_request: {memory_request}")


class CustomPipelineService:
    @staticmethod
    async def _generate_unique_pipeline_key(session: AsyncSession, org_id: int, name: str) -> str:
        base = _slugify(name)
        if not base:
            base = "pipeline"
        candidate = base
        suffix = 2
        while True:
            pipeline_existing = await session.execute(
                select(CustomPipeline).where(
                    CustomPipeline.organization_id == org_id,
                    CustomPipeline.pipeline_key == candidate,
                )
            )
            catalog_existing = await session.execute(
                select(PipelineCatalogEntry).where(
                    PipelineCatalogEntry.organization_id == org_id,
                    PipelineCatalogEntry.pipeline_key == candidate,
                )
            )
            if pipeline_existing.scalar_one_or_none() is None and catalog_existing.scalar_one_or_none() is None:
                return candidate
            candidate = f"{base}-{suffix}"[:100]
            suffix += 1

    @staticmethod
    async def create_pipeline(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        data: CustomPipelineCreateRequest,
    ) -> CustomPipeline:
        pipeline_key = await CustomPipelineService._generate_unique_pipeline_key(session, org_id, data.name)

        pipeline = CustomPipeline(
            organization_id=org_id,
            name=data.name,
            description=data.description,
            pipeline_key=pipeline_key,
            created_by_user_id=user_id,
        )
        session.add(pipeline)
        await session.flush()

        catalog_entry = PipelineCatalogEntry(
            organization_id=org_id,
            pipeline_key=pipeline_key,
            name=data.name,
            description=data.description,
            source_type="custom",
            custom_pipeline_id=pipeline.id,
            is_builtin=False,
            enabled=True,
        )
        session.add(catalog_entry)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="custom_pipeline",
            entity_id=pipeline.id,
            action="create",
            details={"name": data.name, "pipeline_key": pipeline_key},
        )
        return pipeline

    @staticmethod
    async def list_pipelines(session: AsyncSession, org_id: int) -> list[CustomPipeline]:
        result = await session.execute(
            select(CustomPipeline)
            .where(CustomPipeline.organization_id == org_id)
            .options(selectinload(CustomPipeline.created_by))
            .order_by(CustomPipeline.name)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_pipeline(session: AsyncSession, org_id: int, pipeline_id: int) -> CustomPipeline | None:
        result = await session.execute(
            select(CustomPipeline)
            .where(
                CustomPipeline.id == pipeline_id,
                CustomPipeline.organization_id == org_id,
            )
            .options(
                selectinload(CustomPipeline.versions).selectinload(CustomPipelineVersion.variables),
                selectinload(CustomPipeline.created_by),
            )
        )
        pipeline = result.scalar_one_or_none()
        if pipeline is not None:
            pipeline.versions.sort(key=lambda v: v.version_number, reverse=True)
        return pipeline

    @staticmethod
    async def _get_pipeline_unscoped(session: AsyncSession, org_id: int, pipeline_id: int) -> CustomPipeline | None:
        result = await session.execute(
            select(CustomPipeline).where(
                CustomPipeline.id == pipeline_id,
                CustomPipeline.organization_id == org_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_pipeline(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        pipeline_id: int,
        data: CustomPipelineUpdateRequest,
    ) -> CustomPipeline:
        pipeline = await CustomPipelineService._get_pipeline_unscoped(session, org_id, pipeline_id)
        if pipeline is None:
            raise ValueError("Custom pipeline not found")

        previous = {"name": pipeline.name, "description": pipeline.description}
        updates: dict = {}

        if data.name is not None and data.name != pipeline.name:
            pipeline.name = data.name
            updates["name"] = data.name
        if data.description is not None and data.description != pipeline.description:
            pipeline.description = data.description
            updates["description"] = data.description

        if updates:
            catalog_result = await session.execute(
                select(PipelineCatalogEntry).where(
                    PipelineCatalogEntry.organization_id == org_id,
                    PipelineCatalogEntry.custom_pipeline_id == pipeline.id,
                )
            )
            catalog_entry = catalog_result.scalar_one_or_none()
            if catalog_entry is not None:
                if "name" in updates:
                    catalog_entry.name = updates["name"]
                if "description" in updates:
                    catalog_entry.description = updates["description"]

            await session.flush()

            await log_action(
                session,
                user_id=user_id,
                entity_type="custom_pipeline",
                entity_id=pipeline.id,
                action="update",
                details=updates,
                previous_value={k: previous[k] for k in updates},
            )
        return pipeline

    @staticmethod
    async def delete_pipeline(session: AsyncSession, org_id: int, user_id: int, pipeline_id: int) -> None:
        pipeline = await CustomPipelineService._get_pipeline_unscoped(session, org_id, pipeline_id)
        if pipeline is None:
            raise ValueError("Custom pipeline not found")

        catalog_result = await session.execute(
            select(PipelineCatalogEntry).where(
                PipelineCatalogEntry.organization_id == org_id,
                PipelineCatalogEntry.custom_pipeline_id == pipeline.id,
            )
        )
        catalog_entry = catalog_result.scalar_one_or_none()
        if catalog_entry is not None:
            catalog_entry.enabled = False
            await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="custom_pipeline",
            entity_id=pipeline.id,
            action="delete",
            details={"pipeline_key": pipeline.pipeline_key, "name": pipeline.name},
        )

    @staticmethod
    async def create_version(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        pipeline_id: int,
        data: CustomPipelineVersionCreateRequest,
    ) -> CustomPipelineVersion:
        pipeline = await CustomPipelineService._get_pipeline_unscoped(session, org_id, pipeline_id)
        if pipeline is None:
            raise ValueError("Custom pipeline not found")

        if data.code_source_type not in VALID_CODE_SOURCE_TYPES:
            raise ValueError(f"Invalid code_source_type: {data.code_source_type}")

        if not data.entrypoint_command or not data.entrypoint_command.strip():
            raise ValueError("entrypoint_command must not be empty")

        _validate_resource_strings(data.cpu_request, data.memory_request)

        if data.log_file_path is not None and not data.log_file_path.startswith("/outputs/"):
            raise ValueError("log_file_path must start with /outputs/")

        if data.code_source_type == "github_repo":
            if data.github_repo_id is None:
                raise ValueError("github_repo_id is required when code_source_type is 'github_repo'")
            repo_result = await session.execute(
                select(GitHubRepo).where(
                    GitHubRepo.id == data.github_repo_id,
                    GitHubRepo.organization_id == org_id,
                )
            )
            if repo_result.scalar_one_or_none() is None:
                raise ValueError("github_repo_id is not valid for this organization")
        else:
            if not data.code_content:
                raise ValueError(f"code_content is required when code_source_type is '{data.code_source_type}'")

        env_version_result = await session.execute(
            select(EnvironmentVersion).where(EnvironmentVersion.id == data.environment_version_id)
        )
        env_version = env_version_result.scalar_one_or_none()
        if env_version is None:
            raise ValueError("environment_version_id is not valid")
        if env_version.status != "ready":
            raise ValueError(f"Environment version must have status 'ready', got '{env_version.status}'")

        max_result = await session.execute(
            select(func.coalesce(func.max(CustomPipelineVersion.version_number), 0)).where(
                CustomPipelineVersion.custom_pipeline_id == pipeline.id
            )
        )
        next_version_number = (max_result.scalar() or 0) + 1

        version = CustomPipelineVersion(
            custom_pipeline_id=pipeline.id,
            version_number=next_version_number,
            code_source_type=data.code_source_type,
            github_repo_id=data.github_repo_id if data.code_source_type == "github_repo" else None,
            code_content=data.code_content if data.code_source_type != "github_repo" else None,
            entrypoint_command=data.entrypoint_command,
            environment_version_id=data.environment_version_id,
            cpu_request=data.cpu_request,
            memory_request=data.memory_request,
            log_file_path=data.log_file_path,
            version_trigger="user",
            status="active",
            created_by_user_id=user_id,
            qc_template=data.qc_template,
            qc_config_json=data.qc_config_json,
        )
        session.add(version)
        await session.flush()

        for var in data.variables:
            session.add(
                CustomPipelineVariable(
                    custom_pipeline_version_id=version.id,
                    variable_name=var.variable_name,
                    default_value=var.default_value,
                    variable_type=var.variable_type,
                    reference_category=var.reference_category,
                    is_required=var.is_required,
                )
            )
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="custom_pipeline_version",
            entity_id=version.id,
            action="create",
            details={
                "custom_pipeline_id": pipeline.id,
                "version_number": next_version_number,
                "code_source_type": data.code_source_type,
                "environment_version_id": data.environment_version_id,
            },
        )

        # Re-load with variables for return
        result = await session.execute(
            select(CustomPipelineVersion)
            .where(CustomPipelineVersion.id == version.id)
            .options(selectinload(CustomPipelineVersion.variables))
        )
        return result.scalar_one()

    @staticmethod
    async def list_versions(session: AsyncSession, org_id: int, pipeline_id: int) -> list[CustomPipelineVersion]:
        pipeline = await CustomPipelineService._get_pipeline_unscoped(session, org_id, pipeline_id)
        if pipeline is None:
            raise ValueError("Custom pipeline not found")

        result = await session.execute(
            select(CustomPipelineVersion)
            .where(CustomPipelineVersion.custom_pipeline_id == pipeline.id)
            .options(selectinload(CustomPipelineVersion.variables))
            .order_by(CustomPipelineVersion.version_number.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def handle_environment_build_completed(payload: dict[str, Any]) -> None:
        """Event subscriber: cascade new pipeline versions when a pipeline environment is rebuilt."""
        if payload.get("environment_type") != "pipeline":
            return

        environment_id = payload.get("environment_id")
        environment_version_id = payload.get("environment_version_id")
        if environment_id is None or environment_version_id is None:
            logger.warning("ENVIRONMENT_BUILD_COMPLETED missing required fields: %s", payload)
            return

        from app.database import async_session_factory

        async with async_session_factory() as session:
            await CustomPipelineService.cascade_pipeline_versions(
                session,
                environment_id=int(environment_id),
                environment_version_id=int(environment_version_id),
            )
            await session.commit()

    @staticmethod
    async def cascade_pipeline_versions(
        session: AsyncSession,
        environment_id: int,
        environment_version_id: int,
    ) -> list[CustomPipelineVersion]:
        """Create cascade versions of all pipelines whose latest active version uses this environment.

        Returns the list of newly created cascade versions.
        """
        max_active_subq = (
            select(
                CustomPipelineVersion.custom_pipeline_id.label("pipeline_id"),
                func.max(CustomPipelineVersion.version_number).label("max_v"),
            )
            .where(CustomPipelineVersion.status == "active")
            .group_by(CustomPipelineVersion.custom_pipeline_id)
            .subquery()
        )

        affected_result = await session.execute(
            select(CustomPipelineVersion)
            .join(EnvironmentVersion, CustomPipelineVersion.environment_version_id == EnvironmentVersion.id)
            .join(
                max_active_subq,
                and_(
                    CustomPipelineVersion.custom_pipeline_id == max_active_subq.c.pipeline_id,
                    CustomPipelineVersion.version_number == max_active_subq.c.max_v,
                ),
            )
            .where(
                EnvironmentVersion.environment_id == environment_id,
                CustomPipelineVersion.status == "active",
            )
            .options(selectinload(CustomPipelineVersion.variables))
        )
        affected_versions = list(affected_result.scalars().unique().all())

        if not affected_versions:
            return []

        new_env_version_result = await session.execute(
            select(EnvironmentVersion).where(EnvironmentVersion.id == environment_version_id)
        )
        new_env_version = new_env_version_result.scalar_one_or_none()
        if new_env_version is None:
            logger.warning(
                "Cascade skipped: environment_version_id=%d not found",
                environment_version_id,
            )
            return []

        cascade_user_id = new_env_version.created_by_user_id

        created: list[CustomPipelineVersion] = []
        for source in affected_versions:
            if source.environment_version_id == environment_version_id:
                continue

            max_v_result = await session.execute(
                select(func.coalesce(func.max(CustomPipelineVersion.version_number), 0)).where(
                    CustomPipelineVersion.custom_pipeline_id == source.custom_pipeline_id
                )
            )
            next_version_number = (max_v_result.scalar() or 0) + 1

            cascade_version = CustomPipelineVersion(
                custom_pipeline_id=source.custom_pipeline_id,
                version_number=next_version_number,
                code_source_type=source.code_source_type,
                github_repo_id=source.github_repo_id,
                code_content=source.code_content,
                entrypoint_command=source.entrypoint_command,
                environment_version_id=environment_version_id,
                cpu_request=source.cpu_request,
                memory_request=source.memory_request,
                log_file_path=source.log_file_path,
                version_trigger="environment_cascade",
                status="active",
                created_by_user_id=cascade_user_id,
            )
            session.add(cascade_version)
            await session.flush()

            for var in source.variables:
                session.add(
                    CustomPipelineVariable(
                        custom_pipeline_version_id=cascade_version.id,
                        variable_name=var.variable_name,
                        default_value=var.default_value,
                        variable_type=var.variable_type,
                        is_required=var.is_required,
                    )
                )
            await session.flush()

            await log_action(
                session,
                user_id=cascade_user_id,
                entity_type="custom_pipeline_version",
                entity_id=cascade_version.id,
                action="cascade_create",
                details={
                    "custom_pipeline_id": source.custom_pipeline_id,
                    "source_version_id": source.id,
                    "source_version_number": source.version_number,
                    "new_version_number": next_version_number,
                    "environment_version_id": environment_version_id,
                },
            )
            created.append(cascade_version)

        return created

    @staticmethod
    async def deprecate_version(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        pipeline_id: int,
        version_id: int,
    ) -> CustomPipelineVersion:
        pipeline = await CustomPipelineService._get_pipeline_unscoped(session, org_id, pipeline_id)
        if pipeline is None:
            raise ValueError("Custom pipeline not found")

        result = await session.execute(
            select(CustomPipelineVersion).where(
                CustomPipelineVersion.id == version_id,
                CustomPipelineVersion.custom_pipeline_id == pipeline.id,
            )
        )
        version = result.scalar_one_or_none()
        if version is None:
            raise ValueError("Custom pipeline version not found")

        previous_status = version.status
        version.status = "deprecated"
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="custom_pipeline_version",
            entity_id=version.id,
            action="deprecate",
            details={"version_number": version.version_number},
            previous_value={"status": previous_status},
        )
        return version

    # --- Launch orchestration ---

    @staticmethod
    async def launch_run(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        data: CustomPipelineLaunchRequest,
    ) -> PipelineRun:
        """Launch a custom pipeline run. Implements spec-launch-orchestration.md."""
        if data.experiment_id is None and data.project_id is None:
            raise ValueError("Either experiment_id or project_id must be provided")

        # 1. Validate pipeline version
        version_result = await session.execute(
            select(CustomPipelineVersion)
            .where(CustomPipelineVersion.id == data.version_id)
            .options(
                selectinload(CustomPipelineVersion.variables),
                selectinload(CustomPipelineVersion.custom_pipeline),
                selectinload(CustomPipelineVersion.environment_version),
                selectinload(CustomPipelineVersion.github_repo),
            )
        )
        version = version_result.scalar_one_or_none()
        if version is None:
            raise ValueError(f"Pipeline version {data.version_id} not found")

        pipeline = version.custom_pipeline
        if pipeline is None or pipeline.organization_id != org_id:
            raise ValueError("Pipeline version does not belong to this organization")

        if version.status != "active":
            raise ValueError(
                f"Pipeline version {version.version_number} has status '{version.status}', expected 'active'"
            )

        # 2. Validate environment
        env_version = version.environment_version
        if env_version is None:
            raise ValueError("Environment version not found")
        if env_version.status != "ready":
            raise ValueError(f"Environment version has status '{env_version.status}', expected 'ready'")
        image_uri = env_version.image_uri or ""

        # 3. Validate experiment / project / inputs
        experiment: Experiment | None = None
        if data.experiment_id is not None:
            exp_result = await session.execute(
                select(Experiment).where(
                    Experiment.id == data.experiment_id,
                    Experiment.organization_id == org_id,
                )
            )
            experiment = exp_result.scalar_one_or_none()
            if experiment is None:
                raise ValueError(f"Experiment {data.experiment_id} not found")

        project: Project | None = None
        if data.project_id is not None:
            proj_result = await session.execute(
                select(Project).where(
                    Project.id == data.project_id,
                    Project.organization_id == org_id,
                )
            )
            project = proj_result.scalar_one_or_none()
            if project is None:
                raise ValueError(f"Project {data.project_id} not found")

        input_files: list[File] = []
        if data.input_file_ids:
            file_result = await session.execute(select(File).where(File.id.in_(data.input_file_ids)))
            found = {f.id: f for f in file_result.scalars().all()}
            for fid in data.input_file_ids:
                f = found.get(fid)
                if not f or f.organization_id != org_id:
                    raise ValueError(f"File {fid} not found or not accessible")
                input_files.append(f)

        # 4. Validate and resolve variables
        resolved_variables = CustomPipelineService._resolve_variables(version.variables, data.variables)

        # Build name/path context for input files (for staging + manifest)
        from app.services.notebook_service import _build_relative_path, _resolve_input_file_context

        files_by_id = {f.id: f for f in input_files}
        name_cache = (
            await _resolve_input_file_context(session, files_by_id)
            if files_by_id
            else {
                "projects": {},
                "experiments": {},
                "pipelines": {},
                "file_samples": {},
            }
        )

        file_specs: list[dict] = []
        for f in input_files:
            rel_path = _build_relative_path(f, name_cache)
            sample_id, sample_name = CustomPipelineService._lookup_sample_for_file(name_cache, f.id)
            file_specs.append(
                {
                    "file_id": f.id,
                    "filename": f.filename,
                    "relative_path": rel_path,
                    "gcs_uri": f.gcs_uri,
                    "project_id": f.project_id,
                    "project_name": name_cache["projects"].get(f.project_id) if f.project_id else None,
                    "experiment_id": f.experiment_id,
                    "experiment_name": name_cache["experiments"].get(f.experiment_id) if f.experiment_id else None,
                    "sample_id": sample_id,
                    "sample_name": sample_name,
                }
            )

        # 5. Build input staging commands
        stage_commands = CustomPipelineService._build_stage_commands(file_specs)

        # 6. Generate manifest payload (pipeline_run_id is filled in after run is created)
        manifest_payload = {
            "files": [
                {
                    "file_id": fs["file_id"],
                    "filename": fs["filename"],
                    "relative_path": fs["relative_path"],
                    "project_id": fs["project_id"],
                    "project_name": fs["project_name"],
                    "experiment_id": fs["experiment_id"],
                    "experiment_name": fs["experiment_name"],
                    "sample_id": fs["sample_id"],
                    "sample_name": fs["sample_name"],
                }
                for fs in file_specs
            ],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "pipeline_run_id": None,
        }

        # 7. params.json content from resolved variables
        params_payload = dict(resolved_variables)

        # 8. Code init containers + 9. working directory + ssh key
        ssh_private_key: str | None = None
        code_init_containers: list[dict] = []
        has_code_dir = True

        if version.code_source_type == "github_repo":
            repo = version.github_repo
            if repo is None:
                raise ValueError("github_repo not found for version")
            ssh_private_key = await CustomPipelineService._fetch_ssh_private_key(session, user_id)
            code_init_containers.append(CustomPipelineService._build_git_clone_init(repo))
            working_dir = f"/code/{repo.display_name}"
        elif version.code_source_type == "code_blob":
            content = version.code_content or ""
            code_init_containers.append(CustomPipelineService._build_write_code_init(content))
            working_dir = "/code"
        elif version.code_source_type == "inline":
            has_code_dir = False
            working_dir = "/data"
        else:
            raise ValueError(f"Unknown code_source_type: {version.code_source_type}")

        # 10. Output prefix and entrypoint wrapper (run_id substituted after creation)
        results_bucket = await CustomPipelineService._read_platform_config(session, "results_bucket_name")
        if data.experiment_id is not None:
            output_prefix = f"experiments/{data.experiment_id}"
        else:
            output_prefix = f"projects/{data.project_id}"

        # 11. Build PARAM_* env vars
        extra_env = [
            {"name": f"PARAM_{PARAM_NAME_SANITIZER.sub('_', name.upper())}", "value": str(value)}
            for name, value in resolved_variables.items()
        ]

        # 12. Check quota
        allowed, message = await QuotaService.check_quota(session, user_id, estimated_hours=1.0)
        if not allowed:
            raise ValueError(f"Quota exceeded: {message}")

        # 13. Create PipelineRun
        run = PipelineRun(
            organization_id=org_id,
            experiment_id=data.experiment_id,
            project_id=data.project_id,
            submitted_by_user_id=user_id,
            pipeline_name=pipeline.name,
            pipeline_version=str(version.version_number),
            parameters_json=resolved_variables,
            input_files_json=[f.id for f in input_files],
            custom_pipeline_version_id=version.id,
            status="pending",
        )
        session.add(run)
        await session.flush()

        # 14. Link input files
        for f in input_files:
            session.add(PipelineRunInputFile(pipeline_run_id=run.id, file_id=f.id))
        await session.flush()

        # Now substitute run_id into manifest + entrypoint wrapper
        manifest_payload["pipeline_run_id"] = run.id
        manifest_init = CustomPipelineService._build_write_manifest_init(manifest_payload)
        params_init = CustomPipelineService._build_write_params_init(params_payload)

        wrapped_command = CustomPipelineService._build_entrypoint_wrapper(
            entrypoint_command=version.entrypoint_command,
            results_bucket=results_bucket,
            output_prefix=output_prefix,
            run_id=run.id,
        )

        extra_init_containers = [manifest_init, params_init] + code_init_containers

        # 15. Build job spec and submit
        job_spec = {
            "run_id": run.id,
            "pipeline_name": pipeline.name,
            "container_image": image_uri,
            "command": ["/bin/sh", "-c", wrapped_command],
            "stage_commands": stage_commands,
            "namespace": "bioaf-pipelines",
            "has_outputs_dir": True,
            "has_code_dir": has_code_dir,
            "extra_init_containers": extra_init_containers,
            "ssh_private_key": ssh_private_key,
            "cpu_request": version.cpu_request,
            "memory_request": version.memory_request,
            "extra_env": extra_env,
            "working_dir": working_dir,
            "experiment_id": data.experiment_id,
            "project_id": data.project_id,
        }

        try:
            compute_adapter = get_compute_adapter()
            job_result = await compute_adapter.submit_job(job_spec)
            run.status = "running"
            run.started_at = datetime.now(timezone.utc)
            run.k8s_job_name = job_result.get("job_id", "")
            run.k8s_namespace = job_result.get("namespace", "")
            run.slurm_job_id = job_result.get("job_id", "")
            estimated_cost = job_result.get("estimated_cost") or {}
            if estimated_cost:
                run.cost_estimate = estimated_cost.get("estimated_cost_usd")
            await session.flush()
        except Exception as e:
            run.status = "failed"
            run.error_message = str(e)
            await session.flush()
            logger.error("Custom pipeline launch failed for run %d: %s", run.id, e)
            raise

        # 16. Emit events + audit log
        asyncio.create_task(
            event_bus.emit(
                PIPELINE_STARTED,
                {
                    "event_type": PIPELINE_STARTED,
                    "org_id": org_id,
                    "user_id": user_id,
                    "target_user_id": user_id,
                    "entity_type": "pipeline_run",
                    "entity_id": run.id,
                    "title": f"Custom pipeline '{pipeline.name}' started",
                    "message": f"Run {run.id} submitted",
                    "summary": f"Custom pipeline '{pipeline.name}' run {run.id} started",
                },
            )
        )

        await log_action(
            session,
            user_id=user_id,
            entity_type="pipeline_run",
            entity_id=run.id,
            action="launch",
            details={
                "pipeline_id": pipeline.id,
                "custom_pipeline_version_id": version.id,
                "version_number": version.version_number,
                "experiment_id": data.experiment_id,
                "project_id": data.project_id,
                "input_file_count": len(input_files),
                "status": run.status,
            },
        )

        # 17. Advance experiment status if applicable
        if data.experiment_id is not None and experiment is not None:
            try:
                from app.services.experiment_service import ExperimentService

                await ExperimentService.update_status(
                    session,
                    data.experiment_id,
                    org_id,
                    user_id,
                    "processing",
                )
            except Exception as e:
                logger.warning("Could not advance experiment status: %s", e)

        return run

    # --- Launch helpers ---

    @staticmethod
    def _resolve_variables(
        defined: list[CustomPipelineVariable],
        provided: list,
    ) -> dict[str, str]:
        defined_by_name = {v.variable_name: v for v in defined}
        provided_by_name: dict[str, str] = {}
        for entry in provided:
            if entry.variable_name not in defined_by_name:
                raise ValueError(f"Unknown variable: {entry.variable_name}")
            provided_by_name[entry.variable_name] = entry.variable_value

        resolved: dict[str, str] = {}
        for name, var in defined_by_name.items():
            if name in provided_by_name:
                value = provided_by_name[name]
            elif var.default_value is not None:
                value = var.default_value
            elif var.is_required:
                raise ValueError(f"Required variable missing: {name}")
            else:
                continue

            CustomPipelineService._validate_variable_type(name, value, var.variable_type)
            resolved[name] = value
        return resolved

    @staticmethod
    def _validate_variable_type(name: str, value: str, variable_type: str) -> None:
        if variable_type == "number":
            try:
                float(value)
            except ValueError:
                raise ValueError(f"Variable '{name}' must be a number, got '{value}'")
        elif variable_type == "boolean":
            if value.lower() not in ("true", "false"):
                raise ValueError(f"Variable '{name}' must be 'true' or 'false', got '{value}'")
        # "string" accepts any value

    @staticmethod
    def _lookup_sample_for_file(name_cache: dict, file_id: int) -> tuple[int | None, str | None]:
        sample_name = name_cache.get("file_samples", {}).get(file_id)
        return None, sample_name

    @staticmethod
    def _build_stage_commands(file_specs: list[dict]) -> list[str]:
        # gsutil consults ~/.boto before GOOGLE_APPLICATION_CREDENTIALS, which
        # in cloud-sdk:slim picks up the wrong identity even with the SA key
        # mounted. Activate the SA explicitly and use `gcloud storage`, which
        # honors the activated account directly.
        commands: list[str] = []
        if not file_specs:
            return commands
        commands.append("gcloud auth activate-service-account --key-file=/secrets/gcp/key.json --quiet")
        for fs in file_specs:
            rel = fs["relative_path"]
            gcs = fs["gcs_uri"]
            dirname = "/".join(rel.split("/")[:-1])
            if dirname:
                commands.append(f"mkdir -p /data/{dirname} && gcloud storage cp {gcs} /data/{rel}")
            else:
                commands.append(f"gcloud storage cp {gcs} /data/{rel}")
        return commands

    @staticmethod
    def _shell_escape_single(content: str) -> str:
        return content.replace("'", "'\\''")

    @staticmethod
    def _build_write_manifest_init(manifest_payload: dict) -> dict:
        manifest_json = json.dumps(manifest_payload)
        escaped = CustomPipelineService._shell_escape_single(manifest_json)
        return {
            "name": "write-manifest",
            "image": "alpine:3.19",
            "command": ["/bin/sh", "-c", f"printf '%s' '{escaped}' > /data/manifest.json"],
            "volumeMounts": [{"name": "data", "mountPath": "/data"}],
        }

    @staticmethod
    def _build_write_params_init(params_payload: dict) -> dict:
        params_json = json.dumps(params_payload)
        escaped = CustomPipelineService._shell_escape_single(params_json)
        return {
            "name": "write-params",
            "image": "alpine:3.19",
            "command": ["/bin/sh", "-c", f"printf '%s' '{escaped}' > /data/params.json"],
            "volumeMounts": [{"name": "data", "mountPath": "/data"}],
        }

    @staticmethod
    def _build_git_clone_init(repo: GitHubRepo) -> dict:
        ssh_url = repo.git_ssh_url
        target = f"/code/{repo.display_name}"
        clone_script = (
            "mkdir -p /root/.ssh && "
            "cp /secrets/ssh/id_rsa /root/.ssh/id_rsa && "
            "chmod 600 /root/.ssh/id_rsa && "
            "ssh-keyscan github.com >> /root/.ssh/known_hosts 2>/dev/null && "
            f"git clone '{ssh_url}' '{target}'"
        )
        return {
            "name": "clone-repo",
            "image": "alpine/git:latest",
            "command": ["/bin/sh", "-c", clone_script],
            "volumeMounts": [
                {"name": "code", "mountPath": "/code"},
                {"name": "ssh-key", "mountPath": "/secrets/ssh", "readOnly": True},
            ],
        }

    @staticmethod
    def _build_write_code_init(code_content: str) -> dict:
        escaped = CustomPipelineService._shell_escape_single(code_content)
        return {
            "name": "write-code",
            "image": "alpine:3.19",
            "command": ["/bin/sh", "-c", f"printf '%s' '{escaped}' > /code/script"],
            "volumeMounts": [{"name": "code", "mountPath": "/code"}],
        }

    @staticmethod
    def _build_entrypoint_wrapper(
        entrypoint_command: str,
        results_bucket: str,
        output_prefix: str,
        run_id: int,
    ) -> str:
        if results_bucket:
            sync_target = f"gs://{results_bucket}/{output_prefix}/pipeline-runs/{run_id}/"
            # Activate the mounted SA explicitly (same reason as stage-inputs:
            # gsutil's ~/.boto precedence picks up the wrong identity even with
            # GOOGLE_APPLICATION_CREDENTIALS set). `|| true` keeps the trap
            # non-fatal so a sync failure doesn't mask the pipeline's real
            # exit status, but stderr is left visible so failures show up in
            # pod logs instead of silently dropping outputs.
            sync_cmd = (
                "gcloud auth activate-service-account "
                "--key-file=/secrets/gcp/key.json --quiet && "
                f"gcloud storage cp -r /outputs/* {sync_target}"
            )
            trap = f"trap '{sync_cmd} || true' EXIT"
        else:
            trap = "trap 'true' EXIT"
        return "#!/bin/sh\nset -e\n" + trap + "\n" + entrypoint_command

    @staticmethod
    async def _fetch_ssh_private_key(session: AsyncSession, user_id: int) -> str | None:
        from app.services.session_credential_service import SessionCredentialService

        cred = await SessionCredentialService.get_by_user_id(session, user_id)
        if cred is None or not cred.ssh_private_key:
            raise ValueError(
                "No SSH private key found for user. Configure session credentials with an SSH key before "
                "launching a github_repo pipeline."
            )
        return cred.ssh_private_key

    @staticmethod
    async def _read_platform_config(session: AsyncSession, key: str) -> str:
        result = await session.execute(
            sa_text("SELECT value FROM platform_config WHERE key = :k"),
            {"k": key},
        )
        row = result.first()
        if not row:
            return ""
        val = (row[0] or "").strip()
        if val and val != "null":
            return val
        return ""
