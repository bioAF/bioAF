#!/usr/bin/env bats
# End-to-end tests for how install-gcp.sh and install-aws.sh start on a
# user's machine: run as `curl ... | bash` (the documented one-liner), on a
# new Mac with nothing installed, and without a terminal.
#
# Every external tool is a stub on a PATH that excludes the real Homebrew
# prefix. The "terminal" is a file of the user's answers (BIOAF_TTY), one per
# line in prompt order. Each run is stopped at a known point by a stub that
# fails (gcloud auth login, aws sts), and the tests assert on what happened
# before it.

REPO="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"

setup() {
    TEST_DIR="$(mktemp -d)"
    export TEST_DIR
    export HOME="$TEST_DIR/home"
    export STUBS_DIR="$TEST_DIR/stubs"
    export TEMPLATES="$TEST_DIR/templates"
    export CALL_LOG="$TEST_DIR/calls.log"
    export BREW_PREFIX="$TEST_DIR/homebrew"
    export BIOAF_BREW_PREFIXES="$BREW_PREFIX"
    export BIOAF_TTY="$TEST_DIR/tty"
    mkdir -p "$HOME" "$STUBS_DIR" "$TEMPLATES"
    : > "$CALL_LOG"
    : > "$BIOAF_TTY"
    export PATH="$STUBS_DIR:/usr/bin:/bin:/usr/sbin:/sbin"
    # A login shell named zsh (so profile choice matches a new Mac) that
    # exits instead of running whatever is left on its stdin.
    mkdir -p "$TEST_DIR/shell"
    printf '#!/bin/sh\necho "login_shell_started" >> "$CALL_LOG"\n' > "$TEST_DIR/shell/zsh"
    chmod +x "$TEST_DIR/shell/zsh"
    export SHELL="$TEST_DIR/shell/zsh"
    export CASK_DEPS="python@3.12"
    export GCLOUD_DEFAULT_PY="3.9.6"
    unset CLOUDSDK_PYTHON AWS_DEFAULT_REGION AWS_REGION AWS_PROFILE

    set_os Darwin

    # Homebrew, once "installed" by the fake Homebrew installer.
    cat > "$TEMPLATES/brew" <<'STUB'
#!/usr/bin/env bash
printf 'brew %s\n' "$*" >> "$CALL_LOG"
case "$1" in
    shellenv)
        printf 'export PATH="%s/bin:$PATH";\n' "$BREW_PREFIX" ;;
    install)
        case "$*" in
            *"--cask gcloud-cli"*)
                cp "$TEMPLATES/gcloud" "$BREW_PREFIX/bin/gcloud"
                mkdir -p "$TEST_DIR/opt/python@3.12/bin"
                cp "$TEMPLATES/python" "$TEST_DIR/opt/python@3.12/bin/python3.12" ;;
            *awscli*)
                cp "$TEMPLATES/aws" "$BREW_PREFIX/bin/aws" ;;
        esac ;;
    deps)    printf '%s\n' $CASK_DEPS ;;
    --prefix) echo "$TEST_DIR/opt/$2" ;;
    list)    [ -x "$BREW_PREFIX/bin/gcloud" ] || exit 1 ;;
esac
exit 0
STUB

    # Homebrew's python@3.12 interpreter.
    cat > "$TEMPLATES/python" <<'STUB'
#!/usr/bin/env bash
case "$1" in
    --version) echo "Python 3.12.4" ;;
    -c) echo "3.12" ;;
esac
STUB

    # gcloud: reports the Python it runs on, meets bioAF's component
    # minimums, and has no account. `auth login` records what it was given
    # and fails, which ends the run.
    cat > "$TEMPLATES/gcloud" <<'STUB'
#!/usr/bin/env bash
printf 'gcloud %s\n' "$*" >> "$CALL_LOG"
case "$1 $2" in
    "info "*)
        if [ -n "${CLOUDSDK_PYTHON:-}" ]; then
            v="$("$CLOUDSDK_PYTHON" --version | awk '{print $2}')"
        else
            v="$GCLOUD_DEFAULT_PY"
        fi
        echo "$v (main) [Clang]" ;;
    "version --format=json")
        echo '{"Google Cloud SDK":"589.0.0","alpha":"2026.10.01","bq":"2.1.31","core":"2026.10.01","gcloud-crc32c":"1.0.0","gke-gcloud-auth-plugin":"0.5.12","gsutil":"5.36"}' ;;
    "version "*) echo "Google Cloud SDK 589.0.0" ;;
    "auth login")
        read -r line || true
        echo "login_stdin=$line" >> "$CALL_LOG"
        echo "login_python=${CLOUDSDK_PYTHON:-unset}" >> "$CALL_LOG"
        exit 1 ;;
    "auth print-access-token") exit 1 ;;
esac
exit 0
STUB

    # aws: present but with no credentials, which ends the run.
    cat > "$TEMPLATES/aws" <<'STUB'
#!/usr/bin/env bash
printf 'aws %s\n' "$*" >> "$CALL_LOG"
case "$1" in
    --version) echo "aws-cli/2.31.0 Python/3.13.7 Darwin/25.0.0" ;;
    sts) exit 255 ;;
esac
exit 0
STUB

    # curl: serves the Homebrew and Google installers; everything else fails.
    cat > "$STUBS_DIR/curl" <<'STUB'
