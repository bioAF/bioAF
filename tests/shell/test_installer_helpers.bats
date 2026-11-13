#!/usr/bin/env bats
# How install-gcp.sh and install-aws.sh load their helpers (plan 23): from installer/ beside the
# script only when they run from a script file, otherwise downloaded from the installer's own ref,
# and never from the current directory. A failed, empty, malformed or incompatible download is never
# executed. curl, aws and uname are stubs; nothing here reaches the network or a cloud account.

REPO="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"

setup_file() {
    local dir tool name
    export SYSTEM_BIN="$BATS_FILE_TMPDIR/system-bin"
    mkdir -p "$SYSTEM_BIN"
    for dir in /usr/bin /bin /usr/sbin /sbin; do
        for tool in "$dir"/*; do
            name="${tool##*/}"
            case "$name" in gcloud | gsutil | bq | aws | aws_completer | brew | curl) continue ;; esac
            [ -e "$SYSTEM_BIN/$name" ] || ln -s "$tool" "$SYSTEM_BIN/$name"
        done
    done
}

setup() {
    TEST_DIR="$(mktemp -d)"
    export TEST_DIR
    export HOME="$TEST_DIR/home"
    export STUBS_DIR="$TEST_DIR/stubs"
    export CALL_LOG="$TEST_DIR/calls.log"
    export BIOAF_TTY="$TEST_DIR/tty"
    export MARKERS="$TEST_DIR/markers"
    export HELPERS="$REPO/installer"
    mkdir -p "$HOME" "$STUBS_DIR" "$MARKERS"
    : > "$CALL_LOG"; : > "$BIOAF_TTY"
    export PATH="$STUBS_DIR:$SYSTEM_BIN"
    unset BIOAF_INSTALLER_REF AWS_DEFAULT_REGION AWS_REGION AWS_PROFILE CLOUDSDK_PYTHON

    printf '#!/usr/bin/env bash\necho Linux\n' > "$STUBS_DIR/uname"

    # curl: serves installer/<helper> for the refs in FAKE_REFS (default main) from this checkout.
    # FAKE_HELPER_MODE changes what the helper named by FAKE_HELPER_TARGET (default any) returns:
    #   partial  the first half of the file and exit 18;  http-error  a marker line and exit 22
    #   empty    nothing, exit 0;  syntax  a marker line then a syntax error;  nofuncs  valid, defines nothing
    cat > "$STUBS_DIR/curl" <<'STUB'
#!/usr/bin/env bash
url="${*: -1}"
echo "curl $url" >> "$CALL_LOG"
case "$url" in
    https://raw.githubusercontent.com/bioAF/bioAF/*/installer/*) ;;
    *) exit 1 ;;
esac
rest="${url#https://raw.githubusercontent.com/bioAF/bioAF/}"
ref="${rest%/installer/*}"
helper="${rest##*/installer/}"
known=false
for r in ${FAKE_REFS:-main}; do [ "$r" = "$ref" ] && known=true; done
[ "$known" = true ] || exit 22
file="$HELPERS/$helper"
[ -f "$file" ] || exit 22
mode="${FAKE_HELPER_MODE:-ok}"
if [ -n "${FAKE_HELPER_TARGET:-}" ] && [ "$FAKE_HELPER_TARGET" != "$helper" ]; then mode=ok; fi
case "$mode" in
    ok) cat "$file" ;;
    partial) head -c "$(( $(wc -c < "$file") / 2 ))" "$file"; exit 18 ;;
    http-error) printf ': > "$MARKERS/ran-%s"\n' "$helper"; exit 22 ;;
    empty) ;;
    syntax) printf ': > "$MARKERS/ran-%s"\nif then fi\n' "$helper" ;;
    nofuncs) printf ': > "$MARKERS/ran-%s"\n' "$helper" ;;
