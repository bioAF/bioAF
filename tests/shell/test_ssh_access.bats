#!/usr/bin/env bats
# installer/ssh_access.sh (plan 03B): the Restore SSH access link and saved access sheet, the optional
# administrator laptop key, and the automatic throwaway-key recovery probe. gcloud, aws, ssh and
# ssh-keygen are stubs: these prove what the installer asks the cloud and guest to do, not that a live
# provider accepts it (provider acceptance is a separate release gate).

HELPER="$BATS_TEST_DIRNAME/../../installer/ssh_access.sh"

setup() {
    TEST_DIR="$(mktemp -d)"
    export HOME="$TEST_DIR/home"; mkdir -p "$HOME/.ssh"
    export STUBS="$TEST_DIR/stubs"; mkdir -p "$STUBS"
    export CALLS="$TEST_DIR/calls"
    export PATH="$STUBS:$PATH"
    # ssh-keygen really generates (it is local and harmless); the cloud CLIs and ssh are stubs.
    for tool in gcloud aws ssh; do
        cat > "$STUBS/$tool" <<STUB
#!/usr/bin/env bash
printf '%s\n' "$tool \$*" >> "$CALLS"
case "$tool \$*" in
    *"\${FAIL_ON:-__never__}"*) exit 1 ;;
esac
exit 0
STUB
        chmod +x "$STUBS/$tool"
    done
    # shellcheck source=installer/ssh_access.sh
    source "$HELPER"
}

teardown() { rm -rf "$TEST_DIR"; }

@test "console links match the backend's Restore SSH access links" {
    [ "$(ssh_access_console_url gcp lab-project us-central1-b bioaf)" = \
      "https://console.cloud.google.com/compute/instancesDetail/zones/us-central1-b/instances/bioaf?project=lab-project" ]
    [ "$(ssh_access_console_url aws us-west-1 i-0abc123def4567890 aws)" = \
      "https://us-west-1.console.aws.amazon.com/ec2/home?region=us-west-1#ConnectToInstance:instanceId=i-0abc123def4567890" ]
    [ "$(ssh_access_console_url aws cn-north-1 i-0abc123def4567890 aws-cn)" = \
      "https://cn-north-1.console.amazonaws.cn/ec2/home?region=cn-north-1#ConnectToInstance:instanceId=i-0abc123def4567890" ]
    run ssh_access_console_url gcp 'lab;rm' us-central1-b bioaf
    [ "$status" -ne 0 ]
}

@test "the path-specific instruction and public-GCP Cloud Shell command" {
    [ "$(ssh_access_instruction gcp private)" = "Sign in to the Google Cloud account used for this server, then choose SSH." ]
    [ "$(ssh_access_instruction gcp public)" = "Open Cloud Shell on this server's page, paste the recovery command below, and press Enter." ]
    [ "$(ssh_access_instruction aws public)" = "Sign in to the AWS account used for this server, choose Connect, then EC2 Instance Connect and Connect." ]
    [ "$(ssh_access_gcp_command bioaf lab-project us-central1-b)" = \
      "gcloud compute ssh bioaf --project=lab-project --zone=us-central1-b --tunnel-through-iap" ]
}

@test "the access sheet holds the binding, link and instruction and nothing secret" {
    sheet="$HOME/.bioaf/access-bioaf.txt"
    ssh_access_write_sheet "$sheet" gcp lab-project us-central1 us-central1-b 4815162342 bioaf public ""
    [ -f "$sheet" ]
    [ "$(stat -f %Lp "$sheet" 2>/dev/null || stat -c %a "$sheet")" = 600 ]
    grep -q "https://console.cloud.google.com/compute/instancesDetail/zones/us-central1-b/instances/bioaf?project=lab-project" "$sheet"
    grep -q "Open Cloud Shell on this server's page" "$sheet"
    grep -q "gcloud compute ssh bioaf --project=lab-project --zone=us-central1-b --tunnel-through-iap" "$sheet"
    grep -q "Instance ID: 4815162342" "$sheet"
    grep -qi "bookmark" "$sheet"
    ! grep -qi "PRIVATE KEY\|password" "$sheet"
}

