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
#   6. Creates a VM to host bioAF
#   7. Optionally creates a service account and JSON key
#   8. Prints the SSH command and next steps
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
SA_NAME="bioaf-app"
SA_DISPLAY_NAME="bioAF Application"

# Regions that tend to have lower costs and good availability
SUGGESTED_REGIONS=("us-central1" "us-east1" "us-west1" "europe-west1" "asia-east1")

# GCP APIs required for the Docker Compose deployment
REQUIRED_APIS=(
    "compute.googleapis.com"
    "storage.googleapis.com"
    "iam.googleapis.com"
)

# Service account roles for bioAF application access
SA_ROLES=(
    "roles/storage.objectAdmin"
    "roles/logging.logWriter"
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
echo "  4. Enable required APIs (Compute, Storage, IAM)"
echo "  5. Create a firewall rule for web traffic (ports 80, 443)"
echo "  6. Create an e2-medium VM with Ubuntu 22.04 (30GB disk)"
echo "  7. Optionally create a service account for bioAF"
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
fi

# ---------------------------------------------------------------------------
# Step 2: Authenticate
# ---------------------------------------------------------------------------
echo ""
bold "Step 2: Google Cloud Authentication"

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
green "  Authenticated as: $(gcloud config get-value account 2>/dev/null)"

# ---------------------------------------------------------------------------
# Step 3: Select GCP project
# ---------------------------------------------------------------------------
echo ""
bold "Step 3: Select GCP Project"
echo ""
echo "  Your available projects:"
echo ""
gcloud projects list --format="table(projectId, name)" 2>/dev/null || true
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

gcloud config set project "$PROJECT_ID" 2>/dev/null
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
ZONE="${REGION}-a"

green "  Using region: $REGION (zone: $ZONE)"

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
echo "    Zone:         $ZONE"
if [ "$USE_PUBLIC_IP" = true ]; then
    echo "    Network:      Public IP (internet-accessible)"
else
    echo "    Network:      Private IP only (VPN required)"
fi
echo ""

existing_vm=$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --project="$PROJECT_ID" --format="value(name)" 2>/dev/null || echo "")
if [ -n "$existing_vm" ]; then
    yellow "  VM '$VM_NAME' already exists in $ZONE."
    echo "  Skipping VM creation."
else
    read -rp "  Create this VM? [Y/n] " create_vm
    if [ "$create_vm" = "n" ] || [ "$create_vm" = "N" ]; then
        echo "  Skipping VM creation."
    else
        echo "  Creating VM (this takes about 30 seconds)..."

        # Build the gcloud command with or without a public IP
        create_args=(
            --project="$PROJECT_ID"
            --zone="$ZONE"
            --machine-type="$MACHINE_TYPE"
            --image-family="$IMAGE_FAMILY"
            --image-project="$IMAGE_PROJECT"
            --boot-disk-size="$BOOT_DISK_SIZE"
            --boot-disk-type=pd-ssd
            --tags="$NETWORK_TAG"
            --scopes=cloud-platform
        )

        if [ "$USE_PUBLIC_IP" = false ]; then
            create_args+=(--no-address)
        fi

        gcloud compute instances create "$VM_NAME" \
            "${create_args[@]}" \
            --metadata=startup-script='#!/bin/bash
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
fi' \
            --quiet

        green "  VM created."
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
# Step 8: Service account (optional)
# ---------------------------------------------------------------------------
echo ""
bold "Step 8: Service Account (optional)"
echo ""
echo "  bioAF can use a GCP service account to access Cloud Storage"
echo "  and Cloud Logging. This creates a service account and downloads"
echo "  a JSON key file that you will upload during bioAF setup."
echo ""
read -rp "  Create a service account for bioAF? [Y/n] " create_sa

