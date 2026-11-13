#!/usr/bin/env bash
# shellcheck shell=bash
# Reconcile one host file that bioAF manages (a service's config or unit file) with the template
# this release ships. Sourced by ./bioaf; the Ops Agent config is the first consumer (plan 22) and
# the update-agent systemd unit is the second (plan 23).
#
#   managed_file_reconcile <identity> <template> <dest> <legacy_sha256s> <validate_fn> <activate_fn> <detail_dir>
#
# The installed file is the template prefixed with one marker line:
#   # bioaf-managed: <identity> sha256=<sha256 of the template>
# What is at <dest> decides the outcome (MF_OUTCOME; return 0 for the first four, 1 otherwise):
#   unchanged         already the rendered template: nothing is validated, written or restarted
#   installed         nothing there: validated, installed atomically, activated
#   migrated          a known file (an intact managed marker of any version, or a whole-file hash in
#                     <legacy_sha256s>, space-separated) is replaced the same way
#   operator_managed  the file contains the line "# bioaf-managed: operator": left alone
#   custom            anything else, including a managed file edited after install: left alone; the
#                     rendered candidate is saved to <detail_dir> for the operator to merge
#   install_failed    staging, backing up or installing the file failed: the current file is unchanged
#                     and nothing was activated
#   invalid           <validate_fn> rejected the staged candidate: nothing installed
#   activation_failed <activate_fn> failed: the previous file was restored and reactivated
#   rollback_failed   the previous file could not be restored, or reactivating it failed too
#
# The callbacks are function names, called as `<fn> <path>` (validate gets the staged candidate,
# activate the installed path); no command string is evaluated. MF_PRIV, when set (e.g. sudo),
# prefixes the file operations at <dest>. Callers keep template rendering, legacy recognition,
# privileges and service policy.
#
# Output safety: <dest> and candidate content are never printed. Callback output goes to the
# terminal only when stdout is one; otherwise it goes to <detail_dir>/<identity>.log (mode 600).
# <detail_dir> must be host-only: not a directory the backend mounts (update-status, update-requests,
# backups). Under the update agent this script's output is captured into the agent's private
# host-only/agent.log, or into the service's stdout/stderr (the journal) when that log is unavailable;
# neither is web-visible, but raw detail still belongs only in <detail_dir>.

_mf_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum | cut -d' ' -f1; else shasum -a 256 | cut -d' ' -f1; fi
}

managed_file_render() {
    local identity="$1" template="$2"
    printf '# bioaf-managed: %s sha256=%s\n' "$identity" "$(_mf_sha256 < "$template")"
    cat "$template"
}

