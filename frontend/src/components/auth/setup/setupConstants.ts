/** Setup wizard steps and the cloud reference lists its credential steps show. */

export const STEPS = [
  "Setup Code",
  "Create Admin Account",
  "Organization Name",
  "GCP Credentials",
  "SMTP Settings",
  "Infrastructure",
  "Select Stack",
  "Select Components",
  "Deploying",
  "Getting Started",
];

export const GCP_REGIONS = [
  "us-central1", "us-east1", "us-east4", "us-west1", "us-west2", "us-west3", "us-west4",
  "europe-west1", "europe-west2", "europe-west3", "europe-west4", "europe-west6",
  "asia-east1", "asia-east2", "asia-northeast1", "asia-south1", "asia-southeast1",
];

const GCP_ZONES: Record<string, string[]> = {
  "us-central1": ["us-central1-a", "us-central1-b", "us-central1-c", "us-central1-f"],
  "us-east1": ["us-east1-b", "us-east1-c", "us-east1-d"],
  "us-east4": ["us-east4-a", "us-east4-b", "us-east4-c"],
  "us-west1": ["us-west1-a", "us-west1-b", "us-west1-c"],
};

export function zonesForRegion(region: string): string[] {
  return GCP_ZONES[region] ?? [`${region}-b`, `${region}-c`, `${region}-d`];
}

// SA hardening: bioaf-bootstrap holds the broad project-level roles;
// bioaf-app holds a small set of scoped roles. The installer applies all
// of this automatically. These lists exist for transparency.
export const SETUP_BOOTSTRAP_ROLES = [
  { role: "roles/storage.admin", description: "Storage Admin" },
  { role: "roles/pubsub.admin", description: "Pub/Sub Admin" },
  { role: "roles/container.admin", description: "Kubernetes Engine Admin" },
  { role: "roles/iam.serviceAccountUser", description: "Service Account User" },
  { role: "roles/iam.serviceAccountAdmin", description: "Service Account Admin" },
  { role: "roles/compute.admin", description: "Compute Admin" },
  { role: "roles/resourcemanager.projectIamAdmin", description: "Project IAM Admin" },
  { role: "roles/bigquery.dataEditor", description: "BigQuery Data Editor" },
  { role: "roles/artifactregistry.admin", description: "Artifact Registry Admin" },
  { role: "roles/cloudbuild.builds.editor", description: "Cloud Build Editor" },
  { role: "roles/logging.logWriter", description: "Logs Writer" },
  { role: "roles/serviceusage.serviceUsageAdmin", description: "Service Usage Admin" },
  { role: "roles/viewer", description: "Viewer" },
];

export const SETUP_APP_ROLES = [
  { role: "roles/storage.admin", description: "Storage Admin (scoped to bioaf-* buckets)" },
  { role: "projects/<PROJECT>/roles/bioafSaManager", description: "Custom: list/delete bioaf-* SAs" },
  { role: "roles/compute.instanceAdmin.v1", description: "Compute (scoped to bioaf-* VMs)" },
  { role: "roles/container.admin", description: "Kubernetes Engine (scoped to bioaf-* clusters)" },
  { role: "roles/logging.logWriter", description: "Logs Writer" },
  { role: "roles/monitoring.metricWriter", description: "Monitoring Metric Writer (Ops Agent)" },
  { role: "roles/browser", description: "Project metadata read" },
  { role: "roles/serviceusage.serviceUsageViewer", description: "Service Usage Viewer" },
  { role: "roles/secretmanager.viewer", description: "Secret Manager metadata viewer" },
  { role: "roles/bigquery.jobUser", description: "BigQuery Job User" },
  { role: "roles/iam.serviceAccountTokenCreator", description: "Token Creator on bioaf-bootstrap only" },
];

export const SETUP_REQUIRED_APIS = [
  "cloudresourcemanager.googleapis.com",
  "compute.googleapis.com",
  "container.googleapis.com",
  "iam.googleapis.com",
  "secretmanager.googleapis.com",
  "servicenetworking.googleapis.com",
  "serviceusage.googleapis.com",
  "pubsub.googleapis.com",
  "storage.googleapis.com",
  "sqladmin.googleapis.com",
  "cloudbilling.googleapis.com",
  "bigquery.googleapis.com",
  "artifactregistry.googleapis.com",
  "cloudbuild.googleapis.com",
];

// The minimum set that gives the user a working "first pipeline + first notebook" experience
// without opening the Infrastructure menu. Keys match the canonical KUBERNETES_COMPONENTS list
// (the same 7 the post-install Infrastructure > Components page renders).
export const DEFAULT_SELECTED_COMPONENTS = ["nextflow", "jupyterhub"];
