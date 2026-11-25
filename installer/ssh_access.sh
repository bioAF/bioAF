#!/usr/bin/env bash
# Installer SSH access helpers (plan 03B), shared by install-gcp.sh and install-aws.sh.
#
#   ssh_access_console_url / ssh_access_instruction / ssh_access_gcp_command
#       The same Restore SSH access link, instruction and public-GCP Cloud Shell command the backend
#       shows in Settings (backend/app/services/ssh_recovery.py), for the final summary and sheet.
#   ssh_access_write_sheet
#       A non-secret access sheet saved beside the installer's state on this computer.
#   ssh_access_admin_key / ssh_access_admin_policy_script
#       The optional "Require this computer's SSH key for administrator access?" step: a dedicated
#       local key (never another key overwritten, the private half never leaves this computer) and
#       the guest script that requires public-key login for that one administrator account,
#       keeping any stronger provider requirement and restoring the previous file on failure.
#   ssh_access_probe_gcp / ssh_access_probe_aws
#       The automatic recovery probe: a throwaway key, the installing person's own cloud identity,
#       no fallback to any other key or agent, strict host verification, cleanup on every outcome.
#   ssh_access_prefill_probe
#       Hands the probe result (bound to the instance) to setup through the prefill file.
#
# Nothing here grants the backend any shell, key-delivery or IAM authority.

SSH_ACCESS_GCP_PRIVATE="Sign in to the Google Cloud account used for this server, then choose SSH."
SSH_ACCESS_GCP_PUBLIC="Open Cloud Shell on this server's page, paste the recovery command below, and press Enter."
SSH_ACCESS_AWS="Sign in to the AWS account used for this server, choose Connect, then EC2 Instance Connect and Connect."
SSH_ACCESS_ADMIN_KEY_QUESTION="Require this computer's SSH key for administrator access?"
SSH_ACCESS_ADMIN_KEY_HELP="Use an SSH key stored on this computer for direct administrator access to the bioAF server. bioAF installs the public key; the private key stays here. This does not limit which networks can reach SSH."

_ssh_access_match() { [[ "$2" =~ $1 ]]; }

ssh_access_console_url() {
    # ssh_access_console_url gcp <project> <zone> <instance-name> | aws <region> <instance-id> [partition]
    case "$1" in
        gcp)
            _ssh_access_match '^[a-z][-a-z0-9]{4,28}[a-z0-9]$' "$2" || return 1
            _ssh_access_match '^[a-z]+(-[a-z0-9]+)+-[a-z]$' "$3" || return 1
            _ssh_access_match '^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$' "$4" || return 1
            printf 'https://console.cloud.google.com/compute/instancesDetail/zones/%s/instances/%s?project=%s\n' "$3" "$4" "$2"
            ;;
        aws)
            _ssh_access_match '^[a-z]{2}(-[a-z]+)+-[0-9]+$' "$2" || return 1
            _ssh_access_match '^i-[0-9a-f]{8,17}$' "$3" || return 1
            local origin
            case "${4:-aws}" in
                aws) origin="https://$2.console.aws.amazon.com" ;;
                aws-cn) origin="https://$2.console.amazonaws.cn" ;;
                aws-us-gov) origin="https://$2.console.amazonaws-us-gov.com" ;;
                *) return 1 ;;
            esac
            printf '%s/ec2/home?region=%s#ConnectToInstance:instanceId=%s\n' "$origin" "$2" "$3"
            ;;
        *) return 1 ;;
    esac
}

ssh_access_instruction() {
    # ssh_access_instruction <gcp|aws> <public|private>
    case "$1:$2" in
        gcp:public) printf '%s\n' "$SSH_ACCESS_GCP_PUBLIC" ;;
        gcp:private) printf '%s\n' "$SSH_ACCESS_GCP_PRIVATE" ;;
        aws:*) printf '%s\n' "$SSH_ACCESS_AWS" ;;
        *) return 1 ;;
    esac
}