# _mf_state <dest> <rendered> <legacy_sha256s>: absent | current | operator | known | custom
_mf_state() {
    local dest="$1" rendered="$2" legacy="$3" first marked_sha body_sha whole_sha h
    if ! ${MF_PRIV:-} test -e "$dest"; then echo absent; return; fi
    if ${MF_PRIV:-} cmp -s "$dest" "$rendered"; then echo current; return; fi
    if ${MF_PRIV:-} grep -qx '# bioaf-managed: operator' "$dest"; then echo operator; return; fi
    first=$(${MF_PRIV:-} head -n 1 "$dest")
    if [[ "$first" =~ ^#\ bioaf-managed:\ [^[:space:]]+\ sha256=([0-9a-f]{64})$ ]]; then
        marked_sha="${BASH_REMATCH[1]}"
        body_sha=$(${MF_PRIV:-} tail -n +2 "$dest" | _mf_sha256)
        if [ "$body_sha" = "$marked_sha" ]; then echo known; return; fi
        echo custom; return
    fi
    whole_sha=$(${MF_PRIV:-} cat "$dest" | _mf_sha256)
    for h in $legacy; do
        if [ "$h" = "$whole_sha" ]; then echo known; return; fi
    done
    echo custom
}

# _mf_quiet <log> <fn> <args...>: run a callback, its output to the terminal or to the restricted log.
_mf_quiet() {
    local log="$1"; shift
    if [ -t 1 ]; then "$@"; return; fi
    ( umask 077; : >> "$log" )
    chmod 600 "$log"
    "$@" >> "$log" 2>&1
}

# shellcheck disable=SC2034  # MF_OUTCOME is read by the caller.
managed_file_reconcile() {
    local identity="$1" template="$2" dest="$3" legacy="$4" validate_fn="$5" activate_fn="$6" detail_dir="$7"
    local name="${identity//\//-}" rendered state staged previous had_previous=false log
    MF_OUTCOME=""
    ( umask 077; mkdir -p "$detail_dir" )
    log="$detail_dir/$name.log"
    if ! rendered=$(mktemp) || ! managed_file_render "$identity" "$template" > "$rendered"; then
        MF_OUTCOME=install_failed
        echo "Could not render $template, so $dest was not changed."
        return 1
    fi
    state=$(_mf_state "$dest" "$rendered" "$legacy")

    case "$state" in
        current) MF_OUTCOME=unchanged; rm -f "$rendered"; return 0 ;;
        operator)
            MF_OUTCOME=operator_managed
            echo "$dest is marked operator-managed; bioAF left it unchanged. It must stay compatible with what this release expects."
            rm -f "$rendered"; return 0 ;;
        custom)
            MF_OUTCOME=custom
            echo "$dest is not a file bioAF recognizes, so it was left unchanged."
            if ( umask 077; cp "$rendered" "$detail_dir/$name.candidate" ) && chmod 600 "$detail_dir/$name.candidate"; then
                if [ -t 1 ]; then ${MF_PRIV:-} diff -u "$dest" "$detail_dir/$name.candidate" || true; fi
                echo "Merge $detail_dir/$name.candidate into it (or replace it), then rerun."
            else
                echo "The candidate could not be saved to $detail_dir; render it with managed_file_render, merge it, then rerun."
            fi
            echo "To keep your own file, add the line '# bioaf-managed: operator' to it."
            rm -f "$rendered"; return 1 ;;
    esac

    staged="$dest.bioaf-staged"
    previous="$dest.bioaf-previous"
    # Every file operation is checked explicitly: callers run this on the left of `||`, where Bash
    # ignores errexit, so a failed step must never fall through to activation or a success report.
    if ! { ${MF_PRIV:-} mkdir -p "$(dirname "$dest")" \
        && ${MF_PRIV:-} cp "$rendered" "$staged" \
        && ${MF_PRIV:-} chmod 644 "$staged"; }; then
        ${MF_PRIV:-} rm -f "$staged" 2>/dev/null || true
        rm -f "$rendered"
        MF_OUTCOME=install_failed
        echo "Could not stage the new $dest, so nothing was changed."
        return 1
    fi
    rm -f "$rendered"
    if ! _mf_quiet "$log" "$validate_fn" "$staged"; then
        ${MF_PRIV:-} rm -f "$staged"
        MF_OUTCOME=invalid
        echo "The new $dest did not pass validation, so nothing was changed. Details: $log"
        return 1
    fi
    if [ "$state" = known ]; then
        if ! ${MF_PRIV:-} cp -p "$dest" "$previous"; then
            ${MF_PRIV:-} rm -f "$staged" 2>/dev/null || true
            MF_OUTCOME=install_failed
            echo "Could not keep a copy of the current $dest, so nothing was changed."
            return 1
        fi
        had_previous=true
    fi
    if ! ${MF_PRIV:-} mv -f "$staged" "$dest"; then
        ${MF_PRIV:-} rm -f "$staged" 2>/dev/null || true
        MF_OUTCOME=install_failed
        echo "Could not install the new $dest; the current one is unchanged and still active."
        return 1
    fi
    if _mf_quiet "$log" "$activate_fn" "$dest"; then
        [ "$state" = known ] && MF_OUTCOME=migrated || MF_OUTCOME=installed
        return 0
    fi

    echo "Activating the new $dest failed; restoring the previous one. Details: $log"
    local restored=true
    if [ "$had_previous" = true ]; then
        ${MF_PRIV:-} mv -f "$previous" "$dest" || restored=false
    else
        ${MF_PRIV:-} rm -f "$dest" || restored=false
    fi
    if [ "$restored" = false ]; then
        MF_OUTCOME=rollback_failed
        echo "Could not restore the previous $dest; the new, failing one is still in place."
        return 1
    fi
    if _mf_quiet "$log" "$activate_fn" "$dest"; then
        MF_OUTCOME=activation_failed
    else
        MF_OUTCOME=rollback_failed
        echo "Reactivating the previous $dest also failed. Details: $log"
    fi
    return 1
}
