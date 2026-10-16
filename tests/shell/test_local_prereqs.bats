#!/usr/bin/env bats
# Tests for installer/local_prereqs.sh: the laptop-side prerequisites shared by
# install-gcp.sh and install-aws.sh (terminal input, Homebrew, and the Python
# interpreter gcloud runs on).
#
# Every external tool (brew, curl, gcloud, python) is a stub on a PATH that
# excludes the real Homebrew prefix, so the tests behave the same on a laptop
# that has Homebrew installed as on a clean CI runner.

HELPER="$BATS_TEST_DIRNAME/../../installer/local_prereqs.sh"

setup() {
    TEST_DIR="$(mktemp -d)"
    export HOME="$TEST_DIR/home"
    export STUBS_DIR="$TEST_DIR/stubs"
    export CALL_LOG="$TEST_DIR/calls.log"
    export BREW_PREFIX="$TEST_DIR/homebrew"
    export BIOAF_BREW_PREFIXES="$BREW_PREFIX"
    export BIOAF_TTY="$TEST_DIR/tty"
    mkdir -p "$HOME" "$STUBS_DIR"
    : > "$CALL_LOG"
    : > "$BIOAF_TTY"
    export PATH="$STUBS_DIR:/usr/bin:/bin:/usr/sbin:/sbin"
    export SHELL=/bin/zsh
    unset CLOUDSDK_PYTHON

    # The brew stub is installed into the fake prefix, not onto PATH, so
    # "Homebrew installed" and "Homebrew on PATH" are separate test states.
    mkdir -p "$TEST_DIR/templates"
    cat > "$TEST_DIR/templates/brew" <<'STUB'
#!/usr/bin/env bash
printf 'brew %s\n' "$*" >> "$CALL_LOG"
case "$1" in
    shellenv)
        printf 'export HOMEBREW_PREFIX="%s";\n' "$BREW_PREFIX"
        printf 'export PATH="%s/bin:$PATH";\n' "$BREW_PREFIX"
        ;;
    deps)
        # brew deps --direct --cask <cask>
        [ -n "${CASK_DEPS:-}" ] && printf '%s\n' $CASK_DEPS
        echo "Warning: hint text goes to stderr" >&2
        ;;
    --prefix)
        echo "$TEST_DIR/opt/$2"
        ;;
    list)
        # brew list --cask <cask>
        [ "${CASK_INSTALLED:-0}" = "1" ] || exit 1
        ;;
esac
exit 0
STUB
    chmod +x "$TEST_DIR/templates/brew"
    export TEST_DIR
}

teardown() {
    rm -rf "$TEST_DIR"
}

install_brew_into_prefix() {
    mkdir -p "$BREW_PREFIX/bin"
    cp "$TEST_DIR/templates/brew" "$BREW_PREFIX/bin/brew"
}

# A fake Python interpreter for formula $1 (e.g. python@3.12) that reports
# version $2 (e.g. 3.12.4).
make_formula_python() {
    local formula="$1" version="$2" minor="${1#python@}"
    mkdir -p "$TEST_DIR/opt/$formula/bin"
    cat > "$TEST_DIR/opt/$formula/bin/python$minor" <<STUB
#!/usr/bin/env bash
case "\$1" in
    --version) echo "Python $version" ;;
    -c) echo "${version%.*}" ;;
esac
STUB
    chmod +x "$TEST_DIR/opt/$formula/bin/python$minor"
}

# A gcloud stub that reports the Python it runs on: CLOUDSDK_PYTHON when set
# (as gcloud's launcher does), else $GCLOUD_DEFAULT_PY.
make_gcloud() {
    cat > "$STUBS_DIR/gcloud" <<'STUB'
#!/usr/bin/env bash
printf 'gcloud %s\n' "$*" >> "$CALL_LOG"
if [ "$1" = "info" ]; then
    if [ -n "${CLOUDSDK_PYTHON:-}" ] && [ "${GCLOUD_IGNORES_ENV:-0}" != "1" ]; then
        v="$("$CLOUDSDK_PYTHON" --version | awk '{print $2}')"
    else
        v="$GCLOUD_DEFAULT_PY"
    fi
    echo "$v (main, Jan  1 2026, 00:00:00) [Clang 17.0.0]"
fi
STUB
    chmod +x "$STUBS_DIR/gcloud"
}

# ---------------------------------------------------------------------------
# Terminal input
# ---------------------------------------------------------------------------

@test "attach_terminal fails with a plain message when there is no terminal" {
    export BIOAF_TTY="$TEST_DIR/no-such-terminal"
    run bash -c 'source "$1"; attach_terminal' _ "$HELPER"
    [ "$status" -ne 0 ]
    [[ "$output" == *"Terminal"* ]]
}

