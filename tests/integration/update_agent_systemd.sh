#!/usr/bin/env bash
# The update agent's unit reconciliation and activation against real systemd (plan 23).
#
# The Bats suites prove ordering and outcomes with stub systemctl/systemd-run; this proves them with
# the real ones: systemd-analyze verify, daemon-reload, enable, restart, transient units, SIGSTOP
# holds and process inspection. The updater is a stand-in (each update is one second and records
# its start and finish), so no Docker images, database or network are involved.
#
# Scenarios, each checked, never just printed:
#   A  a unit and agent an earlier release installed are migrated; a rerun changes nothing; a
#      stopped agent is started
#   B  an update run by the previous release's agent reconciles mid-update: nothing changes until
#      that update has finished, then the agent is brought to the new unit and code
#   C  the new agent rejects hostile triggers and still processes a valid one
#   D  a unit whose service does not stay up is rolled back (immediately)
#   E  the same, when the reconciliation was deferred behind a running update
#   F  a held agent's finished poll sleep stays visible (the idle check depends on it)
#   G  three concurrent reconciliations under a stream of triggers: no update is cut off
#   H  SIGTERM while a reconciliation holds the agent: the agent is resumed
#   I  systemd cannot report the agent's state: nothing changes
#
# Needs Linux with systemd as PID 1, passwordless sudo, python3, pgrep and ps; run as the non-root
# account the agent should run as. It installs and removes /etc/systemd/system/bioaf-update-agent.service,
# so it runs only on a disposable host (a CI runner or a throwaway VM/container), declared with:
#   BIOAF_SYSTEMD_TEST_HOST=disposable tests/integration/update_agent_systemd.sh
# It refuses to run when that unit already exists, and removes everything it added on exit.

set -uo pipefail

if [ "${BIOAF_SYSTEMD_TEST_HOST:-}" != disposable ]; then
    echo "Refusing to run: set BIOAF_SYSTEMD_TEST_HOST=disposable on a host whose systemd units may be changed." >&2
    exit 2
fi
if [ "$(id -u)" = 0 ]; then
    echo "Run as the non-root account the agent should run as (with passwordless sudo), not as root." >&2
    exit 2
fi
for tool in systemctl systemd-run systemd-analyze python3 pgrep ps git; do
    command -v "$tool" >/dev/null || { echo "Missing $tool." >&2; exit 2; }
done
[ "$(ps -o comm= -p 1)" = systemd ] || { echo "systemd is not PID 1 here." >&2; exit 2; }
sudo -n true || { echo "Needs passwordless sudo." >&2; exit 2; }

SERVICE=bioaf-update-agent
UNIT="/etc/systemd/system/$SERVICE.service"
if [ -e "$UNIT" ]; then
    echo "Refusing to run: $UNIT already exists (a real install?)." >&2
    exit 2
fi

REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
WORK="$(mktemp -d)"
chmod 755 "$WORK"
D="$WORK/bioAF"
OLD_AGENT="$REPO/tests/shell/fixtures/update-agent.v2026.11.22.sh"
FAILURES=0
STANDIN_DOCKER=false

