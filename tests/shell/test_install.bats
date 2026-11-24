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
    mkdir -p "$BIOAF_ROOT/installer"
    cp "$BATS_TEST_DIRNAME/../../installer/host_prereqs.sh" "$BIOAF_ROOT/installer/"
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

@test "generate-env produces a valid 44-char urlsafe Fernet key for BIOAF_ENCRYPTION_KEYS" {
    cd "$BIOAF_ROOT"
    run bash install.sh generate-env --non-interactive
    [ "$status" -eq 0 ]
    # Fernet key contract: 44 chars, urlsafe-base64 (only A-Z a-z 0-9 - _ =).
    # The backend's validate_encryption_keys rejects anything else, so this
    # test is the canary for "fresh install will start cleanly".
    local key
    key=$(grep "^BIOAF_ENCRYPTION_KEYS=" "$BIOAF_ROOT/docker/.env" | cut -d= -f2-)
    [ -n "$key" ]
    [ "${#key}" -eq 44 ]
    [[ "$key" =~ ^[A-Za-z0-9_-]+=$ ]]
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
    if ! command -v openssl >/dev/null 2>&1; then
        skip "openssl required for Fernet key generation"
    fi
    run bash install.sh ensure-encryption-key
    [ "$status" -eq 0 ]

    # Existing lines must be untouched.
    grep -q "^POSTGRES_PASSWORD=existing_password_abc123$" "$BIOAF_ROOT/docker/.env"
    grep -q "^SECRET_KEY=existing_secret_xyz789$" "$BIOAF_ROOT/docker/.env"

    # New variable added with a syntactically valid Fernet key (44 chars,
    # urlsafe-base64, ends with '=', uses '-' and '_' instead of '+' and '/').
    local key
    key=$(grep "^BIOAF_ENCRYPTION_KEYS=" "$BIOAF_ROOT/docker/.env" | cut -d= -f2-)
    [ -n "$key" ]
    [ "${#key}" -eq 44 ]
    [[ "$key" == *"=" ]]
    # Must not contain the non-urlsafe base64 characters.
    [[ "$key" != *"+"* ]]
    [[ "$key" != *"/"* ]]
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
    if ! command -v openssl >/dev/null 2>&1; then
        skip "openssl required for Fernet key generation"
    fi
    run bash install.sh ensure-encryption-key
    [ "$status" -eq 0 ]

    local key
    key=$(grep "^BIOAF_ENCRYPTION_KEYS=" "$BIOAF_ROOT/docker/.env" | cut -d= -f2-)
    [ "${#key}" -eq 44 ]
}

# The gate-aware caller contract (plan 03A): this release's updater sets BIOAF_UPDATE_GATE=caller and the
# hook does its normal work; only an older updater mid-update gets the preparation pass.
gate_fixture() {
    mkdir -p "$BIOAF_ROOT/scripts" "$BIOAF_ROOT/update-status"
    cp "$BATS_TEST_DIRNAME/../../scripts/update_host_io.py" "$BIOAF_ROOT/scripts/"
    printf '#!/usr/bin/env bash\necho "bioaf $*" >> "%s/calls"\n' "$BIOAF_ROOT" > "$BIOAF_ROOT/bioaf"
    printf '{"status": "in_progress", "step": "ensure_encryption_key", "from_version": "2026.11.21", "to_version": "2026.12.1"}\n' \
        > "$BIOAF_ROOT/update-status/current.json"
    printf 'POSTGRES_USER=bioaf\n' > "$BIOAF_ROOT/docker/.env"
}

@test "ensure-encryption-key under the gate-aware updater does its normal work" {
    command -v openssl >/dev/null 2>&1 || skip "openssl required for Fernet key generation"
    cd "$BIOAF_ROOT"; gate_fixture
    run env BIOAF_UPDATE_GATE=caller BIOAF_HOST_LOG_COLLECTION=caller bash install.sh ensure-encryption-key
    [ "$status" -eq 0 ]
    grep -q '^BIOAF_ENCRYPTION_KEYS=' "$BIOAF_ROOT/docker/.env"
    [ ! -e "$BIOAF_ROOT/host-state/update-preparation.json" ]
}

