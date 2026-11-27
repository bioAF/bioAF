#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# bioAF AWS Installer
#
# One-command setup for the AWS prerequisites bioAF runs on, plus an EC2 VM.
# The AWS sibling of install-gcp.sh. Designed to run on the user's local
# machine (macOS or Linux).
#
# SCOPE: this provisions the account substrate (IAM role + instance profile,
# security group) and brings up the EC2 VM, then offers to finish bioAF setup
# on the VM for you (the automatic handoff) or prints a worksheet so you can SSH
# in and run it yourself. The AWS sibling of install-gcp.sh's finish step. It
# does NOT provision the S3/Secrets/EKS substrate (Stage 6/7, run later).
#
# Usage:
#   ./install-aws.sh [--region us-east-1] [--ssh-cidr 203.0.113.7/32] [--ref <ref>] [--verbose]
#   ./install-aws.sh --reconcile-permissions [--region us-east-1]   (existing installation)
#
# What this script does:
#   1. Checks for (or installs) the AWS CLI v2; on macOS it installs it with
#      Homebrew, installing Homebrew first (added to your shell profile)
#   2. Verifies your AWS credentials (aws sts get-caller-identity)
#   3. Selects a region
#   4. Creates an EC2 key pair for SSH (saves the private key locally)
#   5. Creates a security group (tcp:80, tcp:443, tcp:8443 from anywhere; tcp:22 from you)
#   6. Creates the bioaf-app IAM role + instance profile (starter policy)
#   7. Launches an Ubuntu 22.04 EC2 VM (IMDSv2-only) with the profile attached
#   8. Writes a prefill (~/.bioaf/prefill.yaml) stamped cloud_provider: aws
#   9. Offers to finish setup on the VM (automatic) or prints a worksheet
#
# What this script does NOT do:
#   - Store any long-lived credentials (the VM authenticates via its instance
#     profile, the AWS analog of the GCP VM's attached service account)
#   - Provision S3/EKS/Secrets/etc. (Stage 6/7 substrate; provisioned next)
# ---------------------------------------------------------------------------

set -euo pipefail

# Read the whole script before running any of it. Under `curl ... | bash`
# bash reads this script from stdin, and attach_terminal (below) points
# stdin at the terminal instead. The matching brace is at the end of the file.
{

# >>> bioaf helper loader
# The same block is in install-gcp.sh and install-aws.sh. It stays in each installer (not in a
# helper) because it is what loads the first helper.
#
# Helpers come from installer/ beside this script only when it runs from a script file that has
# them (a clone). Run any other way, including `curl ... | bash`, they are downloaded from the ref
# this installer came from: --ref or BIOAF_INSTALLER_REF, else main (the rolling one-liner; a piped
# script cannot tell which URL it was fetched from). The current directory is never searched. A
# download that fails, is empty, is not valid shell, or lacks a function the installer needs is
# never used, and a selected ref is never replaced by main.
BIOAF_RAW_BASE="https://raw.githubusercontent.com/bioAF/bioAF"
BIOAF_SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    BIOAF_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

bioaf_ref_valid() {
    # A branch, tag or commit name: no path tricks, spaces, shell syntax or option-like values.
    case "$1" in "" | -* | *..* | *//* | */ | *.lock) return 1 ;; esac
    [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$ ]]
}

_bioaf_helper_error() {
    # _bioaf_helper_error <required|optional> <source> <problem>
    if [ "$1" = required ]; then
        printf 'Could not load %s: it %s.\n' "$2" "$3" >&2
        printf 'Check your network. A pinned installer needs its ref: --ref <tag or commit> (see --help).\n' >&2
    else
        printf '  Skipping optional helper %s: it %s.\n' "$2" "$3" >&2
    fi
}

bioaf_load_helper() {
    # bioaf_load_helper <installer/file> <required|optional> <function>...: load one helper and check
    # that it defines every named function. Returns 1, after a message, instead of using a bad one.
    local rel="$1" need="$2" origin payload fn
    shift 2
    if [ -n "$BIOAF_SCRIPT_DIR" ] && [ -f "$BIOAF_SCRIPT_DIR/$rel" ]; then
        origin="$BIOAF_SCRIPT_DIR/$rel"
        # shellcheck disable=SC1090
        source "$origin" || { _bioaf_helper_error "$need" "$origin" "failed to load"; return 1; }
    else
        origin="$BIOAF_RAW_BASE/$BIOAF_INSTALLER_REF/$rel"
        if ! payload="$(curl -fsSL "$origin")"; then
            _bioaf_helper_error "$need" "$origin" "could not be downloaded"; return 1
        elif [ -z "$payload" ]; then
            _bioaf_helper_error "$need" "$origin" "was empty"; return 1
        elif ! "${BASH:-bash}" -n -c "$payload" 2>/dev/null; then
            _bioaf_helper_error "$need" "$origin" "is not valid shell"; return 1
        elif ! eval "$payload"; then
            _bioaf_helper_error "$need" "$origin" "failed to load"; return 1
        fi
    fi
    for fn in "$@"; do
        if ! declare -F "$fn" >/dev/null; then
            _bioaf_helper_error "$need" "$origin" "does not define $fn (a different bioAF version?)"; return 1
        fi
    done
}
# <<< bioaf helper loader

