#!/usr/bin/env bats
# installer/gcp_iam.sh: retry_iam retries GCP IAM calls only on transient errors. Propagation lag
# keeps its short retry; rate limits (429 / RESOURCE_EXHAUSTED) get bounded exponential backoff with
# jitter; permission and configuration errors fail at once. sleep is recorded, never slept.

HELPER="$BATS_TEST_DIRNAME/../../installer/gcp_iam.sh"

setup() {
    TEST_DIR="$(mktemp -d)"
    export SLEEPS="$TEST_DIR/sleeps" CALLS="$TEST_DIR/calls"
    : > "$SLEEPS"; : > "$CALLS"
    # shellcheck source=installer/gcp_iam.sh
    source "$HELPER"
    sleep() { printf '%s\n' "$1" >> "$SLEEPS"; }
}

teardown() { rm -rf "$TEST_DIR"; }

# fake <failures> <message>: fails <failures> times with <message>, then succeeds.
fake() {
    local n
    n=$(wc -l < "$CALLS")
    echo call >> "$CALLS"
    if [ "$n" -lt "$1" ]; then printf '%s\n' "$2" >&2; return 1; fi
    echo ok
}

@test "a rate-limited call is retried with growing, bounded waits and then succeeds" {
    run retry_iam fake 4 "ERROR: (gcloud.projects.add-iam-policy-binding) RESOURCE_EXHAUSTED: Quota exceeded for quota metric 'Read requests' (code 429)"
    [ "$status" -eq 0 ]
    [ "$(wc -l < "$CALLS")" -eq 5 ]
    mapfile -t waits < "$SLEEPS"
    [ "${#waits[@]}" -eq 4 ]
    for i in 1 2 3; do [ "${waits[$i]}" -ge "${waits[$((i-1))]}" ]; done
    for w in "${waits[@]}"; do [ "$w" -ge 5 ] && [ "$w" -le 90 ]; done
}

@test "a rate limit that never clears gives up after a bounded number of attempts" {
    run retry_iam fake 99 "HTTPError 429: Too Many Requests"
    [ "$status" -ne 0 ]
    [ "$(wc -l < "$CALLS")" -eq 6 ]
}

@test "a permission error fails at once, without waiting" {
    run retry_iam fake 99 "ERROR: PERMISSION_DENIED: Policy update access denied. (code 403)"
    [ "$status" -ne 0 ]
    [ "$(wc -l < "$CALLS")" -eq 1 ]
    [ ! -s "$SLEEPS" ]
}

@test "service account propagation lag keeps its short retry" {
    run retry_iam fake 1 "INVALID_ARGUMENT: Service account bioaf-app@p.iam.gserviceaccount.com does not exist."
    [ "$status" -eq 0 ]
    [ "$(wc -l < "$CALLS")" -eq 2 ]
    [ "$(cat "$SLEEPS")" = 5 ]
}

@test "install-gcp.sh loads retry_iam from the helper instead of defining its own" {
    grep -q 'bioaf_load_helper installer/gcp_iam.sh required retry_iam' "$BATS_TEST_DIRNAME/../../install-gcp.sh"
    if grep -q '^retry_iam()' "$BATS_TEST_DIRNAME/../../install-gcp.sh"; then return 1; fi
}
