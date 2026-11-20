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

# shellcheck disable=SC2034  # HOST_PYTHON_VERSION is read by the caller
host_python_check() {
    # Succeeds when python3 on PATH runs, is at least BIOAF_HOST_PYTHON_MIN and has the standard-library
    # capabilities host operations use; sets HOST_PYTHON_VERSION. Otherwise prints why, and how to
    # install it, on stderr and fails. Installs nothing and needs no privileges.
    local py out="" status=0 reason
    HOST_PYTHON_VERSION=""
    if ! py="$(command -v python3)"; then
        reason="No python3 was found on PATH."
    else
        out="$(python3 -I -c "$_HOST_PYTHON_PROBE" 2>/dev/null)" || status=$?
        case "$status:$out" in
            "0:ok "*) HOST_PYTHON_VERSION="${out#ok }"; return 0 ;;
            "3:version "*) reason="python3 ($py) is Python ${out#version }." ;;
            "4:missing "*) reason="python3 ($py) lacks ${out#missing }, which bioAF host operations need." ;;
            *) reason="python3 ($py) was found but could not run." ;;
        esac
    fi
    {
        printf 'ERROR: bioAF needs python3 (Python %s or newer, with its standard library) on this host. %s\n' \
            "$BIOAF_HOST_PYTHON_MIN" "$reason"
        printf 'Install it with the host package manager, then run the command again:\n'
        printf '  Debian/Ubuntu:              sudo apt-get install -y python3\n'
        printf '  RHEL/Fedora/Amazon Linux:   sudo dnf install -y python3\n'
    } >&2
    return 1
}
