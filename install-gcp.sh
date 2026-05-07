#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# bioAF GCP Installer
#
# One-command setup for running bioAF on a Google Cloud VM.
# Designed to run on the user's local machine (macOS or Linux).
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/not-that-guy-again/bioAF/main/install-gcp.sh | bash
#
# What this script does:
#   1. Checks for (or installs) the gcloud CLI
#   2. Authenticates with Google Cloud
#   3. Selects or creates a GCP project
#   4. Enables required APIs
#   5. Creates a firewall rule for web traffic (ports 80, 443)
#   6. Creates the bioaf-managed Resource Manager tag and a custom IAM role
#   7. Creates the bioaf-bootstrap and bioaf-app service accounts (no keys)
#   8. Creates a VM with bioaf-app attached and the bootstrap email in metadata
#   9. Prints the SSH command and next steps
#
# What this script does NOT do:
#   - Store any passwords or credentials
#   - Modify your local machine beyond installing gcloud (if needed)
#   - Install bioAF itself (that happens on the VM)
# ---------------------------------------------------------------------------

set -euo pipefail

# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
red()    { printf '\033[0;31m%s\033[0m\n' "$*"; }
green()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[0;33m%s\033[0m\n' "$*"; }
bold()   { printf '\033[1m%s\033[0m\n' "$*"; }
dim()    { printf '\033[2m%s\033[0m\n' "$*"; }

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
VM_NAME="bioaf"
MACHINE_TYPE="e2-medium"
BOOT_DISK_SIZE="30GB"
IMAGE_FAMILY="ubuntu-2204-lts"
IMAGE_PROJECT="ubuntu-os-cloud"
FIREWALL_RULE_NAME="bioaf-allow-web"
NETWORK_TAG="bioaf"
SA_NAME_PREFIX="bioaf-app"
SA_DISPLAY_NAME="bioAF Application"

# Regions that tend to have lower costs and good availability
SUGGESTED_REGIONS=("us-central1" "us-east1" "us-west1" "europe-west1" "asia-east1")

# Zone lookup -- not every region has a "-a" zone (e.g. us-east1, europe-west1).
# Must stay in sync with backend/app/gcp_zones.py.
zones_for_region() {
    local region="$1"
    case "$region" in
        us-central1)    echo "us-central1-a us-central1-b us-central1-c us-central1-f" ;;
        us-east1)       echo "us-east1-b us-east1-c us-east1-d" ;;
        us-east4)       echo "us-east4-a us-east4-b us-east4-c" ;;
        us-west1)       echo "us-west1-a us-west1-b us-west1-c" ;;
        us-west2)       echo "us-west2-a us-west2-b us-west2-c" ;;
        us-west3)       echo "us-west3-a us-west3-b us-west3-c" ;;
        us-west4)       echo "us-west4-a us-west4-b us-west4-c" ;;
        europe-west1)   echo "europe-west1-b europe-west1-c europe-west1-d" ;;
        europe-west2)   echo "europe-west2-a europe-west2-b europe-west2-c" ;;
        europe-west3)   echo "europe-west3-a europe-west3-b europe-west3-c" ;;
        europe-west4)   echo "europe-west4-a europe-west4-b europe-west4-c" ;;
        europe-west6)   echo "europe-west6-a europe-west6-b europe-west6-c" ;;
        asia-east1)     echo "asia-east1-a asia-east1-b asia-east1-c" ;;
        asia-east2)     echo "asia-east2-a asia-east2-b asia-east2-c" ;;
        asia-northeast1) echo "asia-northeast1-a asia-northeast1-b asia-northeast1-c" ;;
        asia-south1)    echo "asia-south1-a asia-south1-b asia-south1-c" ;;
        asia-southeast1) echo "asia-southeast1-a asia-southeast1-b asia-southeast1-c" ;;
        *)              echo "${region}-b ${region}-c ${region}-d" ;;
    esac
}

# GCP APIs required by bioAF
REQUIRED_APIS=(
    "compute.googleapis.com"
    "storage.googleapis.com"
    "iam.googleapis.com"
    "cloudresourcemanager.googleapis.com"
    "pubsub.googleapis.com"
    "container.googleapis.com"
    "bigquery.googleapis.com"
    "artifactregistry.googleapis.com"
    "cloudbuild.googleapis.com"
    "secretmanager.googleapis.com"
    "serviceusage.googleapis.com"
    "logging.googleapis.com"
    "cloudquotas.googleapis.com"
)

# SA hardening (see documentation/sa-hardening/03-consolidated-plan.md):
# - bioaf-bootstrap holds the broad project-level roles. Impersonated by
#   bioaf-app via roles/iam.serviceAccountTokenCreator on bioaf-bootstrap only.
# - bioaf-app is attached to the GCE VM and holds a small set of scoped
#   roles. Storage / SAs / compute scoping uses IAM Conditions on resource
#   names; container.admin uses a Resource Manager tag; pubsub.subscriber
#   and secretmanager.secretAccessor use per-resource bindings created by
#   Terraform.
# Must match installer/roles_manifest.yaml.
BOOTSTRAP_SA_NAME="bioaf-bootstrap"
APP_SA_NAME="bioaf-app"
READER_SA_NAME="bioaf-reader"
BIOAF_TAG_KEY="bioaf-managed"
BIOAF_TAG_VALUE="true"
BIOAFSAMANAGER_ROLE_ID="bioafSaManager"