@test "the administrator key is a dedicated key that is created once and never overwrites another key" {
    printf 'existing\n' > "$HOME/.ssh/id_ed25519"
    key="$(ssh_access_admin_key)"
    [ "$key" = "$HOME/.ssh/bioaf_admin_ed25519" ]
    [ -f "$key" ] && [ -f "$key.pub" ]
    [ "$(cat "$HOME/.ssh/id_ed25519")" = existing ]
    first="$(cat "$key.pub")"
    [ "$(ssh_access_admin_key)" = "$key" ]
    [ "$(cat "$key.pub")" = "$first" ]
    [ "$(stat -f %Lp "$key" 2>/dev/null || stat -c %a "$key")" = 600 ]
}

@test "the administrator policy keeps required MFA, validates sshd and restores on failure" {
    script="$(ssh_access_admin_policy_script ubuntu)"
    [[ "$script" == *"Match User ubuntu"* ]]
    [[ "$script" == *"sshd -t"* ]]
    [[ "$script" == *"sshd -T -C"* ]]
    # An existing stronger requirement (e.g. publickey,keyboard-interactive) is kept, not downgraded.
    [[ "$script" == *'publickey,*'* ]]
    [[ "$script" == *"PasswordAuthentication no"* ]]
    [[ "$script" == *"BIOAF_ADMIN_POLICY=restored"* ]]
    run ssh_access_admin_policy_script 'root; id'
    [ "$status" -ne 0 ]
}

@test "the GCP probe uses a throwaway key over IAP, no other identity, and always cleans up" {
    run ssh_access_probe_gcp bioaf us-central1-b lab-project
    [ "$status" -eq 0 ]
    [ "$output" = succeeded ]
    line="$(grep '^gcloud compute ssh' "$CALLS")"
    [[ "$line" == *"--tunnel-through-iap"* ]]
    [[ "$line" == *"--ssh-key-file="* ]]
    [[ "$line" == *"--strict-host-key-checking=yes"* ]]
    [[ "$line" == *"IdentitiesOnly=yes"* ]]
    [[ "$line" == *"IdentityAgent=none"* ]]
    [[ "$line" != *"bioaf_admin_ed25519"* ]]
    keyfile="$(sed -E 's/.*--ssh-key-file=([^ ]+).*/\1/' <<< "$line")"
    [ ! -e "$keyfile" ] && [ ! -e "$keyfile.pub" ]
    grep -q "^gcloud compute os-login ssh-keys remove" "$CALLS"
}

@test "a denied GCP probe is reported as such and still cleans up" {
    run env FAIL_ON="compute ssh" bash -c "source '$HELPER'; ssh_access_probe_gcp bioaf us-central1-b lab-project"
    [ "$output" = failed ]
    grep -q "^gcloud compute os-login ssh-keys remove" "$CALLS"
}

@test "the AWS probe sends a throwaway key through EC2 Instance Connect and logs in with it alone" {
    run ssh_access_probe_aws i-0abc123def4567890 us-west-1 ubuntu 54.1.2.3
    [ "$output" = succeeded ]
    grep -q '^aws ec2-instance-connect send-ssh-public-key --instance-id i-0abc123def4567890 --instance-os-user ubuntu' "$CALLS"
    line="$(grep '^ssh ' "$CALLS")"
    [[ "$line" == *"-o IdentitiesOnly=yes"* && "$line" == *"-o IdentityAgent=none"* && "$line" == *"-o StrictHostKeyChecking=yes"* ]]
    [[ "$line" == *"ubuntu@54.1.2.3"* ]]
}

@test "a denied EC2 Instance Connect key delivery is a probe failure, never a laptop-key fallback" {
    run env FAIL_ON="send-ssh-public-key" bash -c "source '$HELPER'; ssh_access_probe_aws i-0abc123def4567890 us-west-1 ubuntu 54.1.2.3"
    [ "$output" = denied ]
    ! grep -q '^ssh ' "$CALLS"
}

@test "the probe record for setup is bound to the instance and carries no key" {
    prefill="$TEST_DIR/prefill.yaml"
    ssh_access_prefill_probe "$prefill" 4815162342 gcp_iap succeeded
    grep -q '^ssh_recovery_probe: ' "$prefill"
    python3 -c "
import json, yaml
d = yaml.safe_load(open('$prefill'))
p = json.loads(d['ssh_recovery_probe'])
assert p['instance_id'] == '4815162342' and p['result'] == 'succeeded' and p['filter_revision'] == 0 and p['path'] == 'gcp_iap', p
"
}