# ---------------------------------------------------------------------------
# Value checks. The region ends up in AWS calls, a key-file path and the prefill
# YAML; the SSH range in a security-group rule. Pure Bash: no Python exists yet.
# ---------------------------------------------------------------------------
aws_region_valid() {
    # One AWS region name, e.g. us-east-1, ap-southeast-2, us-gov-west-1.
    [[ "$1" =~ ^[a-z]{2}(-[a-z]+){1,3}-[0-9]{1,2}$ ]]
}

ipv4_valid() {
    local IFS=. octet
    local -a octets
    [[ "$1" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]] || return 1
    read -r -a octets <<<"$1"
    for octet in "${octets[@]}"; do
        [[ "$octet" =~ ^(0|[1-9][0-9]*)$ ]] && [ "$octet" -le 255 ] || return 1
    done
}

ipv4_cidr_valid() {
    # address/prefix, the form the security group's IpRanges take.
    [[ "$1" == */* ]] || return 1
    local prefix="${1##*/}"
    ipv4_valid "${1%/*}" && [[ "$prefix" =~ ^(0|[1-9][0-9]?)$ ]] && [ "$prefix" -le 32 ]
}

# ---------------------------------------------------------------------------
# CLI flags (parsed before any output so --verbose disables log redirect, and
# before any download or AWS call so a bad value stops the run first)
# ---------------------------------------------------------------------------
BIOAF_VERBOSE=0
BIOAF_INSTALLER_REF="${BIOAF_INSTALLER_REF:-}"
REGION_FLAG=""
SSH_CIDR=""
ADMIN_SSH_KEY=ask
RECONCILE_PERMISSIONS=no
aws_usage() {
    cat <<'USAGE'
Usage: install-aws.sh [--region <region>] [--ssh-cidr <cidr>] [--admin-ssh-key] [--ref <ref>] [--verbose]
       install-aws.sh --reconcile-permissions [--region <region>] [--ref <ref>] [--verbose]

Options:
  --region <region>   AWS region to deploy into (e.g. us-east-1). Defaults to
                      your configured region, else prompts.
  --ssh-cidr <cidr>   IPv4 source range allowed to SSH (tcp:22), e.g.
                      203.0.113.7/32. Defaults to your current public IP /32;
                      if that cannot be detected the install stops and asks for
                      this option. Pass 0.0.0.0/0 to allow SSH from anywhere
                      (e.g. to let a remote agent connect to the VM); only do
                      this on a disposable account.
  --admin-ssh-key     Require this computer's SSH key for administrator access to the
                      bioAF server (asked interactively when not given). The private
                      key stays here; this does not limit which networks reach SSH.
  --ref <ref>         The bioAF branch, tag or commit this script came from.
                      Its helpers are downloaded from the same ref when there is
                      no installer/ folder beside the script (e.g. curl | bash).
                      Defaults to $BIOAF_INSTALLER_REF, else main. A pinned install:
                        curl -fsSL https://raw.githubusercontent.com/bioAF/bioAF/<ref>/install-aws.sh \
                          | bash -s -- --ref <ref>
  --verbose, -v       Print all command output inline instead of routing it to
                      the install log. Use when debugging a failed step.
  --reconcile-permissions
                      On an existing installation only: list the roles and EC2
                      resources bioAF created, and after you confirm, put the
                      role boundary on those roles, tag them and the resources
                      with this installation, and replace the bioaf-app policy
                      with the narrowed one. Creates nothing else and does not
                      touch the VM or the app. See
                      docs/operations/aws-permissions-reconcile.md.

Both "--option value" and "--option=value" work.

The full transcript of each run is always written to:
  ~/.bioaf/install-aws.log
USAGE
}
aws_flag_error() {
    printf 'install-aws.sh: %s (see --help)\n' "$1" >&2
    exit 2
}
while [ $# -gt 0 ]; do
    case "$1" in
        --verbose|-v) BIOAF_VERBOSE=1 ;;
        --admin-ssh-key) ADMIN_SSH_KEY=yes ;;
        --reconcile-permissions) RECONCILE_PERMISSIONS=yes ;;
        --region|--ssh-cidr|--ref)
            if [ $# -lt 2 ] || [ -z "$2" ] || [[ "$2" == -* ]]; then aws_flag_error "$1 needs a value"; fi
            case "$1" in
                --region) REGION_FLAG="$2" ;;
                --ssh-cidr) SSH_CIDR="$2" ;;
                --ref) BIOAF_INSTALLER_REF="$2" ;;
            esac
            shift ;;
        --region=*) REGION_FLAG="${1#*=}"; [ -n "$REGION_FLAG" ] || aws_flag_error "--region needs a value" ;;
        --ssh-cidr=*) SSH_CIDR="${1#*=}"; [ -n "$SSH_CIDR" ] || aws_flag_error "--ssh-cidr needs a value" ;;
        --ref=*) BIOAF_INSTALLER_REF="${1#*=}"; [ -n "$BIOAF_INSTALLER_REF" ] || aws_flag_error "--ref needs a value" ;;
        --help|-h) aws_usage; exit 0 ;;
        *) aws_flag_error "unknown option $(printf '%q' "$1")" ;;
    esac
    shift
