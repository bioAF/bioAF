#!/usr/bin/env bash
# bioAF Installer
# Idempotent: safe to run multiple times. Preserves existing secrets and certs.
#
# Usage:
#   ./install.sh              Interactive install (prompts for values)
#   ./install.sh --help       Show usage
#   ./install.sh check-prereqs   Check prerequisites only
#   ./install.sh generate-env    Generate docker/.env (preserves existing values)
#   ./install.sh generate-certs  Generate self-signed TLS certificate

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/docker/.env"
CERTS_DIR="$SCRIPT_DIR/docker/certs"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
red()    { printf '\033[0;31m%s\033[0m\n' "$*"; }
green()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[0;33m%s\033[0m\n' "$*"; }
bold()   { printf '\033[1m%s\033[0m\n' "$*"; }

# ---------------------------------------------------------------------------
# OS check -- bioAF requires a Linux host (typically a GCP VM)
# ---------------------------------------------------------------------------
check_os() {
    local os
    os="$(uname -s)"
    case "$os" in
        Darwin)
            echo ""
            red "bioAF cannot be installed on macOS."
            echo ""
            echo "bioAF requires a Linux server, typically a VM in Google Cloud."
            echo "See the installation guide for step-by-step instructions:"
            echo ""
            bold "  https://bioaf.co/docs/installation/gcp-setup/"
            echo ""
            exit 1
            ;;
        MINGW*|MSYS*|CYGWIN*|Windows_NT)
            echo ""
            red "bioAF cannot be installed on Windows."
            echo ""
            echo "bioAF requires a Linux server, typically a VM in Google Cloud."
            echo "See the installation guide for step-by-step instructions:"
            echo ""
            bold "  https://bioaf.co/docs/installation/gcp-setup/"
            echo ""
            exit 1
            ;;
    esac
}

usage() {
    bold "bioAF Installer"
    echo ""
    echo "Usage: ./install.sh [command] [options]"
    echo ""
    bold "Commands:"
    echo "  (none)            Run full interactive install"
    echo "  check-prereqs     Check that required tools are installed"
    echo "  generate-env      Generate docker/.env (preserves existing values)"
    echo "  generate-certs    Generate self-signed TLS certificate"
    echo "  prepare-letsencrypt  Install certbot + create /var/www/letsencrypt (requires sudo)"
    echo "  --help, -h        Show this usage information"
    echo ""
    bold "Options:"
    echo "  --non-interactive   Skip prompts, use generated defaults"
    echo "  --force             Regenerate secrets (WARNING: breaks existing DB)"
    echo ""
}

# The host python3 check (installer/host_prereqs.sh), loaded when a command needs it.
host_python_require() {
    # shellcheck source=installer/host_prereqs.sh
    if ! source "$SCRIPT_DIR/installer/host_prereqs.sh" 2>/dev/null; then
        red "ERROR: installer/host_prereqs.sh is missing from $SCRIPT_DIR; this checkout is incomplete." >&2
        return 1
    fi
    host_python_check
}

# Read a value from the existing .env file, or return empty string.
read_env_value() {
    local key="$1"
    if [ -f "$ENV_FILE" ]; then
        grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d'=' -f2- || true
    fi
}

# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------
check_prereqs() {
    check_os

    local missing=0

    bold "Checking prerequisites..."
    echo ""

    # Docker
    if command -v docker &>/dev/null; then
        green "  docker ......... $(docker --version 2>/dev/null | head -1)"
    else
        red "  docker ......... NOT FOUND"
        missing=1
    fi

    # Docker Compose (v2 plugin)
    if docker compose version &>/dev/null 2>&1; then
        green "  docker compose . $(docker compose version 2>/dev/null | head -1)"
    else
        red "  docker compose . NOT FOUND"
        missing=1
    fi

    # Git
    if command -v git &>/dev/null; then
        green "  git ............ $(git --version 2>/dev/null)"
    else
        red "  git ............ NOT FOUND"
        missing=1
    fi

    # openssl (for secret and cert generation)
    if command -v openssl &>/dev/null; then
        green "  openssl ........ $(openssl version 2>/dev/null | head -1)"
    else
        red "  openssl ........ NOT FOUND"
        missing=1
    fi

    # python3 on the host: ./bioaf and the update agent run it (not the backend container's).
    if host_python_require; then
        green "  python3 ........ Python $HOST_PYTHON_VERSION"
    else
        red "  python3 ........ NOT USABLE (see above)"
        missing=1
    fi

    echo ""

    if [ "$missing" -ne 0 ]; then
        red "Some prerequisites are missing. Please install them before continuing."
        return 1
    fi

    # Check Docker socket permissions (catches the "usermod but no re-login" case)
    if ! docker info &>/dev/null; then
        echo ""
        red "Docker is installed but your user cannot connect to it."
        echo "Add yourself to the docker group, then try again:"
        bold "  sudo usermod -aG docker \$USER && newgrp docker"
        echo ""
        return 1
    fi

    green "All prerequisites satisfied."
    return 0
}

