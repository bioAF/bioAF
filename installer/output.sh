#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# bioAF installer output helpers.
#
# Sourced by install-gcp.sh (runs on the user's laptop) and bioaf
# (runs on the VM) to render a clean per-step UI:
#
#   [ ] Doing X...           (in progress -- updated in place on a tty)
#   [✓] Doing X              (success, green check)
#   [x] Doing X              (failure, red X, followed by tail of log)
#   [o] Doing X              (warning, yellow circle; collected for summary)
#
# Each wrapped command's stdout/stderr is redirected to a per-script
# install log so the terminal stays readable. Other output from the
# caller (echo, read -rp prompts, etc.) is untouched, so interactive
# prompts still work normally.
#
# Pass --verbose to keep wrapped command output inline; useful when
# diagnosing a failed step.
#
# Caller contract:
#   export BIOAF_INSTALL_LOG=/path/to/log
#   export BIOAF_VERBOSE=0|1
#   source installer/output.sh
#   io_init
#   step "Doing X" -- some_command --flag arg
#   maybe_step "Best-effort thing" -- other_command
#   warn "An out-of-band warning"
#   ...
#   io_finish
#
# Notes:
#   - The "--" between label and command is optional; both forms work.
#   - step returns the wrapped command's exit code, so under `set -e`
#     the script aborts after the [x] line is rendered. Use
#     `step ... || warn "..."` to keep going.
#   - maybe_step never aborts -- any non-zero exit is reported as a
#     warning and collected for the final summary.
# ---------------------------------------------------------------------------

# ANSI codes. _IO_ prefix to avoid clashing with caller-defined helpers.
_IO_RED=$'\033[0;31m'
_IO_GRN=$'\033[0;32m'
_IO_YEL=$'\033[0;33m'
_IO_DIM=$'\033[2m'
_IO_BLD=$'\033[1m'
_IO_RST=$'\033[0m'

# Status glyphs. Mostly ASCII so they copy-paste cleanly into bug reports;
# success uses U+2713 ✓ which renders in every modern terminal font.
_IO_OK="[✓]"
_IO_FAIL="[x]"
_IO_WARN="[o]"
_IO_PROG="[ ]"

_IO_WARNINGS=()
_IO_INITED=0
_IO_FINISH_CALLED=0

# Open the log file. Does NOT redirect global stdout/stderr -- only
# commands wrapped in step/maybe_step have their output captured.
io_init() {
    if [ -z "${BIOAF_INSTALL_LOG:-}" ]; then
        printf 'io_init: BIOAF_INSTALL_LOG must be set before sourcing output.sh\n' >&2
        return 1
    fi
    mkdir -p "$(dirname "$BIOAF_INSTALL_LOG")"
    : >"$BIOAF_INSTALL_LOG"
    _IO_INITED=1
    _io_log "=== bioAF install log opened $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
    # Always emit the warnings summary + log pointer on exit, even when
    # the script aborts via set -e. Idempotent if the caller already
    # invoked io_finish manually.
    trap _io_on_exit EXIT
}

# Internal: fired by the EXIT trap. Calls io_finish unless the caller
# already did, so warnings never get lost on fail-fast.
_io_on_exit() {
    [ "$_IO_FINISH_CALLED" = "1" ] && return 0
    io_finish
}

# Internal: append a line to the log file.
_io_log() {
    if [ "$_IO_INITED" = "1" ]; then
        printf '%s\n' "$*" >>"$BIOAF_INSTALL_LOG"
    fi
}

# Detect whether stdout is a real terminal. If not (CI, piped to less,
# etc.), suppress the in-place rewrites and just print the final line
# once per step.
_io_is_tty() {
    [ -t 1 ] 2>/dev/null
}

# Print a line to the terminal AND mirror to the log.
say() {
    printf '%b\n' "$*"
    if [ "$_IO_INITED" = "1" ]; then
        printf '%s\n' "$*" \
            | sed $'s/\033\\[[0-9;]*m//g' >>"$BIOAF_INSTALL_LOG"
    fi
}

# Dimmed informational line.
note() {
    say "${_IO_DIM}$*${_IO_RST}"
}

# Section heading. Bold, with a leading blank line.
section() {
    say ""
    say "${_IO_BLD}$*${_IO_RST}"
}

# Out-of-band warning -- not tied to a specific step. Collected for the
# final summary.
warn() {
    local msg="$*"
    _IO_WARNINGS+=("$msg")
    say "  ${_IO_YEL}${_IO_WARN}${_IO_RST} $msg"
}

# Out-of-band failure marker (use when the failing thing isn't a single
# wrapped command call).
fail_line() {
    say "  ${_IO_RED}${_IO_FAIL}${_IO_RST} $*"
}