cleanup() {
    sudo systemctl stop "$SERVICE-activation" 2>/dev/null
    sudo systemctl stop "$SERVICE" 2>/dev/null
    sudo systemctl disable "$SERVICE" 2>/dev/null
    sudo rm -f "$UNIT" "$UNIT.bioaf-staged" "$UNIT.bioaf-previous"
    [ "$STANDIN_DOCKER" = true ] && sudo rm -f /etc/systemd/system/docker.service
    sudo systemctl daemon-reload
    sudo systemctl reset-failed "$SERVICE" "$SERVICE-activation" 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT

# The unit Requires=docker.service; a host without Docker gets a stand-in for the run.
if ! systemctl cat docker.service >/dev/null 2>&1; then
    printf '[Unit]\nDescription=stand-in docker (bioAF systemd test)\n[Service]\nExecStart=/bin/sleep infinity\n' \
        | sudo tee /etc/systemd/system/docker.service >/dev/null
    STANDIN_DOCKER=true
    sudo systemctl daemon-reload
fi

check() {
    # check <description> <command...>
    local what="$1"; shift
    if "$@"; then echo "  ok   $what"; else echo "  FAIL $what"; FAILURES=$((FAILURES + 1)); fi
}

wait_for() {
    # wait_for <seconds> <command...>: poll until the command succeeds or the time is up.
    local deadline=$((SECONDS + $1)); shift
    until "$@"; do
        [ "$SECONDS" -ge "$deadline" ] && return 1
        sleep 0.2
    done
}

agent_pid() { systemctl show -p MainPID --value "$SERVICE"; }
agent_active() { systemctl is-active --quiet "$SERVICE"; }
agent_held() { case "$(ps -o stat= -p "$1" 2>/dev/null)" in *T*) return 0 ;; esac; return 1; }
agent_not_held() { ! agent_held "$1"; }
waiter_idle() { ! systemctl is-active --quiet "$SERVICE-activation"; }
requests_empty() { [ -z "$(ls -A "$D/update-requests")" ]; }
unit_sha() { sha256sum "$UNIT" | cut -c1-16; }
# First line read whole: `head -1 | grep -q` can fail under pipefail when grep exits early.
unit_first_line() { local line; IFS= read -r line < "$UNIT"; printf '%s' "$line"; }
unit_managed() { [[ "$(unit_first_line)" == "# bioaf-managed: update-agent-unit/v1 sha256="* ]]; }
unit_legacy() { [ "$(unit_first_line)" = "[Unit]" ]; }
code_current() { python3 -I "$D/scripts/update_host_io.py" agent-code-current "$D"; }
finished() { grep -qx "finish $1" "$D/updates.log" 2>/dev/null; }
mode_of() { python3 -c 'import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777))' "$1"; }
pid_changed_from() { local p; p=$(agent_pid); [ "$p" != 0 ] && [ "$p" != "$1" ]; }
ensure() { (cd "$D" && ./bioaf.real ensure-update-agent </dev/null); }

# fresh_checkout [hook]: this checkout's tracked files, with ./bioaf replaced by a stand-in updater.
# With "hook", each update also runs ./bioaf ensure-update-agent mid-update, as the target release's
# install.sh does during a real update.
fresh_checkout() {
    sudo systemctl stop "$SERVICE" "$SERVICE-activation" 2>/dev/null
    sudo rm -f "$UNIT"; sudo systemctl daemon-reload; sudo systemctl reset-failed "$SERVICE" 2>/dev/null
    rm -rf "$D"; mkdir -p "$D"
    (cd "$REPO" && git ls-files -z | tar --null -T - -cf -) | tar -xf - -C "$D"
    cp "$D/bioaf" "$D/bioaf.real"
    local hook=""
    [ "${1:-}" = hook ] && hook='sleep 1; bash "$dir/bioaf.real" ensure-update-agent; sleep 2'
    cat > "$D/bioaf" <<STUB
#!/usr/bin/env bash
dir="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
# Sourced (the waiter does): behave as the real ./bioaf.
if [ "\${BASH_SOURCE[0]}" != "\$0" ]; then source "\$dir/bioaf.real"; return 0; fi
if [ "\$1" = update ]; then
    echo "start \$2" >> "\$dir/updates.log"
    $hook
    sleep 1
    echo "finish \$2" >> "\$dir/updates.log"
    exit 0
fi
exec bash "\$dir/bioaf.real" "\$@"
STUB
    chmod +x "$D/bioaf"
}

# legacy_install: what an earlier release's setup left: its unmarked unit and its agent, running.
legacy_install() {
    fresh_checkout "${1:-}"
    cp "$D/scripts/update-agent.sh" "$WORK/new-update-agent.sh"
    cp "$OLD_AGENT" "$D/scripts/update-agent.sh"; chmod +x "$D/scripts/update-agent.sh"
    sed -e "s|__BIOAF_DIR__|$D|g" -e "s|__BIOAF_USER__|$(whoami)|g" -e "s|__BIOAF_GROUP__|$(id -gn)|g" \
        "$D/installer/update-agent-legacy/v0.11.5.service" | sudo tee "$UNIT" >/dev/null
    sudo systemctl daemon-reload
    sudo systemctl enable --now "$SERVICE" >/dev/null 2>&1
    # Active is reported before the agent has created its directories; wait for those too.
    wait_for 10 agent_active && wait_for 10 test -d "$D/update-requests" \
        || { echo "  FAIL the legacy agent did not start"; FAILURES=$((FAILURES + 1)); }
}

