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
    echo "  --help, -h        Show this usage information"
    echo ""
    bold "Options:"
    echo "  --non-interactive   Skip prompts, use generated defaults"
    echo "  --force             Regenerate secrets (WARNING: breaks existing DB)"
    echo ""
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

    echo ""

    if [ "$missing" -ne 0 ]; then
        red "Some prerequisites are missing. Please install them before continuing."
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

    mkdir -p "$(dirname "$ENV_FILE")"

    # Read existing values (empty string if not set)
    local existing_pg_user existing_pg_password existing_pg_db
    local existing_secret_key existing_environment
    existing_pg_user=$(read_env_value "POSTGRES_USER")
    existing_pg_password=$(read_env_value "POSTGRES_PASSWORD")
    existing_pg_db=$(read_env_value "POSTGRES_DB")
    existing_secret_key=$(read_env_value "SECRET_KEY")
    existing_environment=$(read_env_value "BIOAF_ENVIRONMENT")

    # Determine values: keep existing unless --force or missing
    local pg_user pg_password pg_db secret_key environment

    if [ "$force" = true ]; then
        pg_password=$(openssl rand -hex 16)
        secret_key=$(openssl rand -hex 32)
        yellow "Regenerated secrets. If the database volume still has the old"
        yellow "password, you must remove it before starting."
    else
        pg_password="${existing_pg_password:-$(openssl rand -hex 16)}"
        secret_key="${existing_secret_key:-$(openssl rand -hex 32)}"
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

    # Step 4: Show next steps (skip when called from ./bioaf setup)
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
