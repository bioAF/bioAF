# Host-Python stand-ins for the host prerequisite tests.
#
# host_python_path <dir> <kind>: prints a PATH on which the only python3 is one of <kind>:
#   none    no python3 at all (no other interpreter on PATH either)
#   broken  a python3 that cannot run (fails before executing anything)
#   old     the real interpreter reporting itself as Python 3.8
#   nocap   the real interpreter without the fcntl module (a capability-deficient build)
#   real    the real interpreter
# Every other command on the caller's PATH stays available through links in <dir>/base, which never
# holds a python* name, so a missing interpreter cannot be found later on PATH.

_host_python_real() {
    command -v python3
}

_host_python_base() {
    local base="$1/base" entry dir name
    [ -d "$base" ] && { echo "$base"; return; }
    mkdir -p "$base"
    local IFS=:
    for dir in $PATH; do
        [ -d "$dir" ] || continue
        for entry in "$dir"/*; do
            name="${entry##*/}"
            case "$name" in python*) continue ;; esac
            [ -x "$entry" ] && [ ! -d "$entry" ] && [ ! -e "$base/$name" ] && ln -s "$entry" "$base/$name"
        done
    done
    echo "$base"
}

# _host_python_wrapper <file> <real> <patch>: a python3 that runs `-c` programs on <real> after <patch>.
_host_python_wrapper() {
    cat > "$1" <<WRAP
#!/usr/bin/env bash
while [ \$# -gt 0 ]; do
    case "\$1" in
        -I) shift ;;
        -c) code="\$2"; shift 2; break ;;
        *) echo "stand-in python3 only runs -c programs" >&2; exit 9 ;;
    esac
done
exec '$2' -I -c 'import sys
$3
src = sys.argv[1]
sys.argv = ["-c"] + sys.argv[2:]
exec(compile(src, "<string>", "exec"), {"__name__": "__main__"})' "\$code" "\$@"
WRAP
    chmod +x "$1"
}

host_python_path() {
    local dir="$1" kind="$2" real base variant
    real="$(_host_python_real)"
    base="$(_host_python_base "$dir")"
    variant="$dir/python-$kind"
    mkdir -p "$variant"
    case "$kind" in
        none) ;;
        broken)
            printf '#!/bin/sh\necho "python3: error while loading shared libraries: libpython3.so" >&2\nexit 127\n' > "$variant/python3"
            chmod +x "$variant/python3" ;;
        old) _host_python_wrapper "$variant/python3" "$real" 'sys.version_info = (3, 8, 18, "final", 0)' ;;
        nocap) _host_python_wrapper "$variant/python3" "$real" 'sys.modules["fcntl"] = None' ;;
        real) ln -sf "$real" "$variant/python3" ;;
        *) echo "unknown python kind: $kind" >&2; return 1 ;;
    esac
    echo "$variant:$base"
}