# new_code: the release being installed puts its agent on disk.
new_code() { cp "$WORK/new-update-agent.sh" "$D/scripts/update-agent.sh"; }

trigger() { printf '{"version": "%s", "upgrade_id": %s}' "$1" "$2" > "$D/update-requests/update_$2.json"; }

echo "systemd: $(systemctl --version | head -1); bash: ${BASH_VERSION}; user: $(whoami):$(id -gn)"

echo "A. an earlier release's unit and agent are migrated"
legacy_install; new_code
old=$(agent_pid)
check "ensure-update-agent succeeds" ensure
check "the unit carries the managed marker" unit_managed
check "the agent was restarted and is active" wait_for 10 pid_changed_from "$old"
check "the new agent records the code on disk" wait_for 10 code_current
check "the private log directory is 0700" test "$(mode_of "$D/host-only")" = 0o700
check "the private log is 0600" test "$(mode_of "$D/host-only/agent.log")" = 0o600
check "the unit is enabled" systemctl is-enabled --quiet "$SERVICE"
pid=$(agent_pid); sha=$(unit_sha)
check "a rerun succeeds" ensure
check "a rerun neither rewrites the unit nor restarts the agent" test "$(unit_sha)/$(agent_pid)" = "$sha/$pid"
sudo systemctl stop "$SERVICE"
check "a stopped agent is started again" ensure
check "the agent is active" agent_active

echo "B. an update run by the previous release's agent reconciles mid-update"
legacy_install hook; new_code
old=$(agent_pid)
trigger 9.9.9 5
check "the deferred reconciliation is scheduled" wait_for 15 systemctl is-active --quiet "$SERVICE-activation"
check "mid-update the unit is untouched" unit_legacy
check "mid-update the agent is the same process" test "$(agent_pid)" = "$old"
check "the update runs to completion" wait_for 20 finished 9.9.9
check "then the agent is restarted" wait_for 30 pid_changed_from "$old"
check "the old agent recorded the update as completed before it was restarted" \
    grep -q 'Update to 9.9.9 completed successfully' "$D/update-status/agent.log"
check "the unit is the new one" unit_managed
check "the agent runs the new code" wait_for 10 code_current

echo "C. the new agent against hostile triggers"
(
    cd "$D/update-requests" || exit 1
    printf '{"version": "9.9.1"}' > "a'+open('PWNED','w').write('x')+'.json"
    ln -s /etc/hostname update_1.json
    mkfifo update_2.json
)
trigger 9.9.8 9
check "every trigger is consumed" wait_for 20 requests_empty
check "the valid trigger's update ran" wait_for 10 finished 9.9.8
check "the injected code never ran" test ! -e "$D/PWNED"
check "the symlink and FIFO were rejected" test "$(grep -c 'Rejected an update trigger' "$D/host-only/agent.log")" -ge 3
check "the rejected update never ran" bash -c "! grep -q '9.9.1' '$D/updates.log'"

echo "D. a unit that does not stay up is rolled back"
sha=$(unit_sha)
cp "$D/docker/$SERVICE.service" "$WORK/template"
sed -i 's|^ExecStart=.*|ExecStart=/bin/false|' "$D/docker/$SERVICE.service"
check "ensure-update-agent fails" bash -c "! (cd '$D' && ./bioaf.real ensure-update-agent </dev/null)"
check "the previous unit is restored" test "$(unit_sha)" = "$sha"
check "the agent is active on it" wait_for 15 agent_active
cp "$WORK/template" "$D/docker/$SERVICE.service"

