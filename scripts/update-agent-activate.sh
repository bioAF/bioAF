#!/usr/bin/env bash
# Restart the update agent once it is idle. Run by ./bioaf as a transient systemd unit
# (bioaf-update-agent-activation), outside the agent's cgroup, when the agent must pick up new code
# or a new unit while it is running an update: restarting it then would stop that update.
#
# Usage: update-agent-activate.sh <agent main PID>
#
# The logic lives in ./bioaf (update_agent_restart_when_idle). This file exists so the transient
# unit's command is one line with no $ or %: systemd stores that command in a unit file and reads it
# back on every daemon-reload.

set -uo pipefail
# shellcheck source=bioaf
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/bioaf" >/dev/null
set +e
update_agent_restart_when_idle "$1"