done
if [ -n "$REGION_FLAG" ] && ! aws_region_valid "$REGION_FLAG"; then
    aws_flag_error "--region $(printf '%q' "$REGION_FLAG") is not an AWS region name like us-east-1"
fi
if [ -n "$SSH_CIDR" ] && ! ipv4_cidr_valid "$SSH_CIDR"; then
    aws_flag_error "--ssh-cidr $(printf '%q' "$SSH_CIDR") is not an IPv4 range like 203.0.113.7/32"
fi
BIOAF_INSTALLER_REF="${BIOAF_INSTALLER_REF:-main}"
if ! bioaf_ref_valid "$BIOAF_INSTALLER_REF"; then
    aws_flag_error "$(printf '%q' "$BIOAF_INSTALLER_REF") is not a branch, tag or commit name"
fi
export BIOAF_VERBOSE
export BIOAF_INSTALL_LOG="${HOME}/.bioaf/install-aws.log"

# ---------------------------------------------------------------------------
# Output and display helpers (red/green/.../step/section/warn/io_finish).
# ---------------------------------------------------------------------------
bioaf_load_helper installer/output.sh required io_init io_finish step maybe_step section say note warn \
    fail_line red green yellow bold dim || exit 1
io_init

# ---------------------------------------------------------------------------
# Laptop prerequisites (terminal input, Homebrew). Same local-or-HTTPS
# loading as the output helper.
# ---------------------------------------------------------------------------
bioaf_load_helper installer/local_prereqs.sh required attach_terminal ensure_homebrew || exit 1

# SSH recovery access (plan 03B): the console link, access sheet, optional administrator key and the
# automatic recovery probe. Loaded once a VM exists; optional: without it the install continues.
ssh_access_ready() {
    [ "${SSH_ACCESS_LOADED:-}" = yes ] && return 0
    [ "${SSH_ACCESS_LOADED:-}" = no ] && return 1
    if bioaf_load_helper installer/ssh_access.sh optional ssh_access_console_url ssh_access_write_sheet \
        ssh_access_admin_key ssh_access_admin_policy_script ssh_access_probe_aws ssh_access_prefill_probe; then
        SSH_ACCESS_LOADED=yes
    else
        SSH_ACCESS_LOADED=no
        return 1
    fi
}
# Prompts and interactive commands (sudo, ssh) all read from the terminal
# from here on.
attach_terminal || exit 1

# ---------------------------------------------------------------------------
# Defaults. The AWS analogs of install-gcp.sh's VM_NAME / MACHINE_TYPE / etc.
# ---------------------------------------------------------------------------
VM_NAME="bioaf"
INSTANCE_TYPE="t3.medium"          # ~2 vCPU / 4 GiB, the EC2 analog of e2-medium
BOOT_DISK_GB="30"
KEY_NAME="bioaf"                   # EC2 key pair name
SECURITY_GROUP_NAME="bioaf-allow-web"
APP_ROLE_NAME="bioaf-app"          # IAM role + instance profile (analog of the app SA)
APP_POLICY_NAME="bioaf-app"
# Canonical's public SSM parameters for the latest Ubuntu 22.04 amd64 AMI, in
# preference order. ebs-gp2 is the variant Canonical publishes in every region;
# ebs-gp3 is NOT (it 404s in e.g. us-west-1), so we resolve the first that exists.
# The boot volume type is set independently at launch (gp3 below), so the AMI's
# own backing type does not matter.
UBUNTU_SSM_PARAMS=(
    "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
    "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
)

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
    if [ "$(uname -s)" = "Darwin" ]; then
        read -rp "  Install it now with Homebrew (installing Homebrew first if needed)? [Y/n] " ans
        if [ "$ans" != "n" ] && [ "$ans" != "N" ]; then
            ensure_homebrew || exit 1
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
if ! [[ "$ACCOUNT_ID" =~ ^[0-9]{12}$ ]]; then
    red "  AWS returned an account ID that is not 12 digits; nothing was created."
    exit 1
