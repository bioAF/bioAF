#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# bioAF laptop prerequisites.
#
# Sourced by install-gcp.sh and install-aws.sh (both run on the user's own
# machine). Assumes nothing is installed: a new Mac has no Homebrew, no
# Command Line Tools, and only a placeholder python3. Must stay compatible
# with the Bash 3.2 that macOS ships.
#
#   attach_terminal             read all further input from the terminal
#   ensure_homebrew             install Homebrew if needed; put it on PATH
#   cask_python_interpreter C   the Python interpreter cask C depends on
#   use_cask_python_for_gcloud  run gcloud on its cask's Python, verified
#   ensure_gcloud_python        make sure gcloud runs on a supported Python
#
# Test seams (environment):
#   BIOAF_TTY            terminal device to read from (default /dev/tty)
#   BIOAF_BREW_PREFIXES  where Homebrew may live (default: the two
#                        prefixes Homebrew installs to on macOS)
# ---------------------------------------------------------------------------

HOMEBREW_INSTALL_URL="https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
GCLOUD_CASK="gcloud-cli"

# Oldest Python gcloud supports. gcloud 589.0.0 warns below this
# (PythonVersion.MIN_SUPPORTED_PY3_VERSION in core/util/platforms.py).
GCLOUD_MIN_PYTHON="3.10"

_lp_err() { printf '\033[0;31m  %s\033[0m\n' "$*" >&2; }

# Return 0 if version $1 >= version $2.
_lp_version_ge() {
    [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" = "$2" ]
}

# Point stdin at the user's terminal, for this shell and every command it
# runs, so prompts and interactive tools (sudo, Homebrew, gcloud auth, ssh)
# get keyboard input. Under `curl ... | bash` stdin is the script itself,
# and a prompt would otherwise read the next line of the script.
#
# Callers must already have read their whole script (wrap it in { }) before
# calling this, because bash reads a piped script from stdin too.
attach_terminal() {
    local tty="${BIOAF_TTY:-/dev/tty}"
    if ! { : <"$tty"; } 2>/dev/null; then
        _lp_err "The bioAF installer needs to run in a Terminal window so it can"
        _lp_err "ask you questions. Open the Terminal app, paste the install"
        _lp_err "command there, and press Return."
        return 1
    fi
    exec <"$tty"
}

# Load brew into this shell's PATH from the first prefix that has it.
_lp_load_brew_shellenv() {
    local prefix
    for prefix in ${BIOAF_BREW_PREFIXES:-/opt/homebrew /usr/local}; do
        if [ -x "$prefix/bin/brew" ]; then
            eval "$("$prefix/bin/brew" shellenv)"
            _lp_persist_brew_shellenv "$prefix/bin/brew"
            return 0
        fi
    done
    return 1
}

# Add brew to the login shell's profile, as Homebrew's installer asks users
# to do, so new Terminal windows find brew and what it installed.
_lp_persist_brew_shellenv() {
    local brew_bin="$1" profile line
    case "$(basename "${SHELL:-/bin/zsh}")" in
        bash) profile="$HOME/.bash_profile" ;;
        *)    profile="$HOME/.zprofile" ;;
    esac
    line="eval \"\$($brew_bin shellenv)\""
    if [ -f "$profile" ] && grep -qF "$brew_bin shellenv" "$profile"; then
        return 0
    fi
    printf '\n%s\n' "$line" >>"$profile"
}

