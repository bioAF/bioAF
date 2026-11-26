#!/usr/bin/env bash
# HTTPS certificates for bioAF's nginx (plan 03.5A; owner decision 2026-11-25).
#
# - Public IP-only installations: a Let's Encrypt IP-address certificate (the "shortlived" profile,
#   about six days), requested and renewed by a pinned certbot container over the HTTP-01 webroot
#   nginx already serves on port 80.
# - Private installations: a certificate from the organization's own CA, uploaded in Settings and
#   installed here by the update agent's fixed host operation.
#
# Every certificate is installed the same way: the pair is validated (readable, key matches, not
# expiring within the hour), staged beside nginx's files and renamed into place, then nginx -t; if
# nginx refuses it the previous pair is restored and nothing is reloaded. One certificate covers the
# application (443) and the notebook gateway (8443).
#
# Sourced by ./bioaf. Callers may override the TLS_* paths (tests do).

TLS_CERTBOT_IMAGE="${TLS_CERTBOT_IMAGE:-certbot/certbot:v5.6.0@sha256:0107d084c225631fc64a8313e19adb07275f7296fde338f7dfa93986c80b2e3e}"
_TLS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TLS_CERTS_DIR="${TLS_CERTS_DIR:-${SSL_CERT_DIR:-$_TLS_ROOT/docker/certs}}"
TLS_STATE_DIR="${TLS_STATE_DIR:-$_TLS_ROOT/host-state/tls}"
TLS_WEBROOT="${TLS_WEBROOT:-${LETSENCRYPT_WEBROOT:-/var/www/letsencrypt}}"
TLS_UPLOAD_DIR="${TLS_UPLOAD_DIR:-$_TLS_ROOT/tls-upload}"
# Host-written, backend-readable (read-only mount): what Settings shows for the installed certificate.
TLS_REPORT_DIR="${TLS_REPORT_DIR:-$_TLS_ROOT/tls-report}"
TLS_COMPOSE="${TLS_COMPOSE:-docker compose -f $_TLS_ROOT/docker/docker-compose.yml --env-file $_TLS_ROOT/docker/.env}"
TLS_CERT_NAME="bioaf"

_tls_err() { printf '%s\n' "$*" >&2; }