fi
CALLER_ARN="$(aws sts get-caller-identity --query Arn --output text)"

# The bioaf-app policy, the boundary for roles the app creates, and existing-installation
# reconciliation (security plan 04B). Loaded once credentials work, like the later helpers.
bioaf_load_helper installer/aws_iam.sh required aws_iam_installation_id aws_iam_boundary_arn \
    aws_iam_role_boundary_policy aws_iam_app_policy aws_iam_ensure_boundary aws_iam_put_app_policy \
    aws_iam_owned_roles aws_iam_owned_ec2 aws_iam_claim || exit 1
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
if ! aws_region_valid "$REGION"; then
    red "  $(printf '%q' "$REGION") is not an AWS region name like us-east-1; nothing was created."
    red "  Re-run with --region <region>, or fix the region in your AWS configuration."
    exit 1
fi
export AWS_DEFAULT_REGION="$REGION"
step "Using region ${REGION}" -- true

# The SSH source range, settled before anything is created. Without --ssh-cidr it
# is this machine's public IPv4 /32; if that cannot be detected the install stops
# rather than opening SSH wider than asked.
if [ -z "$SSH_CIDR" ] && [ "$RECONCILE_PERMISSIONS" != yes ]; then
    # Only a lookup that succeeded counts: a failed or partial transfer may still print an address.
    MY_IP=""
    if MY_IP_BODY="$(curl -fsS --max-time 10 https://checkip.amazonaws.com 2>/dev/null)"; then
        MY_IP="$(printf '%s' "$MY_IP_BODY" | tr -d '[:space:]')"
    fi
    if ! ipv4_valid "$MY_IP"; then
        red "  Could not detect this machine's public IPv4 address, so nothing was created."
        red "  Re-run with the range allowed to SSH to the VM, e.g. --ssh-cidr 203.0.113.7/32"
        exit 1
    fi
    SSH_CIDR="${MY_IP}/32"
fi

# Resolve the default VPC + a subnet in it (new accounts have a default VPC).
VPC_ID="$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
    --query 'Vpcs[0].VpcId' --output text 2>/dev/null || echo "None")"
if [ "$VPC_ID" = "None" ] || [ -z "$VPC_ID" ]; then
    red "  No default VPC in ${REGION}. Create one (aws ec2 create-default-vpc) or"
    red "  pick a region that has one, then re-run."
    exit 1
fi
INSTALLATION_ID="$(aws_iam_installation_id "$ACCOUNT_ID" "$REGION")"

# ---------------------------------------------------------------------------
# --reconcile-permissions: an existing installation adopts the narrowed permissions (plan 04B).
# ---------------------------------------------------------------------------
if [ "$RECONCILE_PERMISSIONS" = yes ]; then
    section "Reconcile bioAF permissions (existing installation)"
    if ! aws iam get-role --role-name "$APP_ROLE_NAME" >/dev/null 2>&1; then
        red "  No ${APP_ROLE_NAME} role in this account: there is no installation to reconcile."
        exit 1
    fi
    APP_VM="$(aws ec2 describe-instances \
        --filters "Name=tag:Name,Values=${VM_NAME}" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
        --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || echo "None")"
    APP_VPC="$VPC_ID"
    if [ "$APP_VM" != "None" ] && [ -n "$APP_VM" ]; then
        APP_VPC="$(aws ec2 describe-instances --instance-ids "$APP_VM" \
            --query 'Reservations[0].Instances[0].VpcId' --output text)"
    else
        APP_VM=""
    fi
    RECONCILE_ROLES="$(mktemp -t bioaf-roles-XXXXXX)"
    RECONCILE_EC2="$(mktemp -t bioaf-ec2-XXXXXX)"
    aws_iam_owned_roles "$APP_ROLE_NAME" > "$RECONCILE_ROLES"
    aws_iam_owned_ec2 "$REGION" > "$RECONCILE_EC2"
    echo ""
    echo "  Installation:   ${INSTALLATION_ID}"
    echo "  App role:       ${APP_ROLE_NAME} (policy replaced; never bounded or tagged)"
    echo "  App VM:         ${APP_VM:-none found}"
    echo "  App VPC:        ${APP_VPC}"
    echo "  Roles to bound and tag ($(grep -c . "$RECONCILE_ROLES" || true)):"
    sed 's/^/    /' "$RECONCILE_ROLES"
    echo "  EC2 resources to tag ($(grep -c . "$RECONCILE_EC2" || true)):"
    sed 's/^/    /' "$RECONCILE_EC2"
    echo ""
    echo "  Review the lists: anything here becomes this installation's to change and clean up."
    read -rp "  Apply? [y/N] " reconcile_answer || true
    if [ "$reconcile_answer" != "y" ] && [ "$reconcile_answer" != "Y" ]; then
        say "  Nothing was changed."
        rm -f "$RECONCILE_ROLES" "$RECONCILE_EC2"
        exit 0
    fi
    step "Create or update the boundary for roles bioAF creates" -- aws_iam_ensure_boundary "$ACCOUNT_ID" "$REGION"
    step "Bound and tag bioAF's roles, tag its EC2 resources and the app VM" -- \
        aws_iam_claim "$ACCOUNT_ID" "$REGION" "$RECONCILE_ROLES" "$RECONCILE_EC2" "$APP_VM"
    step "Replace the ${APP_ROLE_NAME} policy with the narrowed one" -- \
        aws_iam_put_app_policy "$ACCOUNT_ID" "$REGION" "$APP_VPC" "$APP_ROLE_NAME"
    rm -f "$RECONCILE_ROLES" "$RECONCILE_EC2"
    green "  Permissions reconciled. Next: run Check for Infrastructure Updates in bioAF."
    exit 0
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
if [ "$SSH_CIDR" = "0.0.0.0/0" ]; then
    warn "SSH (tcp:22) will be open to any address (--ssh-cidr 0.0.0.0/0)."
