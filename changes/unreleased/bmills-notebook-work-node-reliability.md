### Notebook sessions

- Open Jupyter and RStudio sessions through a protected address on the same server (port 8443) with a
  one-time sign-in from bioAF. Only the person who started a session can open it. New sessions no longer
  get a public address, and Jupyter now has its own sign-in token behind the protected address.
- Run Jupyter as an unprivileged user, and keep notebook code from using the cluster credential that let
  one session reach other sessions in the shared notebook namespace.
- Build the built-in notebook image and notebook environments on one pinned base with a checksum-verified
  RStudio Server. A build now fails unless Jupyter runs a Python cell as the notebook user and, for
  environments with R, R runs and RStudio serves its sign-in page. Each environment version records what
  its build proved; older versions show as unverified.
- Show a session as running only once Jupyter or RStudio itself answers. Refuse a launch of a tool the
  environment was proven not to have, and fail an image without the tool with a plain reason.
- Add `./bioaf contain-notebook-exposure [--apply]` to remove the public address of sessions started
  before this release without stopping them or deleting files.

### HTTPS

- Request and automatically renew a Let's Encrypt certificate for a public IP-only installation
  (`./bioaf tls-issue-ip`). The installers keep the public address static and open port 8443.
- Upload a certificate from your organization's own certificate authority for a private installation in
  Settings > Networking.

### Work Nodes

- Show the Experiment a Work Node was launched for in the list and its details; saved outputs carry the
  same Experiment.
- Check each running Work Node about every five minutes over its verified connection. Last Heartbeat is
  the last successful check, and a Reporting row says whether checks are succeeding. Missing reports never
  stop a Work Node.

### Fixes

- Refuse setup and invitation tokens wherever a signed-in session is required.