@test "ensure-encryption-key under an older updater prepares the agent and refuses before the env changes" {
    cd "$BIOAF_ROOT"; gate_fixture
    cp docker/.env env.before
    run env -u BIOAF_UPDATE_GATE bash install.sh ensure-encryption-key
    [ "$status" -ne 0 ]
    cmp -s docker/.env env.before
    [ "$(cat "$BIOAF_ROOT/calls")" = "bioaf ensure-update-agent" ]
    [[ "$output" == *"The update helper is ready. bioAF and your work nodes are still running. Retry the update to continue."* ]]
    grep -q '"target": "2026.12.1"' "$BIOAF_ROOT/host-state/update-preparation.json"
}

@test "ensure-encryption-key outside an update keeps its normal behavior" {
    command -v openssl >/dev/null 2>&1 || skip "openssl required for Fernet key generation"
    cd "$BIOAF_ROOT"; gate_fixture
    printf '{"status": "completed", "step": "done"}\n' > "$BIOAF_ROOT/update-status/current.json"
    run env -u BIOAF_UPDATE_GATE bash install.sh ensure-encryption-key
    [ "$status" -eq 0 ]
    grep -q '^BIOAF_ENCRYPTION_KEYS=' "$BIOAF_ROOT/docker/.env"
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

# ---------------------------------------------------------------------------
# Host Python prerequisite. ./bioaf, the update agent and parts of this installer run python3 on the
# host (not the backend container's interpreter), so installation checks it before changing anything.
# ---------------------------------------------------------------------------

load host_python

# host_stubs: a Linux uname and a docker that answers the prerequisite probes, each logging its calls,
# in $TEST_DIR/stubs. PATH order puts them ahead of everything host_python_path links.
host_stubs() {
    mkdir -p "$TEST_DIR/stubs"
    printf '#!/bin/sh\necho Linux\n' > "$TEST_DIR/stubs/uname"
    cat > "$TEST_DIR/stubs/docker" <<'STUB'
#!/bin/sh
echo "docker $*" >> "$TEST_DIR/calls"
case "$1" in
    --version) echo "Docker version 27.0.0" ;;
    compose) echo "Docker Compose version v2.29.0" ;;
esac
exit 0
STUB
    printf '#!/bin/sh\necho "sudo $*" >> "$TEST_DIR/calls"\nexit 0\n' > "$TEST_DIR/stubs/sudo"
    chmod +x "$TEST_DIR/stubs/"*
    export TEST_DIR
}

# with_python <kind> <command...>: run <command> with only a <kind> python3 (see host_python.bash).
with_python() {
    local kind="$1"; shift
    run env PATH="$TEST_DIR/stubs:$(host_python_path "$TEST_DIR/py" "$kind")" "$@" </dev/null
}

@test "check-prereqs fails, naming python3 and its minimum, when no python3 is installed" {
    host_stubs
    with_python none bash "$BIOAF_ROOT/install.sh" check-prereqs
    [ "$status" -ne 0 ]
    [[ "$output" == *"python3"* ]]
    [[ "$output" == *"3.10"* ]]
    [[ "$output" == *"apt-get install"* ]]
}

@test "check-prereqs fails when python3 cannot run" {
    host_stubs
    with_python broken bash "$BIOAF_ROOT/install.sh" check-prereqs
    [ "$status" -ne 0 ]
    [[ "$output" == *"python3"* ]]
    [[ "$output" == *"could not run"* ]]
}

@test "check-prereqs fails when python3 is older than the supported minimum" {
    host_stubs
    with_python old bash "$BIOAF_ROOT/install.sh" check-prereqs
    [ "$status" -ne 0 ]
    [[ "$output" == *"3.8"* ]]
    [[ "$output" == *"3.10"* ]]
}

@test "check-prereqs fails when python3 lacks a standard-library capability host operations use" {
    host_stubs
    with_python nocap bash "$BIOAF_ROOT/install.sh" check-prereqs
    [ "$status" -ne 0 ]
    [[ "$output" == *"fcntl"* ]]
}

@test "check-prereqs accepts a working python3 and still checks the other tools" {
    host_stubs
    with_python real bash "$BIOAF_ROOT/install.sh" check-prereqs
    [ "$status" -eq 0 ]
    [[ "$output" == *"python3"* ]]
    [[ "$output" == *"docker"* ]]
    [[ "$output" == *"git"* ]]
    [[ "$output" == *"openssl"* ]]
}

@test "a missing python3 is reported together with other missing tools" {
    host_stubs
    rm "$TEST_DIR/stubs/docker"
    printf '#!/bin/sh\nexit 1\n' > "$TEST_DIR/stubs/docker"; chmod +x "$TEST_DIR/stubs/docker"
    with_python none bash "$BIOAF_ROOT/install.sh" check-prereqs
    [ "$status" -ne 0 ]
    [[ "$output" == *"docker compose . NOT FOUND"* ]]
    [[ "$output" == *"python3"* ]]
}