else
    note "Allowing SSH (tcp:22) from ${SSH_CIDR}"
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
    SG_REUSED=true
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
authorize_ingress tcp 8443 "0.0.0.0/0" "Notebooks (HTTPS)"
authorize_ingress tcp 22 "$SSH_CIDR" "SSH"

# Adding this rule removes nothing: a reused group keeps any SSH rules it already
# had, possibly broader ones. Say so instead of implying SSH is now restricted.
if [ "${SG_REUSED:-false}" = true ]; then
    other_ssh=""
    for cidr in $(aws ec2 describe-security-groups --group-ids "$SG_ID" \
        --query 'SecurityGroups[0].IpPermissions[?FromPort==`22`].IpRanges[].CidrIp' --output text 2>/dev/null || true); do
        [ "$cidr" = "$SSH_CIDR" ] || other_ssh="${other_ssh} ${cidr}"
    done
    if [ -n "$other_ssh" ]; then
        warn "Security group ${SECURITY_GROUP_NAME} also allows SSH from:${other_ssh}. Those rules were kept;"
        warn "remove any you no longer want (aws ec2 revoke-security-group-ingress --group-id ${SG_ID} ...)."
    fi
fi

# Cloud recovery access (plan 03B): a separate group allowing SSH only from the Region's AWS-managed
# EC2 Instance Connect range, so the browser Connect route works even if the SSH range above
# excludes you. The backend keeps it in place afterwards. Your AWS permissions are still required.
RECOVERY_SG_ID="$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=bioaf-ssh-recovery" "Name=vpc-id,Values=${VPC_ID}" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")"
if [ "$RECOVERY_SG_ID" = "None" ] || [ -z "$RECOVERY_SG_ID" ]; then
    RECOVERY_SG_ID="$(aws ec2 create-security-group --group-name bioaf-ssh-recovery \
        --description "bioAF cloud recovery (EC2 Instance Connect)" --vpc-id "$VPC_ID" \
        --query 'GroupId' --output text 2>/dev/null || echo "")"
fi
EIC_PREFIX_LIST="$(aws ec2 describe-managed-prefix-lists \
    --filters "Name=prefix-list-name,Values=com.amazonaws.${REGION}.ec2-instance-connect" \
    --query 'PrefixLists[0].PrefixListId' --output text 2>/dev/null || echo "")"
if [ -n "$RECOVERY_SG_ID" ] && [[ "$EIC_PREFIX_LIST" == pl-* ]]; then
    if aws ec2 authorize-security-group-ingress --group-id "$RECOVERY_SG_ID" \
        --ip-permissions "IpProtocol=tcp,FromPort=22,ToPort=22,PrefixListIds=[{PrefixListId=${EIC_PREFIX_LIST}}]" \
        >/dev/null 2>&1; then
        step "Allow cloud recovery SSH (EC2 Instance Connect, ${EIC_PREFIX_LIST})" -- true
    else
        maybe_step "Cloud recovery SSH (EC2 Instance Connect) - already allowed" -- true
    fi
else
    warn "Cloud recovery access could not be prepared here; bioAF checks it again in Settings."
    RECOVERY_SG_ID=""
fi

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

# The app role's permissions (security plan 04B; installer/aws_iam.sh documents them): reads stay
# account-wide where AWS cannot scope them, and every change is limited to this installation. The
# boundary policy comes first, since the app may only create roles under it. The app cannot edit
# its own role or the boundary.
step "Create or update the boundary for roles bioAF creates" -- aws_iam_ensure_boundary "$ACCOUNT_ID" "$REGION"
step "Attach the ${APP_POLICY_NAME} policy" -- \
    aws_iam_put_app_policy "$ACCOUNT_ID" "$REGION" "$VPC_ID" "$APP_ROLE_NAME"

