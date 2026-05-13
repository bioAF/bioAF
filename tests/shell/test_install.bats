#!/usr/bin/env bats
# Tests for the install.sh installer script

INSTALL_SCRIPT="$BATS_TEST_DIRNAME/../../install.sh"

setup() {
    # Create a temp directory for each test
    TEST_DIR="$(mktemp -d)"
    export BIOAF_ROOT="$TEST_DIR/bioaf"
    mkdir -p "$BIOAF_ROOT/docker"
    # Copy install script into the fake root
    cp "$INSTALL_SCRIPT" "$BIOAF_ROOT/install.sh"
    # Create a minimal .env.example so the script has something to copy
    cat > "$BIOAF_ROOT/.env.example" <<'EOF'
POSTGRES_USER=bioaf
POSTGRES_PASSWORD=CHANGE_ME
POSTGRES_DB=bioaf
DATABASE_URL=postgresql+asyncpg://bioaf:CHANGE_ME@db:5432/bioaf
SECRET_KEY=CHANGE_ME_TO_RANDOM_STRING
NEXT_PUBLIC_API_URL=
BIOAF_ENVIRONMENT=production
EOF
}

teardown() {
    rm -rf "$TEST_DIR"
}

# ---------------------------------------------------------------------------
# Script basics
# ---------------------------------------------------------------------------

@test "install.sh exists and is executable" {
    [ -f "$INSTALL_SCRIPT" ]
    [ -x "$INSTALL_SCRIPT" ]
}

@test "install.sh --help shows usage" {
    run bash "$INSTALL_SCRIPT" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage"* ]] || [[ "$output" == *"usage"* ]] || [[ "$output" == *"install"* ]]
}

# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------

@test "install.sh check-prereqs succeeds when docker and git are present" {
    # install.sh refuses to run on non-Linux hosts (macOS / Windows) by
    # design -- it is meant to be executed on the GCP VM, not on the
    # operator's laptop. Skip this contract test on those hosts.
    if [ "$(uname -s)" != "Linux" ]; then
        skip "install.sh requires Linux ($(uname -s) detected)"
    fi
    if ! command -v docker &>/dev/null; then
        skip "docker not installed on test runner"
    fi
    if ! command -v git &>/dev/null; then
        skip "git not installed on test runner"
    fi
    run bash "$INSTALL_SCRIPT" check-prereqs
    [ "$status" -eq 0 ]
}

@test "install.sh check-prereqs fails when a required tool is missing" {
    # Create a restricted PATH with no docker. On non-Linux hosts the
    # OS-guard fires first and we still expect a non-zero exit, just for
    # a different reason -- both are valid "fail" outcomes for this test.
    run env PATH="/usr/bin" bash "$INSTALL_SCRIPT" check-prereqs
    [ "$status" -ne 0 ] || [[ "$output" == *"docker"* ]]
}

# ---------------------------------------------------------------------------
# .env generation
# ---------------------------------------------------------------------------

@test "install.sh generate-env creates docker/.env from .env.example" {
    cd "$BIOAF_ROOT"
    run bash install.sh generate-env --non-interactive
    [ "$status" -eq 0 ]
    [ -f "$BIOAF_ROOT/docker/.env" ]
}

@test "generated .env contains auto-generated secrets (not CHANGE_ME)" {
    cd "$BIOAF_ROOT"
    run bash install.sh generate-env --non-interactive
    [ "$status" -eq 0 ]
    # The generated password should NOT be CHANGE_ME
    local pg_pass
    pg_pass=$(grep "^POSTGRES_PASSWORD=" "$BIOAF_ROOT/docker/.env" | cut -d= -f2)
    [ "$pg_pass" != "CHANGE_ME" ]
    [ -n "$pg_pass" ]
}

@test "generated .env has matching password in DATABASE_URL" {
    cd "$BIOAF_ROOT"
    run bash install.sh generate-env --non-interactive
    [ "$status" -eq 0 ]
    local pg_pass db_url
    pg_pass=$(grep "^POSTGRES_PASSWORD=" "$BIOAF_ROOT/docker/.env" | cut -d= -f2)
    db_url=$(grep "^DATABASE_URL=" "$BIOAF_ROOT/docker/.env" | cut -d= -f2-)
    [[ "$db_url" == *"$pg_pass"* ]]
}

@test "generated .env has a non-placeholder SECRET_KEY" {
    cd "$BIOAF_ROOT"
    run bash install.sh generate-env --non-interactive
    [ "$status" -eq 0 ]
    local secret
    secret=$(grep "^SECRET_KEY=" "$BIOAF_ROOT/docker/.env" | cut -d= -f2)
    [ "$secret" != "CHANGE_ME_TO_RANDOM_STRING" ]
    [ -n "$secret" ]
}

@test "generate-env preserves existing POSTGRES_PASSWORD and SECRET_KEY across re-runs" {
    cd "$BIOAF_ROOT"
    cat > "$BIOAF_ROOT/docker/.env" <<'EOF'
POSTGRES_USER=bioaf
POSTGRES_PASSWORD=preserve_this_password_abc123
POSTGRES_DB=bioaf
DATABASE_URL=postgresql+asyncpg://bioaf:preserve_this_password_abc123@db:5432/bioaf
SECRET_KEY=preserve_this_secret_key_xyz789
BIOAF_ENVIRONMENT=production
EOF
    run bash install.sh generate-env --non-interactive
    [ "$status" -eq 0 ]
    # Existing secrets must survive a re-run -- regenerating them silently
    # would orphan the database volume and break login sessions.
    grep -q "^POSTGRES_PASSWORD=preserve_this_password_abc123$" "$BIOAF_ROOT/docker/.env"
    grep -q "^SECRET_KEY=preserve_this_secret_key_xyz789$" "$BIOAF_ROOT/docker/.env"
}