# ---------------------------------------------------------------------------
# TLS certificate generation
# ---------------------------------------------------------------------------
generate_certs() {
    local force=false
    for arg in "$@"; do
        case "$arg" in
            --force) force=true ;;
        esac
    done

    mkdir -p "$CERTS_DIR"

    # Ensure the current user can write to the certs directory
    if [ ! -w "$CERTS_DIR" ]; then
        red "ERROR: Cannot write to $CERTS_DIR (owned by $(stat -c '%U' "$CERTS_DIR" 2>/dev/null || stat -f '%Su' "$CERTS_DIR"))."
        red "Fix with: sudo chown -R \$(whoami) $CERTS_DIR"
        return 1
    fi

    if [ -f "$CERTS_DIR/tls.crt" ] && [ -f "$CERTS_DIR/tls.key" ] && [ "$force" = false ]; then
        green "TLS certificates already exist at docker/certs/. Skipping."
        return 0
    fi

    bold "Generating self-signed TLS certificate..."

    # Try with -addext first (OpenSSL 1.1.1+), fall back without it for
    # older versions that do not support the flag.
    if ! openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout "$CERTS_DIR/tls.key" \
        -out "$CERTS_DIR/tls.crt" \
        -days 365 \
        -subj "/CN=bioaf-local" \
        -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
        2>&1; then

        yellow "Retrying without -addext (older OpenSSL)..."
        openssl req -x509 -newkey rsa:2048 -nodes \
            -keyout "$CERTS_DIR/tls.key" \
            -out "$CERTS_DIR/tls.crt" \
            -days 365 \
            -subj "/CN=bioaf-local" \
            2>&1
    fi

    # Verify the files were actually created
    if [ ! -f "$CERTS_DIR/tls.crt" ] || [ ! -f "$CERTS_DIR/tls.key" ]; then
        red "ERROR: Certificate generation failed. Check openssl output above."
        return 1
    fi

    chmod 600 "$CERTS_DIR/tls.key"
    chmod 644 "$CERTS_DIR/tls.crt"

    green "Self-signed certificate generated at docker/certs/"
    yellow "Browsers will show a security warning for self-signed certificates."
    yellow "For trusted certificates, replace with Let's Encrypt or your CA's cert."
    return 0
}

