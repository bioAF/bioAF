#!/usr/bin/env bats
# installer/static_ip.sh (plan 03.5A): a Let's Encrypt IP certificate names the server's public address,
# so the installers keep that address. GCP promotes the VM's current address to a static one (no
# restart, same address); AWS associates an Elastic IP once. gcloud and aws are stubs.

HELPER="$BATS_TEST_DIRNAME/../../installer/static_ip.sh"

absent() { if grep -q "$@"; then return 1; fi; }

setup() {
    TEST_DIR="$(mktemp -d)"
    export STUBS="$TEST_DIR/stubs"; mkdir -p "$STUBS"
    export CALLS="$TEST_DIR/calls"; : > "$CALLS"
    export PATH="$STUBS:$PATH"
    cat > "$STUBS/gcloud" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "gcloud $*" >> "$CALLS"
case "$*" in
    *"addresses describe"*) [ -n "${FAKE_STATIC:-}" ] && printf '%s\n' "$FAKE_STATIC"; [ -n "${FAKE_STATIC:-}" ] || exit 1 ;;
    *"addresses create"*) [ -z "${FAIL_CREATE:-}" ] || exit 1 ;;
esac
exit 0
STUB
    cat > "$STUBS/aws" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "aws $*" >> "$CALLS"
case "$*" in
    *describe-addresses*) printf '%s\n' "${FAKE_EIP:-None}" ;;
    *allocate-address*) printf '%s\t%s\n' eipalloc-1 198.51.100.9 ;;
    *associate-address*) [ -z "${FAIL_ASSOCIATE:-}" ] || exit 1 ;;
esac
exit 0
STUB
    chmod +x "$STUBS"/*
    # shellcheck source=installer/static_ip.sh
    source "$HELPER"
}

teardown() { rm -rf "$TEST_DIR"; }

@test "GCP keeps the VM's current public address as a static one" {
    run gcp_keep_public_ip bioaf us-central1 lab-project 34.1.2.3
    [ "$status" -eq 0 ]
    grep -q -- "compute addresses create bioaf-ip --addresses=34.1.2.3 --region=us-central1 --project=lab-project" "$CALLS"
}

@test "GCP does nothing when the address is already static" {
    export FAKE_STATIC=34.1.2.3
    run gcp_keep_public_ip bioaf us-central1 lab-project 34.1.2.3
    [ "$status" -eq 0 ]
    absent "addresses create" "$CALLS"
}

@test "GCP reports a failure without hiding it" {
    export FAIL_CREATE=1
    run gcp_keep_public_ip bioaf us-central1 lab-project 34.1.2.3
    [ "$status" -ne 0 ]
}

@test "AWS associates one Elastic IP and prints it" {
    run aws_keep_public_ip i-0abc bioaf
    [ "$status" -eq 0 ]
    [ "$output" = 198.51.100.9 ]
    grep -q -- "ec2 allocate-address --domain vpc" "$CALLS"
    grep -q -- "Key=Name,Value=bioaf-ip" "$CALLS"
    grep -q -- "ec2 associate-address --instance-id i-0abc --allocation-id eipalloc-1" "$CALLS"
}

@test "AWS keeps an existing Elastic IP" {
    export FAKE_EIP=198.51.100.4
    run aws_keep_public_ip i-0abc bioaf
    [ "$status" -eq 0 ]
    [ "$output" = 198.51.100.4 ]
    absent allocate-address "$CALLS"
}

@test "a failed AWS association releases the new address" {
    export FAIL_ASSOCIATE=1
    run aws_keep_public_ip i-0abc bioaf
    [ "$status" -ne 0 ]
    grep -q -- "ec2 release-address --allocation-id eipalloc-1" "$CALLS"
}