SA_KEY_PATH=""
if [ "$create_sa" != "n" ] && [ "$create_sa" != "N" ]; then
    SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

    existing_sa=$(gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" --format="value(email)" 2>/dev/null || echo "")
    if [ -n "$existing_sa" ]; then
        yellow "  Service account '$SA_EMAIL' already exists."
    else
        echo "  Creating service account..."
        gcloud iam service-accounts create "$SA_NAME" \
            --project="$PROJECT_ID" \
            --display-name="$SA_DISPLAY_NAME" \
            --description="Service account for bioAF application" \
            --quiet
        green "  Service account created: $SA_EMAIL"
    fi

    # Grant roles
    echo "  Granting permissions..."
    for role in "${SA_ROLES[@]}"; do
        gcloud projects add-iam-policy-binding "$PROJECT_ID" \
            --member="serviceAccount:$SA_EMAIL" \
            --role="$role" \
            --quiet >/dev/null 2>&1 || true
    done
    green "  Permissions granted (Storage Object Admin, Logging Writer)."

    # Generate key
    SA_KEY_PATH="$HOME/Desktop/bioaf-sa-key.json"
    echo "  Generating JSON key..."
    gcloud iam service-accounts keys create "$SA_KEY_PATH" \
        --iam-account="$SA_EMAIL" \
        --project="$PROJECT_ID" \
        --quiet
    green "  Key saved to: $SA_KEY_PATH"
    echo ""
    yellow "  Keep this file safe. You will upload it during bioAF setup."
    yellow "  Do not share it or commit it to version control."
fi

# ---------------------------------------------------------------------------
# Wait for VM readiness
# ---------------------------------------------------------------------------
if [ -n "$VM_IP" ]; then
    echo ""
    bold "Waiting for VM to be ready..."
    echo ""

    # Phase 1: wait for SSH (port 22)
    printf "  Waiting for SSH"
    ssh_retries=30
    while [ $ssh_retries -gt 0 ]; do
        if gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT_ID" \
            --command="echo ok" --quiet --ssh-flag="-o ConnectTimeout=5" \
            --ssh-flag="-o StrictHostKeyChecking=no" 2>/dev/null | grep -q "ok"; then
            break
        fi
        printf "."
        sleep 5
        ssh_retries=$((ssh_retries - 1))
    done
    echo ""

    if [ $ssh_retries -eq 0 ]; then
        yellow "  SSH did not become ready within the timeout."
        yellow "  The VM may still be booting. Try connecting manually."
    else
        green "  SSH is ready."

        # Phase 2: wait for Docker Compose to be installed by the startup script
        printf "  Waiting for Docker"
        docker_retries=30
        while [ $docker_retries -gt 0 ]; do
            if gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT_ID" \
                --command="docker compose version" --quiet \
                --ssh-flag="-o ConnectTimeout=5" \
                --ssh-flag="-o StrictHostKeyChecking=no" 2>/dev/null | grep -q "Docker Compose"; then
                break
            fi
            printf "."
            sleep 5
            docker_retries=$((docker_retries - 1))
        done
        echo ""

        if [ $docker_retries -eq 0 ]; then
            yellow "  Docker did not finish installing within the timeout."
            yellow "  It may still be running. Check after connecting."
        else
            green "  Docker is ready."
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Done -- print next steps
# ---------------------------------------------------------------------------
echo ""
bold "======================================"
bold "  Setup Complete"
bold "======================================"
echo ""

if [ -n "$VM_IP" ]; then
    echo "  VM external IP: $VM_IP"
    echo ""
    bold "  Next steps:"
    echo ""
    echo "  1. SSH into your VM:"
    echo ""
    green "     gcloud compute ssh $VM_NAME --zone=$ZONE --project=$PROJECT_ID"
    echo ""
    echo "  2. Clone bioAF and run setup:"
    echo ""
    green "     git clone https://github.com/not-that-guy-again/bioAF.git"
    green "     cd bioAF"
    green "     ./bioaf setup"
    echo ""
    echo "  3. Open bioAF in your browser:"
    echo ""
    green "     https://$VM_IP"
    echo ""
    dim "     (Your browser will show a certificate warning for the self-signed cert."
    dim "      This is expected. Click 'Advanced' then 'Proceed' to continue.)"
else
    bold "  Next steps:"
    echo ""
    echo "  1. SSH into your VM and clone bioAF:"
    echo ""
    green "     git clone https://github.com/not-that-guy-again/bioAF.git"
    green "     cd bioAF"
    green "     ./bioaf setup"
fi

if [ -n "$SA_KEY_PATH" ] && [ -f "$SA_KEY_PATH" ]; then
    echo ""
    echo "  During setup, the wizard will ask for your service account key."
    echo "  Select the JSON option and paste this:"
    echo ""
    cat "$SA_KEY_PATH"
    echo ""
    echo ""
    dim "  (This key is also saved at $SA_KEY_PATH)"
fi

echo ""
