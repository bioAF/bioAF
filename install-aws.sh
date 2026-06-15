#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# bioAF AWS Installer
#
# One-command setup for the AWS prerequisites bioAF runs on, plus an EC2 VM.
# The AWS sibling of install-gcp.sh. Designed to run on the user's local
# machine (macOS or Linux).
#
# SCOPE (deliberately stops early): this provisions the account substrate and
# brings up the VM, then STOPS so you can verify each piece by hand before
# installing bioAF itself. It does NOT run the bioAF setup wizard on the VM
# (that is the GCP script's auto-handoff; the AWS equivalent lands once the
# pieces below are validated).
#
# Usage:
#   ./install-aws.sh [--region us-east-1] [--ssh-cidr 0.0.0.0/0] [--verbose]
#
# What this script does:
#   1. Checks for (or installs) the AWS CLI v2
#   2. Verifies your AWS credentials (aws sts get-caller-identity)
#   3. Selects a region
#   4. Creates an EC2 key pair for SSH (saves the private key locally)
#   5. Creates a security group (tcp:80, tcp:443 from anywhere; tcp:22 from you)
#   6. Creates the bioaf-app IAM role + instance profile (starter policy)
#   7. Launches an Ubuntu 22.04 EC2 VM (IMDSv2-only) with the profile attached
#   8. Writes a prefill.yaml stamped cloud_provider: aws
#   9. Prints the SSH command and next steps, then stops
#
# What this script does NOT do:
#   - Store any long-lived credentials (the VM authenticates via its instance
#     profile, the AWS analog of the GCP VM's attached service account)
#   - Install bioAF itself (that happens on the VM, later)
#   - Provision S3/EKS/Secrets/etc. (Stage 6/7 substrate; provisioned next)
# ---------------------------------------------------------------------------

set -euo pipefail

# ---------------------------------------------------------------------------
# CLI flags (parsed before any output so --verbose disables log redirect)
# ---------------------------------------------------------------------------
BIOAF_VERBOSE=0
REGION_FLAG=""
SSH_CIDR=""
for arg in "$@"; do
    case "$arg" in
        --verbose|-v) BIOAF_VERBOSE=1 ;;
        --region=*)   REGION_FLAG="${arg#*=}" ;;
        --ssh-cidr=*) SSH_CIDR="${arg#*=}" ;;
        --help|-h)
            cat <<'USAGE'
Usage: install-aws.sh [--region <region>] [--ssh-cidr <cidr>] [--verbose]

Options:
  --region <region>   AWS region to deploy into (e.g. us-east-1). Defaults to
                      your configured region, else prompts.
  --ssh-cidr <cidr>   Source CIDR allowed to SSH (tcp:22). Defaults to your
                      current public IP /32. Pass 0.0.0.0/0 to allow SSH from
                      anywhere (e.g. to let a remote agent connect to the VM);
                      only do this on a disposable account.
  --verbose, -v       Print all command output inline instead of routing it to
                      the install log. Use when debugging a failed step.

The full transcript of each run is always written to:
  ~/.bioaf/install-aws.log
USAGE
            exit 0
            ;;
    esac
done
export BIOAF_VERBOSE
export BIOAF_INSTALL_LOG="${HOME}/.bioaf/install-aws.log"

# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
red()    { printf '\033[0;31m%s\033[0m\n' "$*"; }
green()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[0;33m%s\033[0m\n' "$*"; }
bold()   { printf '\033[1m%s\033[0m\n' "$*"; }
dim()    { printf '\033[2m%s\033[0m\n' "$*"; }

# ---------------------------------------------------------------------------
# Output helpers (step / maybe_step / section / warn / io_finish). Sourced from
# a local clone if present, otherwise fetched over HTTPS so this script still
# works as `curl|bash`. Identical contract to install-gcp.sh.
# ---------------------------------------------------------------------------
OUTPUT_HELPER_LOCAL="$(dirname "${BASH_SOURCE[0]:-$0}")/installer/output.sh"
OUTPUT_HELPER_URL="https://raw.githubusercontent.com/bioAF/bioAF/main/installer/output.sh"
if [ -f "$OUTPUT_HELPER_LOCAL" ]; then
    # shellcheck source=installer/output.sh
    source "$OUTPUT_HELPER_LOCAL"
else
    output_helper_payload="$(curl -fsSL "$OUTPUT_HELPER_URL" 2>/dev/null || true)"
    if [ -z "$output_helper_payload" ]; then
        printf 'Could not load the output helper from %s\n' "$OUTPUT_HELPER_URL" >&2
        printf 'Re-run with the bioAF repo cloned locally, or check your network.\n' >&2
        exit 1
    fi
    eval "$output_helper_payload"
fi
io_init

# ---------------------------------------------------------------------------
# Defaults. The AWS analogs of install-gcp.sh's VM_NAME / MACHINE_TYPE / etc.
# ---------------------------------------------------------------------------
VM_NAME="bioaf"
INSTANCE_TYPE="t3.medium"          # ~2 vCPU / 4 GiB, the EC2 analog of e2-medium
BOOT_DISK_GB="30"
KEY_NAME="bioaf"                   # EC2 key pair name
SECURITY_GROUP_NAME="bioaf-allow-web"
APP_ROLE_NAME="bioaf-app"          # IAM role + instance profile (analog of the app SA)
APP_POLICY_NAME="bioaf-app-starter"
# Canonical's public SSM parameter for the latest Ubuntu 22.04 amd64 AMI. Region
# resolves the right AMI id, so we never hardcode a per-region id.
UBUNTU_SSM_PARAM="/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp3/ami-id"

SUGGESTED_REGIONS=("us-east-1" "us-east-2" "us-west-2" "eu-west-1" "ap-southeast-1")

KEY_FILE_DIR="${HOME}/.ssh"

# ---------------------------------------------------------------------------
# Intro
# ---------------------------------------------------------------------------
section "bioAF AWS Installer"
note "Provisions the AWS prerequisites + an EC2 VM, then stops for manual checks."
note "Transcript: ${BIOAF_INSTALL_LOG}"

# ---------------------------------------------------------------------------
# 1. AWS CLI
# ---------------------------------------------------------------------------
section "Step 1 / 8  -  AWS CLI"
if command -v aws &>/dev/null; then
    step "AWS CLI present ($(aws --version 2>&1 | awk '{print $1}'))" -- true
else
    yellow "  The AWS CLI v2 is not installed."
    if [ "$(uname -s)" = "Darwin" ] && command -v brew &>/dev/null; then
        read -rp "  Install it now with Homebrew? [Y/n] " ans
        if [ "$ans" != "n" ] && [ "$ans" != "N" ]; then
            step "Install awscli via Homebrew" -- brew install awscli
        fi
    fi
fi
if ! command -v aws &>/dev/null; then
    red "  AWS CLI still not found. Install it, then re-run this script:"
    echo ""
    if [ "$(uname -s)" = "Darwin" ]; then
        green '    curl "https://awscli.amazonaws.com/AWSCLIV2.pkg" -o /tmp/AWSCLIV2.pkg'
        green "    sudo installer -pkg /tmp/AWSCLIV2.pkg -target /"
    else
        green '    curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip'
        green "    unzip -q /tmp/awscliv2.zip -d /tmp && sudo /tmp/aws/install"
    fi
    echo ""
    note "Docs: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
fi

# ---------------------------------------------------------------------------
# 2. Credentials
# ---------------------------------------------------------------------------
section "Step 2 / 8  -  Credentials"
if ! aws sts get-caller-identity >/dev/null 2>&1; then
    yellow "  No working AWS credentials were found."
    echo ""
    echo "  Create an access key in the AWS console (IAM -> Users -> your user ->"
    echo "  Security credentials -> Create access key), then configure the CLI:"
    echo ""
    green "    aws configure"
    echo ""
    echo "  (You'll paste the Access Key ID + Secret, and set a default region.)"
    echo "  Then re-run this script."
    exit 1
fi
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
CALLER_ARN="$(aws sts get-caller-identity --query Arn --output text)"
step "Authenticated as ${CALLER_ARN}" -- true
note "Account: ${ACCOUNT_ID}"

# ---------------------------------------------------------------------------
# 3. Region
# ---------------------------------------------------------------------------
section "Step 3 / 8  -  Region"
REGION="${REGION_FLAG:-}"
if [ -z "$REGION" ]; then
    REGION="$(aws configure get region 2>/dev/null || true)"
fi
if [ -z "$REGION" ]; then
    echo "  Suggested regions: ${SUGGESTED_REGIONS[*]}"
    read -rp "  Region to deploy into: [us-east-1] " REGION
    REGION="${REGION:-us-east-1}"
fi
export AWS_DEFAULT_REGION="$REGION"
step "Using region ${REGION}" -- true

# Resolve the default VPC + a subnet in it (new accounts have a default VPC).
VPC_ID="$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
    --query 'Vpcs[0].VpcId' --output text 2>/dev/null || echo "None")"
if [ "$VPC_ID" = "None" ] || [ -z "$VPC_ID" ]; then
    red "  No default VPC in ${REGION}. Create one (aws ec2 create-default-vpc) or"
    red "  pick a region that has one, then re-run."
    exit 1
fi
SUBNET_ID="$(aws ec2 describe-subnets \
    --filters "Name=vpc-id,Values=${VPC_ID}" "Name=default-for-az,Values=true" \
    --query 'Subnets[0].SubnetId' --output text 2>/dev/null || echo "None")"
if [ "$SUBNET_ID" = "None" ] || [ -z "$SUBNET_ID" ]; then
    SUBNET_ID="$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=${VPC_ID}" \
        --query 'Subnets[0].SubnetId' --output text)"