ssh_access_gcp_command() {
    # ssh_access_gcp_command <instance-name> <project> <zone>
    printf 'gcloud compute ssh %s --project=%s --zone=%s --tunnel-through-iap\n' "$1" "$2" "$3"
}

ssh_access_write_sheet() {
    # ssh_access_write_sheet <path> <gcp|aws> <project-or-account> <region> <zone> <instance-id> <instance-name>
    #                        <public|private> <os-username> [aws-partition]
    local path="$1" provider="$2" account="$3" region="$4" zone="$5" id="$6" name="$7" placement="$8" user="$9"
    local partition="${10:-aws}" url command=""
    if [ "$provider" = gcp ]; then
        url="$(ssh_access_console_url gcp "$account" "$zone" "$name")" || return 1
        [ "$placement" = public ] && command="$(ssh_access_gcp_command "$name" "$account" "$zone")"
    else
        url="$(ssh_access_console_url aws "$region" "$id" "$partition")" || return 1
    fi
    mkdir -p "$(dirname "$path")"
    (
        umask 077
        {
            echo "bioAF server access sheet (it contains no secrets; keep it with your records)"
            echo ""
            echo "Restore SSH access: open this page in a browser (bookmark it; it works while bioAF is down):"
            echo "  $url"
            echo ""
            echo "$(ssh_access_instruction "$provider" "$placement")"
            [ -n "$command" ] && echo "  $command"
            [ "$provider" = aws ] && echo "Choose $( [ "$placement" = public ] && echo "Public IP" || echo "Private IP" ) and the username ${user:-ubuntu}."
            echo ""
            echo "Server"
            echo "  Cloud: $provider"
            echo "  $( [ "$provider" = gcp ] && echo Project || echo Account ): $account"
            echo "  Region: $region"
            echo "  Zone: $zone"
            echo "  Instance name: $name"
            echo "  Instance ID: $id"
            echo "  Address: $placement"
            [ -n "$user" ] && echo "  Operator username: $user"
            echo ""
            echo "If this sheet and this computer are lost: sign in to the cloud account, find the instance named"
            echo "'$name' (labelled or tagged bioaf) in $( [ "$provider" = gcp ] && echo "project $account" || echo "region $region" ), and use the same steps."
            echo "Your cloud account permissions are still required; this sheet grants no access by itself."
        } > "$path"
    )
    chmod 600 "$path"
}

ssh_access_admin_key() {
    # Echo the dedicated administrator key path, creating it once. Never touches another key.
    local key="$HOME/.ssh/bioaf_admin_ed25519"
    mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
    if [ ! -e "$key" ] && [ ! -e "$key.pub" ]; then
        ssh-keygen -q -t ed25519 -C "bioaf-admin@$(hostname -s 2>/dev/null || echo laptop)" -f "$key" -N "" >/dev/null || return 1
    elif [ ! -f "$key" ] || [ ! -f "$key.pub" ]; then
        printf 'The key %s is incomplete; fix or remove it, then try again.\n' "$key" >&2
        return 1
    fi
    chmod 600 "$key"
    printf '%s\n' "$key"
}

ssh_access_admin_policy_script() {
    # ssh_access_admin_policy_script <os-username>: the guest script (run with sudo) requiring
    # public-key login for that account. It prints BIOAF_ADMIN_POLICY=applied|restored.
    local user="$1"
    _ssh_access_match '^[a-z_][a-z0-9_-]{0,31}$' "$user" || return 1
    cat <<SCRIPT
set -u
f=/etc/ssh/sshd_config.d/00-bioaf-admin-policy.conf
backup=\$(mktemp)
had=no; [ -f "\$f" ] && { cp "\$f" "\$backup"; had=yes; }
eff() { /usr/sbin/sshd -T -C "user=$user,addr=203.0.113.1,host=bioaf-check,laddr=127.0.0.1,lport=22" 2>/dev/null; }
current=\$(eff | awk '\$1 == "authenticationmethods" { print \$2 }')
# Keep a stronger existing requirement (for example publickey,keyboard-interactive for MFA).
case "\$current" in
    publickey|publickey,*) methods="\$current" ;;
    *) methods=publickey ;;