BOOTSTRAP_ROLES=(
    "roles/storage.admin"
    "roles/pubsub.admin"
    "roles/container.admin"
    "roles/iam.serviceAccountUser"
    "roles/iam.serviceAccountAdmin"
    "roles/compute.admin"
    "roles/resourcemanager.projectIamAdmin"
    "roles/bigquery.dataEditor"
    "roles/artifactregistry.admin"
    "roles/cloudbuild.builds.editor"
    "roles/logging.logWriter"
    "roles/serviceusage.serviceUsageAdmin"
    "roles/viewer"
)

# bioaf-app: unconditioned bindings (low blast radius / read-only).
APP_UNCONDITIONED_ROLES=(
    "roles/logging.logWriter"
    "roles/monitoring.metricWriter"
    "roles/browser"
    "roles/serviceusage.serviceUsageViewer"
    "roles/secretmanager.viewer"
    "roles/bigquery.jobUser"
)

# ---------------------------------------------------------------------------
# Introduction
# ---------------------------------------------------------------------------
echo ""
bold "======================================"
bold "  bioAF GCP Installer"
bold "======================================"
echo ""
echo "This script will set up the Google Cloud infrastructure needed"
echo "to run bioAF. Here is what it will do:"
echo ""
echo "  1. Verify or install the Google Cloud CLI (gcloud)"
echo "  2. Authenticate your Google account"
echo "  3. Select your GCP project"
echo "  4. Enable required GCP APIs"
echo "  5. Create a firewall rule for web traffic (ports 80, 443)"
echo "  6. Create two scoped service accounts (no JSON keys are downloaded)"
echo "  7. Create an e2-medium VM with Ubuntu 22.04 (30GB disk)"
echo ""
echo "Estimated monthly cost: ~\$27/month for the VM + disk."
echo "You can stop the VM at any time to pause billing."
echo ""
read -rp "Continue? [Y/n] " confirm
if [ "$confirm" = "n" ] || [ "$confirm" = "N" ]; then
    echo "Cancelled."
    exit 0
fi

# ---------------------------------------------------------------------------
# Step 1: Check for gcloud CLI
# ---------------------------------------------------------------------------
echo ""
bold "Step 1: Google Cloud CLI"

if command -v gcloud &>/dev/null; then
    green "  gcloud CLI found: $(gcloud version 2>/dev/null | head -1)"
else
    echo ""
    yellow "  gcloud CLI is not installed."
    echo ""
    echo "  Google provides an installer for your platform."
    echo "  Visit: https://cloud.google.com/sdk/docs/install"
    echo ""
    echo "  On macOS with Homebrew:"
    dim "    brew install --cask google-cloud-sdk"
    echo ""
    echo "  On Linux:"
    dim "    curl https://sdk.cloud.google.com | bash"
    echo ""
    read -rp "  Install gcloud now? (runs Google's installer) [Y/n] " install_gcloud
    if [ "$install_gcloud" = "n" ] || [ "$install_gcloud" = "N" ]; then
        echo ""
        echo "  Install gcloud manually, then re-run this script."
        exit 0
    fi

    os="$(uname -s)"
    case "$os" in
        Darwin)
            if command -v brew &>/dev/null; then
                echo "  Installing via Homebrew..."
                brew install --cask google-cloud-sdk
            else
                echo "  Running Google's installer..."
                curl https://sdk.cloud.google.com | bash
                exec -l "$SHELL"
            fi
            ;;
        Linux)
            echo "  Running Google's installer..."
            curl https://sdk.cloud.google.com | bash
            exec -l "$SHELL"
            ;;
        *)
            red "  Unsupported OS: $os"
            echo "  Install gcloud manually: https://cloud.google.com/sdk/docs/install"
            exit 1
            ;;
    esac

    # Verify installation
    if ! command -v gcloud &>/dev/null; then
        red "  gcloud installation did not complete."
        echo "  You may need to restart your terminal, then re-run this script."
        exit 1
    fi
    green "  gcloud CLI installed."

    # Pre-generate the GCE SSH key so we control whether it has a
    # passphrase. If we don't, gcloud creates one lazily on the first
    # `gcloud compute ssh` call and prompts interactively, which the
    # later VM-readiness wait can't influence. We only do this when we
    # just installed gcloud -- existing installs keep their key untouched.
    GCE_SSH_KEY="$HOME/.ssh/google_compute_engine"
    if [ ! -f "$GCE_SSH_KEY" ]; then
        echo ""
        bold "  GCE SSH key"
        echo ""
        echo "  This key is used to SSH into your bioAF VM (and any other GCE"
        echo "  VM). You can protect it with a passphrase or leave it unprotected:"
        echo ""
        echo "    With passphrase:    you'll be prompted on each SSH connection"
        echo "                        unless ssh-agent caches it. Stronger"
        echo "                        protection if your laptop is compromised."
        echo "    Without passphrase: zero prompts, equivalent to your old laptop"
        echo "                        if you never set one there."
        echo ""
        read -rp "  Protect the GCE SSH key with a passphrase? [y/N] " add_pass
        mkdir -p "$HOME/.ssh"
        chmod 700 "$HOME/.ssh"
        sa_account="$(gcloud config get-value account 2>/dev/null || whoami)"
        if [ "$add_pass" = "y" ] || [ "$add_pass" = "Y" ]; then
            ssh-keygen -t rsa -b 4096 -f "$GCE_SSH_KEY" -C "$sa_account"
        else
            ssh-keygen -t rsa -b 4096 -f "$GCE_SSH_KEY" -N "" -C "$sa_account"
        fi
        green "  GCE SSH key generated at $GCE_SSH_KEY."
    fi