fi
note "Default VPC: ${VPC_ID}  Subnet: ${SUBNET_ID}"

# ---------------------------------------------------------------------------
# 4. SSH key pair
# ---------------------------------------------------------------------------
section "Step 4 / 8  -  SSH key pair"
mkdir -p "$KEY_FILE_DIR"
KEY_FILE="${KEY_FILE_DIR}/${KEY_NAME}-aws-${REGION}.pem"
if aws ec2 describe-key-pairs --key-names "$KEY_NAME" >/dev/null 2>&1; then
    step "Key pair '${KEY_NAME}' already exists in ${REGION}" -- true
    if [ ! -f "$KEY_FILE" ]; then
        warn "Private key ${KEY_FILE} is not on this machine. SSH needs the .pem from"
        warn "when this key pair was first created (AWS cannot re-export it). To start"
        warn "fresh, delete the key pair (aws ec2 delete-key-pair --key-name ${KEY_NAME})"
        warn "and re-run."
    fi
else
    step "Create key pair '${KEY_NAME}' (private key -> ${KEY_FILE})" -- bash -c "
        umask 177
        aws ec2 create-key-pair --key-name '${KEY_NAME}' \
            --query 'KeyMaterial' --output text > '${KEY_FILE}'
    "
    chmod 400 "$KEY_FILE" 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# 5. Security group
# ---------------------------------------------------------------------------
section "Step 5 / 8  -  Security group"
if [ -z "$SSH_CIDR" ]; then
    MY_IP="$(curl -fsS https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
    if [ -n "$MY_IP" ]; then
        SSH_CIDR="${MY_IP}/32"
        note "Restricting SSH (tcp:22) to your current IP: ${SSH_CIDR}"
    else
        SSH_CIDR="0.0.0.0/0"
        warn "Could not detect your public IP; opening SSH to ${SSH_CIDR}."
    fi
fi

SG_ID="$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=${SECURITY_GROUP_NAME}" "Name=vpc-id,Values=${VPC_ID}" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")"
if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
    SG_ID="$(aws ec2 create-security-group --group-name "$SECURITY_GROUP_NAME" \
        --description "bioAF web + SSH" --vpc-id "$VPC_ID" \
        --query 'GroupId' --output text)"
    step "Created security group ${SECURITY_GROUP_NAME} (${SG_ID})" -- true
else
    step "Security group ${SECURITY_GROUP_NAME} already exists (${SG_ID})" -- true
fi

# Authorize ingress rules (idempotent: a duplicate rule returns
# InvalidPermission.Duplicate, which we treat as already-present).
authorize_ingress() {
    local proto="$1" port="$2" cidr="$3" label="$4"
    if aws ec2 authorize-security-group-ingress --group-id "$SG_ID" \
        --ip-permissions "IpProtocol=${proto},FromPort=${port},ToPort=${port},IpRanges=[{CidrIp=${cidr}}]" \
        >/dev/null 2>&1; then
        step "Allow ${label} (${proto}:${port} from ${cidr})" -- true
    else
        maybe_step "Allow ${label} (${proto}:${port} from ${cidr}) - already present" -- true
    fi
}
authorize_ingress tcp 80 "0.0.0.0/0" "HTTP"
authorize_ingress tcp 443 "0.0.0.0/0" "HTTPS"
authorize_ingress tcp 22 "$SSH_CIDR" "SSH"

# ---------------------------------------------------------------------------
# 6. IAM role + instance profile (the AWS analog of the GCP app service account)
# ---------------------------------------------------------------------------
section "Step 6 / 8  -  IAM role + instance profile"
TRUST_DOC="$(mktemp -t bioaf-trust-XXXXXX.json)"
cat >"$TRUST_DOC" <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}
  ]
}
JSON