if aws iam get-instance-profile --instance-profile-name "$APP_ROLE_NAME" >/dev/null 2>&1; then
    step "Instance profile ${APP_ROLE_NAME} already exists" -- true
else
    step "Create instance profile ${APP_ROLE_NAME}" -- \
        aws iam create-instance-profile --instance-profile-name "$APP_ROLE_NAME"
fi
# A profile holds at most one role, so a second add-role returns LimitExceeded.
# Check the current link first so re-runs show a clean check instead of a warning.
LINKED_ROLE="$(aws iam get-instance-profile --instance-profile-name "$APP_ROLE_NAME" \
    --query 'InstanceProfile.Roles[0].RoleName' --output text 2>/dev/null || echo "None")"
if [ "$LINKED_ROLE" = "$APP_ROLE_NAME" ]; then
    step "Role ${APP_ROLE_NAME} already linked to its instance profile" -- true
else
    step "Link role ${APP_ROLE_NAME} to its instance profile" -- \
        aws iam add-role-to-instance-profile \
            --instance-profile-name "$APP_ROLE_NAME" --role-name "$APP_ROLE_NAME"
fi
# IAM is eventually consistent; RunInstances can 400 on a just-created profile.
step "Wait 10s for the instance profile to propagate" -- sleep 10

# ---------------------------------------------------------------------------
# 7. EC2 VM
# ---------------------------------------------------------------------------
section "Step 7 / 8  -  EC2 VM"
AMI_ID=""
for _ami_param in "${UBUNTU_SSM_PARAMS[@]}"; do
    AMI_ID="$(aws ssm get-parameters --names "$_ami_param" \
        --query 'Parameters[0].Value' --output text 2>/dev/null || true)"
    if [ -n "$AMI_ID" ] && [ "$AMI_ID" != "None" ]; then
        break
    fi
    AMI_ID=""
done
if [ -z "$AMI_ID" ]; then
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
    # The app changes its own VM's security groups only when the VM carries the ownership tag.
    maybe_step "Tag ${EXISTING_VM} with this installation" -- \
        aws ec2 create-tags --resources "$EXISTING_VM" --tags "Key=bioaf:installation,Value=${INSTALLATION_ID}"
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

        # IMDSv2 is required (HttpTokens=required). HopLimit=2 (not the default 1)
        # so the app's Docker containers -- one network hop past the host -- can
        # reach IMDS to resolve the bioaf-app instance-profile credentials; the
        # S3 provider and AWS validation authenticate through that path.
        INSTANCE_ID="$(aws ec2 run-instances \
            --image-id "$AMI_ID" \
            --instance-type "$INSTANCE_TYPE" \
            --key-name "$KEY_NAME" \
            --security-group-ids "$SG_ID" ${RECOVERY_SG_ID:+"$RECOVERY_SG_ID"} \
            --subnet-id "$SUBNET_ID" \
            --associate-public-ip-address \
            --iam-instance-profile "Name=${APP_ROLE_NAME}" \
            --metadata-options "HttpTokens=required,HttpEndpoint=enabled,HttpPutResponseHopLimit=2" \
            --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=${BOOT_DISK_GB},VolumeType=gp3}" \
            --user-data "file://${USERDATA_TMP}" \
            --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${VM_NAME}},{Key=bioaf_cloud_provider,Value=aws},{Key=bioaf:installation,Value=${INSTALLATION_ID}}]" \
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
    # Keep the public address: the HTTPS certificate names it (plan 03.5A).
    # Loaded once a VM exists (like the SSH recovery helper); optional.
    if bioaf_load_helper installer/static_ip.sh optional aws_keep_public_ip \
        && static_ip="$(aws_keep_public_ip "$INSTANCE_ID" "$VM_NAME")" && [ -n "$static_ip" ]; then
        VM_IP="$static_ip"
        step "Keep this server's public address ${VM_IP} (Elastic IP)" -- true
    else
        warn "An Elastic IP could not be attached; if the address changes, run ./bioaf tls-issue-ip on the server."
    fi
    note "Public IP: ${VM_IP}"
fi

# ---------------------------------------------------------------------------
# 8. Prefill + finish setup (automatic handoff or worksheet)
# ---------------------------------------------------------------------------
section "Step 8 / 8  -  Prefill + finish setup"
APP_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${APP_ROLE_NAME}"

# Build the prefill the setup wizard pre-populates from. Same filename contract
# as install-gcp.sh so `bioaf`'s discover_prefill_file() finds it: the local
# copy is ~/.bioaf/prefill.yaml; the auto-handoff drops it on the VM as
# ~/.bioaf-prefill.yaml (the name `./bioaf setup` auto-discovers there).
PREFILL_LOCAL="${HOME}/.bioaf/prefill.yaml"
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