# Render the "in-progress" indicator for a step. On a tty we leave the
# cursor on the same line so the final state can overwrite it. When stdout
# is not a tty (CI, `gcloud compute ssh --command=...`, piped to tee,
# etc.) we skip this line entirely; _io_step_end will emit the final
# state once. Printing both would double every step.
_io_step_start() {
    local label="$1"
    if _io_is_tty; then
        printf '  %s%s%s %s' "$_IO_DIM" "$_IO_PROG" "$_IO_RST" "$label"
    fi
}

# Render the final state line for a step. On a tty we erase the in-
# progress line and reprint; otherwise just print a fresh line.
_io_step_end() {
    local glyph_color="$1" glyph="$2" label="$3"
    if _io_is_tty; then
        printf '\r\033[2K'
    fi
    printf '  %s%s%s %s\n' "$glyph_color" "$glyph" "$_IO_RST" "$label"
    if [ "$_IO_INITED" = "1" ]; then
        printf '  %s %s\n' "$glyph" "$label" >>"$BIOAF_INSTALL_LOG"
    fi
}

# Tail recent log output to the terminal so a failure has immediate
# context without forcing the user to open the log.
_io_dump_log_tail() {
    if [ -f "$BIOAF_INSTALL_LOG" ] && [ "${BIOAF_VERBOSE:-0}" != "1" ]; then
        printf '      %s(last lines from %s)%s\n' \
            "$_IO_DIM" "$BIOAF_INSTALL_LOG" "$_IO_RST"
        tail -n 20 "$BIOAF_INSTALL_LOG" 2>/dev/null \
            | sed 's/^/      /'
    fi
}

# Run a command as a step.
#   step "label" cmd args...
#   step "label" -- cmd args...
# Returns the command's exit code. Under `set -e` the script aborts on
# non-zero unless the caller wraps the call in `||` / `&&` / `if`.
step() {
    local label="$1"; shift
    if [ "${1:-}" = "--" ]; then shift; fi
    _io_step_start "$label"
    _io_log ">>> step: $label"
    local rc=0
    if [ "${BIOAF_VERBOSE:-0}" = "1" ]; then
        # Verbose: drop the in-progress line first so command output
        # doesn't overwrite it.
        if _io_is_tty; then printf '\n'; fi
        "$@" || rc=$?
    elif [ "$_IO_INITED" = "1" ]; then
        "$@" >>"$BIOAF_INSTALL_LOG" 2>&1 || rc=$?
    else
        # No log path -- best effort, throw output away to keep the UI tidy.
        "$@" >/dev/null 2>&1 || rc=$?
    fi
    if [ "$rc" = "0" ]; then
        _io_step_end "$_IO_GRN" "$_IO_OK" "$label"
    else
        _io_step_end "$_IO_RED" "$_IO_FAIL" "$label"
        _io_dump_log_tail
    fi
    return "$rc"
}

# Run a command as a "best-effort" step. Non-zero exit is recorded as a
# warning; the script keeps going. Always returns 0 so `set -e` stays happy.
maybe_step() {
    local label="$1"; shift
    if [ "${1:-}" = "--" ]; then shift; fi
    _io_step_start "$label"
    _io_log ">>> maybe_step: $label"
    local rc=0
    if [ "${BIOAF_VERBOSE:-0}" = "1" ]; then
        if _io_is_tty; then printf '\n'; fi
        "$@" || rc=$?
    elif [ "$_IO_INITED" = "1" ]; then
        "$@" >>"$BIOAF_INSTALL_LOG" 2>&1 || rc=$?
    else
        "$@" >/dev/null 2>&1 || rc=$?
    fi
    if [ "$rc" = "0" ]; then
        _io_step_end "$_IO_GRN" "$_IO_OK" "$label"
    else
        _IO_WARNINGS+=("$label (exit $rc)")
        _io_step_end "$_IO_YEL" "$_IO_WARN" "$label"
    fi
    return 0
}

# Print accumulated warnings and the log path. Safe to call multiple
# times: subsequent invocations are no-ops, which lets the EXIT trap
# fire without duplicating output when the caller already called this
# explicitly.
io_finish() {
    [ "$_IO_FINISH_CALLED" = "1" ] && return 0
    _IO_FINISH_CALLED=1
    if [ "${#_IO_WARNINGS[@]}" -gt 0 ]; then
        say ""
        say "${_IO_BLD}${_IO_YEL}Warnings (${#_IO_WARNINGS[@]})${_IO_RST}"
        local w
        for w in "${_IO_WARNINGS[@]}"; do
            say "  ${_IO_YEL}${_IO_WARN}${_IO_RST} $w"
        done
    fi
    say ""
    note "Full install log: ${BIOAF_INSTALL_LOG}"
}