# ---------------------------------------------------------------------------
# .env generation (idempotent -- preserves existing values)
# ---------------------------------------------------------------------------
generate_env() {
    local non_interactive=false
    local force=false

    for arg in "$@"; do
        case "$arg" in
            --non-interactive) non_interactive=true ;;
            --force)           force=true ;;
        esac
    done

    # Replacing secrets ends with a python3-backed check for the old database volume; check python3
    # first, so a host without it keeps its secrets instead of losing that warning.
    if [ "$force" = true ]; then
        host_python_require || return 1
    fi

    mkdir -p "$(dirname "$ENV_FILE")"

    # Read existing values (empty string if not set)
    local existing_pg_user existing_pg_password existing_pg_db
    local existing_secret_key existing_environment existing_encryption_keys
    existing_pg_user=$(read_env_value "POSTGRES_USER")
    existing_pg_password=$(read_env_value "POSTGRES_PASSWORD")
    existing_pg_db=$(read_env_value "POSTGRES_DB")
    existing_secret_key=$(read_env_value "SECRET_KEY")
    existing_environment=$(read_env_value "BIOAF_ENVIRONMENT")
    existing_encryption_keys=$(read_env_value "BIOAF_ENCRYPTION_KEYS")

    # Determine values: keep existing unless --force or missing
    local pg_user pg_password pg_db secret_key environment encryption_keys

    # Fernet key = urlsafe-base64(32 random bytes). Same primitive as
    # cryptography.Fernet.generate_key(), implemented with openssl so the
    # installer needs no third-party Python package (python3-cryptography or
    # any other). The host python3 itself is a prerequisite (check_prereqs,
    # installer/host_prereqs.sh): ./bioaf and the update agent use it, and
    # --force uses it for the stale-volume warning below.
    local fernet_gen='openssl rand -base64 32 | tr "+/" "-_" | tr -d "\n"'

    if [ "$force" = true ]; then
        pg_password=$(openssl rand -hex 16)
        secret_key=$(openssl rand -hex 32)
        encryption_keys=$(eval "$fernet_gen")
        yellow "Regenerated secrets. If the database volume still has the old"
        yellow "password, you must remove it before starting."
        yellow "BIOAF_ENCRYPTION_KEYS was also regenerated. Encrypted columns"
        yellow "in the existing DB will no longer be readable. Restore from a"
        yellow "backup that matches the new key, or wipe the volume."
    else
        pg_password="${existing_pg_password:-$(openssl rand -hex 16)}"
        secret_key="${existing_secret_key:-$(openssl rand -hex 32)}"
        encryption_keys="${existing_encryption_keys:-$(eval "$fernet_gen")}"
    fi

    environment="${existing_environment:-production}"

    if [ "$non_interactive" = true ]; then
        pg_user="${existing_pg_user:-bioaf}"
        pg_db="${existing_pg_db:-bioaf}"
    else
        if [ -n "$existing_pg_user" ] && [ "$force" = false ]; then
            # Existing config, not forcing -- preserve silently
            pg_user="$existing_pg_user"
            pg_db="${existing_pg_db:-bioaf}"
        else
            echo ""
            bold "Generating environment configuration..."
            echo ""
            echo "Press Enter to accept defaults."
            echo ""

            read -rp "PostgreSQL user [${existing_pg_user:-bioaf}]: " pg_user
            pg_user=${pg_user:-${existing_pg_user:-bioaf}}

            read -rp "PostgreSQL database [${existing_pg_db:-bioaf}]: " pg_db
            pg_db=${pg_db:-${existing_pg_db:-bioaf}}
        fi
    fi

    cat > "$ENV_FILE" <<ENVEOF
# Generated by bioAF installer on $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# PostgreSQL
POSTGRES_USER=$pg_user
POSTGRES_PASSWORD=$pg_password
POSTGRES_DB=$pg_db

# Backend
DATABASE_URL=postgresql+asyncpg://${pg_user}:${pg_password}@db:5432/${pg_db}
SECRET_KEY=$secret_key

# Data-at-rest encryption (Fernet). Comma-separated keys; the first key is
# the primary writer, all keys are accepted readers (so rotation can prepend
# a new key while the old one still reads legacy rows).
# LOSING THIS VALUE LOSES THE DATA -- back it up alongside the DB dump.
BIOAF_ENCRYPTION_KEYS=$encryption_keys

# Environment
BIOAF_ENVIRONMENT=$environment
ENVEOF

    green "Environment file written to docker/.env"

    # Warn about stale database volume when regenerating secrets
    if [ "$force" = true ] && command -v docker &>/dev/null; then
        local compose_file="$SCRIPT_DIR/docker/docker-compose.yml"
        if [ -f "$compose_file" ]; then
            local dc="docker compose -f $compose_file --env-file $ENV_FILE"
            local vol_name
            vol_name=$($dc config --format json 2>/dev/null | python3 -c "
import sys, json
cfg = json.load(sys.stdin)
for v in cfg.get('volumes', {}).values():
    print(v.get('name', ''))
    break
" 2>/dev/null || echo "")

            if [ -n "$vol_name" ] && docker volume inspect "$vol_name" > /dev/null 2>&1; then
                echo ""
                yellow "WARNING: Database volume '$vol_name' exists from a previous install."
                yellow "The old volume has a different password baked in."
                yellow "Run './bioaf stop && docker volume rm $vol_name' before starting,"
                yellow "or use './bioaf setup' which handles this automatically."
            fi
        fi
    fi

    return 0
}

