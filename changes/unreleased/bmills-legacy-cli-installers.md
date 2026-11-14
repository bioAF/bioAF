### Updates

- The update agent now treats every file in `update-requests/` as untrusted. A trigger is read once
  without following links and must be a regular JSON file with a `version` like `1.2.3` (optional
  integer `upgrade_id`, optional string `requested_at`); anything else is removed without running an
  update, and its name is never logged.
- `update-status/current.json` is now written by both `./bioaf update` and the agent as one complete
  JSON record, staged in the private `host-state/` directory and renamed into place at mode 0644. A
  link planted in `update-status/` is replaced, never written through.
- The agent's log moves from `update-status/agent.log` to `host-only/agent.log` (private, never
  mounted into the backend). If it cannot be opened safely the agent logs to its service output
  (`sudo journalctl -u bioaf-update-agent`) and keeps processing updates. The old file is no longer
  written and can be deleted.
- `./bioaf setup`, `start` and every update (including one started from an older release) now bring
  the `bioaf-update-agent` systemd unit to this release's version: verified with `systemd-analyze`,
  reloaded, restarted, and restored if the agent does not stay up. A unit you changed is left alone
  and reported; add `# bioaf-managed: operator` to keep managing it. An agent running older code is
  restarted, but never during an update it is running: it restarts once that update has finished.
  `setup` now configures the agent after any `--version` checkout.

### Installers

- `install-aws.sh` now accepts `--region VALUE` and `--ssh-cidr VALUE` as documented (the `=` forms
  still work) and rejects missing, malformed or unknown options before doing anything. The region
  (from the flag, your AWS configuration or the prompt) and the SSH range are checked before use.
- If `install-aws.sh` cannot detect your public IP address it now stops and asks for `--ssh-cidr`
  instead of opening SSH to every address. An explicit `--ssh-cidr 0.0.0.0/0` still works. When it
  reuses an existing security group it lists any broader SSH rules that group keeps.
- `install-gcp.sh` rejects unknown options.
- Both installers, run as `curl ... | bash`, download their helper files and never use files from
  the directory you run them in. A helper that fails to download completely stops the install. To
  install a release instead of `main`, pass the same ref with `--ref` (or `BIOAF_INSTALLER_REF`); see
  "Pinned installer versions" in the deployment guide.