@test "generate-env --force regenerates secrets even when they already exist" {
    cd "$BIOAF_ROOT"
    cat > "$BIOAF_ROOT/docker/.env" <<'EOF'
POSTGRES_USER=bioaf
POSTGRES_PASSWORD=will_be_regenerated
POSTGRES_DB=bioaf
DATABASE_URL=postgresql+asyncpg://bioaf:will_be_regenerated@db:5432/bioaf
SECRET_KEY=will_also_be_regenerated
BIOAF_ENVIRONMENT=production
EOF
    run bash install.sh generate-env --non-interactive --force
    [ "$status" -eq 0 ]
    # --force is the explicit "yes, please rotate" path; the old values
    # must be gone.
    ! grep -q "will_be_regenerated" "$BIOAF_ROOT/docker/.env"
    ! grep -q "will_also_be_regenerated" "$BIOAF_ROOT/docker/.env"
}

@test "generated .env does not contain NEXT_PUBLIC_API_URL" {
    cd "$BIOAF_ROOT"
    run bash install.sh generate-env --non-interactive
    [ "$status" -eq 0 ]
    # NEXT_PUBLIC_API_URL should not appear -- nginx handles routing
    ! grep -q "NEXT_PUBLIC_API_URL" "$BIOAF_ROOT/docker/.env"
}

# ---------------------------------------------------------------------------
# ensure-encryption-key: upgrade path for installs that predate v0.13.0.
# Their docker/.env has no BIOAF_ENCRYPTION_KEYS, so the backend's startup
# check would refuse to come up after a `./bioaf update`. The helper must
# append (never rewrite) the variable, leaving every other line untouched.
# ---------------------------------------------------------------------------

@test "ensure-encryption-key appends BIOAF_ENCRYPTION_KEYS when missing" {
    cd "$BIOAF_ROOT"
    cat > "$BIOAF_ROOT/docker/.env" <<'EOF'
POSTGRES_USER=bioaf
POSTGRES_PASSWORD=existing_password_abc123
POSTGRES_DB=bioaf
DATABASE_URL=postgresql+asyncpg://bioaf:existing_password_abc123@db:5432/bioaf
SECRET_KEY=existing_secret_xyz789
BIOAF_ENVIRONMENT=production
EOF
    if ! command -v python3 >/dev/null 2>&1; then
        skip "python3 required for Fernet key generation"
    fi
    run bash install.sh ensure-encryption-key
    [ "$status" -eq 0 ]

    # Existing lines must be untouched.
    grep -q "^POSTGRES_PASSWORD=existing_password_abc123$" "$BIOAF_ROOT/docker/.env"
    grep -q "^SECRET_KEY=existing_secret_xyz789$" "$BIOAF_ROOT/docker/.env"

    # New variable added with a syntactically valid Fernet key (44 chars,
    # urlsafe-base64, ends with '=').
    local key
    key=$(grep "^BIOAF_ENCRYPTION_KEYS=" "$BIOAF_ROOT/docker/.env" | cut -d= -f2-)
    [ -n "$key" ]
    [ "${#key}" -eq 44 ]
    [[ "$key" == *"=" ]]
}

@test "ensure-encryption-key is idempotent when key already present" {
    cd "$BIOAF_ROOT"
    cat > "$BIOAF_ROOT/docker/.env" <<'EOF'
POSTGRES_USER=bioaf
POSTGRES_PASSWORD=existing_password
BIOAF_ENCRYPTION_KEYS=yQWeSjhut-D91YUcqvDUfQ62wQHNq1G3vUstCSJpk9U=
BIOAF_ENVIRONMENT=production
EOF
    local before
    before=$(cat "$BIOAF_ROOT/docker/.env")

    run bash install.sh ensure-encryption-key
    [ "$status" -eq 0 ]

    # Existing key value must survive untouched -- second run is a no-op.
    local after
    after=$(cat "$BIOAF_ROOT/docker/.env")
    [ "$before" = "$after" ]
}

@test "ensure-encryption-key replaces empty BIOAF_ENCRYPTION_KEYS= line" {
    cd "$BIOAF_ROOT"
    # Some installs may have an empty placeholder (e.g. from the
    # .env.example shipped pre-v0.13.0 once we added the line as a hint).
    cat > "$BIOAF_ROOT/docker/.env" <<'EOF'
POSTGRES_USER=bioaf
BIOAF_ENCRYPTION_KEYS=
BIOAF_ENVIRONMENT=production
EOF
    if ! command -v python3 >/dev/null 2>&1; then
        skip "python3 required for Fernet key generation"
    fi
    run bash install.sh ensure-encryption-key
    [ "$status" -eq 0 ]

    local key
    key=$(grep "^BIOAF_ENCRYPTION_KEYS=" "$BIOAF_ROOT/docker/.env" | cut -d= -f2-)
    [ "${#key}" -eq 44 ]
}

@test "ensure-encryption-key is a no-op when docker/.env does not exist" {
    cd "$BIOAF_ROOT"
    rm -f "$BIOAF_ROOT/docker/.env"
    run bash install.sh ensure-encryption-key
    # Nothing to amend; the helper should exit cleanly with a clear message
    # rather than crash. The operator hasn't run generate-env yet, so this
    # is "not applicable", not an error.
    [ "$status" -eq 0 ]
    [ ! -f "$BIOAF_ROOT/docker/.env" ]
}