fi

# ---------------------------------------------------------------------------
# Step 2: Authenticate
# ---------------------------------------------------------------------------
echo ""
bold "Step 2: Google Cloud Authentication"

# A "current account" only means a row exists in the auth list; the access
# and refresh tokens may still be expired. Verify both, and force a fresh
# login on failure -- this is much friendlier than letting a downstream
# gcloud command die with "Reauthentication failed".
verify_credentials_or_relogin() {
    local label="$1"
    if gcloud auth print-access-token --quiet >/dev/null 2>&1; then
        return 0
    fi
    yellow "  ${label} -- credentials appear expired."
    echo "  Opening browser to re-authenticate..."
    gcloud auth login
    if ! gcloud auth print-access-token --quiet >/dev/null 2>&1; then
        red "  Re-authentication did not produce a working token. Aborting."
        exit 1
    fi
}

# Check if already authenticated
current_account=$(gcloud config get-value account 2>/dev/null || true)
if [ -n "$current_account" ] && [ "$current_account" != "(unset)" ]; then
    echo "  Currently authenticated as: $current_account"
    read -rp "  Use this account? [Y/n] " use_current
    if [ "$use_current" = "n" ] || [ "$use_current" = "N" ]; then
        echo "  Opening browser for Google sign-in..."
        gcloud auth login
    fi
else
    echo "  Opening browser for Google sign-in..."
    gcloud auth login
fi
verify_credentials_or_relogin "Cached login for $(gcloud config get-value account 2>/dev/null)"
green "  Authenticated as: $(gcloud config get-value account 2>/dev/null)"

# ---------------------------------------------------------------------------
# Step 3: Select GCP project
# ---------------------------------------------------------------------------
echo ""
bold "Step 3: Select GCP Project"
echo ""
echo "  Your available projects:"
echo ""
if ! gcloud projects list --format="table(projectId, name)"; then
    red "  Failed to list projects. The auth token may have expired."
    exit 1
fi
echo ""

current_project=$(gcloud config get-value project 2>/dev/null || true)
if [ -n "$current_project" ] && [ "$current_project" != "(unset)" ]; then
    echo "  Current project: $current_project"
    read -rp "  Use this project? [Y/n] " use_project
    if [ "$use_project" != "n" ] && [ "$use_project" != "N" ]; then
        PROJECT_ID="$current_project"
    else
        read -rp "  Enter project ID: " PROJECT_ID
    fi
else
    read -rp "  Enter project ID: " PROJECT_ID
fi

if [ -z "$PROJECT_ID" ]; then
    red "  No project selected."
    exit 1
fi

if ! gcloud config set project "$PROJECT_ID"; then
    red "  Failed to set project to $PROJECT_ID."
    exit 1
fi
green "  Using project: $PROJECT_ID"

# Verify billing is enabled
billing_status=$(gcloud billing projects describe "$PROJECT_ID" --format="value(billingEnabled)" 2>/dev/null || echo "")
if [ "$billing_status" != "True" ]; then
    echo ""
    yellow "  WARNING: Billing does not appear to be enabled for this project."
    echo "  VM creation requires an active billing account."
    echo "  Enable billing at: https://console.cloud.google.com/billing/linkedaccount?project=$PROJECT_ID"
    echo ""
    read -rp "  Continue anyway? [y/N] " continue_anyway
    if [ "$continue_anyway" != "y" ] && [ "$continue_anyway" != "Y" ]; then
        exit 0
    fi
fi

# ---------------------------------------------------------------------------
# Step 4: Enable required APIs
# ---------------------------------------------------------------------------
echo ""
bold "Step 4: Enabling Required APIs"

for api in "${REQUIRED_APIS[@]}"; do
    echo "  Enabling $api..."
    gcloud services enable "$api" --project="$PROJECT_ID" 2>/dev/null || {
        yellow "  Could not enable $api (may already be enabled or require permissions)"
    }
done
green "  APIs enabled."

# ---------------------------------------------------------------------------
# Step 5: Select region and zone
# ---------------------------------------------------------------------------
echo ""
bold "Step 5: Select Region"
echo ""
echo "  Suggested regions (lower cost, good availability):"
echo ""
for i in "${!SUGGESTED_REGIONS[@]}"; do
    echo "    $((i+1)). ${SUGGESTED_REGIONS[$i]}"
done
echo ""
read -rp "  Choose a number (1-${#SUGGESTED_REGIONS[@]}) or enter a region name: " region_input

if [[ "$region_input" =~ ^[0-9]+$ ]] && [ "$region_input" -ge 1 ] && [ "$region_input" -le "${#SUGGESTED_REGIONS[@]}" ]; then
    REGION="${SUGGESTED_REGIONS[$((region_input-1))]}"
