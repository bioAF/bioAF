#!/usr/bin/env bats
# installer/tls.sh (plan 03.5A, owner decision 2026-11-25): Let's Encrypt IP certificates with a pinned
# certbot, automatic renewal, and the organization-CA upload, all installed the same way: validated,
# swapped atomically into nginx's certificate directory, checked with nginx -t and reloaded, with the
# previous pair restored if nginx refuses. docker is a stub (certbot and nginx run inside containers);
# openssl is real.

HELPER="$BATS_TEST_DIRNAME/../../installer/tls.sh"

absent() { if grep -q "$@"; then return 1; fi; }

make_pair() {
    # make_pair <dir> <cn> [days]: a self-signed certificate and its key.
    mkdir -p "$1"
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout "$1/key.pem" \
        -out "$1/cert.pem" -days "${3:-7}" -subj "/CN=$2" >/dev/null 2>&1
}

make_expired_pair() {
    # make_expired_pair <workdir> <cert> <key>: a certificate valid only in 2020, signed by a throwaway CA
    # (openssl ca takes explicit dates on OpenSSL and LibreSSL alike; req -days 0 is refused by OpenSSL 3).
    local d="$1"
    mkdir -p "$d/new"; : > "$d/index.txt"; echo 01 > "$d/serial"
    cat > "$d/ca.cnf" <<CNF
[ca]
default_ca = d
[d]
dir = $d
database = $d/index.txt
new_certs_dir = $d/new
serial = $d/serial
default_md = sha256
policy = p
[p]
commonName = supplied
CNF
    openssl req -x509 -newkey rsa:2048 -nodes -keyout "$d/ca.key" -out "$d/ca.crt" -days 1 -subj /CN=old-ca >/dev/null 2>&1
    openssl req -new -newkey rsa:2048 -nodes -keyout "$3" -out "$d/req.csr" -subj /CN=expired >/dev/null 2>&1
    openssl ca -batch -config "$d/ca.cnf" -cert "$d/ca.crt" -keyfile "$d/ca.key" -in "$d/req.csr" -out "$2" \
        -startdate 20200101000000Z -enddate 20200102000000Z >/dev/null 2>&1
}

setup() {
    TEST_DIR="$(mktemp -d)"
    export STUBS="$TEST_DIR/stubs"; mkdir -p "$STUBS"
    export CALLS="$TEST_DIR/calls"; : > "$CALLS"
    export PATH="$STUBS:$PATH"
    export TLS_CERTS_DIR="$TEST_DIR/certs"; mkdir -p "$TLS_CERTS_DIR"
    export TLS_STATE_DIR="$TEST_DIR/state"
    export TLS_WEBROOT="$TEST_DIR/webroot"; mkdir -p "$TLS_WEBROOT"
    export TLS_REPORT_DIR="$TEST_DIR/report"; mkdir -p "$TLS_REPORT_DIR"
    export ISSUED="$TEST_DIR/issued"
    make_pair "$TEST_DIR/old" old
    cp "$TEST_DIR/old/cert.pem" "$TLS_CERTS_DIR/tls.crt"; cp "$TEST_DIR/old/key.pem" "$TLS_CERTS_DIR/tls.key"
    # docker stub: "run" of the certbot image issues/renews into the mounted state dir (from $ISSUED);
    # "run --entrypoint sh" copies out as the real container would; "compose ... exec" is nginx.
    cat > "$STUBS/docker" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "docker $*" >> "$CALLS"
args="$*"
# ROOT_ONLY: certbot's live/ directory is unreadable to the host user except inside the container,
# as with the real root-run certbot image.
live="$TLS_STATE_DIR/letsencrypt/config/live"
inside() { [ -z "${ROOT_ONLY:-}" ] || chmod 700 "$live" 2>/dev/null; }
outside() { [ -z "${ROOT_ONLY:-}" ] || chmod 000 "$live" 2>/dev/null; }
case "$args" in
    *"nginx -t"*) [ -z "${NGINX_REFUSES:-}" ] || exit 1; exit 0 ;;
    *"nginx -s reload"*) [ -z "${NGINX_RELOAD_FAILS:-}" ] || exit 1; exit 0 ;;
    *"--entrypoint sh"*)
        # Copy-out: the last argument is the sh -c script; run it with the mounts mapped to local paths.
        script="${@: -1}"
        script="${script//\/le\//$TLS_STATE_DIR/letsencrypt/}"
        script="${script//\/stage\//$TLS_STATE_DIR/stage/}"
        script="$(printf '%s' "$script" | sed -E 's/chown [0-9]+:[0-9]+ [^&;]*(&&|;)?//')"
        inside; sh -c "$script"; rc=$?; outside; exit $rc ;;
    *" certonly "*|*" renew "*)
        [ -z "${CERTBOT_FAILS:-}" ] || exit 1
        [ -d "$ISSUED" ] || exit 0
        inside
        mkdir -p "$TLS_STATE_DIR/letsencrypt/config/live/bioaf"
        cp "$ISSUED/cert.pem" "$TLS_STATE_DIR/letsencrypt/config/live/bioaf/fullchain.pem"
        cp "$ISSUED/key.pem" "$TLS_STATE_DIR/letsencrypt/config/live/bioaf/privkey.pem"
        outside
        exit 0 ;;