echo "E. a deferred reconciliation of a unit that does not stay up is rolled back"
legacy_install hook; new_code
sed -i 's|^ExecStart=.*|ExecStart=/bin/false|' "$D/docker/$SERVICE.service"
sha=$(unit_sha)
trigger 9.9.7 6
check "the update runs to completion" wait_for 20 finished 9.9.7
check "the waiter finishes" wait_for 60 waiter_idle
check "the previous unit is restored" test "$(unit_sha)" = "$sha"
check "the agent is active on it" wait_for 15 agent_active
check "the waiter reported the failed activation" \
    bash -c "sudo journalctl -u $SERVICE-activation --no-pager -q -o cat | grep -q 'activation_failed'"

echo "F. a held agent's finished poll sleep stays visible"
fresh_checkout; ensure >/dev/null
m=$(agent_pid)
kill -STOP "$m"
zombie_sleep() { local kids; kids=$(pgrep -P "$m" | xargs -r ps -o stat=,comm= -p); [[ "$kids" =~ Z[^$'\n']*sleep ]]; }
check "an exited poll sleep is listed as a zombie child" wait_for 5 zombie_sleep
kill -CONT "$m"

echo "G. three concurrent reconciliations under a stream of triggers"
fresh_checkout; ensure >/dev/null; wait_for 10 code_current
echo '# newer agent code' >> "$D/scripts/update-agent.sh"
( for i in $(seq 20 29); do trigger "9.9.$i" "$i"; sleep 0.3; done ) &
for n in 1 2 3; do
    ( ensure > "$WORK/caller$n.out" 2>&1; echo $? > "$WORK/caller$n.rc" ) &
    sleep 0.5
done
wait
check "every caller succeeds" test "$(cat "$WORK"/caller*.rc | tr -d '\n')" = 000
check "the trigger queue drains" wait_for 60 requests_empty
check "any scheduled waiter finishes" wait_for 60 waiter_idle
settled() { [ "$(grep -c '^start' "$D/updates.log")" = "$(grep -c '^finish' "$D/updates.log")" ]; }
check "no update was cut off" wait_for 10 settled
check "all ten updates ran" test "$(grep -c '^finish 9.9.2' "$D/updates.log")" -eq 10
check "the agent runs the new code" wait_for 10 code_current

echo "H. SIGTERM while a reconciliation holds the agent"
fresh_checkout; ensure >/dev/null; wait_for 10 code_current
echo '# newer agent code again' >> "$D/scripts/update-agent.sh"
mkdir -p "$WORK/slow"
printf '#!/bin/bash\nsleep 3\nexec %s "$@"\n' "$(command -v pgrep)" > "$WORK/slow/pgrep"; chmod +x "$WORK/slow/pgrep"
m=$(agent_pid)
(cd "$D" && PATH="$WORK/slow:$PATH" ./bioaf.real ensure-update-agent </dev/null >/dev/null 2>&1) &
check "the caller holds the agent" wait_for 10 agent_held "$m"
pkill -TERM -f 'bioaf.real ensure-update-agent'
check "the agent is resumed" wait_for 10 agent_not_held "$m"
check "and was not restarted" test "$(agent_pid)" = "$m"
wait

echo "I. systemd cannot report the agent's state"
mkdir -p "$WORK/broken"
printf '#!/bin/bash\n[ "$1" = show ] && exit 1\nexec %s "$@"\n' "$(command -v systemctl)" > "$WORK/broken/systemctl"
chmod +x "$WORK/broken/systemctl"
sha=$(unit_sha); m=$(agent_pid)
check "ensure-update-agent fails" bash -c "! (cd '$D' && PATH='$WORK/broken:$PATH' ./bioaf.real ensure-update-agent </dev/null >/dev/null 2>&1)"
check "the unit is unchanged" test "$(unit_sha)" = "$sha"
check "the agent was not restarted" test "$(agent_pid)" = "$m"

echo
if [ "$FAILURES" -gt 0 ]; then
    echo "FAILED: $FAILURES check(s)"
    exit 1
fi
echo "All checks passed."