else
    REGION="${region_input:-us-central1}"
fi

# Read the zone list for this region into an array
read -ra REGION_ZONES <<< "$(zones_for_region "$REGION")"
ZONE="${REGION_ZONES[0]}"

green "  Using region: $REGION"

# ---------------------------------------------------------------------------
# Step 5b: Auto-request GCP quota increases
#
# Fresh GCP projects ship with very tight default quotas (12 vCPUs and
# 250 GB regional SSD). Even one bioAF pipeline pod cannot be scheduled
# under those defaults. The Cloud Quotas API accepts programmatic
# QuotaPreference submissions and -- on paid billing accounts -- auto-
# approves typical bumps in seconds. On free-trial billing accounts the
# request goes to human review (1-2 business days).
#
# This step asks for the bumps bioAF needs and tells the user what is
# happening at each phase. The install does not abort if a request is
# denied or stuck pending: pipeline launches that depend on the quota
# will surface the underlying QUOTA_EXCEEDED reason in the run logs.
# ---------------------------------------------------------------------------
echo ""
bold "Step 5b: GCP Quota Auto-Request"

# Source installer/quota.sh from a local clone if present (clone-then-run
# install) or fetch it over HTTPS pinned to main (curl|bash install).
QUOTA_HELPER_LOCAL="$(dirname "${BASH_SOURCE[0]:-$0}")/installer/quota.sh"
QUOTA_HELPER_URL="https://raw.githubusercontent.com/not-that-guy-again/bioAF/main/installer/quota.sh"
if [ -f "$QUOTA_HELPER_LOCAL" ]; then
    # shellcheck source=installer/quota.sh
    source "$QUOTA_HELPER_LOCAL"
else
    quota_helper_payload="$(curl -fsSL "$QUOTA_HELPER_URL" 2>/dev/null || true)"
    if [ -z "$quota_helper_payload" ]; then
        yellow "  Could not load the quota helper. Skipping auto-quota-request."
        yellow "  If pipeline runs hit QUOTA_EXCEEDED later, request increases in"
        yellow "  the Cloud Console: IAM & Admin -> Quotas."
    else
        eval "$quota_helper_payload"
    fi
fi

if declare -F bioaf_quota_ensure_all >/dev/null 2>&1; then
    bioaf_quota_ensure_all "$PROJECT_ID" "$REGION"
fi

# ---------------------------------------------------------------------------
# Step 6: Create firewall rule
# ---------------------------------------------------------------------------
echo ""
bold "Step 6: Firewall Rule"

existing_fw=$(gcloud compute firewall-rules describe "$FIREWALL_RULE_NAME" --project="$PROJECT_ID" --format="value(name)" 2>/dev/null || echo "")
if [ -n "$existing_fw" ]; then
    green "  Firewall rule '$FIREWALL_RULE_NAME' already exists."
else
    echo "  Creating firewall rule for ports 80 (HTTP) and 443 (HTTPS)..."
    gcloud compute firewall-rules create "$FIREWALL_RULE_NAME" \
        --project="$PROJECT_ID" \
        --allow=tcp:80,tcp:443 \
        --target-tags="$NETWORK_TAG" \
        --source-ranges=0.0.0.0/0 \
        --description="Allow web traffic to bioAF" \
        --quiet
    green "  Firewall rule created."
fi

# ---------------------------------------------------------------------------
# Step 7a: Service accounts (bioaf-bootstrap + bioaf-app) and tag
# ---------------------------------------------------------------------------
echo ""
bold "Step 7a: Service accounts and tag"
echo ""
echo "  Creating two scoped SAs (no JSON keys are generated):"
echo "    - bioaf-app:       attached to the VM as the runtime data plane"
echo "    - bioaf-bootstrap: impersonated for IAM/Terraform/Cloud Build"
echo ""

BOOTSTRAP_SA_EMAIL="${BOOTSTRAP_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
APP_SA_EMAIL="${APP_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# 1. Resource Manager tag (project-scoped). Idempotent.
if ! gcloud resource-manager tags keys describe "${PROJECT_ID}/${BIOAF_TAG_KEY}" \
        --quiet >/dev/null 2>&1; then
    echo "  Creating tag key ${BIOAF_TAG_KEY}..."
    gcloud resource-manager tags keys create "${BIOAF_TAG_KEY}" \
        --parent="projects/${PROJECT_ID}" \
        --description="Marks resources managed by bioAF" \
        --quiet || yellow "  Tag key creation returned non-zero (may already exist)."
fi

if ! gcloud resource-manager tags values describe "${PROJECT_ID}/${BIOAF_TAG_KEY}/${BIOAF_TAG_VALUE}" \
        --quiet >/dev/null 2>&1; then
    echo "  Creating tag value ${BIOAF_TAG_VALUE}..."
    gcloud resource-manager tags values create "${BIOAF_TAG_VALUE}" \
        --parent="${PROJECT_ID}/${BIOAF_TAG_KEY}" \
        --description="bioAF-owned resource" \
        --quiet || yellow "  Tag value creation returned non-zero (may already exist)."
fi

# 2. Custom IAM role bioafSaManager (project-scoped). Idempotent.
if gcloud iam roles describe "${BIOAFSAMANAGER_ROLE_ID}" --project="${PROJECT_ID}" \
        --quiet >/dev/null 2>&1; then
    echo "  Custom role ${BIOAFSAMANAGER_ROLE_ID} already exists."
