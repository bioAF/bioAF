### Fixes

- Installing and managing bioAF now checks the server's own Python before
  changing anything. `./install.sh`, `./bioaf setup`, `start`, `restart`,
  `build`, `update` and the update agent stop with a clear message and
  installation guidance when `python3` is missing, cannot run, is older than
  3.10 or lacks part of its standard library. Before, the prerequisite check
  passed without Python and the command failed partway through. `help`,
  `status`, `stop`, `logs` and commands that run inside the backend container
  still work without it.
- When the update agent cannot start for this reason, it leaves pending
  updates and the last update status untouched and explains why in
  `sudo journalctl -u bioaf-update-agent`.
- `./bioaf help` and its version errors now show release versions in the
  current `YYYY.M.N` form (for example `./bioaf update 2026.11.21`) instead of
  old `0.8.1`-style examples. Older version pins are still accepted.

### Upgrade notes

- Servers whose `python3` is older than 3.10 (for example Ubuntu 20.04,
  Debian 11, RHEL 9 or Amazon Linux 2023) need Python 3.10 or newer before
  updating. Installing the distribution's `python3` package does not raise
  it there: install a newer interpreter (for example `python3.11`) and link
  it as `/usr/local/bin/python3`, then restart the update agent. On such a
  server the update stops before the services change and reports
  "encryption key bootstrap failed"; the update log names the Python problem
  and these steps.