# Make brew usable in this process, installing Homebrew first if needed.
# Homebrew's installer also installs the Command Line Tools, and asks for
# the user's Mac password, so it runs in the open rather than inside a step.
ensure_homebrew() {
    command -v brew >/dev/null 2>&1 && return 0
    _lp_load_brew_shellenv && return 0

    echo ""
    echo "  Installing Homebrew, the standard tool for installing software on a"
    echo "  Mac. It will ask for the password you use to log in to this Mac."
    echo "  Nothing appears on screen as you type it; that is normal."
    echo ""
    local script
    if ! script="$(curl -fsSL "$HOMEBREW_INSTALL_URL")" || [ -z "$script" ]; then
        _lp_err "Could not download the Homebrew installer. Check your internet"
        _lp_err "connection and run this installer again."
        return 1
    fi
    if ! /bin/bash -c "$script"; then
        _lp_err "Homebrew did not install. Installing it needs an administrator"
        _lp_err "account on this Mac. See https://brew.sh for help."
        return 1
    fi
    if ! _lp_load_brew_shellenv; then
        _lp_err "Homebrew finished installing but brew could not be found."
        return 1
    fi
}

# Print the Python interpreter cask $1 depends on, after checking it is
# installed, reports the version its formula names, and is new enough for
# gcloud. The dependency is read from the cask, never assumed, because
# Homebrew moves it to newer Pythons over time.
cask_python_interpreter() {
    local cask="$1" formula minor prefix py reported
    formula="$(brew deps --direct --cask "$cask" 2>/dev/null \
        | grep -E '^python@[0-9]+\.[0-9]+$' | sort -V | tail -1)"
    if [ -z "$formula" ]; then
        _lp_err "Homebrew's $cask does not name a Python dependency."
        return 1
    fi
    minor="${formula#python@}"
    prefix="$(brew --prefix "$formula" 2>/dev/null)"
    py="$prefix/bin/python$minor"
    if [ ! -x "$py" ]; then
        _lp_err "$cask needs $formula, but $py is not installed."
        return 1
    fi
    reported="$("$py" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)"
    if [ "$reported" != "$minor" ]; then
        _lp_err "$py reports Python '${reported}', expected $minor."
        return 1
    fi
    if ! _lp_version_ge "$minor" "$GCLOUD_MIN_PYTHON"; then
        _lp_err "$cask depends on Python $minor; gcloud needs $GCLOUD_MIN_PYTHON or newer."
        return 1
    fi
    echo "$py"
}

# The X.Y.Z version of the Python gcloud runs on, or nothing.
gcloud_python_version() {
    gcloud info --format='value(basic.python_version)' 2>/dev/null | awk '{print $1}'
}

# Run gcloud on the Python its Homebrew cask depends on, for this process
# and everything it starts, and confirm gcloud picked it up.
use_cask_python_for_gcloud() {
    local py want got
    py="$(cask_python_interpreter "$GCLOUD_CASK")" || return 1
    export CLOUDSDK_PYTHON="$py"
    want="$("$py" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    got="$(gcloud_python_version)"
    if [ "${got%.*}" != "$want" ]; then
        _lp_err "gcloud runs on Python '${got}', not the $want it was pointed at ($py)."
        return 1
    fi
}

# Make sure gcloud runs on a Python it supports. An existing gcloud may run
# on an older system Python; on a Mac with the Homebrew cask, move it to the
# cask's Python. Otherwise stop with instructions.
ensure_gcloud_python() {
    local cur
    cur="$(gcloud_python_version)"
    if [ -n "$cur" ] && _lp_version_ge "$cur" "$GCLOUD_MIN_PYTHON"; then
        return 0
    fi
    if [ "$(uname -s)" = "Darwin" ] && command -v brew >/dev/null 2>&1 \
        && brew list --cask "$GCLOUD_CASK" >/dev/null 2>&1; then
        use_cask_python_for_gcloud && return 0
    fi
    _lp_err "gcloud is running on Python ${cur:-(unknown)}; it needs Python $GCLOUD_MIN_PYTHON or newer."
    if [ "$(uname -s)" = "Darwin" ]; then
        _lp_err "Install gcloud with Homebrew (brew install --cask $GCLOUD_CASK), then"
        _lp_err "run this installer again."
    else
        _lp_err "Install Python $GCLOUD_MIN_PYTHON or newer and set CLOUDSDK_PYTHON to it,"
        _lp_err "then run this installer again."
    fi
    return 1
}