# ---------------------------------------------------------------------------
# Upgrade path: ensure BIOAF_ENCRYPTION_KEYS exists in docker/.env.
#
# Installs that predate v0.13.0 have an existing docker/.env with no
# BIOAF_ENCRYPTION_KEYS line. After upgrading the bioAF code, the backend's
# startup check fails fast on the missing variable. Rather than rewrite the
# operator's whole .env (which would risk losing custom lines), this helper
# appends exactly one line if the variable is missing or empty. Idempotent.
# ---------------------------------------------------------------------------
prepare_host_log_collection() {
    # Every ./bioaf update since v0.13.1 runs this release's `install.sh ensure-encryption-key`
    # after checking out the release and before restarting the services, and stops the update if
    # it fails. It is the one point where an older updater runs this release's code before the new
    # backend starts, so it is where an older updater's update installs this release's Ops Agent
    # parser (plan 22): the backend's JSON stdout must not become the centralized route before the
    # agent can parse it. An updater that reconciles the agent itself sets
    # BIOAF_HOST_LOG_COLLECTION=caller. Off GCE, and in direct delivery, ./bioaf does nothing.
    [ "${BIOAF_HOST_LOG_COLLECTION:-}" = caller ] && return 0
    [ -f "$SCRIPT_DIR/bioaf" ] || return 0
    bash "$SCRIPT_DIR/bioaf" ensure-cloud-logging
}

prepare_update_agent() {
    # The same hook point delivers this release's update-agent unit and agent code (plan 23), for any
    # updater: ./bioaf reconciles the unit and restarts the agent, deferring the restart until the
    # agent is idle when the agent itself is running this update. The services do not depend on the
    # agent, so a failure is reported and the update continues.
    [ -f "$SCRIPT_DIR/bioaf" ] || return 0
    if ! bash "$SCRIPT_DIR/bioaf" ensure-update-agent; then
        yellow "The update agent could not be brought to this release; the update continues."
        yellow "Run ./bioaf start on the host to see why and retry."
    fi
    return 0
}

# ---------------------------------------------------------------------------
# One-time updater preparation (plan 03A first-update gate).
#
# Every supported updater since v0.13.0 checks out the target release, records step
# ensure_encryption_key in update-status/current.json, and runs this release's
# `install.sh ensure-encryption-key` before it pins or restarts anything. An older updater cannot
# check whether Work Nodes launched by the running backend would lose their in-memory sync keys, so
# it must not replace that backend. When the caller has not declared the gate-aware contract
# (BIOAF_UPDATE_GATE=caller, set by this release's ./bioaf update), this hook prepares only the host
# update agent, records the host-owned outcome in host-state/, and refuses before the environment
# file, image pin or containers change. The operator retries; the retry runs the new updater.
# ---------------------------------------------------------------------------
PREPARED_UPDATE_MESSAGE="The update helper is ready. bioAF and your work nodes are still running. Retry the update to continue."

older_updater_in_progress() {
    # True when an update that has not declared the gate-aware contract is running this hook.
    [ "${BIOAF_UPDATE_GATE:-}" = caller ] && return 1
    [ -f "$SCRIPT_DIR/bioaf" ] && [ -f "$SCRIPT_DIR/scripts/update_host_io.py" ] || return 1
    local fields status step
    fields=$(python3 -I "$SCRIPT_DIR/scripts/update_host_io.py" read-status-fields "$SCRIPT_DIR" 2>/dev/null) || return 1
    IFS=$'\t' read -r status step _ _ <<< "$fields"
    [ "$status" = in_progress ] && [ "$step" = ensure_encryption_key ]
}