_tls_public_ipv4() {
    # A dotted-quad public IPv4 address: publicly trusted CAs do not issue for private or reserved ones.
    local ip="$1" a b c d
    [[ "$ip" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
    a=${BASH_REMATCH[1]}; b=${BASH_REMATCH[2]}; c=${BASH_REMATCH[3]}; d=${BASH_REMATCH[4]}
    for o in "$a" "$b" "$c" "$d"; do [ "$o" -le 255 ] || return 1; done
    [ "$a" -eq 10 ] && return 1
    [ "$a" -eq 127 ] && return 1
    [ "$a" -eq 0 ] && return 1
    [ "$a" -eq 169 ] && [ "$b" -eq 254 ] && return 1
    [ "$a" -eq 172 ] && [ "$b" -ge 16 ] && [ "$b" -le 31 ] && return 1
    [ "$a" -eq 192 ] && [ "$b" -eq 168 ] && return 1
    [ "$a" -eq 100 ] && [ "$b" -ge 64 ] && [ "$b" -le 127 ] && return 1
    [ "$a" -ge 224 ] && return 1
    return 0
}

tls_pair_ok() {
    # tls_pair_ok <cert> <key>: readable regular files, the key matches, valid for at least an hour.
    local cert="$1" key="$2" cert_pub key_pub
    for f in "$cert" "$key"; do
        if [ -L "$f" ] || [ ! -f "$f" ]; then _tls_err "The certificate files must be regular files."; return 1; fi
    done
    if ! openssl x509 -in "$cert" -noout >/dev/null 2>&1; then
        _tls_err "The certificate could not be read."; return 1
    fi
    if ! openssl x509 -in "$cert" -noout -checkend 3600 >/dev/null 2>&1; then
        _tls_err "The certificate has expired or expires within the hour."; return 1
    fi
    cert_pub="$(openssl x509 -in "$cert" -noout -pubkey 2>/dev/null)" || return 1
    key_pub="$(openssl pkey -in "$key" -pubout 2>/dev/null)" || { _tls_err "The private key could not be read."; return 1; }
    if [ "$cert_pub" != "$key_pub" ]; then
        _tls_err "The private key does not match the certificate."; return 1
    fi
}

tls_install_pair() {
    # tls_install_pair <cert> <key>: validate, swap atomically, nginx -t, reload; restore on refusal.
    local cert="$1" key="$2" dir="$TLS_CERTS_DIR"
    tls_pair_ok "$cert" "$key" || return 1
    [ -d "$dir" ] && [ -w "$dir" ] || { _tls_err "Cannot write to $dir."; return 1; }
    local had_previous=false
    if [ -f "$dir/tls.crt" ] && [ -f "$dir/tls.key" ]; then
        cp -p "$dir/tls.crt" "$dir/.tls.crt.prev" && (umask 077 && cp "$dir/tls.key" "$dir/.tls.key.prev") || return 1
        had_previous=true
    fi
    (umask 077 && cp "$key" "$dir/.tls.key.new") || return 1
    cp "$cert" "$dir/.tls.crt.new" || { rm -f "$dir/.tls.key.new"; return 1; }
    chmod 600 "$dir/.tls.key.new"; chmod 644 "$dir/.tls.crt.new"
    mv -f "$dir/.tls.key.new" "$dir/tls.key" && mv -f "$dir/.tls.crt.new" "$dir/tls.crt" || return 1
    if ! $TLS_COMPOSE exec -T nginx nginx -t >/dev/null 2>&1; then
        _tls_err "nginx refused the new certificate; the previous one was kept."
        if [ "$had_previous" = true ]; then
            mv -f "$dir/.tls.key.prev" "$dir/tls.key"; mv -f "$dir/.tls.crt.prev" "$dir/tls.crt"
        fi
        return 1
    fi
    rm -f "$dir/.tls.key.prev" "$dir/.tls.crt.prev"
    if ! $TLS_COMPOSE exec -T nginx nginx -s reload >/dev/null 2>&1; then
        _tls_err "The certificate was installed but nginx did not reload; it will be used at the next restart."
        return 1
    fi
}

# Renewal ownership: the SHA-256 of the certificate bioAF's certbot last installed, in host-owned
# state. Renewal runs only while that is still the installed certificate, so an uploaded or otherwise
# installed certificate is never replaced, and certbot's own state (root-only, as its container
# writes it) never has to be readable from the host.
_tls_marker() { printf '%s' "$TLS_STATE_DIR/acme-installed"; }

_tls_digest() { openssl dgst -sha256 < "$1" 2>/dev/null | awk '{print $NF}'; }

_tls_record_acme_install() {
    # After an install attempt: record ownership if the staged certbot certificate is now in place.
    cmp -s "$TLS_STATE_DIR/stage/tls.crt" "$TLS_CERTS_DIR/tls.crt" || return 0
    local marker; marker="$(_tls_marker)"
    (umask 077 && _tls_digest "$TLS_CERTS_DIR/tls.crt" > "$marker.new") && mv -f "$marker.new" "$marker"
}

_tls_acme_owns_installed() {
    local marker; marker="$(_tls_marker)"
    [ -f "$marker" ] && [ -f "$TLS_CERTS_DIR/tls.crt" ] || return 1
    [ "$(cat "$marker")" = "$(_tls_digest "$TLS_CERTS_DIR/tls.crt")" ]
}

_tls_json() { printf '%s' "$1" | tr -d '\000-\037' | sed 's/\\/\\\\/g; s/"/\\"/g'; }

_tls_cert_field() {
    # _tls_cert_field <enddate|subject|issuer>: the installed certificate's field, without its label.
    openssl x509 -in "$TLS_CERTS_DIR/tls.crt" -noout "-$1" 2>/dev/null | sed -E 's/^[A-Za-z]+= *//'
}

_tls_write_report() {
    # _tls_write_report <succeeded|failed|none>: the installed certificate and the last renewal attempt
    # ("none": bioAF does not renew it). Replaced atomically; never fails the operation that called it.
    local result="$1" dir="$TLS_REPORT_DIR" certificate=null renewal=null owned=false
    [ -d "$dir" ] || mkdir -m 755 "$dir" 2>/dev/null
    [ -d "$dir" ] && [ -w "$dir" ] || return 0
    if [ -f "$TLS_CERTS_DIR/tls.crt" ]; then
        certificate="{\"not_after\": \"$(_tls_json "$(_tls_cert_field enddate)")\", \"subject\": \"$(_tls_json "$(_tls_cert_field subject)")\", \"issuer\": \"$(_tls_json "$(_tls_cert_field issuer)")\"}"
    fi
    _tls_acme_owns_installed && owned=true
    if [ "$result" != none ]; then
        renewal="{\"at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\", \"result\": \"$result\"}"
    fi
    if (umask 022 && printf '{"certificate": %s, "renewed_by_bioaf": %s, "last_renewal": %s}\n' \
        "$certificate" "$owned" "$renewal" > "$dir/.status.json.new"); then
        mv -f "$dir/.status.json.new" "$dir/status.json"
    else
        rm -f "$dir/.status.json.new"
    fi
    return 0
}

_tls_certbot() {
    # _tls_certbot <certbot args...>: the pinned certbot with bioAF's own state directory.
    mkdir -p "$TLS_STATE_DIR/letsencrypt"
    docker run --rm \
        -v "$TLS_STATE_DIR/letsencrypt:/le" \
        -v "$TLS_WEBROOT:/var/www/letsencrypt" \
        "$TLS_CERTBOT_IMAGE" "$@" \
        --config-dir /le/config --work-dir /le/work --logs-dir /le/logs --non-interactive
}

_tls_copy_out() {
    # The issued pair, copied out of certbot's root-owned state into a private staging directory.
    local stage="$TLS_STATE_DIR/stage"
    rm -rf "$stage"; (umask 077 && mkdir -p "$stage")
    docker run --rm --entrypoint sh \
        -v "$TLS_STATE_DIR/letsencrypt:/le:ro" -v "$stage:/stage" "$TLS_CERTBOT_IMAGE" -c \
        "cp -L /le/config/live/$TLS_CERT_NAME/fullchain.pem /stage/tls.crt && cp -L /le/config/live/$TLS_CERT_NAME/privkey.pem /stage/tls.key && chown $(id -u):$(id -g) /stage/tls.crt /stage/tls.key && chmod 600 /stage/tls.key"
}

tls_issue_ip() {
    # tls_issue_ip <public-ipv4> [acme-directory-url]: request and install a Let's Encrypt IP certificate.
    local ip="$1" server="${2:-}"
    if ! _tls_public_ipv4 "$ip"; then
        _tls_err "A public IPv4 address is required for a Let's Encrypt IP certificate."
        return 1
    fi
    local extra=()
    [ -n "$server" ] && extra=(--server "$server")
    if ! _tls_certbot certonly --webroot -w /var/www/letsencrypt --preferred-profile shortlived \
        --ip-address "$ip" --cert-name "$TLS_CERT_NAME" --agree-tos --register-unsafely-without-email \
        "${extra[@]}"; then
        _tls_err "The certificate request failed; the current certificate is unchanged."
        _tls_write_report failed
        return 1
    fi
    _tls_copy_out || { _tls_err "The issued certificate could not be read."; _tls_write_report failed; return 1; }
    tls_install_pair "$TLS_STATE_DIR/stage/tls.crt" "$TLS_STATE_DIR/stage/tls.key"
    local rc=$?
    _tls_record_acme_install
    rm -rf "$TLS_STATE_DIR/stage"
    if [ "$rc" -eq 0 ]; then _tls_write_report succeeded; else _tls_write_report failed; fi
    return $rc
}

tls_renew() {
    # Renew a bioAF-issued certificate when certbot judges it due; install only a changed one.
    _tls_acme_owns_installed || return 0
    if ! _tls_certbot renew --cert-name "$TLS_CERT_NAME"; then
        _tls_err "Certificate renewal failed; the current certificate is unchanged."
        _tls_write_report failed
        return 1
    fi
    _tls_copy_out || { _tls_err "The renewed certificate could not be read."; _tls_write_report failed; return 1; }
    local rc=0
    if ! cmp -s "$TLS_STATE_DIR/stage/tls.crt" "$TLS_CERTS_DIR/tls.crt"; then
        tls_install_pair "$TLS_STATE_DIR/stage/tls.crt" "$TLS_STATE_DIR/stage/tls.key" || rc=$?
        _tls_record_acme_install
    fi
    rm -rf "$TLS_STATE_DIR/stage"
    if [ "$rc" -eq 0 ]; then _tls_write_report succeeded; else _tls_write_report failed; fi
    return $rc
}

tls_install_uploaded() {
    # The organization-CA pair the backend staged (Settings > Networking); validated again here, since
    # the staging directory is backend-writable. The staged copy is removed either way.
    local dir="$TLS_UPLOAD_DIR" rc=0
    tls_install_pair "$dir/tls.crt" "$dir/tls.key" || rc=$?
    # An installed upload ends bioAF's renewal of any earlier Let's Encrypt certificate.
    if [ "$rc" -eq 0 ]; then
        rm -f "$(_tls_marker)"
        _tls_write_report none
    fi
    rm -f "$dir/tls.crt" "$dir/tls.key"
    return $rc
}