if aws iam get-role --role-name "$APP_ROLE_NAME" >/dev/null 2>&1; then
    step "IAM role ${APP_ROLE_NAME} already exists" -- true
else
    step "Create IAM role ${APP_ROLE_NAME}" -- \
        aws iam create-role --role-name "$APP_ROLE_NAME" \
            --assume-role-policy-document "file://${TRUST_DOC}" \
            --description "bioAF application role (EC2 instance profile)"
fi

# Starter inline policy. Intentionally broad enough to bring the app up and let
# the S3StorageProvider reach bioaf-* buckets via the instance profile; TIGHTEN
# in Stage 7 (least-privilege per seam). S3 is scoped to bioaf-* names; the
# control-plane services are left at "*" because most do not support
# resource-level scoping cleanly and this is a starter.
POLICY_DOC="$(mktemp -t bioaf-policy-XXXXXX.json)"
cat >"$POLICY_DOC" <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BioafS3Buckets",
      "Effect": "Allow",
      "Action": "s3:*",
      "Resource": ["arn:aws:s3:::bioaf-*", "arn:aws:s3:::bioaf-*/*"]
    },
    {
      "Sid": "BioafControlPlaneStarter",
      "Effect": "Allow",
      "Action": [
        "ec2:Describe*",
        "ec2:CreateTags",
        "secretsmanager:GetSecretValue",
        "secretsmanager:CreateSecret",
        "secretsmanager:PutSecretValue",
        "ecr:GetAuthorizationToken",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer",
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "ce:GetCostAndUsage"
      ],
      "Resource": "*"
    }
  ]
}
JSON
step "Attach starter policy ${APP_POLICY_NAME} (TIGHTEN in Stage 7)" -- \
    aws iam put-role-policy --role-name "$APP_ROLE_NAME" \
        --policy-name "$APP_POLICY_NAME" \
        --policy-document "file://${POLICY_DOC}"

if aws iam get-instance-profile --instance-profile-name "$APP_ROLE_NAME" >/dev/null 2>&1; then
    step "Instance profile ${APP_ROLE_NAME} already exists" -- true
else
    step "Create instance profile ${APP_ROLE_NAME}" -- \
        aws iam create-instance-profile --instance-profile-name "$APP_ROLE_NAME"
fi
# add-role-to-instance-profile is idempotent-ish: a second add returns
# LimitExceeded (a profile holds one role), so treat failure as already-linked.
maybe_step "Link role ${APP_ROLE_NAME} to its instance profile" -- \
    aws iam add-role-to-instance-profile \
        --instance-profile-name "$APP_ROLE_NAME" --role-name "$APP_ROLE_NAME"
# IAM is eventually consistent; RunInstances can 400 on a just-created profile.
step "Wait 10s for the instance profile to propagate" -- sleep 10

# ---------------------------------------------------------------------------
# 7. EC2 VM
# ---------------------------------------------------------------------------
section "Step 7 / 8  -  EC2 VM"
AMI_ID="$(aws ssm get-parameters --names "$UBUNTU_SSM_PARAM" \
    --query 'Parameters[0].Value' --output text 2>/dev/null || echo "None")"
if [ "$AMI_ID" = "None" ] || [ -z "$AMI_ID" ]; then
    red "  Could not resolve the Ubuntu 22.04 AMI in ${REGION} from SSM."
    exit 1
fi
note "Ubuntu 22.04 AMI: ${AMI_ID}"

# Skip if a bioAF VM is already running/pending in this region.
EXISTING_VM="$(aws ec2 describe-instances \
    --filters "Name=tag:Name,Values=${VM_NAME}" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || echo "None")"

echo ""
echo "  Configuration:"
echo "    Name:          ${VM_NAME}"
echo "    Instance type: ${INSTANCE_TYPE} (~\$30/month on-demand)"
echo "    Disk:          ${BOOT_DISK_GB} GiB gp3"
echo "    OS:            Ubuntu 22.04 LTS"
echo "    Region:        ${REGION}"
echo "    Profile:       ${APP_ROLE_NAME} (instance profile)"
echo "    SSH from:      ${SSH_CIDR}"
echo ""

if [ "$EXISTING_VM" != "None" ] && [ -n "$EXISTING_VM" ]; then
    warn "VM '${VM_NAME}' already exists (${EXISTING_VM}) - skipping launch."
    INSTANCE_ID="$EXISTING_VM"