esac
kbd=no; case "\$methods" in *keyboard-interactive*) kbd=yes ;; esac
cat > "\$f" <<EOF
# Managed by the bioAF installer (plan 03B): administrator login requires this account's SSH key.
Match User $user
    AuthenticationMethods \$methods
    PasswordAuthentication no
    KbdInteractiveAuthentication \$kbd
    PubkeyAuthentication yes
Match all
EOF
chmod 644 "\$f"
if /usr/sbin/sshd -t && eff | grep -qx "passwordauthentication no" && eff | grep -qx "pubkeyauthentication yes" \\
    && eff | grep -q "^authenticationmethods publickey"; then
    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
    echo BIOAF_ADMIN_POLICY=applied
else
    if [ "\$had" = yes ]; then cp "\$backup" "\$f"; else rm -f "\$f"; fi
    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
    echo BIOAF_ADMIN_POLICY=restored
    exit 1
fi
SCRIPT
}

_ssh_access_probe_key() {
    # A throwaway keypair in a private temporary directory; echoes the private key path.
    local dir
    dir="$(mktemp -d)" || return 1
    chmod 700 "$dir"
    ssh-keygen -q -t ed25519 -C "bioaf-recovery-probe" -f "$dir/probe" -N "" >/dev/null || { rm -rf "$dir"; return 1; }
    printf '%s\n' "$dir/probe"
}

ssh_access_probe_gcp() {
    # ssh_access_probe_gcp <instance> <zone> <project>: prints succeeded | failed | unavailable.
    local key result=failed
    key="$(_ssh_access_probe_key)" || { echo unavailable; return 0; }
    if gcloud compute ssh "$1" --zone="$2" --project="$3" --tunnel-through-iap \
        --ssh-key-file="$key" --strict-host-key-checking=yes --ssh-key-expire-after=10m --quiet \
        --command=true -- -o IdentitiesOnly=yes -o IdentityAgent=none -o PreferredAuthentications=publickey \
        -o ConnectTimeout=20 </dev/null >/dev/null 2>&1; then
        result=succeeded
    fi
    # Remove the probe key from the person's OS Login profile (metadata keys expire on their own).
    gcloud compute os-login ssh-keys remove --key-file="$key.pub" --quiet >/dev/null 2>&1 || true
    rm -rf "$(dirname "$key")"
    echo "$result"
}

ssh_access_probe_aws() {
    # ssh_access_probe_aws <instance-id> <region> <os-user> <address>: prints succeeded | failed |
    # denied (EC2 Instance Connect refused the key) | unavailable.
    local key result=failed
    key="$(_ssh_access_probe_key)" || { echo unavailable; return 0; }
    if ! aws ec2-instance-connect send-ssh-public-key --instance-id "$1" --instance-os-user "$3" \
        --ssh-public-key "file://$key.pub" --region "$2" >/dev/null 2>&1; then
        rm -rf "$(dirname "$key")"
        echo denied
        return 0
    fi
    if ssh -i "$key" -o IdentitiesOnly=yes -o IdentityAgent=none -o PreferredAuthentications=publickey \
        -o StrictHostKeyChecking=yes -o BatchMode=yes -o ConnectTimeout=20 "$3@$4" true </dev/null >/dev/null 2>&1; then
        result=succeeded
    fi
    # The pushed key expires after 60 seconds on the instance; the local material is removed now.
    rm -rf "$(dirname "$key")"
    echo "$result"
}

ssh_access_prefill_probe() {
    # ssh_access_prefill_probe <prefill.yaml> <instance-id> <path> <result>: one flat prefill row.
    local json
    json=$(printf '{"instance_id": "%s", "path": "%s", "result": "%s", "probed_at": "%s", "filter_revision": 0}' \
        "$2" "$3" "$4" "$(date -u +%Y-%m-%dT%H:%M:%SZ)")
    printf "ssh_recovery_probe: '%s'\n" "$json" >> "$1"
}