else
    echo "  Creating custom role ${BIOAFSAMANAGER_ROLE_ID}..."
    gcloud iam roles create "${BIOAFSAMANAGER_ROLE_ID}" \
        --project="${PROJECT_ID}" \
        --title="bioAF SA Manager" \
        --description="Lookup/list/delete bioAF-prefixed service accounts" \
        --permissions="iam.serviceAccounts.get,iam.serviceAccounts.list,iam.serviceAccounts.delete" \
        --stage=GA \
        --quiet
fi

# Wait for an SA to be globally visible to IAM before granting roles. Newly
# created SAs can take 5-30 seconds to propagate; until then,
# `add-iam-policy-binding` returns "Service account ... does not exist".
wait_for_sa() {
    local sa_email="$1"
    local attempts=20
    local delay=3
    for ((i=1; i<=attempts; i++)); do
        if gcloud iam service-accounts describe "${sa_email}" \
                --project="${PROJECT_ID}" --quiet >/dev/null 2>&1; then
            # Probe a getIamPolicy call -- describe sometimes succeeds before
            # IAM policy operations on the SA do.
            if gcloud iam service-accounts get-iam-policy "${sa_email}" \
                    --project="${PROJECT_ID}" --quiet >/dev/null 2>&1; then
                return 0
            fi
        fi
        sleep "${delay}"
    done
    red "  Timed out waiting for ${sa_email} to propagate."
    return 1
}

# 3. Create bioaf-bootstrap (idempotent).
if gcloud iam service-accounts describe "${BOOTSTRAP_SA_EMAIL}" \
        --project="${PROJECT_ID}" --quiet >/dev/null 2>&1; then
    echo "  ${BOOTSTRAP_SA_NAME} already exists."
else
    gcloud iam service-accounts create "${BOOTSTRAP_SA_NAME}" \
        --project="${PROJECT_ID}" \
        --display-name="bioAF Bootstrap" \
        --description="Impersonated by bioAF backend for IAM/Terraform/Cloud Build" \
        --quiet
    green "  Created ${BOOTSTRAP_SA_NAME}."
fi
wait_for_sa "${BOOTSTRAP_SA_EMAIL}"

# 4. Create bioaf-app (idempotent).
if gcloud iam service-accounts describe "${APP_SA_EMAIL}" \
        --project="${PROJECT_ID}" --quiet >/dev/null 2>&1; then
    echo "  ${APP_SA_NAME} already exists."
else
    gcloud iam service-accounts create "${APP_SA_NAME}" \
        --project="${PROJECT_ID}" \
        --display-name="bioAF Application" \
        --description="Attached to the bioAF VM; runtime data-plane SA" \
        --quiet
    green "  Created ${APP_SA_NAME}."
fi
wait_for_sa "${APP_SA_EMAIL}"

# 5. Grant the broad set to bioaf-bootstrap.
echo "  Granting project roles to ${BOOTSTRAP_SA_NAME}..."
for role in "${BOOTSTRAP_ROLES[@]}"; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${BOOTSTRAP_SA_EMAIL}" \
        --role="${role}" \
        --condition=None \
        --quiet >/dev/null
done

# 6. Grant the unconditioned bindings to bioaf-app.
echo "  Granting unconditioned project roles to ${APP_SA_NAME}..."
for role in "${APP_UNCONDITIONED_ROLES[@]}"; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${APP_SA_EMAIL}" \
        --role="${role}" \
        --condition=None \
        --quiet >/dev/null
done

