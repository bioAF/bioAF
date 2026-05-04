from fastapi import APIRouter

from app.api.auth import router as auth_router
from app.api.bootstrap import router as bootstrap_router
from app.api.components import router as components_router
from app.api.health import router as health_router
from app.api.terraform import router as terraform_router
from app.api.users import router as users_router
from app.api.experiments import router as experiments_router
from app.api.samples import router as samples_router
from app.api.sample_batches import router as sample_batches_router
from app.api.projects import router as projects_router
from app.api.templates import router as templates_router
from app.api.audit import router as audit_router
from app.api.compute import router as compute_router
from app.api.notebooks import router as notebooks_router
from app.api.quotas import router as quotas_router
from app.api.pipelines import router as pipelines_router
from app.api.pipeline_runs import router as pipeline_runs_router
from app.api.files import router as files_router
from app.api.datasets import router as datasets_router
from app.api.documents import router as documents_router
from app.api.storage import router as storage_router
from app.api.cellxgene import router as cellxgene_router
from app.api.qc_dashboards import router as qc_dashboards_router
from app.api.plots import router as plots_router
from app.api.search import router as search_router
from app.api.gitops import router as gitops_router
from app.api.packages import router as packages_router
from app.api.environments import router as environments_router
from app.api.template_notebooks import router as template_notebooks_router
from app.api.notifications import router as notifications_router
from app.api.backups import router as backups_router
from app.api.costs import router as costs_router
from app.api.upgrades import router as upgrades_router
from app.api.access_logs import router as access_logs_router
from app.api.activity_feed import router as activity_feed_router
from app.api.vocabularies import router as vocabularies_router
from app.api.pipeline_run_reviews import router as pipeline_run_reviews_router
from app.api.references import router as references_router
from app.api.internal_references import router as internal_references_router
from app.api.geo_export import router as geo_export_router
from app.api.snapshots import router as snapshots_router
from app.api.infrastructure import router as infrastructure_router
from app.api.naming_profiles import router as naming_profiles_router
from app.api.ingest import router as ingest_router
from app.api.ingest import claim_router
from app.api.ssh_connect import router as ssh_connect_router
from app.api.gcp_config import router as gcp_config_router
from app.api.terraform_executor import router as terraform_executor_router
from app.api.storage_deploy import router as storage_deploy_router
from app.api.stack_deploy import router as stack_deploy_router
from app.api.auto_ingest import router as auto_ingest_router
from app.api.notebook_sessions import router as notebook_sessions_router
from app.api.notebook_sessions import settings_router as notebook_settings_router
from app.api.billing_export import router as billing_export_router
from app.api.orphaned_resources import router as orphaned_resources_router
from app.api.roles import router as roles_router
from app.api.work_nodes import router as work_nodes_router
from app.api.work_nodes import settings_router as work_node_settings_router
from app.api.provenance_reports import router as provenance_reports_router
from app.api.data_export import router as data_export_router
from app.api.sequencing_batches import router as sequencing_batches_router
from app.api.slack_oauth import router as slack_oauth_router
from app.api.experiment_auto_runs import router as experiment_auto_runs_router
from app.api.content_tokens import router as content_tokens_router
from app.api.sheets_import import router as sheets_import_router
from app.api.github_repos import router as github_repos_router
from app.api.custom_pipelines import router as custom_pipelines_router

api_router = APIRouter()

api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(bootstrap_router)
api_router.include_router(users_router)
api_router.include_router(components_router)
api_router.include_router(terraform_router)
api_router.include_router(experiments_router)
api_router.include_router(samples_router)
api_router.include_router(sample_batches_router)
api_router.include_router(projects_router)
api_router.include_router(templates_router)
api_router.include_router(audit_router)
api_router.include_router(compute_router)
api_router.include_router(notebooks_router)
api_router.include_router(quotas_router)
api_router.include_router(pipelines_router)
api_router.include_router(pipeline_runs_router)
api_router.include_router(files_router)
api_router.include_router(datasets_router)
api_router.include_router(documents_router)
api_router.include_router(storage_router)
api_router.include_router(cellxgene_router)
api_router.include_router(qc_dashboards_router)
api_router.include_router(plots_router)
api_router.include_router(search_router)
api_router.include_router(gitops_router)
api_router.include_router(packages_router)
api_router.include_router(environments_router)
api_router.include_router(template_notebooks_router)
api_router.include_router(notifications_router)
api_router.include_router(backups_router)
api_router.include_router(costs_router)
api_router.include_router(upgrades_router)
api_router.include_router(access_logs_router)
api_router.include_router(activity_feed_router)
api_router.include_router(vocabularies_router)
api_router.include_router(pipeline_run_reviews_router)
api_router.include_router(references_router)
api_router.include_router(internal_references_router)
api_router.include_router(geo_export_router)
api_router.include_router(snapshots_router)
api_router.include_router(infrastructure_router)
api_router.include_router(naming_profiles_router)
api_router.include_router(ingest_router)
api_router.include_router(claim_router)
api_router.include_router(ssh_connect_router)
api_router.include_router(gcp_config_router)
api_router.include_router(terraform_executor_router)
api_router.include_router(storage_deploy_router)
api_router.include_router(stack_deploy_router)
api_router.include_router(auto_ingest_router)
api_router.include_router(notebook_sessions_router)
api_router.include_router(notebook_settings_router)
api_router.include_router(billing_export_router)
api_router.include_router(orphaned_resources_router)
api_router.include_router(roles_router)
api_router.include_router(work_nodes_router)
api_router.include_router(work_node_settings_router)
api_router.include_router(provenance_reports_router)
api_router.include_router(data_export_router)
api_router.include_router(sequencing_batches_router)
api_router.include_router(slack_oauth_router)
api_router.include_router(experiment_auto_runs_router)
api_router.include_router(content_tokens_router)
api_router.include_router(sheets_import_router)
api_router.include_router(github_repos_router)
api_router.include_router(custom_pipelines_router)