else
    read -rp "  Launch this VM? [Y/n] " create_vm
    if [ "$create_vm" = "n" ] || [ "$create_vm" = "N" ]; then
        say "  Skipping VM launch."
        INSTANCE_ID=""
    else
        # First-boot user-data: install Docker + certbot, mirroring the GCP
        # startup script so the VM is identically prepared for bioAF.
        USERDATA_TMP="$(mktemp -t bioaf-userdata-XXXXXX)"
        cat >"$USERDATA_TMP" <<'BIOAF_USERDATA_EOF'
#!/bin/bash
set -e
if ! command -v docker &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    usermod -aG docker ubuntu
fi
if ! command -v certbot &>/dev/null; then
    apt-get install -y -qq certbot
fi
mkdir -p /var/www/letsencrypt
chmod 755 /var/www /var/www/letsencrypt
BIOAF_USERDATA_EOF

        INSTANCE_ID="$(aws ec2 run-instances \
            --image-id "$AMI_ID" \
            --instance-type "$INSTANCE_TYPE" \
            --key-name "$KEY_NAME" \
            --security-group-ids "$SG_ID" \
            --subnet-id "$SUBNET_ID" \
            --associate-public-ip-address \
            --iam-instance-profile "Name=${APP_ROLE_NAME}" \
            --metadata-options "HttpTokens=required,HttpEndpoint=enabled" \
            --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=${BOOT_DISK_GB},VolumeType=gp3}" \
            --user-data "file://${USERDATA_TMP}" \
            --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${VM_NAME}},{Key=bioaf_cloud_provider,Value=aws}]" \
            --query 'Instances[0].InstanceId' --output text)"
        step "Launched VM '${VM_NAME}' (${INSTANCE_ID})" -- true
    fi
fi

VM_IP=""
if [ -n "${INSTANCE_ID}" ]; then
    step "Wait for ${INSTANCE_ID} to reach 'running'" -- \
        aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
    VM_IP="$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
        --query 'Reservations[0].Instances[0].PublicIpAddress' --output text 2>/dev/null || echo "")"
    note "Public IP: ${VM_IP}"
fi

# ---------------------------------------------------------------------------
# 8. Prefill + next steps
# ---------------------------------------------------------------------------
section "Step 8 / 8  -  Prefill + next steps"
APP_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${APP_ROLE_NAME}"
PREFILL_LOCAL="${HOME}/.bioaf/prefill-aws.yaml"
mkdir -p "${HOME}/.bioaf"
cat >"${PREFILL_LOCAL}" <<EOF
# bioAF setup prefill - generated by install-aws.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
cloud_provider: aws
aws_account_id: ${ACCOUNT_ID}
aws_region: ${REGION}
aws_credential_source: instance_profile
aws_app_role_arn: ${APP_ROLE_ARN}
EOF
step "Wrote ${PREFILL_LOCAL}" -- true

echo ""
bold "======================================"
bold "  AWS Infrastructure Ready"
bold "======================================"
echo ""
echo "  Account:        ${ACCOUNT_ID}"
echo "  Region:         ${REGION}"
echo "  Security group: ${SG_ID}"
echo "  Instance role:  ${APP_ROLE_ARN}"
if [ -n "${INSTANCE_ID:-}" ]; then
    echo "  VM:             ${INSTANCE_ID}"
fi
if [ -n "$VM_IP" ]; then
    echo "  VM public IP:   ${VM_IP}"
    echo ""
    bold "  SSH into the VM:"
    if [ -f "$KEY_FILE" ]; then
        green "     ssh -i ${KEY_FILE} ubuntu@${VM_IP}"
    else
        green "     ssh -i <your ${KEY_NAME}.pem> ubuntu@${VM_IP}"
    fi
fi
echo ""
bold "  Stopping here (scoped install). Verify by hand, then we continue:"
echo "    - SSH in and confirm Docker installed (user-data runs on first boot)"
echo "    - confirm the instance profile is attached:"
green "        aws ec2 describe-instances --instance-ids ${INSTANCE_ID:-<id>} \\"
green "          --query 'Reservations[0].Instances[0].IamInstanceProfile'"
echo "    - the prefill (cloud_provider: aws) is at ${PREFILL_LOCAL}"
echo ""
note "Next stages provision S3/Secrets/EKS substrate and run the bioAF setup wizard."

io_finish