@test "after attach_terminal, prompts read the terminal, not the piped script" {
    echo "typed-by-user" > "$BIOAF_TTY"
    run bash -c 'source "$1"; attach_terminal; read -r a; echo "got:$a"' _ "$HELPER" <<< "next-script-line"
    [ "$status" -eq 0 ]
    [ "$output" = "got:typed-by-user" ]
}

@test "after attach_terminal, interactive subprocesses also read the terminal" {
    echo "typed-by-user" > "$BIOAF_TTY"
    run bash -c 'source "$1"; attach_terminal; bash -c "read -r a; echo child:\$a"' _ "$HELPER" <<< "next-script-line"
    [ "$status" -eq 0 ]
    [ "$output" = "child:typed-by-user" ]
}

# ---------------------------------------------------------------------------
# Homebrew
# ---------------------------------------------------------------------------

@test "ensure_homebrew does nothing when brew is already on PATH" {
    install_brew_into_prefix
    export PATH="$BREW_PREFIX/bin:$PATH"
    printf '#!/usr/bin/env bash\necho "curl $*" >> "$CALL_LOG"\nexit 1\n' > "$STUBS_DIR/curl"
    chmod +x "$STUBS_DIR/curl"
    run bash -c 'source "$1"; ensure_homebrew' _ "$HELPER"
    [ "$status" -eq 0 ]
    ! grep -q '^curl' "$CALL_LOG"
}

@test "ensure_homebrew puts an installed but unlisted brew on PATH without reinstalling" {
    install_brew_into_prefix
    printf '#!/usr/bin/env bash\necho "curl $*" >> "$CALL_LOG"\nexit 1\n' > "$STUBS_DIR/curl"
    chmod +x "$STUBS_DIR/curl"
    run bash -c 'source "$1"; ensure_homebrew && command -v brew' _ "$HELPER"
    [ "$status" -eq 0 ]
    [[ "$output" == *"$BREW_PREFIX/bin/brew"* ]]
    ! grep -q '^curl' "$CALL_LOG"
}

@test "ensure_homebrew installs Homebrew and brew is usable in the same process" {
    # The fake Homebrew installer reads one line (the "Press RETURN" prompt)
    # and records it, then creates brew in the prefix.
    cat > "$STUBS_DIR/curl" <<'STUB'
#!/usr/bin/env bash
echo "curl $*" >> "$CALL_LOG"
cat <<'INSTALLER'
read -r line
echo "$line" > "$TEST_DIR/installer_stdin"
mkdir -p "$BREW_PREFIX/bin"
cp "$TEST_DIR/templates/brew" "$BREW_PREFIX/bin/brew"
INSTALLER
STUB
    chmod +x "$STUBS_DIR/curl"
    echo "RETURN" > "$BIOAF_TTY"
    run bash -c 'source "$1"; attach_terminal; ensure_homebrew && command -v brew' _ "$HELPER" <<< "next-script-line"
    [ "$status" -eq 0 ]
    [[ "$output" == *"$BREW_PREFIX/bin/brew"* ]]
    grep -q 'Homebrew/install' "$CALL_LOG"
    [ "$(cat "$TEST_DIR/installer_stdin")" = "RETURN" ]
}

@test "ensure_homebrew adds brew to the zsh profile exactly once" {
    install_brew_into_prefix
    run bash -c 'source "$1"; ensure_homebrew && ensure_homebrew' _ "$HELPER"
    [ "$status" -eq 0 ]
    [ "$(grep -c "$BREW_PREFIX/bin/brew shellenv" "$HOME/.zprofile")" -eq 1 ]
}

@test "ensure_homebrew adds brew to the bash profile when the login shell is bash" {
    install_brew_into_prefix
    export SHELL=/bin/bash
    run bash -c 'source "$1"; ensure_homebrew' _ "$HELPER"
    [ "$status" -eq 0 ]
    grep -q "$BREW_PREFIX/bin/brew shellenv" "$HOME/.bash_profile"
    [ ! -f "$HOME/.zprofile" ]
}

@test "ensure_homebrew fails clearly when the Homebrew installer fails" {
    printf '#!/usr/bin/env bash\necho "exit 1"\n' > "$STUBS_DIR/curl"
    chmod +x "$STUBS_DIR/curl"
    run bash -c 'source "$1"; ensure_homebrew' _ "$HELPER"
    [ "$status" -ne 0 ]
    [[ "$output" == *"Homebrew"* ]]
}

# ---------------------------------------------------------------------------
# The Python interpreter a Homebrew cask requires
# ---------------------------------------------------------------------------

@test "cask_python_interpreter returns the interpreter of the cask's own Python dependency" {
    install_brew_into_prefix
    export PATH="$BREW_PREFIX/bin:$PATH"
    export CASK_DEPS="openssl@3 python@3.12"
    make_formula_python python@3.12 3.12.4
    run bash -c 'source "$1"; cask_python_interpreter gcloud-cli' _ "$HELPER"
    [ "$status" -eq 0 ]
    [ "$output" = "$TEST_DIR/opt/python@3.12/bin/python3.12" ]
}