# The automatic handoff needs the VM's IP and the private key we saved locally;
# a reused key pair leaves no .pem on disk, so we can only offer the worksheet.
CAN_AUTO=false
if [ -n "$VM_IP" ] && [ -f "$KEY_FILE" ]; then
    CAN_AUTO=true
fi

finish_choice=2
if [ "$CAN_AUTO" = true ]; then
    echo "  VM public IP:   ${VM_IP}"
    echo ""
    bold "  How would you like to finish setup?"
    echo ""
    echo "    1. Automatic -- I'll SSH in for you, clone bioAF, run setup,"
    echo "       and pre-populate the wizard with the values from this run."
    echo "    2. Manual    -- print a worksheet and you'll SSH in yourself."
    echo ""
    read -rp "  Choose (1 or 2): [1] " finish_choice || true
    finish_choice="${finish_choice:-1}"
fi

REMOTE_SETUP_SUCCEEDED=false
SSH_OPTS=(-i "$KEY_FILE" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o BatchMode=yes)

# SSH recovery access (plan 03B), once the VM accepts SSH: the optional administrator key, the
# automatic recovery probe under your own AWS identity, and the saved access sheet.
ACCESS_SHEET=""
aws_ssh_access_after_ready() {
    ssh_access_ready || return 0
    section "SSH recovery access"
    if [ "$ADMIN_SSH_KEY" = ask ]; then
        say "  $SSH_ACCESS_ADMIN_KEY_HELP"
        read -rp "  $SSH_ACCESS_ADMIN_KEY_QUESTION [y/N] " admin_answer || true
        case "$admin_answer" in y|Y|yes|Yes) ADMIN_SSH_KEY=yes ;; *) ADMIN_SSH_KEY=no ;; esac
    fi
    if [ "$ADMIN_SSH_KEY" = yes ]; then
        local admin_key pub policy fp
        if admin_key="$(ssh_access_admin_key)" && pub="$(cat "${admin_key}.pub")" \
            && ssh "${SSH_OPTS[@]}" "ubuntu@${VM_IP}" \
                "umask 077; mkdir -p ~/.ssh; grep -qxF '$pub' ~/.ssh/authorized_keys 2>/dev/null || printf '%s\n' '$pub' >> ~/.ssh/authorized_keys" \
            && policy="$(ssh_access_admin_policy_script ubuntu)" \
            && ssh -i "$admin_key" -o IdentitiesOnly=yes -o IdentityAgent=none -o StrictHostKeyChecking=yes -o BatchMode=yes \
                "ubuntu@${VM_IP}" 'sudo bash -s' <<< "$policy" | grep -q BIOAF_ADMIN_POLICY=applied \
            && ssh -i "$admin_key" -o IdentitiesOnly=yes -o IdentityAgent=none -o PreferredAuthentications=publickey \
                -o StrictHostKeyChecking=yes -o BatchMode=yes "ubuntu@${VM_IP}" true; then
            fp="$(ssh-keygen -lf "${admin_key}.pub" | awk '{print $2}')"
            printf "admin_ssh_key: '{\"fingerprint\": \"%s\", \"account\": \"ubuntu\", \"instance_id\": \"%s\"}'\n" "$fp" "$INSTANCE_ID" >> "$PREFILL_LOCAL"
            step "Administrator login now requires ${admin_key} (${fp})" -- true
        else
            warn "The administrator key could not be required; the server's previous SSH login settings were kept."
        fi
    fi
    local probe
    probe="$(ssh_access_probe_aws "$INSTANCE_ID" "$REGION" ubuntu "$VM_IP")"
    case "$probe" in
        succeeded) step "Recovery login check (temporary key through EC2 Instance Connect)" -- true ;;
        denied) warn "Your AWS identity could not send a temporary key (ec2-instance-connect:SendSSHPublicKey). Browser recovery needs that permission." ;;
        *) warn "The recovery login check did not succeed. bioAF shows Needs attention in Settings until it is fixed." ;;
    esac
    ssh_access_prefill_probe "$PREFILL_LOCAL" "$INSTANCE_ID" aws_eic "$probe"
}

aws_ssh_access_sheet() {
    # The non-secret access sheet and the summary lines, whenever a VM exists.
    [ -n "${INSTANCE_ID:-}" ] || return 0
    ssh_access_ready || return 0
    local partition=aws url
    case "$REGION" in cn-*) partition=aws-cn ;; us-gov-*) partition=aws-us-gov ;; esac
    ACCESS_SHEET="${HOME}/.bioaf/access-aws-${ACCOUNT_ID}-${INSTANCE_ID}.txt"
    ssh_access_write_sheet "$ACCESS_SHEET" aws "$ACCOUNT_ID" "$REGION" "${AZ:-$REGION}" "$INSTANCE_ID" "$VM_NAME" public ubuntu "$partition" \
        || { ACCESS_SHEET=""; return 0; }
    url="$(ssh_access_console_url aws "$REGION" "$INSTANCE_ID" "$partition")"
    echo ""
    bold "  Restore SSH access (bookmark this; it works while bioAF is down):"
    green "     ${url}"
    echo "     $(ssh_access_instruction aws public) Choose Public IP and the username ubuntu."
    echo "     Access sheet saved: ${ACCESS_SHEET}"
}