@test "a full install without python3 stops before writing secrets, certificates or host changes" {
    host_stubs
    with_python none bash "$BIOAF_ROOT/install.sh" --non-interactive
    [ "$status" -ne 0 ]
    [[ "$output" == *"python3"* ]]
    [ ! -e "$BIOAF_ROOT/docker/.env" ]
    [ ! -e "$BIOAF_ROOT/docker/certs" ]
    ! grep -q '^sudo' "$TEST_DIR/calls" 2>/dev/null
}

@test "generate-env --force without python3 leaves the existing secrets in place" {
    host_stubs
    printf 'POSTGRES_PASSWORD=keep_me\nSECRET_KEY=keep_me_too\n' > "$BIOAF_ROOT/docker/.env"
    cp "$BIOAF_ROOT/docker/.env" "$TEST_DIR/env.before"
    with_python none bash "$BIOAF_ROOT/install.sh" generate-env --non-interactive --force
    [ "$status" -ne 0 ]
    [[ "$output" == *"python3"* ]]
    cmp "$TEST_DIR/env.before" "$BIOAF_ROOT/docker/.env"
}

@test "generate-env, generate-certs and ensure-encryption-key need no python3 on their own" {
    host_stubs
    with_python none bash "$BIOAF_ROOT/install.sh" generate-env --non-interactive
    [ "$status" -eq 0 ]
    grep -q '^SECRET_KEY=' "$BIOAF_ROOT/docker/.env"
    with_python none bash "$BIOAF_ROOT/install.sh" generate-certs
    [ "$status" -eq 0 ]
    [ -f "$BIOAF_ROOT/docker/certs/tls.crt" ]
    sed -i.bak '/^BIOAF_ENCRYPTION_KEYS=/d' "$BIOAF_ROOT/docker/.env"
    with_python none bash "$BIOAF_ROOT/install.sh" ensure-encryption-key
    [ "$status" -eq 0 ]
    grep -q '^BIOAF_ENCRYPTION_KEYS=.' "$BIOAF_ROOT/docker/.env"
}

@test "ensure-encryption-key without python3 changes nothing when its ./bioaf host hooks would run" {
    host_stubs
    printf '#!/bin/sh\necho "bioaf $*" >> "$TEST_DIR/calls"\n' > "$BIOAF_ROOT/bioaf"
    printf 'POSTGRES_PASSWORD=keep_me\n' > "$BIOAF_ROOT/docker/.env"
    cp "$BIOAF_ROOT/docker/.env" "$TEST_DIR/env.before"
    with_python none bash "$BIOAF_ROOT/install.sh" ensure-encryption-key
    [ "$status" -ne 0 ]
    [[ "$output" == *"python3"* ]]
    cmp "$TEST_DIR/env.before" "$BIOAF_ROOT/docker/.env"
    ! grep -q '^bioaf' "$TEST_DIR/calls" 2>/dev/null
}

# Recovery guidance must fit the failure: installing the distribution's default python3 fixes a missing
# interpreter, but on a release whose default is older than 3.10 it changes nothing, so that case says how
# to provide a newer interpreter where both ./bioaf and the update agent's service will find it.

@test "a missing python3 is answered with the distribution package to install" {
    host_stubs
    with_python none bash "$BIOAF_ROOT/install.sh" check-prereqs
    [[ "$output" == *"sudo apt-get install -y python3"* ]]
    [[ "$output" == *"sudo dnf install -y python3"* ]]
}

@test "an outdated python3 is answered with how to provide a newer one to commands and the agent service" {
    host_stubs
    with_python old bash "$BIOAF_ROOT/install.sh" check-prereqs
    [[ "$output" == *"too old"* ]]
    [[ "$output" != *"apt-get install -y python3"$'\n'* ]]
    [[ "$output" == *"python3.11"* ]]
    [[ "$output" == *"/usr/local/bin/python3"* ]]
    [[ "$output" == *"bioaf-update-agent"* ]]
    [[ "$output" == *"python3 --version"* ]]
}

@test "a python3 that cannot run or lacks its standard library is answered with a full interpreter" {
    host_stubs
    for kind in broken nocap; do
        with_python "$kind" bash "$BIOAF_ROOT/install.sh" check-prereqs
        [[ "$output" == *"/usr/local/bin/python3"* ]]
        [[ "$output" == *"bioaf-update-agent"* ]]
    done
}
