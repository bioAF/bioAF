### Updating from an earlier release (one-time retry)

- If you update from v0.13.0 through v2026.11.21, the first attempt only prepares the new update helper and
  stops without changing bioAF or your work nodes. Your update page may show **Update failed: encryption key
  bootstrap failed** or **Update failed: Update process exited with code 1**; the new helper then shows
  **The update helper is ready. bioAF and your work nodes are still running. Retry the update to continue.**
  Nothing is wrong with your encryption key: do not change any keys. Select **Install Update** again (or run
  `./bioaf update` again). Releases before v0.13.0 need support before updating while work nodes run.
- Before replacing the backend, an update now checks that no work node launched by an earlier release is
  still running, because those nodes' save keys live only in the running backend. If one might be, the update
  waits: **Some older work nodes are still running. Finish saving your work and stop them when convenient,
  then retry the update. bioAF will leave them running.** Use **View work nodes** and **Check again** on
  Settings > Platform Info. Nothing is stopped for you. New work node launches pause for a few minutes while
  the backend is replaced, and bioAF does not answer for about a minute during the final check (the restart
  warning is shown first). If an update is stopped partway, bioAF resumes on its own within seconds and
  shows **The update stopped before it finished. bioAF is running again and nothing was changed. Retry the
  update.**

### Work nodes

- Stopping a work node now saves `/outputs` before the VM is deleted. If saving or deletion cannot be
  confirmed, the node is kept and shows **Stop failed** with a plain reason and **Retry stop**; it stays
  connectable while its files are unsaved. Stop no longer searches home folders for scripts.
- bioAF no longer stops a running work node on its own because it has not reported in; nodes run until
  their owner stops them. The work node **Idle Timeout** in Workbench Settings is paused until nodes can
  report activity.
- Work nodes launched after this release keep their save credentials across backend restarts and updates,
  use a server key bioAF records at launch, and are saved over the private network.
- Your bioAF Git key is also your work node login key. **Download key** on your profile, then
  `ssh -i ~/.ssh/bioaf_id_rsa <username>@<address>`. bioAF will not replace the key while your work nodes may
  still need it.
- Git cloning on work nodes, RStudio sessions and custom pipelines verifies GitHub with keys shipped in
  each release instead of trusting whatever server answers. Another Git server needs its keys approved with
  `./bioaf import-git-known-hosts <file> "<source>"`.
- The unsupported SLURM status poll and its SSH connection were removed. Stored SLURM job records are kept.

### Security and SSH access (Settings)

- New page **Settings > Security and SSH access**, all optional: work node ranges (password or key inside a
  range, key only elsewhere, for work nodes launched afterwards), an SSH-only filter for the application
  server, and **Cloud recovery access is always allowed. Your cloud-account permissions are still
  required.**, with **Configured (automatic checks)** or **Login tested** evidence and **Restore SSH access**,
  a link to this server's page in your cloud console.
- Applying the SSH filter is refused, with the rule named and current access kept, when an existing
  firewall or security group rule would let SSH bypass it. Only one filter change (or other
  infrastructure operation) runs at a time; one that is interrupted is undone before the next.
- The installers prepare cloud recovery access, can **Require this computer's SSH key for administrator
  access?** (password login is turned off for the administrator; other SSH keys already allowed for that
  account keep working), check recovery login with a temporary key, and save a non-secret access sheet with
  the console link in `~/.bioaf/`.
- After updating an AWS installation, re-apply the compute stack (Infrastructure) so new work nodes are
  saved over the private network; until then they keep the existing route.