esac
STUB

    # aws: installed, no credentials (ends the run at step 2).
    printf '#!/usr/bin/env bash\necho "aws $*" >> "$CALL_LOG"\ncase "$1" in --version) echo aws-cli/2.31.0;; sts) exit 255;; esac\nexit 0\n' > "$STUBS_DIR/aws"
    chmod +x "$STUBS_DIR"/*

    # A working directory holding hostile helpers (an old or planted checkout).
    export HOSTILE="$TEST_DIR/hostile"
    mkdir -p "$HOSTILE/installer"
    local h
    for h in output.sh local_prereqs.sh quota.sh; do
        printf ': > "$MARKERS/hostile-%s"\n' "$h" > "$HOSTILE/installer/$h"
    done
}

teardown() {
    rm -rf "$TEST_DIR"
}

answers() { printf '%s\n' "$@" > "$BIOAF_TTY"; }

# run_installer <piped|file|script-only> <installer> [args...]: run from the hostile directory.
run_installer() {
    local how="$1" installer="$2"; shift 2
    case "$how" in
        piped) run bash -c 'cd "$1" && shift && cat "$1" | bash -s -- "${@:2}"' _ "$HOSTILE" "$REPO/$installer" "$@" </dev/null ;;
        file) run bash -c 'cd "$1" && shift && bash "$@"' _ "$HOSTILE" "$REPO/$installer" "$@" </dev/null ;;
        script-only)
            mkdir -p "$TEST_DIR/download"
            cp "$REPO/$installer" "$TEST_DIR/download/"
            run bash -c 'cd "$1" && shift && bash "$@"' _ "$HOSTILE" "$TEST_DIR/download/$installer" "$@" </dev/null ;;
    esac
}

# reached <installer>: the run got past helper loading to its first cloud step.
reached() {
    case "$1" in
        install-aws.sh) [[ "$output" == *"No working AWS credentials"* ]] ;;
        install-gcp.sh) [[ "$output" == *"Cancelled."* ]] ;;
    esac
}

no_markers() { [ -z "$(ls -A "$MARKERS")" ]; }

helper_downloads() { grep -c '^curl https://raw.githubusercontent.com/bioAF/bioAF/' "$CALL_LOG" || true; }

# ---------------------------------------------------------------------------
# Where helpers come from
# ---------------------------------------------------------------------------

@test "piped installers download their helpers and never source the current directory's" {
    local installer
    for installer in install-aws.sh install-gcp.sh; do
        : > "$CALL_LOG"; answers n
        run_installer piped "$installer"
        reached "$installer"
        no_markers
        grep -q '^curl https://raw.githubusercontent.com/bioAF/bioAF/main/installer/output.sh$' "$CALL_LOG"
        grep -q '^curl https://raw.githubusercontent.com/bioAF/bioAF/main/installer/local_prereqs.sh$' "$CALL_LOG"
    done
}

@test "piped installers ignore helpers in an empty or old-checkout working directory alike" {
    rm -rf "$HOSTILE/installer"
    answers n
    run_installer piped install-aws.sh
    reached install-aws.sh
    [ "$(helper_downloads)" -eq 2 ]
}

@test "an installer run from its clone uses the helpers beside it, whatever the working directory" {
    local installer
    for installer in install-aws.sh install-gcp.sh; do
        : > "$CALL_LOG"; answers n
        run_installer file "$installer"
        reached "$installer"
        no_markers
        [ "$(helper_downloads)" -eq 0 ]
    done
}

@test "a downloaded installer without helpers beside it fetches them, not the working directory's" {
    answers n
    run_installer script-only install-aws.sh
    reached install-aws.sh
    no_markers
    [ "$(helper_downloads)" -eq 2 ]
}

# ---------------------------------------------------------------------------
# Failed downloads are never executed
# ---------------------------------------------------------------------------

@test "a failed, partial, empty, malformed or incompatible helper stops the install before any cloud step" {
    local installer mode
    for installer in install-aws.sh install-gcp.sh; do
        for mode in http-error partial empty syntax nofuncs; do
            : > "$CALL_LOG"; rm -f "$MARKERS"/*; answers y y y
            FAKE_HELPER_MODE="$mode" FAKE_HELPER_TARGET=local_prereqs.sh run bash -c 'cd "$1" && cat "$2" | bash' _ "$HOSTILE" "$REPO/$installer" </dev/null
            [ "$status" -ne 0 ]
            [[ "$output" == *"Could not load"* ]]
            [[ "$output" == *"local_prereqs.sh"* ]]
            ! reached "$installer"
            ! grep -qE '^(aws|gcloud) ' "$CALL_LOG"
            [ ! -e "$MARKERS/ran-local_prereqs.sh" ] || [ "$mode" = nofuncs ]
            [[ "$output" != *"Step 1"* ]]
        done
    done
}

@test "a failed output helper download stops the install with an actionable message" {
    FAKE_HELPER_MODE=http-error FAKE_HELPER_TARGET=output.sh run bash -c 'cd "$1" && cat "$2" | bash' _ "$HOSTILE" "$REPO/install-aws.sh" </dev/null
    [ "$status" -ne 0 ]
    [[ "$output" == *"Could not load"*"output.sh"* ]]
    [[ "$output" == *"--ref"* ]]
    no_markers
}

# ---------------------------------------------------------------------------
# The installer's source ref
# ---------------------------------------------------------------------------

@test "every helper comes from the ref given by --ref or BIOAF_INSTALLER_REF" {
    answers n
    FAKE_REFS="v2026.11.22" run bash -c 'cd "$1" && cat "$2" | bash -s -- --ref v2026.11.22' _ "$HOSTILE" "$REPO/install-aws.sh" </dev/null
    reached install-aws.sh
    [ "$(grep -c '/bioAF/bioAF/v2026.11.22/installer/' "$CALL_LOG")" -eq 2 ]
    ! grep -q '/bioAF/bioAF/main/' "$CALL_LOG"
    : > "$CALL_LOG"; answers n
    FAKE_REFS="0123456789abcdef0123456789abcdef01234567" run bash -c 'cd "$1" && cat "$2" | bash -s -- --ref=0123456789abcdef0123456789abcdef01234567' _ "$HOSTILE" "$REPO/install-gcp.sh" </dev/null
    reached install-gcp.sh
    ! grep -q '/bioAF/bioAF/main/' "$CALL_LOG"
    : > "$CALL_LOG"; answers n
    FAKE_REFS="v2026.11.22" BIOAF_INSTALLER_REF=v2026.11.22 run bash -c 'cd "$1" && cat "$2" | bash' _ "$HOSTILE" "$REPO/install-aws.sh" </dev/null
    reached install-aws.sh
    ! grep -q '/bioAF/bioAF/main/' "$CALL_LOG"
}

@test "an unavailable ref fails and never falls back to main" {
    local installer
    for installer in install-aws.sh install-gcp.sh; do
        : > "$CALL_LOG"; answers n
        run bash -c 'cd "$1" && cat "$2" | bash -s -- --ref v9.9.9' _ "$HOSTILE" "$REPO/$installer" </dev/null
        [ "$status" -ne 0 ]
        [[ "$output" == *"Could not load"* ]]
        grep -q '/bioAF/bioAF/v9.9.9/installer/output.sh' "$CALL_LOG"
        ! grep -q '/bioAF/bioAF/main/' "$CALL_LOG"
    done
}

@test "a malformed ref is refused before anything is downloaded" {
    local installer ref
    for installer in install-aws.sh install-gcp.sh; do
        for ref in '../main' 'a b' '$(id)' '-x' 'main/' 'refs//x' ''; do
            : > "$CALL_LOG"
            run bash -c 'cd "$1" && cat "$2" | bash -s -- --ref "$3"' _ "$HOSTILE" "$REPO/$installer" "$ref" </dev/null
            [ "$status" -ne 0 ]
            [ "$(helper_downloads)" -eq 0 ]
        done
    done
}

@test "--help works without a terminal and mentions --ref" {
    local installer
    for installer in install-aws.sh install-gcp.sh; do
        export BIOAF_TTY="$TEST_DIR/no-such-terminal"
        run bash -c 'cat "$1" | bash -s -- --help' _ "$REPO/$installer" </dev/null
        [ "$status" -eq 0 ]
        [[ "$output" == *"--ref"* ]]
        [ "$(helper_downloads)" -eq 0 ]
    done
}

@test "an unknown option is refused before anything is downloaded" {
    local installer
    for installer in install-aws.sh install-gcp.sh; do
        : > "$CALL_LOG"
        run bash -c 'cd "$1" && cat "$2" | bash -s -- --bogus' _ "$HOSTILE" "$REPO/$installer" </dev/null
        [ "$status" -eq 2 ]
        [[ "$output" == *"--bogus"* ]]
        [ "$(helper_downloads)" -eq 0 ]
    done
}

# ---------------------------------------------------------------------------
# The loader block itself, as each installer carries it (the GCP quota helper is optional)
# ---------------------------------------------------------------------------

# loader <installer>: the installer's helper-loader block, extracted verbatim.
loader() {
    sed -n '/^# >>> bioaf helper loader/,/^# <<< bioaf helper loader/p' "$REPO/$1"
}

@test "both installers carry a loader block" {
    [ -n "$(loader install-aws.sh)" ]
    [ -n "$(loader install-gcp.sh)" ]
}

@test "an optional helper that fails to download is skipped and never executed" {
    local installer mode
    for installer in install-aws.sh install-gcp.sh; do
        for mode in http-error partial empty syntax nofuncs; do
            rm -f "$MARKERS"/*
            FAKE_HELPER_MODE="$mode" run bash -c "$(loader "$installer")
                BIOAF_SCRIPT_DIR=''; BIOAF_INSTALLER_REF=main
                if bioaf_load_helper installer/quota.sh optional bioaf_quota_ensure_all; then echo LOADED; else echo SKIPPED; fi" </dev/null
            [ "$status" -eq 0 ]
            [[ "$output" == *"SKIPPED"* ]]
            [ ! -e "$MARKERS/ran-quota.sh" ] || [ "$mode" = nofuncs ]
        done
        run bash -c "$(loader "$installer")
            BIOAF_SCRIPT_DIR=''; BIOAF_INSTALLER_REF=main
            bioaf_load_helper installer/quota.sh optional bioaf_quota_ensure_all && declare -F bioaf_quota_ensure_all" </dev/null
        [ "$status" -eq 0 ]
    done
}
