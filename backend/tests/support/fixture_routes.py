"""Which database fixture route each test file takes (plan 4, stages 5 and 6).

- ``existing``: a fresh create_all schema per test (the default for every file not listed).
- ``migrated_reset``: the worker's migration-built schema, reset to the empty-upgrade baseline
  before the test. Real commits stay real.
- ``rollback``: the worker's migration-built schema inside one outer transaction that is rolled
  back after the test. Only for files the isolation inventory classifies ``single_connection``.

Files join a pilot only after they pass on it; the reasons a file stays off are recorded in
local/oct_refactor/4_test_runtime_and_fixtures/status.md. ``BIOAF_FIXTURE_ROUTES=existing``
forces every file onto the existing route, so one commit can be measured both ways.

``BIOAF_FIXTURE_TRIAL="rollback=single_connection;migrated_reset=real_commit"`` routes whole
isolation-inventory categories for a characterization run, without editing the pilot lists.
The rollback route only ever accepts ``single_connection`` files; ``schema`` and
``unclassified`` files are never routed.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

_INVENTORY = Path(__file__).resolve().parent.parent / "isolation_inventory.json"
_ALLOWED = {"rollback": {"single_connection"}, "migrated_reset": {"single_connection", "real_commit"}}


@lru_cache(maxsize=1)
def _categories() -> dict[str, str]:
    return {name: e["category"] for name, e in json.loads(_INVENTORY.read_text())["files"].items()}


def _trial(filename: str) -> str | None:
    spec = os.environ.get("BIOAF_FIXTURE_TRIAL")
    if not spec:
        return None
    category = _categories().get(filename)
    for part in spec.split(";"):
        route, _, wanted = part.partition("=")
        wanted_set = set(wanted.split(","))
        if not wanted_set <= _ALLOWED.get(route, set()):
            raise ValueError(f"BIOAF_FIXTURE_TRIAL cannot route {sorted(wanted_set)} through {route}")
        if category in wanted_set:
            return route
    return None


# real_commit files that passed on migrated_reset (trial A, 2026-11-05).
MIGRATED_RESET_PILOT: frozenset[str] = frozenset(
    {
        "test_access_logs.py",
        "test_activity_feed.py",
        "test_admin_user_management.py",
        "test_agent_review_artifact_builder.py",
        "test_agent_review_builder_api.py",
        "test_agent_review_job_service.py",
        "test_agent_review_prompt_service.py",
        "test_agent_reviews_api.py",
        "test_api_keys.py",
        "test_assistant_api.py",
        "test_assistant_availability.py",
        "test_audit.py",
        "test_auth.py",
        "test_auto_ingest_api.py",
        "test_auto_ingest_neutralized.py",
        "test_auto_run_launch_outcome.py",
        "test_aws_config_api.py",
        "test_backups.py",
        "test_batch_code_resolve.py",
        "test_batches.py",
        "test_bcrypt_test_rounds.py",
        "test_beta_features.py",
        "test_billing_export.py",
        "test_bootstrap.py",
        "test_bootstrap_roles_assistant.py",
        "test_bootstrap_roles_llm.py",
        "test_bootstrap_setup_code.py",
        "test_build_status_endpoint.py",
        "test_capabilities_bootstrap.py",
        "test_capability_guards.py",
        "test_cellxgene.py",
        "test_cluster_quota_api.py",
        "test_code_service.py",
        "test_component_toggle_audit.py",
        "test_component_toggle_image_build.py",
        "test_components.py",
        "test_components_select_batch.py",
        "test_content_access.py",
        "test_content_tokens.py",
        "test_costs.py",
        "test_csv_preview.py",
        "test_custom_pipeline_qc_config.py",
        "test_custom_pipeline_reference_param.py",
        "test_custom_pipelines_api.py",
        "test_dashboard.py",
        "test_data_export.py",
        "test_data_search_api.py",
        "test_datasets.py",
        "test_deploy_progress.py",
        "test_deployment_interruption.py",
        "test_deployment_outputs.py",
        "test_documents.py",
        "test_encrypted_model_columns.py",
        "test_entity_snapshots.py",
        "test_environments.py",
        "test_experiment_auto_runs.py",
        "test_experiment_field_defaults.py",
        "test_experiments.py",
        "test_file_delete_frees_storage.py",
        "test_file_download.py",
        "test_file_soft_delete.py",
        "test_file_stats.py",
        "test_files.py",
        "test_fixture_lifecycle.py",
        "test_full_search_api.py",
        "test_gcp_config_api.py",
        "test_gcp_config_response_bootstrap_email.py",
        "test_geo_export_api.py",
        "test_geo_export_service.py",
        "test_gitops.py",
        "test_info_disclosure.py",
        "test_infra_update_api.py",
        "test_infrastructure_api.py",
        "test_infrastructure_components.py",
        "test_ingest_api.py",
        "test_ingest_pipeline_bugs.py",
        "test_ingest_real_gcs.py",
        "test_ingest_service.py",
        "test_integrations_admin.py",
        "test_integrations_experiments.py",
        "test_integrations_files.py",
        "test_integrations_openapi.py",
        "test_integrations_projects.py",
        "test_integrations_samples.py",
        "test_lab_documents_api.py",
        "test_lab_glossary_api.py",
        "test_lab_glossary_scan_phase_d.py",
        "test_lab_glossary_scan_service.py",
        "test_launch_outcome.py",
        "test_launch_partial_submission.py",
        "test_literature_agent_review.py",
        "test_literature_api.py",
        "test_literature_attach_pdf.py",
        "test_literature_automation.py",
        "test_literature_full_text_provenance.py",
        "test_literature_lit_review_run.py",
        "test_literature_open_access_parse.py",
        "test_literature_openalex_api.py",
        "test_literature_schema.py",
        "test_literature_search_api.py",
        "test_literature_upload_api.py",
        "test_llm_config_api.py",
        "test_llm_provider_config.py",
        "test_manifest_file_reconciliation.py",
        "test_manifest_position_resolve.py",
        "test_naming_profile_api.py",
        "test_naming_profile_models.py",
        "test_naming_profile_on_template_and_experiment.py",
        "test_naming_profile_service.py",
        "test_networking_api.py",
        "test_nf_core_registry.py",
        "test_notebook_api_v1.py",
        "test_notebook_file_mounting.py",
        "test_notebook_output_discovery.py",
        "test_notebook_output_provenance.py",
        "test_notebook_sessions_v1.py",
        "test_notebooks.py",
        "test_notifications.py",
        "test_orphaned_recovery.py",
        "test_orphaned_resources.py",
        "test_packages_removed.py",
        "test_permissions_and_ownership.py",
        "test_phase23_files.py",
        "test_pipeline_catalog.py",
        "test_pipeline_launch_failure.py",
        "test_pipeline_run_jobspec.py",
        "test_pipeline_run_preflight.py",
        "test_pipeline_run_preview_api.py",
        "test_pipeline_run_reviews.py",
        "test_pipeline_run_samplesheet_provenance.py",
        "test_pipeline_trigger_models.py",
        "test_platform_config_service.py",
        "test_plot_archive_fixes.py",
        "test_plots.py",
        "test_post_commit_events.py",
        "test_project_counts_and_onboarding.py",
        "test_projects.py",
        "test_provenance.py",
        "test_provenance_report_api.py",
        "test_qc_dashboard.py",
        "test_qc_dashboard_api_config.py",
        "test_quick_search.py",
        "test_read_groups.py",
        "test_reference_by_name.py",
        "test_reference_data.py",
        "test_reference_filter_options.py",
        "test_reference_import_routes.py",
        "test_reference_upload_complete_route.py",
        "test_reference_upload_init_route.py",
        "test_request_health.py",
        "test_role_service.py",
        "test_roles_api.py",
        "test_rotate_encryption_keys.py",
        "test_run_samplesheet_provenance_api.py",
        "test_samples.py",
        "test_samplesheet_declared_launch.py",
        "test_samplesheet_mapping_api.py",
        "test_sdr_api.py",
        "test_search.py",
        "test_security_headers.py",
        "test_sequencing_batch.py",
        "test_session_credentials.py",
        "test_session_failure_classification.py",
        "test_session_failure_taxonomy.py",
        "test_sheets_import_api.py",
        "test_slack_oauth.py",
        "test_slurm_guard.py",
        "test_smtp_settings.py",
        "test_snapshots.py",
        "test_ssh_connect.py",
        "test_stack_api.py",
        "test_stack_deploy_background.py",
        "test_storage.py",
        "test_storage_api.py",
        "test_storage_uri_read_paths.py",
        "test_superseries_export.py",
        "test_template_notebooks.py",
        "test_templates.py",
        "test_terraform_api.py",
        "test_terraform_init.py",
        "test_terraform_interruption.py",
        "test_terraform_legacy_removed.py",
        "test_terraform_routes_interruption.py",
        "test_terraform_stream.py",
        "test_upgrades.py",
        "test_url_import_boundary.py",
        "test_users.py",
        "test_validation_api.py",
        "test_validation_deposit_conflict.py",
        "test_validation_provenance.py",
        "test_vocabulary_validation.py",
        "test_work_nodes.py",
    }
)

# single_connection files that passed on rollback (trial B, 2026-11-05) and on migrated_reset.
ROLLBACK_PILOT: frozenset[str] = frozenset(
    {
        "test_assistant_loop_service.py",
        "test_assistant_models.py",
        "test_assistant_tool_service.py",
        "test_bcrypt_service_test_rounds.py",
        "test_bootstrap_bamtofastq.py",
        "test_bootstrap_environments.py",
        "test_bootstrap_metadata.py",
        "test_cellxgene_image_service_aws.py",
        "test_cluster_status.py",
        "test_component_queue_orchestrator.py",
        "test_component_toggle_service.py",
        "test_create_admin.py",
        "test_custom_pipeline_cascade.py",
        "test_custom_pipeline_launch.py",
        "test_custom_pipeline_resources.py",
        "test_custom_pipelines.py",
        "test_custom_version_variables.py",
        "test_data_search_service.py",
        "test_emitted_name_matching.py",
        "test_environment_build_aws.py",
        "test_fetchngs_ingest.py",
        "test_file_asset_identity.py",
        "test_file_global_scope.py",
        "test_file_inheritance.py",
        "test_file_organization.py",
        "test_file_provenance.py",
        "test_file_sequencing_identity.py",
        "test_full_search.py",
        "test_gcs_storage_service.py",
        "test_github_db_columns.py",
        "test_illumina_filename_parsing.py",
        "test_image_build_credentials.py",
        "test_infra_update_service.py",
        "test_ingest_credentials_passthrough.py",
        "test_lab_document_service.py",
        "test_lab_documents_model.py",
        "test_lab_documents_search.py",
        "test_lab_glossary_search.py",
        "test_lab_glossary_term_service.py",
        "test_lab_knowledge_rbac.py",
        "test_manifest_ingest.py",
        "test_manifest_reconciliation.py",
        "test_migration_023.py",
        "test_migration_024.py",
        "test_migration_026.py",
        "test_migration_081.py",
        "test_migration_093.py",
        "test_migration_101.py",
        "test_notebook_execution.py",
        "test_notebook_gcs_bucket.py",
        "test_notebook_image_service.py",
        "test_phase21_migration.py",
        "test_phase22_migration.py",
        "test_pipeline_assay_fallback.py",
        "test_pipeline_io_lineage.py",
        "test_pipeline_launcher.py",
        "test_pipeline_monitor.py",
        "test_pipeline_monitor_custom.py",
        "test_pipeline_monitor_oom.py",
        "test_pipeline_monitor_preemption.py",
        "test_pipeline_output_service.py",
        "test_pipeline_output_visibility.py",
        "test_pipeline_reference_linkage.py",
        "test_pipeline_run_missing_files.py",
        "test_pipeline_run_samplesheet_contract.py",
        "test_pipeline_unschedulable.py",
        "test_provenance_reports.py",
        "test_pubsub_listener.py",
        "test_pubsub_manifest_routing.py",
        "test_qc_dashboard_template_dispatch.py",
        "test_qc_generic_routing.py",
        "test_qc_roster_for_run.py",
        "test_qc_template_fields.py",
        "test_qc_template_resolver.py",
        "test_queue_lifecycle_hooks.py",
        "test_recommend_pipeline_service.py",
        "test_reference_import_progress_model.py",
        "test_reference_import_service.py",
        "test_reference_upload_complete.py",
        "test_reference_upload_init.py",
        "test_references_resource_permissions.py",
        "test_register_outputs_cli.py",
        "test_registry_backends.py",
        "test_reproduction_plan_service.py",
        "test_sample_completeness_trigger.py",
        "test_sample_sex_optional.py",
        "test_samplesheet_contract_refresh.py",
        "test_samplesheet_mappings.py",
        "test_sdr_search.py",
        "test_sdr_service.py",
        "test_session_monitor.py",
        "test_setup_code.py",
        "test_setup_code_service.py",
        "test_stack_deployment.py",
        "test_storage_uri_synonym.py",
        "test_system_test_pipeline.py",
        "test_terraform_executor.py",
        "test_terraform_operations.py",
        "test_upload_credentials_sa_hardening.py",
        "test_validation_c1_gate.py",
        "test_validation_design_edit.py",
        "test_validation_driver.py",
        "test_validation_driver_backhalf.py",
        "test_validation_driver_reproducing.py",
        "test_validation_error_notice.py",
        "test_validation_extraction.py",
        "test_validation_fetch_reaper.py",
        "test_validation_finding_claim.py",
        "test_validation_level3_wiring.py",
        "test_validation_study_service.py",
        "test_webhooks.py",
        "test_work_dir_reaper.py",
    }
)


# Files that failed on a migrated route and stay on the existing one. Every failure traced to
# migration seed rows or a migration-only uniqueness rule (status.md, "Kept off the pilots").
KEPT_EXISTING: frozenset[str] = frozenset(
    {
        "test_batch_position.py",
        "test_csv_upload.py",
        "test_deployment_characterization.py",
        "test_encryption_migration.py",
        "test_gcp_config_migration.py",
        "test_migration_025.py",
        "test_pipeline_reproduction.py",
        "test_pipeline_runs.py",
        "test_provenance_fields.py",
        "test_reference_genome_autopopulate.py",
        "test_sample_batch_rename.py",
        "test_upload_ingest_bucket.py",
        "test_vocabularies.py",
    }
)


def route_for(filename: str) -> str:
    if os.environ.get("BIOAF_FIXTURE_ROUTES") == "existing":
        return "existing"
    trial = _trial(filename)
    if trial:
        return trial
    if filename in ROLLBACK_PILOT:
        return "rollback"
    if filename in MIGRATED_RESET_PILOT:
        return "migrated_reset"
    return "existing"
