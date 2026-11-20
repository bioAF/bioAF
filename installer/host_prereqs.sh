#!/usr/bin/env bash
# Host prerequisites shared by install.sh, ./bioaf and the update agent. Sourcing defines functions
# and changes nothing; host_python_check runs the check.
#
# The host's python3 (not the backend container's) runs ./bioaf's JSON parsing, deployment locks and
# records, the update agent's trigger and status I/O (scripts/update_host_io.py) and version lookups.
# The minimum is the oldest python3 the supported Linux hosts ship: the installers launch Ubuntu 22.04
# (Python 3.10). Code that runs on the host must keep to 3.10 syntax and standard library; tomllib (3.11)
# is used only where a shell fallback exists.

BIOAF_HOST_PYTHON_MIN="3.10"

# Written in syntax any python3 can parse, so an interpreter older than the minimum reports its version
# instead of failing on newer syntax. Prints one line: ok <version>, version <version> (exit 3) or
# missing <capabilities> (exit 4). It only imports and inspects modules; it reads and writes nothing.
_HOST_PYTHON_PROBE='
import sys
if tuple(sys.version_info[:2]) < (3, 10):
    sys.stdout.write("version %d.%d.%d\n" % tuple(sys.version_info[:3]))
    sys.exit(3)
missing = []
for name in ("datetime", "fcntl", "hashlib", "json", "os", "pathlib", "re", "secrets", "shutil",
             "stat", "tempfile", "time", "uuid"):
    try:
        __import__(name)
    except ImportError:
        missing.append(name)
if "os" not in missing:
    import os
    for flag in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK"):
        if not hasattr(os, flag):
            missing.append("os." + flag)
    for func in ("open", "mkdir", "rename", "unlink"):
        if getattr(os, func) not in os.supports_dir_fd:
            missing.append("os.%s with dir_fd" % func)
if missing:
    sys.stdout.write("missing %s\n" % ", ".join(missing))
    sys.exit(4)
sys.stdout.write("ok %d.%d.%d\n" % tuple(sys.version_info[:3]))
'

# The interpreter's version once host_python_check succeeds; callers print it.
HOST_PYTHON_VERSION=""

_host_python_provide_newer() {
    # How to give ./bioaf and the update agent's service a supported interpreter without replacing the
    # distribution's own: both find python3 on the default PATH, where /usr/local/bin precedes /usr/bin
    # (login shells and systemd services alike).
    printf '  1. Install Python %s or newer beside it, for example:\n' "$BIOAF_HOST_PYTHON_MIN"
    printf '       RHEL 9, Amazon Linux 2023:   sudo dnf install -y python3.11\n'
    printf '       Debian/Ubuntu:               sudo apt-get install -y python3.11   (where the release packages it)\n'
    # shellcheck disable=SC2016  # a command for the operator to type, printed literally
    printf '  2. Make it the python3 found first:  sudo ln -sf "$(command -v python3.11)" /usr/local/bin/python3\n'
    printf '     /usr/local/bin comes before /usr/bin for login shells and systemd services, so ./bioaf and the\n'
    printf '     bioaf-update-agent service both use it. /usr/bin/python3, which system tools use, is unchanged.\n'
    printf '  3. Check python3 --version in a new shell, run sudo systemctl restart bioaf-update-agent if this\n'
    printf '     host runs the update agent, then run the command again.\n'
}

# shellcheck disable=SC2034  # HOST_PYTHON_VERSION is read by the caller
host_python_check() {
    # Succeeds when python3 on PATH runs, is at least BIOAF_HOST_PYTHON_MIN and has the standard-library
    # capabilities host operations use; sets HOST_PYTHON_VERSION. Otherwise prints why, and how to
    # provide a supported interpreter, on stderr and fails. Installs nothing and needs no privileges.
    local py out="" status=0 reason kind
    HOST_PYTHON_VERSION=""
    if ! py="$(command -v python3)"; then
        kind=missing
        reason="No python3 was found on PATH."
    else
        out="$(python3 -I -c "$_HOST_PYTHON_PROBE" 2>/dev/null)" || status=$?
        case "$status:$out" in
            "0:ok "*) HOST_PYTHON_VERSION="${out#ok }"; return 0 ;;
            "3:version "*) kind=old; reason="python3 ($py) is Python ${out#version }." ;;
            "4:missing "*) kind=incomplete; reason="python3 ($py) lacks ${out#missing }, which bioAF host operations need." ;;
            *) kind=incomplete; reason="python3 ($py) was found but could not run." ;;
        esac
    fi
    {
        printf 'ERROR: bioAF needs python3 (Python %s or newer, with its standard library) on this host. %s\n' \
            "$BIOAF_HOST_PYTHON_MIN" "$reason"
        case "$kind" in
            missing)
                printf 'Install the distribution package, then run the command again:\n'
                printf '  Debian/Ubuntu:              sudo apt-get install -y python3\n'
                printf '  RHEL/Fedora/Amazon Linux:   sudo dnf install -y python3\n'
                printf 'If python3 --version then reports a version older than %s, this check explains what to do next.\n' \
                    "$BIOAF_HOST_PYTHON_MIN"
                ;;
            old)
                printf 'This python3 is too old, and the distribution package will not update it past its release.\n'
                printf 'Move the host to a release whose python3 is %s or newer (Ubuntu 22.04, Debian 12 or later), or:\n' \
                    "$BIOAF_HOST_PYTHON_MIN"
                _host_python_provide_newer
                ;;
            *)
                printf 'This python3 is damaged or a minimal build. Reinstall the full distribution package\n'
                printf '(sudo apt-get install --reinstall -y python3, or sudo dnf reinstall -y python3), or:\n'
                _host_python_provide_newer
                ;;
        esac
    } >&2
    return 1
}