esac
exit 0
STUB
    chmod +x "$STUBS/docker"
    export TLS_COMPOSE="docker compose -f compose.yml"
    # shellcheck source=installer/tls.sh
    source "$HELPER"
}

teardown() { chmod -R u+rwx "$TEST_DIR" 2>/dev/null; rm -rf "$TEST_DIR"; }

@test "certbot is pinned to a reviewed version by digest" {
    [[ "$TLS_CERTBOT_IMAGE" =~ ^certbot/certbot:v5\.[0-9]+\.[0-9]+@sha256:[0-9a-f]{64}$ ]]
    [[ "$TLS_CERTBOT_IMAGE" != *":latest"* ]]
}

@test "an IP certificate is requested with the shortlived profile over the existing webroot" {
    make_pair "$ISSUED" 203.0.113.7
    run tls_issue_ip 203.0.113.7
    [ "$status" -eq 0 ]
    grep -q -- "certonly" "$CALLS"
    grep -q -- "--preferred-profile shortlived" "$CALLS"
    grep -q -- "--ip-address 203.0.113.7" "$CALLS"
    grep -q -- "--webroot -w /var/www/letsencrypt" "$CALLS"
    grep -q -- "$TLS_WEBROOT:/var/www/letsencrypt" "$CALLS"
    grep -q -- "--cert-name bioaf" "$CALLS"
    grep -q -- "--non-interactive" "$CALLS"
    cmp "$ISSUED/cert.pem" "$TLS_CERTS_DIR/tls.crt"
    cmp "$ISSUED/key.pem" "$TLS_CERTS_DIR/tls.key"
    grep -q "nginx -t" "$CALLS" && grep -q "nginx -s reload" "$CALLS"
}

@test "a malformed address is refused before certbot runs" {
    for bad in "203.0.113.7;rm -rf /" "not-an-ip" "" "10.0.0.5"; do
        run tls_issue_ip "$bad"
        [ "$status" -ne 0 ]
    done
    absent certonly "$CALLS"
}

@test "a failed issuance keeps the current certificate" {
    export CERTBOT_FAILS=1
    run tls_issue_ip 203.0.113.7
    [ "$status" -ne 0 ]
    cmp "$TEST_DIR/old/cert.pem" "$TLS_CERTS_DIR/tls.crt"
    absent "nginx -s reload" "$CALLS"
}

@test "nginx refusing the new pair restores the previous one and does not reload" {
    make_pair "$TEST_DIR/new" new
    export NGINX_REFUSES=1
    run tls_install_pair "$TEST_DIR/new/cert.pem" "$TEST_DIR/new/key.pem"
    [ "$status" -ne 0 ]
    cmp "$TEST_DIR/old/cert.pem" "$TLS_CERTS_DIR/tls.crt"
    cmp "$TEST_DIR/old/key.pem" "$TLS_CERTS_DIR/tls.key"
    absent "nginx -s reload" "$CALLS"
}

@test "a key that does not match its certificate is refused before anything changes" {
    make_pair "$TEST_DIR/a" a; make_pair "$TEST_DIR/b" b
    run tls_install_pair "$TEST_DIR/a/cert.pem" "$TEST_DIR/b/key.pem"
    [ "$status" -ne 0 ]
    [[ "$output" == *"does not match"* ]]
    cmp "$TEST_DIR/old/cert.pem" "$TLS_CERTS_DIR/tls.crt"
    absent "nginx" "$CALLS"
}

@test "an expired or unreadable certificate is refused" {
    make_pair "$TEST_DIR/x" x
    printf 'garbage' > "$TEST_DIR/x/garbage.pem"
    run tls_install_pair "$TEST_DIR/x/garbage.pem" "$TEST_DIR/x/key.pem"
    [ "$status" -ne 0 ]
    # Already expired: refused.
    make_expired_pair "$TEST_DIR/old-ca" "$TEST_DIR/x/c2" "$TEST_DIR/x/k2"
    ! openssl x509 -in "$TEST_DIR/x/c2" -noout -checkend 0 >/dev/null
    run tls_install_pair "$TEST_DIR/x/c2" "$TEST_DIR/x/k2"
    [ "$status" -ne 0 ]
}