@test "cask_python_interpreter fails when the cask declares no Python" {
    install_brew_into_prefix
    export PATH="$BREW_PREFIX/bin:$PATH"
    export CASK_DEPS="openssl@3"
    run bash -c 'source "$1"; cask_python_interpreter gcloud-cli' _ "$HELPER"
    [ "$status" -ne 0 ]
}

@test "cask_python_interpreter fails when the interpreter is not installed" {
    install_brew_into_prefix
    export PATH="$BREW_PREFIX/bin:$PATH"
    export CASK_DEPS="python@3.12"
    run bash -c 'source "$1"; cask_python_interpreter gcloud-cli' _ "$HELPER"
    [ "$status" -ne 0 ]
}

@test "cask_python_interpreter fails when the interpreter reports a different version" {
    install_brew_into_prefix
    export PATH="$BREW_PREFIX/bin:$PATH"
    export CASK_DEPS="python@3.12"
    make_formula_python python@3.12 3.11.9
    run bash -c 'source "$1"; cask_python_interpreter gcloud-cli' _ "$HELPER"
    [ "$status" -ne 0 ]
}

@test "cask_python_interpreter fails when the cask's Python is older than gcloud supports" {
    install_brew_into_prefix
    export PATH="$BREW_PREFIX/bin:$PATH"
    export CASK_DEPS="python@3.9"
    make_formula_python python@3.9 3.9.20
    run bash -c 'source "$1"; cask_python_interpreter gcloud-cli' _ "$HELPER"
    [ "$status" -ne 0 ]
}

# ---------------------------------------------------------------------------
# The Python gcloud runs on
# ---------------------------------------------------------------------------

@test "use_cask_python_for_gcloud points gcloud at the cask's Python and verifies it" {
    install_brew_into_prefix
    export PATH="$BREW_PREFIX/bin:$PATH"
    export CASK_DEPS="python@3.12"
    make_formula_python python@3.12 3.12.4
    make_gcloud
    export GCLOUD_DEFAULT_PY=3.9.6
    run bash -c 'source "$1"; use_cask_python_for_gcloud && echo "py=$CLOUDSDK_PYTHON"' _ "$HELPER"
    [ "$status" -eq 0 ]
    [[ "$output" == *"py=$TEST_DIR/opt/python@3.12/bin/python3.12"* ]]
}

@test "use_cask_python_for_gcloud fails when gcloud does not run on the cask's Python" {
    install_brew_into_prefix
    export PATH="$BREW_PREFIX/bin:$PATH"
    export CASK_DEPS="python@3.12"
    make_formula_python python@3.12 3.12.4
    make_gcloud
    export GCLOUD_DEFAULT_PY=3.9.6 GCLOUD_IGNORES_ENV=1
    run bash -c 'source "$1"; use_cask_python_for_gcloud' _ "$HELPER"
    [ "$status" -ne 0 ]
}

@test "ensure_gcloud_python leaves a supported Python alone" {
    make_gcloud
    export GCLOUD_DEFAULT_PY=3.12.1
    run bash -c 'source "$1"; ensure_gcloud_python && echo "py=${CLOUDSDK_PYTHON:-unset}"' _ "$HELPER"
    [ "$status" -eq 0 ]
    [[ "$output" == *"py=unset"* ]]
}

@test "ensure_gcloud_python moves an unsupported Python to the cask's Python on macOS" {
    install_brew_into_prefix
    export PATH="$BREW_PREFIX/bin:$PATH"
    export CASK_DEPS="python@3.12" CASK_INSTALLED=1
    make_formula_python python@3.12 3.12.4
    make_gcloud
    export GCLOUD_DEFAULT_PY=3.9.6
    printf '#!/usr/bin/env bash\necho Darwin\n' > "$STUBS_DIR/uname"
    chmod +x "$STUBS_DIR/uname"
    run bash -c 'source "$1"; ensure_gcloud_python && echo "py=$CLOUDSDK_PYTHON"' _ "$HELPER"
    [ "$status" -eq 0 ]
    [[ "$output" == *"py=$TEST_DIR/opt/python@3.12/bin/python3.12"* ]]
}

@test "ensure_gcloud_python fails clearly when it cannot fix an unsupported Python" {
    make_gcloud
    export GCLOUD_DEFAULT_PY=3.9.6
    printf '#!/usr/bin/env bash\necho Linux\n' > "$STUBS_DIR/uname"
    chmod +x "$STUBS_DIR/uname"
    run bash -c 'source "$1"; ensure_gcloud_python' _ "$HELPER"
    [ "$status" -ne 0 ]
    [[ "$output" == *"3.9"* ]]
    [[ "$output" == *"3.10"* ]]
}
