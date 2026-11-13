#!/usr/bin/env bats
# Tests for installer/managed_file.sh: version/content-aware reconciliation of a host file bioAF
# manages. Synthetic files and stub callbacks only; nothing here touches a real service.

HELPER="$BATS_TEST_DIRNAME/../../installer/managed_file.sh"

setup() {
    T="$(mktemp -d)"
    mkdir -p "$T/etc" "$T/detail"
    printf 'logging:\n  version: 2\n' > "$T/template.yaml"
    printf 'logging:\n  version: 1\n' > "$T/legacy.yaml"
    LEGACY_SHA="$(shasum -a 256 < "$T/legacy.yaml" | cut -d' ' -f1)"
    DEST="$T/etc/config.yaml"
    : > "$T/calls"
}

# run_reconcile [validate_status] [activate_statuses...]: activate statuses are consumed in order.
run_reconcile() {
    local validate_status="${1:-0}"; shift || true
    printf '%s\n' "$@" > "$T/activate_plan"
    run bash -c "
        source '$HELPER'
        validate() { echo \"validate \$(head -1 \"\$1\")\" >> '$T/calls'; echo 'VALIDATOR-OUTPUT-MARKER'; return $validate_status; }
        activate() {
            local s; s=\$(head -1 '$T/activate_plan'); sed -i.bak 1d '$T/activate_plan'
            echo \"activate \$(tail -1 '$DEST' 2>/dev/null)\" >> '$T/calls'; echo 'ACTIVATOR-OUTPUT-MARKER'
            return \${s:-0}
        }
        MF_PRIV=''
        managed_file_reconcile test-config/v2 '$T/template.yaml' '$DEST' '$LEGACY_SHA' validate activate '$T/detail'
        rc=\$?
        echo \"outcome=\$MF_OUTCOME rc=\$rc\"
        exit \$rc
    " </dev/null
}

rendered() {
    bash -c "source '$HELPER'; managed_file_render test-config/v2 '$T/template.yaml'"
}

@test "rendering prefixes the template with its identity and content hash" {
    run rendered
    [ "$status" -eq 0 ]
    sha="$(shasum -a 256 < "$T/template.yaml" | cut -d' ' -f1)"
    [ "${lines[0]}" = "# bioaf-managed: test-config/v2 sha256=$sha" ]
    [ "${lines[1]}" = "logging:" ]
}

@test "a fresh host gets the file validated, installed and activated" {
    run_reconcile 0 0
    [ "$status" -eq 0 ]
    [[ "$output" == *"outcome=installed"* ]]
    cmp -s "$DEST" <(rendered)
    grep -q '^validate # bioaf-managed: test-config/v2' "$T/calls"
    grep -q '^activate   version: 2$' "$T/calls"
}

@test "the known legacy file is migrated" {
    cp "$T/legacy.yaml" "$DEST"
    run_reconcile 0 0
    [ "$status" -eq 0 ]
    [[ "$output" == *"outcome=migrated"* ]]
    cmp -s "$DEST" <(rendered)
}

@test "an older managed version with intact content is migrated" {
    { printf '# bioaf-managed: test-config/v1 sha256=%s\n' "$LEGACY_SHA"; cat "$T/legacy.yaml"; } > "$DEST"
    run_reconcile 0 0
    [ "$status" -eq 0 ]
    [[ "$output" == *"outcome=migrated"* ]]
    cmp -s "$DEST" <(rendered)
}

@test "the current file is left alone and nothing is validated or restarted" {
    rendered > "$DEST"
    run_reconcile 0 0
    [ "$status" -eq 0 ]
    [[ "$output" == *"outcome=unchanged"* ]]
    [ ! -s "$T/calls" ]
}

@test "repeated runs are idempotent" {
    run_reconcile 0 0
    : > "$T/calls"
    run_reconcile 0 0
    [[ "$output" == *"outcome=unchanged"* ]]
    [ ! -s "$T/calls" ]
}

@test "a managed file edited after install is drift, not silently replaced or accepted" {
    rendered > "$DEST"
    echo "  operator_edit: true" >> "$DEST"
    cp "$DEST" "$T/before"
    run_reconcile 0 0
    [ "$status" -ne 0 ]
    [[ "$output" == *"outcome=custom"* ]]
    cmp -s "$DEST" "$T/before"
    [ ! -s "$T/calls" ]
}

@test "a custom file is preserved and a restricted candidate is saved without printing either" {
    printf 'logging:\n  secret_ish: CUSTOM-CONTENT-MARKER\n' > "$DEST"
    cp "$DEST" "$T/before"
    run_reconcile 0 0
    [ "$status" -ne 0 ]
    [[ "$output" == *"outcome=custom"* ]]
    cmp -s "$DEST" "$T/before"
    [[ "$output" != *"CUSTOM-CONTENT-MARKER"* ]]
    [[ "$output" != *"version: 2"* ]]
    candidate="$T/detail/test-config-v2.candidate"
    cmp -s "$candidate" <(rendered)
    [ "$(stat -c %a "$candidate" 2>/dev/null || stat -f %Lp "$candidate")" = "600" ]
    [[ "$output" == *"$candidate"* ]]
}

@test "an operator-managed file is left alone and reported" {
    printf '# bioaf-managed: operator\nlogging: {}\n' > "$DEST"
    cp "$DEST" "$T/before"
    run_reconcile 0 0
    [ "$status" -eq 0 ]
    [[ "$output" == *"outcome=operator_managed"* ]]
    cmp -s "$DEST" "$T/before"
    [ ! -s "$T/calls" ]
}

@test "a candidate that fails validation is never installed" {
    cp "$T/legacy.yaml" "$DEST"
    run_reconcile 1 0
    [ "$status" -ne 0 ]
    [[ "$output" == *"outcome=invalid"* ]]
    cmp -s "$DEST" "$T/legacy.yaml"
    ! grep -q '^activate' "$T/calls"
    [ -z "$(ls "$T/etc" | grep -v '^config.yaml$')" ]
}

@test "an activation failure restores and reactivates the previous file" {
    cp "$T/legacy.yaml" "$DEST"
    run_reconcile 0 1 0
    [ "$status" -ne 0 ]
    [[ "$output" == *"outcome=activation_failed"* ]]
    cmp -s "$DEST" "$T/legacy.yaml"
    [ "$(grep -c '^activate' "$T/calls")" -eq 2 ]
    [ "$(sed -n 3p "$T/calls")" = "activate   version: 1" ]
}

@test "a failed rollback is reported as such" {
    cp "$T/legacy.yaml" "$DEST"
    run_reconcile 0 1 1
    [ "$status" -ne 0 ]
    [[ "$output" == *"outcome=rollback_failed"* ]]
    cmp -s "$DEST" "$T/legacy.yaml"
}

@test "a fresh install whose activation fails leaves no file behind" {
    run_reconcile 0 1 0
    [ "$status" -ne 0 ]
    [[ "$output" == *"outcome=activation_failed"* ]]
    [ ! -e "$DEST" ]
}

@test "callback output stays out of unattended output and in the restricted detail log" {
    cp "$T/legacy.yaml" "$DEST"
    run_reconcile 0 0
    [[ "$output" != *"VALIDATOR-OUTPUT-MARKER"* ]]
    [[ "$output" != *"ACTIVATOR-OUTPUT-MARKER"* ]]
    log="$T/detail/test-config-v2.log"
    grep -q VALIDATOR-OUTPUT-MARKER "$log"
    [ "$(stat -c %a "$log" 2>/dev/null || stat -f %Lp "$log")" = "600" ]
}