@test "the installed key is private to its owner" {
    make_pair "$TEST_DIR/new" new
    run tls_install_pair "$TEST_DIR/new/cert.pem" "$TEST_DIR/new/key.pem"
    [ "$status" -eq 0 ]
    [ "$(stat -c %a "$TLS_CERTS_DIR/tls.key" 2>/dev/null || stat -f %Lp "$TLS_CERTS_DIR/tls.key")" = 600 ]
    [ ! -e "$TLS_CERTS_DIR/.tls.key.new" ] && [ ! -e "$TLS_CERTS_DIR/.tls.crt.new" ]
}

@test "renewal installs only a changed certificate" {
    make_pair "$ISSUED" 203.0.113.7
    tls_issue_ip 203.0.113.7
    : > "$CALLS"
    run tls_renew
    [ "$status" -eq 0 ]
    grep -q -- " renew " "$CALLS"
    absent "nginx -s reload" "$CALLS"
    make_pair "$ISSUED" 203.0.113.7
    : > "$CALLS"
    run tls_renew
    [ "$status" -eq 0 ]
    cmp "$ISSUED/cert.pem" "$TLS_CERTS_DIR/tls.crt"
    grep -q "nginx -s reload" "$CALLS"
}

@test "renewal runs although certbot's state is readable only inside its container" {
    export ROOT_ONLY=1
    make_pair "$ISSUED" 203.0.113.7
    tls_issue_ip 203.0.113.7
    [ ! -r "$TLS_STATE_DIR/letsencrypt/config/live" ]
    make_pair "$ISSUED" 203.0.113.7
    : > "$CALLS"
    run tls_renew
    [ "$status" -eq 0 ]
    grep -q -- " renew " "$CALLS"
    cmp "$ISSUED/cert.pem" "$TLS_CERTS_DIR/tls.crt"
}

@test "renewal never replaces an uploaded organization certificate" {
    make_pair "$ISSUED" 203.0.113.7
    tls_issue_ip 203.0.113.7
    make_pair "$TEST_DIR/org" bioaf.lab.internal
    mkdir -p "$TEST_DIR/upload"
    cp "$TEST_DIR/org/cert.pem" "$TEST_DIR/upload/tls.crt"; cp "$TEST_DIR/org/key.pem" "$TEST_DIR/upload/tls.key"
    export TLS_UPLOAD_DIR="$TEST_DIR/upload"
    tls_install_uploaded
    make_pair "$ISSUED" 203.0.113.7
    : > "$CALLS"
    run tls_renew
    [ "$status" -eq 0 ]
    absent " renew " "$CALLS"
    cmp "$TEST_DIR/org/cert.pem" "$TLS_CERTS_DIR/tls.crt"
}

@test "renewal never replaces a certificate installed by other means" {
    make_pair "$ISSUED" 203.0.113.7
    tls_issue_ip 203.0.113.7
    make_pair "$TEST_DIR/domain" bioaf.example.org
    cp "$TEST_DIR/domain/cert.pem" "$TLS_CERTS_DIR/tls.crt"; cp "$TEST_DIR/domain/key.pem" "$TLS_CERTS_DIR/tls.key"
    : > "$CALLS"
    run tls_renew
    [ "$status" -eq 0 ]
    absent " renew " "$CALLS"
    cmp "$TEST_DIR/domain/cert.pem" "$TLS_CERTS_DIR/tls.crt"
}

@test "renewal without a bioAF-issued certificate does nothing" {
    run tls_renew
    [ "$status" -eq 0 ]
    absent " renew " "$CALLS"
}

@test "a failed renewal is reported and keeps the current certificate" {
    make_pair "$ISSUED" 203.0.113.7
    tls_issue_ip 203.0.113.7
    export CERTBOT_FAILS=1
    run tls_renew
    [ "$status" -ne 0 ]
    [[ "$output" == *"renewal failed"* ]]
    cmp "$ISSUED/cert.pem" "$TLS_CERTS_DIR/tls.crt"
}

@test "an uploaded organization certificate is installed and its staged copy removed" {
    make_pair "$TEST_DIR/org" bioaf.lab.internal
    mkdir -p "$TEST_DIR/upload"
    cp "$TEST_DIR/org/cert.pem" "$TEST_DIR/upload/tls.crt"; cp "$TEST_DIR/org/key.pem" "$TEST_DIR/upload/tls.key"
    export TLS_UPLOAD_DIR="$TEST_DIR/upload"
    run tls_install_uploaded
    [ "$status" -eq 0 ]
    cmp "$TEST_DIR/org/cert.pem" "$TLS_CERTS_DIR/tls.crt"
    [ ! -e "$TEST_DIR/upload/tls.key" ] && [ ! -e "$TEST_DIR/upload/tls.crt" ]
}

