#!/usr/bin/env bats
# Tests for the backend entrypoint's bootstrap_encryption_key function.
#
# The function is the safety net for upgrades from < v0.13.0 where the
# host-side ensure-encryption-key step could not run. It must:
#   - Skip silently when BIOAF_ENCRYPTION_KEYS is already set.
#   - Generate a Fernet key when unset AND /host/.env is writable.
#   - Persist the key to /host/.env so subsequent compose runs pick it up.
#   - Refuse to overwrite a real value in /host/.env (defense in depth).
#   - Fail loudly when /host/.env is absent or read-only.

ENTRYPOINT="$BATS_TEST_DIRNAME/../../backend/entrypoint.sh"

setup() {
    TEST_DIR="$(mktemp -d)"
    export HOST_ENV="$TEST_DIR/host_env"
    # Source the entrypoint with the bootstrap call disabled so we can
    # invoke bootstrap_encryption_key directly with controlled inputs.
    # The entrypoint calls bootstrap_encryption_key at top level; trim that
    # off into a sourceable shim.
    sed '/^bootstrap_encryption_key$/,$d' "$ENTRYPOINT" > "$TEST_DIR/shim.sh"
    chmod +x "$TEST_DIR/shim.sh"
}

teardown() {
    rm -rf "$TEST_DIR"
}

@test "bootstrap: no-op when BIOAF_ENCRYPTION_KEYS already set" {
    cat > "$HOST_ENV" <<'EOF'
SOMETHING_ELSE=value
EOF
    ln -s "$HOST_ENV" /tmp/.bats-host-env-$$
    # The function uses /host/.env literally. Simulate via subshell that
    # creates that path in a chroot-like scope: use a writable scratch via
    # bind-mounting is not portable in bats, so override the path via a
    # wrapper that swaps /host/.env -> $HOST_ENV.
    BIOAF_ENCRYPTION_KEYS="abc" run bash -c "
        source $TEST_DIR/shim.sh
        sed -i.bak 's|/host/.env|$HOST_ENV|g' \$0 2>/dev/null || true
        # The shim itself references /host/.env. Re-source after rewriting.
        bootstrap_encryption_key
        # Confirm no write happened.
        grep -q BIOAF_ENCRYPTION_KEYS $HOST_ENV && echo BAD || echo OK_NO_WRITE
    "
    [ "$status" -eq 0 ]
    [[ "$output" == *"OK_NO_WRITE"* ]]
}

@test "bootstrap: fails when /host/.env is absent (k8s misconfig path)" {
    # The function literally references /host/.env. To simulate the
    # not-present case, use sed to swap the path to one we control then
    # delete it.
    SHIM_WITH_PATH="$TEST_DIR/shim_absent.sh"
    sed "s|/host/.env|$HOST_ENV|g" "$TEST_DIR/shim.sh" > "$SHIM_WITH_PATH"
    rm -f "$HOST_ENV"

    BIOAF_ENCRYPTION_KEYS="" run bash -c "
        source $SHIM_WITH_PATH
        bootstrap_encryption_key
    "
    [ "$status" -ne 0 ]
    [[ "$output" == *"no host .env is mounted"* ]] || [[ "$output" == *"FATAL"* ]]
}

@test "bootstrap: generates + persists key when /host/.env writable and var unset" {
    SHIM_WITH_PATH="$TEST_DIR/shim_writable.sh"
    sed "s|/host/.env|$HOST_ENV|g" "$TEST_DIR/shim.sh" > "$SHIM_WITH_PATH"
    cat > "$HOST_ENV" <<'EOF'
POSTGRES_USER=bioaf
POSTGRES_PASSWORD=preserved_value_xyz
SECRET_KEY=preserved_secret_abc
BIOAF_ENVIRONMENT=production
EOF
    chmod 644 "$HOST_ENV"

    # python in this test must have cryptography. Skip if not available
    # (the production backend image always has it; the test runner may not).
    if ! python -c "from cryptography.fernet import Fernet" 2>/dev/null; then
        skip "cryptography not available on test runner; production image has it"
    fi

    BIOAF_ENCRYPTION_KEYS="" run bash -c "
        source $SHIM_WITH_PATH
        bootstrap_encryption_key
        echo \"FINAL_KEY=\$BIOAF_ENCRYPTION_KEYS\"
    "
    [ "$status" -eq 0 ]

    # Other lines must be untouched.
    grep -q "^POSTGRES_PASSWORD=preserved_value_xyz$" "$HOST_ENV"
    grep -q "^SECRET_KEY=preserved_secret_abc$" "$HOST_ENV"

    # New variable appended with a 44-char urlsafe Fernet key.
    key=$(grep "^BIOAF_ENCRYPTION_KEYS=" "$HOST_ENV" | tail -1 | cut -d= -f2-)
    [ -n "$key" ]
    [ "${#key}" -eq 44 ]
    [[ "$key" =~ ^[A-Za-z0-9_-]+=$ ]]

    # Exported value matches what was written.
    [[ "$output" == *"FINAL_KEY=$key"* ]]
}

@test "bootstrap: refuses to overwrite an existing value in /host/.env" {
    # Defense-in-depth: if BIOAF_ENCRYPTION_KEYS env var is empty but the
    # .env file has a real value, something is wrong with the env-file
    # plumbing. Don't generate a NEW key and overwrite -- that would
    # orphan every encrypted row.
    SHIM_WITH_PATH="$TEST_DIR/shim_existing.sh"
    sed "s|/host/.env|$HOST_ENV|g" "$TEST_DIR/shim.sh" > "$SHIM_WITH_PATH"
    cat > "$HOST_ENV" <<'EOF'
BIOAF_ENCRYPTION_KEYS=preexisting-key-value-abc123
EOF

    BIOAF_ENCRYPTION_KEYS="" run bash -c "
        source $SHIM_WITH_PATH
        bootstrap_encryption_key
        echo \"FINAL_KEY=\$BIOAF_ENCRYPTION_KEYS\"
    "
    [ "$status" -eq 0 ]
    [[ "$output" == *"WARNING"* ]]
    # The function should re-export the file's value rather than mint a new one.
    [[ "$output" == *"FINAL_KEY=preexisting-key-value-abc123"* ]]
    # File must not have been amended.
    [ "$(grep -c "^BIOAF_ENCRYPTION_KEYS=" "$HOST_ENV")" -eq 1 ]
}
