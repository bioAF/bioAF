#!/usr/bin/env bash
# Bring the update agent's unit and code up to date once the agent is idle. Run by ./bioaf as a
# transient systemd unit (bioaf-update-agent-activation), outside the agent's cgroup and as the
# checkout's account, when the agent was busy with an update: restarting it then would stop that
# update. Every few seconds it tries the same held reconciliation ./bioaf ensure-update-agent runs
# (verify, install, restart, health check, rollback), until the agent is between polls.
#
# The logic lives in ./bioaf. This file exists so the transient unit's command is one line with no
# $ or %: systemd stores that command in a unit file and reads it back on every daemon-reload.

set -uo pipefail
# shellcheck source=bioaf
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/bioaf" >/dev/null
set +e

update_agent_identity || exit 1
waited=0
limit="${UPDATE_AGENT_WAIT_LIMIT:-86400}"
while :; do
    update_agent_reconcile_held
    status=$?
    [ "$status" = "$UPDATE_AGENT_BUSY" ] || exit "$status"
    if [ "$waited" -ge "$limit" ]; then
        echo "The update agent stayed busy for ${limit}s; run ./bioaf start to bring it up to date."
        exit 1
    fi
    sleep 2
    waited=$((waited + 2))
done