if [ "$finish_choice" = "1" ] && [ "$CAN_AUTO" = true ]; then
    section "Auto-handoff: copying prefill and starting setup on the VM"

    # instance-running != sshd ready: poll until the VM accepts a connection.
    ssh_ready=false
    for _ in $(seq 1 30); do
        if ssh "${SSH_OPTS[@]}" "ubuntu@${VM_IP}" true 2>/dev/null; then
            ssh_ready=true
            break
        fi
        sleep 5
    done

    if [ "$ssh_ready" = true ]; then
        aws_ssh_access_after_ready
    fi

    if [ "$ssh_ready" != true ]; then
        warn "VM not reachable over SSH yet; falling back to the worksheet."
        finish_choice=2
    elif step "Copy prefill to ${VM_NAME}:~/.bioaf-prefill.yaml" -- \
        scp "${SSH_OPTS[@]}" "${PREFILL_LOCAL}" "ubuntu@${VM_IP}:~/.bioaf-prefill.yaml"; then
        echo ""
        echo "  Cloning bioAF and running setup on the VM. This will take a few minutes."
        echo "  You'll see the bioAF setup output stream below; it ends with a one-time"
        echo "  setup code and the wizard URL."
        echo ""
        # Remote: wait for first-boot Docker to finish, ensure git, clone, run setup.
        if ssh "${SSH_OPTS[@]}" "ubuntu@${VM_IP}" '
set -euo pipefail
for _ in $(seq 1 60); do
    if command -v docker >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then break; fi
    sleep 5
done
if ! command -v git >/dev/null 2>&1; then
    sudo apt-get update -qq && sudo apt-get install -y -qq git
fi
if [ ! -d "$HOME/bioAF/.git" ]; then
    git clone https://github.com/bioAF/bioAF.git "$HOME/bioAF"
fi
cd "$HOME/bioAF"
git pull --ff-only origin main 2>/dev/null || true
./bioaf setup --prefill "$HOME/.bioaf-prefill.yaml"
./bioaf tls-issue-ip || echo "HTTPS still uses the temporary certificate. Run ./bioaf tls-issue-ip on the server to retry."
'; then
            REMOTE_SETUP_SUCCEEDED=true
        else
            red "  Remote setup failed. Falling back to the worksheet so you can run it yourself."
        fi
    else
        warn "Failed to copy prefill to the VM; falling back to the worksheet."
        finish_choice=2
    fi
fi

aws_ssh_access_sheet

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
    echo "  will already have your account, region, and role values pre-populated."
    io_finish
    exit 0
fi

# ---------------------------------------------------------------------------
# Manual path: print the worksheet so you finish setup on the VM yourself.
# ---------------------------------------------------------------------------
echo ""
bold "  Manual Next Steps"
echo ""
if [ -n "$VM_IP" ]; then
    if [ -f "$KEY_FILE" ]; then
        SSH_KEY_HINT="$KEY_FILE"
    else
        SSH_KEY_HINT="<your ${KEY_NAME}.pem>"
    fi
    echo "  1. Copy the prefill file we generated locally to the VM:"
    echo ""
    green "     scp -i ${SSH_KEY_HINT} ${PREFILL_LOCAL} ubuntu@${VM_IP}:~/.bioaf-prefill.yaml"
    echo ""
    echo "  2. SSH into your VM:"
    echo ""
    green "     ssh -i ${SSH_KEY_HINT} ubuntu@${VM_IP}"
    echo ""
    echo "  3. On the VM, clone bioAF and run setup:"
    echo ""
    green "     git clone https://github.com/bioAF/bioAF.git"
    green "     cd bioAF"
    green "     ./bioaf setup --prefill ~/.bioaf-prefill.yaml"
    echo ""
    echo "  4. Open bioAF in your browser:"
    echo ""
    green "     https://${VM_IP}"
    echo ""
    dim "     (Your browser will show a certificate warning for the self-signed cert."
    dim "      This is expected. Click 'Advanced' then 'Proceed' to continue.)"
else
    echo "  No VM was launched. Re-run this script (or launch a VM) before setup."
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
dim "  The setup wizard reads the VM's instance profile for credentials, so there"
dim "  is no key to upload. Pre-populated values come from the prefill file."

io_finish
exit
}
