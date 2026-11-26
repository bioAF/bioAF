#!/usr/bin/env bash
# Keep a public installation's address stable (plan 03.5A): a Let's Encrypt IP certificate names it,
# and an ephemeral address can change when the VM stops. Sourced by the installers.

gcp_keep_public_ip() {
    # gcp_keep_public_ip <vm-name> <region> <project> <current-ip>: promote the VM's current
    # ephemeral address to a static one (same address, no restart). Billed while the VM is stopped.
    local vm="$1" region="$2" project="$3" ip="$4" name="${1}-ip"
    if gcloud compute addresses describe "$name" --region="$region" --project="$project" \
        --format="value(address)" >/dev/null 2>&1; then
        return 0
    fi
    gcloud compute addresses create "$name" --addresses="$ip" --region="$region" --project="$project" --quiet
}

aws_keep_public_ip() {
    # aws_keep_public_ip <instance-id> <vm-name>: the instance's Elastic IP, associating a new one if it
    # has none. Prints the address. A new Elastic IP replaces the instance's automatic public address.
    local instance="$1" vm="$2" existing allocation address
    existing="$(aws ec2 describe-addresses --filters "Name=instance-id,Values=${instance}" \
        --query 'Addresses[0].PublicIp' --output text 2>/dev/null || true)"
    if [ -n "$existing" ] && [ "$existing" != None ]; then
        printf '%s\n' "$existing"
        return 0
    fi
    read -r allocation address < <(aws ec2 allocate-address --domain vpc \
        --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=${vm}-ip},{Key=bioaf_cloud_provider,Value=aws}]" \
        --query '[AllocationId,PublicIp]' --output text) || return 1
    [ -n "$allocation" ] || return 1
    if ! aws ec2 associate-address --instance-id "$instance" --allocation-id "$allocation" >/dev/null; then
        aws ec2 release-address --allocation-id "$allocation" >/dev/null 2>&1 || true
        return 1
    fi
    printf '%s\n' "$address"
}