prepare_for_gated_update() {
    # Prepare only the host update agent, record the outcome, then refuse. Never returns success.
    local fields from target host_io="$SCRIPT_DIR/scripts/update_host_io.py"
    fields=$(python3 -I "$host_io" read-status-fields "$SCRIPT_DIR" 2>/dev/null) || fields=""
    IFS=$'\t' read -r _ _ from target <<< "$fields"
    [ "$from" = - ] && from=""
    [ "$target" = - ] && target=""
    yellow "This release checks running work nodes before it replaces bioAF. Preparing the update helper first;"
    yellow "nothing else is changed on this pass."
    if bash "$SCRIPT_DIR/bioaf" ensure-update-agent; then
        if [ -n "$from" ] && [ -n "$target" ]; then
            python3 -I "$host_io" record-preparation "$SCRIPT_DIR" "$target" "$from" prepared \
                || yellow "The preparation could not be recorded; the update page may show the older updater's message."
        fi
        green "$PREPARED_UPDATE_MESSAGE"
        exit 3
    fi
    if [ -n "$from" ] && [ -n "$target" ]; then
        python3 -I "$host_io" record-preparation "$SCRIPT_DIR" "$target" "$from" failed || true
    fi
    red "The update helper could not be prepared. bioAF and your work nodes are still running unchanged."
    red "Run ./bioaf start on the host to see why, then retry the update."
    exit 1
}

ensure_encryption_key_in_env() {
    if [ ! -f "$ENV_FILE" ]; then
        # Nothing to amend; generate-env hasn't been run. Not an error.
        return 0
    fi

    local existing
    existing=$(read_env_value "BIOAF_ENCRYPTION_KEYS")
    if [ -n "$existing" ]; then
        return 0
    fi

    if ! command -v openssl >/dev/null 2>&1; then
        red "openssl is required to generate a Fernet key; install openssl and re-run."
        return 1
    fi

    # Same urlsafe-base64(32 random bytes) primitive as Fernet.generate_key().
    local new_key
    new_key=$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')
    if [ -z "$new_key" ] || [ "${#new_key}" -ne 44 ]; then
        red "Failed to generate a Fernet key from openssl rand."
        return 1
    fi

    # If there's an empty BIOAF_ENCRYPTION_KEYS= line, replace it in place.
    # Otherwise append. Using a portable sed invocation (-i.bak then rm) so
    # this works on both GNU sed (Linux) and BSD sed (macOS).
    if grep -qE "^BIOAF_ENCRYPTION_KEYS=" "$ENV_FILE"; then
        sed -i.bak "s|^BIOAF_ENCRYPTION_KEYS=.*$|BIOAF_ENCRYPTION_KEYS=${new_key}|" "$ENV_FILE"
        rm -f "${ENV_FILE}.bak"
    else
        # Ensure the file ends with a newline before appending.
        if [ -s "$ENV_FILE" ] && [ "$(tail -c 1 "$ENV_FILE" | wc -l | tr -d ' ')" = "0" ]; then
            printf '\n' >> "$ENV_FILE"
        fi
        cat >> "$ENV_FILE" <<APPEND_EOF

# Data-at-rest encryption (added by an upgrade; see ADR-047).
# LOSING THIS VALUE LOSES THE DATA -- back it up alongside the DB dump.
BIOAF_ENCRYPTION_KEYS=${new_key}
APPEND_EOF
    fi

    yellow ""
    yellow "Generated BIOAF_ENCRYPTION_KEYS and added it to $ENV_FILE."
    yellow "This key encrypts sensitive DB columns (Slack/SMTP secrets, SSH keys,"
    yellow "GCP SA keys, heartbeat tokens, etc.). Losing it makes those columns"
    yellow "unrecoverable. Back it up SEPARATELY from your DB backup."
    yellow ""
    return 0
}