# 7. Conditioned bindings for bioaf-app.
echo "  Granting scoped (conditioned) project roles to ${APP_SA_NAME}..."
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${APP_SA_EMAIL}" \
    --role="roles/storage.admin" \
    --condition='expression=resource.name.startsWith("projects/_/buckets/bioaf-"),title=bioaf_buckets_only,description=bioaf_buckets_only' \
    --quiet >/dev/null

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${APP_SA_EMAIL}" \
    --role="projects/${PROJECT_ID}/roles/${BIOAFSAMANAGER_ROLE_ID}" \
    --condition="expression=resource.name.startsWith(\"projects/${PROJECT_ID}/serviceAccounts/bioaf-\"),title=bioaf_sas_only,description=bioaf_sas_only" \
    --quiet >/dev/null

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${APP_SA_EMAIL}" \
    --role="roles/compute.instanceAdmin.v1" \
    --condition='expression=resource.name.extract("/instances/{name}").startsWith("bioaf-"),title=bioaf_worknodes_only,description=bioaf_worknodes_only' \
    --quiet >/dev/null

# container.admin: scope by GKE cluster name prefix. We previously tried a
# matchTag() condition referencing a Resource Manager tag, but GKE clusters
# are regional resources and google_tags_tag_binding (the global tag API)
# does not accept them. The extract/startsWith pattern matches both
# container.clusters.* (where resource.name ends in /clusters/<name>) and
# container.nodePools.* (which IAM-checks against the parent cluster).
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${APP_SA_EMAIL}" \
    --role="roles/container.admin" \
    --condition='expression=resource.name.extract("/clusters/{name}").startsWith("bioaf-"),title=bioaf_clusters_only,description=bioaf_clusters_only' \
    --quiet >/dev/null

# 8. Resource-scoped tokenCreator on bioaf-bootstrap only.
gcloud iam service-accounts add-iam-policy-binding "${BOOTSTRAP_SA_EMAIL}" \
    --project="${PROJECT_ID}" \
    --member="serviceAccount:${APP_SA_EMAIL}" \
    --role="roles/iam.serviceAccountTokenCreator" \
    --quiet >/dev/null

# 8a. Sheets reader SA (keyless). bioaf-app impersonates this SA to read
#     Google Sheets the user has shared with READER_SA_EMAIL. No JSON key
#     is created; the SA is reachable via short-lived impersonated tokens.
READER_SA_EMAIL="${READER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SHEETS_READER_PROVISIONED=false

if gcloud iam service-accounts describe "${READER_SA_EMAIL}" \
        --project="${PROJECT_ID}" --quiet >/dev/null 2>&1; then
    echo "  ${READER_SA_NAME} already exists."
    SHEETS_READER_PROVISIONED=true
else
    gcloud iam service-accounts create "${READER_SA_NAME}" \
        --project="${PROJECT_ID}" \
        --display-name="bioAF Sheets Reader" \
        --description="Read-only access to Google Sheets shared with this email" \
        --quiet
    green "  Created ${READER_SA_NAME}."
    SHEETS_READER_PROVISIONED=true
fi

if [ "${SHEETS_READER_PROVISIONED}" = true ]; then
    wait_for_sa "${READER_SA_EMAIL}"

    # Grant bioaf-app tokenCreator on bioaf-reader so the runtime can mint
    # impersonated tokens for it. Resource-scoped to this SA only.
    gcloud iam service-accounts add-iam-policy-binding "${READER_SA_EMAIL}" \
        --project="${PROJECT_ID}" \
        --member="serviceAccount:${APP_SA_EMAIL}" \
        --role="roles/iam.serviceAccountTokenCreator" \
        --quiet >/dev/null

    # Enable the Sheets API so impersonated calls succeed.
    gcloud services enable sheets.googleapis.com \
        --project="${PROJECT_ID}" \
        --quiet >/dev/null 2>&1 || \
        yellow "  Could not enable sheets.googleapis.com automatically; enable it later if needed."
fi

# 9. tagUser on the bioaf-managed tag VALUE for bioaf-bootstrap so Terraform
#    can attach the tag to GKE resources it creates.
gcloud resource-manager tags values add-iam-policy-binding \
    "${PROJECT_ID}/${BIOAF_TAG_KEY}/${BIOAF_TAG_VALUE}" \
    --member="serviceAccount:${BOOTSTRAP_SA_EMAIL}" \
    --role="roles/resourcemanager.tagUser" \
    --quiet >/dev/null

green "  Service accounts, tag, and IAM bindings ready."

# ---------------------------------------------------------------------------
# Step 7: Create VM
# ---------------------------------------------------------------------------
echo ""
bold "Step 7: Create VM"
echo ""
echo "  How will you access bioAF?"
echo ""
echo "    1. Public IP -- accessible from anywhere over the internet"
echo "    2. Private IP -- accessible only through a VPN or internal network"
echo ""
read -rp "  Choose (1 or 2): [1] " access_choice
access_choice="${access_choice:-1}"

USE_PUBLIC_IP=true
if [ "$access_choice" = "2" ]; then
    USE_PUBLIC_IP=false
    echo ""
    yellow "  You chose private IP. The VM will not be reachable from the internet."
    echo "  You will need a VPN or other connectivity to your GCP VPC to access"
    echo "  the VM. This script cannot set up a VPN for you."
    echo ""
    read -rp "  Continue with private IP? [Y/n] " confirm_private
    if [ "$confirm_private" = "n" ] || [ "$confirm_private" = "N" ]; then
        echo "  Switching to public IP."
        USE_PUBLIC_IP=true
    fi
fi

echo ""
echo "  Configuration:"
echo "    Name:         $VM_NAME"
echo "    Machine type: $MACHINE_TYPE (~\$25/month)"
echo "    Disk:         $BOOT_DISK_SIZE SSD (~\$2/month)"
echo "    OS:           Ubuntu 22.04 LTS"
echo "    Region:       $REGION"
if [ "$USE_PUBLIC_IP" = true ]; then
    echo "    Network:      Public IP (internet-accessible)"
else
    echo "    Network:      Private IP only (VPN required)"
fi
echo ""

# Check if VM already exists in any zone for this region
existing_vm=""
for z in "${REGION_ZONES[@]}"; do
    existing_vm=$(gcloud compute instances describe "$VM_NAME" --zone="$z" --project="$PROJECT_ID" --format="value(name)" 2>/dev/null || echo "")
    if [ -n "$existing_vm" ]; then
        ZONE="$z"
        break
    fi
done

if [ -n "$existing_vm" ]; then
    yellow "  VM '$VM_NAME' already exists in $ZONE."
    echo "  Skipping VM creation."
else
    read -rp "  Create this VM? [Y/n] " create_vm
    if [ "$create_vm" = "n" ] || [ "$create_vm" = "N" ]; then
        echo "  Skipping VM creation."
    else
        # Try each zone in the region until one succeeds
        vm_created=false
        for try_zone in "${REGION_ZONES[@]}"; do
            echo "  Creating VM in $try_zone (this takes about 30 seconds)..."

            create_args=(
                --project="$PROJECT_ID"
                --zone="$try_zone"
                --machine-type="$MACHINE_TYPE"
                --image-family="$IMAGE_FAMILY"
                --image-project="$IMAGE_PROJECT"
                --boot-disk-size="$BOOT_DISK_SIZE"
                --boot-disk-type=pd-ssd
                --tags="$NETWORK_TAG"
                --scopes=cloud-platform
                --service-account="${APP_SA_EMAIL}"
            )

            if [ "$USE_PUBLIC_IP" = false ]; then
                create_args+=(--no-address)
            fi

            # Two metadata attributes: the Docker startup script, and the
            # bioaf-bootstrap SA email so the backend can persist it to
            # platform_config on first startup. The startup script is
            # multi-line so we materialise it to a temp file and pass via
            # --metadata-from-file alongside the inline --metadata key.
            STARTUP_TMP="$(mktemp -t bioaf-startup-XXXXXX)"
            cat >"${STARTUP_TMP}" <<'BIOAF_STARTUP_EOF'
#!/bin/bash
# Install Docker on first boot
if ! command -v docker &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    usermod -aG docker $(ls /home/ | head -1)
fi
BIOAF_STARTUP_EOF

            if gcloud compute instances create "$VM_NAME" \
                "${create_args[@]}" \
                --metadata="bioaf_bootstrap_sa_email=${BOOTSTRAP_SA_EMAIL}" \
                --metadata-from-file="startup-script=${STARTUP_TMP}" \
                --quiet 2>&1; then
                rm -f "${STARTUP_TMP}"
                ZONE="$try_zone"
                vm_created=true
                green "  VM created in $ZONE."
                break
            else
                rm -f "${STARTUP_TMP}"
                yellow "  Could not create VM in $try_zone. Trying next zone..."
            fi
        done

        if [ "$vm_created" = false ]; then
            echo ""
            red "  Could not create the VM in any zone in $REGION."
            echo ""
            echo "  Google Cloud does not have enough capacity for $MACHINE_TYPE VMs"
            echo "  in this region right now. This is a temporary GCP limitation,"
            echo "  not a bioAF issue."
            echo ""
            echo "  You can:"
            echo "    1. Wait a few minutes and try again"
            echo "    2. Re-run this script and select a different region"
            echo ""
            exit 1
        fi
    fi
fi

# Get the VM's IP address
if [ "$USE_PUBLIC_IP" = true ]; then
    VM_IP=$(gcloud compute instances describe "$VM_NAME" \
        --zone="$ZONE" \
        --project="$PROJECT_ID" \
        --format="value(networkInterfaces[0].accessConfigs[0].natIP)" 2>/dev/null || echo "")
else
    VM_IP=$(gcloud compute instances describe "$VM_NAME" \
        --zone="$ZONE" \
        --project="$PROJECT_ID" \
        --format="value(networkInterfaces[0].networkIP)" 2>/dev/null || echo "")
fi

# ---------------------------------------------------------------------------
# Wait for VM readiness
# ---------------------------------------------------------------------------
if [ -n "$VM_IP" ]; then
    echo ""
    bold "Waiting for VM to finish booting..."
    echo ""
    # Fixed 2-minute wait. The previous SSH/Docker liveness probes each
    # forked a fresh ssh process and prompted for the user's
    # ~/.ssh/google_compute_engine passphrase up to three times. A flat
    # sleep is generous enough for both sshd and the Docker startup-script
    # to come up on an e2-medium and avoids the prompts entirely.
    echo "  Sleeping 120s to let sshd start and the Docker startup script run."
    echo "  (If you've already added your GCE SSH key to ssh-agent, you can"
    echo "  Ctrl-C this wait and connect immediately.)"
    sleep 120
    green "  VM should be ready."
fi

# ---------------------------------------------------------------------------
# Build a prefill YAML so the setup wizard can pre-populate everything we
# already know. Used by both the auto-handoff path and the worksheet path.
# ---------------------------------------------------------------------------
PREFILL_LOCAL="${HOME}/.bioaf/prefill.yaml"
mkdir -p "${HOME}/.bioaf"
cat >"${PREFILL_LOCAL}" <<EOF
# bioAF setup prefill -- generated by install-gcp.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
gcp_project_id: ${PROJECT_ID}
gcp_region: ${REGION}
gcp_zone: ${ZONE}
gcp_credential_source: vm_default
gcp_bootstrap_sa_email: ${BOOTSTRAP_SA_EMAIL}
EOF

if [ "${SHEETS_READER_PROVISIONED}" = true ]; then
    cat >>"${PREFILL_LOCAL}" <<EOF
sheets_reader_sa_email: ${READER_SA_EMAIL}
sheets_reader_sa_created: "true"
EOF
fi

# ---------------------------------------------------------------------------
# Offer to finish the setup automatically: SCP the prefill, SSH in, clone
# bioAF, and run `./bioaf setup --prefill ...`. The user gets dropped into
# the same setup wizard with values already populated.
# ---------------------------------------------------------------------------
echo ""
bold "======================================"
bold "  GCP Infrastructure Ready"
bold "======================================"
echo ""

if [ -n "$VM_IP" ]; then
    echo "  VM external IP: $VM_IP"
    echo ""
    bold "  How would you like to finish setup?"
    echo ""
    echo "    1. Automatic -- I'll SSH in for you, clone bioAF, run setup,"
    echo "       and pre-populate the wizard with the values from this run."
    echo "    2. Manual    -- print a worksheet and you'll SSH in yourself."
    echo ""
    read -rp "  Choose (1 or 2): [1] " finish_choice
    finish_choice="${finish_choice:-1}"
fi

REMOTE_SETUP_SUCCEEDED=false

if [ "${finish_choice:-2}" = "1" ] && [ -n "$VM_IP" ]; then
    echo ""
    bold "Auto-handoff: copying prefill and starting setup on the VM"
    echo ""
    # Copy the prefill into the VM's user home. /tmp would be readable by
    # other users on the same VM, so prefer ~/.bioaf-prefill.yaml.
    if gcloud compute scp "${PREFILL_LOCAL}" \
        "${VM_NAME}:~/.bioaf-prefill.yaml" \
        --zone="${ZONE}" --project="${PROJECT_ID}" --quiet; then
        green "  Prefill copied to VM."
    else
        red "  Failed to copy prefill to the VM. Falling back to the worksheet."
        finish_choice=2
    fi
fi

if [ "${finish_choice:-2}" = "1" ] && [ -n "$VM_IP" ] && [ -f "${PREFILL_LOCAL}" ]; then
    echo ""
    echo "  Cloning bioAF and running setup on the VM. This will take a few minutes."
    echo "  You'll see the bioAF setup output stream below; the script ends with a"
    echo "  one-time setup code and the wizard URL."
    echo ""
    # Run setup non-interactively. The remote command:
    #   - clones bioAF (idempotent: skip if already there)
    #   - runs ./bioaf setup --prefill ~/.bioaf-prefill.yaml
    if gcloud compute ssh "${VM_NAME}" --zone="${ZONE}" --project="${PROJECT_ID}" --command='
set -euo pipefail
if [ ! -d "$HOME/bioAF/.git" ]; then
    git clone https://github.com/not-that-guy-again/bioAF.git "$HOME/bioAF"
fi
cd "$HOME/bioAF"
git pull --ff-only origin main 2>/dev/null || true
./bioaf setup --prefill "$HOME/.bioaf-prefill.yaml"
'; then
        REMOTE_SETUP_SUCCEEDED=true
    else
        red "  Remote setup failed. Falling back to the worksheet so you can run it yourself."
    fi
fi

if [ "$REMOTE_SETUP_SUCCEEDED" = true ]; then
    echo ""
    bold "======================================"
    bold "  Done"
    bold "======================================"
    echo ""
    echo "  Open bioAF in your browser:"
    echo ""
    green "     https://${VM_IP}"
    echo ""
    dim "     (Your browser will show a certificate warning for the self-signed cert."
    dim "      This is expected. Click 'Advanced' then 'Proceed' to continue.)"
    echo ""
    echo "  Use the setup code printed above to finish in the wizard. The wizard"
    echo "  will already have your project, region, and service account values"
    echo "  pre-populated from this installer run."
    echo ""
    exit 0
fi

# ---------------------------------------------------------------------------
# Manual path: print the worksheet and walk the user through the steps.
# ---------------------------------------------------------------------------
echo ""
bold "  Manual Next Steps"
echo ""
if [ -n "$VM_IP" ]; then
    echo "  1. SSH into your VM:"
    echo ""
    green "     gcloud compute ssh $VM_NAME --zone=$ZONE --project=$PROJECT_ID"
    echo ""
    echo "  2. Copy the prefill file we generated locally to the VM, then run setup:"
    echo ""
    green "     gcloud compute scp ${PREFILL_LOCAL} ${VM_NAME}:~/.bioaf-prefill.yaml \\"
    green "         --zone=${ZONE} --project=${PROJECT_ID}"
    echo ""
    echo "  Then on the VM:"
    echo ""
    green "     git clone https://github.com/not-that-guy-again/bioAF.git"
    green "     cd bioAF"
    green "     ./bioaf setup --prefill ~/.bioaf-prefill.yaml"
    echo ""
    echo "  3. Open bioAF in your browser:"
    echo ""
    green "     https://$VM_IP"
    echo ""
    dim "     (Your browser will show a certificate warning for the self-signed cert."
    dim "      This is expected. Click 'Advanced' then 'Proceed' to continue.)"
else
    echo "  1. SSH into your VM and clone bioAF:"
    echo ""
    green "     git clone https://github.com/not-that-guy-again/bioAF.git"
    green "     cd bioAF"
    green "     ./bioaf setup --prefill ~/.bioaf-prefill.yaml"
fi

echo ""
bold "  Setup Worksheet"
bold "  ----------------"
echo ""
echo "  Prefill file (also embedded above):"
echo ""
green "     ${PREFILL_LOCAL}"
echo ""
echo "  Contents:"
echo ""
sed 's/^/     /' "${PREFILL_LOCAL}"
echo ""
dim "  The setup wizard will detect the VM's attached identity and skip the"
dim "  key-upload step. Pre-populated values come from the prefill file."

echo ""