#!/usr/bin/env bash
echo "curl $*" >> "$CALL_LOG"
case "$*" in
    *Homebrew/install*)
        cat <<'INSTALLER'
mkdir -p "$BREW_PREFIX/bin"
cp "$TEMPLATES/brew" "$BREW_PREFIX/bin/brew"
INSTALLER
        ;;
    *sdk.cloud.google.com*)
        cat <<'INSTALLER'
echo "google_installer_args=$*" >> "$CALL_LOG"
mkdir -p "$HOME/google-cloud-sdk/bin"
cp "$TEMPLATES/gcloud" "$HOME/google-cloud-sdk/bin/gcloud"
INSTALLER
        ;;
    *) exit 1 ;;
esac
STUB
    chmod +x "$TEMPLATES"/* "$STUBS_DIR/curl"
}

teardown() {
    rm -rf "$TEST_DIR"
}

set_os() {
    printf '#!/usr/bin/env bash\necho %s\n' "$1" > "$STUBS_DIR/uname"
    chmod +x "$STUBS_DIR/uname"
}

answers() {
    printf '%s\n' "$@" > "$BIOAF_TTY"
}

# Run an installer the way the README tells users to: piped into bash.
run_piped() {
    run bash -c 'cd "$1" && cat "$2" | bash' _ "$REPO" "$1" </dev/null
}

# ---------------------------------------------------------------------------
# No terminal
# ---------------------------------------------------------------------------

@test "install-gcp.sh stops with a plain message when there is no terminal" {
    export BIOAF_TTY="$TEST_DIR/no-such-terminal"
    run bash "$REPO/install-gcp.sh" </dev/null
    [ "$status" -ne 0 ]
    [[ "$output" == *"needs to run in a Terminal window"* ]] || false
    [[ "$output" != *"Step 1"* ]] || false
}

@test "install-aws.sh stops with a plain message when there is no terminal" {
    export BIOAF_TTY="$TEST_DIR/no-such-terminal"
    run bash "$REPO/install-aws.sh" </dev/null
    [ "$status" -ne 0 ]
    [[ "$output" == *"needs to run in a Terminal window"* ]] || false
    [[ "$output" != *"Step 1"* ]] || false
}

@test "--help works without a terminal" {
    export BIOAF_TTY="$TEST_DIR/no-such-terminal"
    run bash "$REPO/install-gcp.sh" --help </dev/null
    [ "$status" -eq 0 ]
    run bash "$REPO/install-aws.sh" --help </dev/null
    [ "$status" -eq 0 ]
}

# ---------------------------------------------------------------------------
# curl | bash
# ---------------------------------------------------------------------------

@test "piped install-gcp.sh takes the user's answer, not the next script line" {
    # A prompt that reads the script instead of the terminal swallows the
    # `if` that checks the answer, and the script then cancels regardless.
    answers y
    run_piped install-gcp.sh
    [[ "$output" != *"Cancelled."* ]] || false
    [[ "$output" == *"Step 1"* ]] || false
}

# ---------------------------------------------------------------------------
# A new Mac with nothing installed
# ---------------------------------------------------------------------------

@test "install-gcp.sh on a new Mac installs Homebrew and gcloud, then signs in, in one run" {
    # Continue, install gcloud, no SSH key passphrase, then the line the
    # browser sign-in step reads.
    answers y y n from-terminal
    run_piped install-gcp.sh
    grep -q 'Homebrew/install' "$CALL_LOG"
    grep -q '^brew install --cask gcloud-cli' "$CALL_LOG"
    grep -q '^gcloud auth login' "$CALL_LOG"
    grep -q '^login_stdin=from-terminal$' "$CALL_LOG"
    grep -q "^login_python=$TEST_DIR/opt/python@3.12/bin/python3.12\$" "$CALL_LOG"
    grep -q "$BREW_PREFIX/bin/brew shellenv" "$HOME/.zprofile"
}

@test "install-aws.sh on a new Mac installs Homebrew and the AWS CLI, then checks credentials, in one run" {
    answers y
    run_piped install-aws.sh
    [ "$status" -ne 0 ]
    grep -q 'Homebrew/install' "$CALL_LOG"
    grep -q '^brew install awscli' "$CALL_LOG"
    grep -q '^aws sts get-caller-identity' "$CALL_LOG"
    [[ "$output" == *"No working AWS credentials"* ]] || false
}

# ---------------------------------------------------------------------------
# Linux and existing installs
# ---------------------------------------------------------------------------

@test "install-gcp.sh on Linux installs gcloud and continues in the same run" {
    set_os Linux
    export GCLOUD_DEFAULT_PY="3.12.3"
    answers y y n from-terminal
    run_piped install-gcp.sh
    grep -q "^google_installer_args=.*--install-dir=$HOME" "$CALL_LOG"
    grep -q '^gcloud auth login' "$CALL_LOG"
    grep -q '^login_stdin=from-terminal$' "$CALL_LOG"
}

@test "install-gcp.sh stops before sign-in when an existing gcloud runs on an unsupported Python" {
    set_os Linux
    mkdir -p "$TEST_DIR/existing"
    cp "$TEMPLATES/gcloud" "$TEST_DIR/existing/gcloud"
    export PATH="$TEST_DIR/existing:$PATH"
    answers y
    run_piped install-gcp.sh
    [ "$status" -ne 0 ]
    [[ "$output" == *"3.9.6"* ]] || false
    ! grep -q '^gcloud auth login' "$CALL_LOG" || false
}
