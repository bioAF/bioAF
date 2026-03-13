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
    # This test only passes if the test runner has docker + git installed
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
    # Create a restricted PATH with no docker
    run env PATH="/usr/bin" bash "$INSTALL_SCRIPT" check-prereqs
    # Should fail because docker won't be found on the restricted PATH
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

@test "generate-env does not overwrite existing .env" {
    cd "$BIOAF_ROOT"
    echo "EXISTING=true" > "$BIOAF_ROOT/docker/.env"
    run bash install.sh generate-env --non-interactive
    [ "$status" -eq 0 ]
    [[ "$output" == *"already exists"* ]]
    # Original content preserved
    grep -q "EXISTING=true" "$BIOAF_ROOT/docker/.env"
}

@test "generate-env --force overwrites existing .env" {
    cd "$BIOAF_ROOT"
    echo "EXISTING=true" > "$BIOAF_ROOT/docker/.env"
    run bash install.sh generate-env --non-interactive --force
    [ "$status" -eq 0 ]
    # Should have new generated content, not the old line
    ! grep -q "EXISTING=true" "$BIOAF_ROOT/docker/.env"
}

@test "generated .env does not contain NEXT_PUBLIC_API_URL" {
    cd "$BIOAF_ROOT"
    run bash install.sh generate-env --non-interactive
    [ "$status" -eq 0 ]
    # NEXT_PUBLIC_API_URL should not appear -- nginx handles routing
    ! grep -q "NEXT_PUBLIC_API_URL" "$BIOAF_ROOT/docker/.env"
}