@test "a symlinked upload is refused" {
    make_pair "$TEST_DIR/org" org
    mkdir -p "$TEST_DIR/upload"
    ln -s "$TEST_DIR/org/cert.pem" "$TEST_DIR/upload/tls.crt"; cp "$TEST_DIR/org/key.pem" "$TEST_DIR/upload/tls.key"
    export TLS_UPLOAD_DIR="$TEST_DIR/upload"
    run tls_install_uploaded
    [ "$status" -ne 0 ]
    cmp "$TEST_DIR/old/cert.pem" "$TLS_CERTS_DIR/tls.crt"
}

# ---------------------------------------------------------------------------
# The certificate report: what Settings shows for the installed certificate (expiry, who renews it, the
# last renewal attempt), written by the host for the backend to read.
# ---------------------------------------------------------------------------

report() { python3 -c "import json,sys; d=json.load(open('$TLS_REPORT_DIR/status.json')); print(eval(sys.argv[1]))" "$1"; }

@test "an issued IP certificate is reported with its expiry, as renewed by bioAF" {
    make_pair "$ISSUED" 203.0.113.7
    tls_issue_ip 203.0.113.7
    [ "$(report 'd["renewed_by_bioaf"]')" = True ]
    [ "$(report 'd["certificate"]["not_after"]')" = "$(openssl x509 -in "$ISSUED/cert.pem" -noout -enddate | cut -d= -f2)" ]
    [[ "$(report 'd["certificate"]["subject"]')" == *203.0.113.7* ]]
    [ "$(report 'd["last_renewal"]["result"]')" = succeeded ]
    [[ "$(report 'd["last_renewal"]["at"]')" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z$ ]]
}

@test "a failed renewal is reported and the certificate it kept is still shown" {
    make_pair "$ISSUED" 203.0.113.7
    tls_issue_ip 203.0.113.7
    export CERTBOT_FAILS=1
    run tls_renew
    [ "$(report 'd["last_renewal"]["result"]')" = failed ]
    [ "$(report 'd["certificate"]["not_after"]')" = "$(openssl x509 -in "$ISSUED/cert.pem" -noout -enddate | cut -d= -f2)" ]
}

@test "an uploaded certificate is reported as not renewed by bioAF" {
    make_pair "$ISSUED" 203.0.113.7
    tls_issue_ip 203.0.113.7
    make_pair "$TEST_DIR/org" bioaf.lab.internal
    mkdir -p "$TEST_DIR/upload"
    cp "$TEST_DIR/org/cert.pem" "$TEST_DIR/upload/tls.crt"; cp "$TEST_DIR/org/key.pem" "$TEST_DIR/upload/tls.key"
    export TLS_UPLOAD_DIR="$TEST_DIR/upload"
    tls_install_uploaded
    [ "$(report 'd["renewed_by_bioaf"]')" = False ]
    [ "$(report 'd["last_renewal"]')" = None ]
    [[ "$(report 'd["certificate"]["subject"]')" == *bioaf.lab.internal* ]]
}

@test "the report is replaced atomically and readable by the backend" {
    make_pair "$ISSUED" 203.0.113.7
    tls_issue_ip 203.0.113.7
    [ -r "$TLS_REPORT_DIR/status.json" ]
    [ -z "$(find "$TLS_REPORT_DIR" -name '*.new')" ]
    [ "$(python3 -c 'import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777)[2:])' "$TLS_REPORT_DIR/status.json")" = 644 ]
}

@test "a failed reload keeps the serving certificate on disk and the next renewal retries" {
    make_pair "$ISSUED" 203.0.113.7
    tls_issue_ip 203.0.113.7
    cp "$TLS_CERTS_DIR/tls.crt" "$TEST_DIR/serving.crt"
    make_pair "$ISSUED" 203.0.113.7
    export NGINX_RELOAD_FAILS=1
    run tls_renew
    [ "$status" -ne 0 ]
    # nginx still serves the previous certificate, so that is what stays installed.
    cmp "$TEST_DIR/serving.crt" "$TLS_CERTS_DIR/tls.crt"
    [ "$(report 'd["last_renewal"]["result"]')" = failed ]
    unset NGINX_RELOAD_FAILS
    : > "$CALLS"
    run tls_renew
    [ "$status" -eq 0 ]
    cmp "$ISSUED/cert.pem" "$TLS_CERTS_DIR/tls.crt"
    grep -q "nginx -s reload" "$CALLS"
    [ "$(report 'd["last_renewal"]["result"]')" = succeeded ]
}