# ---------------------------------------------------------------------------
# Let's Encrypt prerequisites (certbot + webroot dir)
# ---------------------------------------------------------------------------
# Idempotent: skips work if both certbot and the webroot are already present.
# The webroot dir is mounted into the nginx container by docker-compose so
# nginx can serve /.well-known/acme-challenge/ over HTTP. install-gcp.sh's
# VM startup script runs the same steps; this function exists for operators
# who installed bioAF on an existing Linux server.
prepare_letsencrypt() {
    local missing_certbot=false
    local missing_webroot=false

    if ! command -v certbot &>/dev/null; then
        missing_certbot=true
    fi

    if [ ! -d "/var/www/letsencrypt" ]; then
        missing_webroot=true
    fi

    if [ "$missing_certbot" = false ] && [ "$missing_webroot" = false ]; then
        green "Let's Encrypt prerequisites already in place (certbot + /var/www/letsencrypt)."
        return 0
    fi

    bold "Preparing Let's Encrypt prerequisites..."

    if [ "$missing_certbot" = true ]; then
        if ! command -v apt-get &>/dev/null; then
            yellow "  certbot is missing and this is not a Debian/Ubuntu host."
            yellow "  Install certbot for your distribution before running the cert flow."
        else
            echo "  Installing certbot via apt-get (requires sudo)..."
            if ! sudo apt-get update -qq || ! sudo apt-get install -y -qq certbot; then
                red "  Failed to install certbot. Install it manually, then re-run."
                return 1
            fi
            green "  certbot installed."
        fi
    fi

    if [ "$missing_webroot" = true ]; then
        echo "  Creating /var/www/letsencrypt (requires sudo)..."
        if ! sudo mkdir -p /var/www/letsencrypt || ! sudo chmod 755 /var/www /var/www/letsencrypt; then
            red "  Failed to create /var/www/letsencrypt. Create it manually, then re-run."
            return 1
        fi
        green "  Webroot /var/www/letsencrypt created."
    fi

    return 0
}

# ---------------------------------------------------------------------------
# Full install
# ---------------------------------------------------------------------------
full_install() {
    local non_interactive=false
    local force=false
    local quiet=false

    for arg in "$@"; do
        case "$arg" in
            --non-interactive) non_interactive=true ;;
            --force)           force=true ;;
            --quiet)           quiet=true ;;
        esac
    done

    bold "=== bioAF Installer ==="
    echo ""

    # Step 1: Prerequisites (includes OS check)
    check_prereqs || exit 1
    echo ""

    # Step 2: Generate .env (idempotent)
    local env_args=()
    [ "$non_interactive" = true ] && env_args+=(--non-interactive)
    [ "$force" = true ] && env_args+=(--force)
    generate_env "${env_args[@]}" || exit 1
    echo ""

    # Step 3: Generate TLS certs (idempotent)
    generate_certs || exit 1
    echo ""

    # Step 4: Prepare Let's Encrypt prerequisites on the host so the
    # Settings -> Networking cert flow works without manual setup later.
    # Best-effort: failures here do not abort the install.
    prepare_letsencrypt || yellow "Skipping Let's Encrypt prep; you can run './install.sh prepare-letsencrypt' later."
    echo ""

    # Step 5: Show next steps (skip when called from ./bioaf setup)
    if [ "$quiet" = false ]; then
        bold "=== Installation Complete ==="
        echo ""
        echo "Next steps:"
        echo "  1. Review docker/.env and adjust if needed"
        echo "  2. Run './bioaf setup' to build, migrate, and create your admin account"
        echo ""
        echo "Or run individual commands:"
        echo "  ./bioaf build      Build container images"
        echo "  ./bioaf start      Start all services"
        echo "  ./bioaf migrate    Run database migrations"
        echo "  ./bioaf status     Check service status"
        echo "  ./bioaf help       Show all commands"
        echo ""
    fi
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
command="${1:-}"

case "$command" in
    check-prereqs)
        shift
        check_prereqs
        ;;
    generate-env)
        shift
        generate_env "$@"
        ;;
    generate-certs)
        shift
        generate_certs "$@"
        ;;
    prepare-letsencrypt)
        shift
        prepare_letsencrypt "$@"
        ;;
    ensure-encryption-key)
        shift
        # The ./bioaf host hooks below run python3; check it before the env file changes.
        if [ -f "$SCRIPT_DIR/bioaf" ]; then
            host_python_require || exit 1
        fi
        # An older, ungated updater gets the one-time preparation pass and stops here, before the
        # environment file changes (see prepare_for_gated_update).
        if older_updater_in_progress; then
            prepare_for_gated_update
        fi
        ensure_encryption_key_in_env "$@"
        prepare_host_log_collection
        prepare_update_agent
        ;;
    --help|-h)
        usage
        ;;
    "")
        shift 2>/dev/null || true
        full_install "$@"
        ;;
    *)
        # Pass-through flags for full install (e.g., --non-interactive)
        full_install "$@"
        ;;
esac
