### Networking Settings

- Settings -> Networking now matches the real bioAF install topology (single
  VM behind nginx) instead of the GKE Ingress topology assumed by the
  initial release. The certificate status pill is now driven by the actual
  on-disk nginx certificate: if `/etc/nginx/certs/tls.crt` is currently
  valid and its Common Name or a SubjectAltName matches the configured
  FQDN, the card shows the cert as Active; otherwise it shows as Not
  requested so the operator knows to install one.
- Clicking **Request certificate** now returns an operator-facing
  instruction with the exact certbot command to run on the host, rather
  than recording a fake "Provisioning" state that never advances.
  Automated, in-UI Let's Encrypt issuance is tracked as a follow-up that
  requires a host-side cert agent on the VM.
- The unused GKE-targeted helm scaffolding (`ManagedCertificate` template
  and the `networking.gke.io` RBAC role) has been removed from the chart.
- Internal: removes `KubernetesNetworkingApplier` and replaces it with
  `VmNginxApplier`. `get_networking_applier()` no longer branches on
  `BIOAF_COMPUTE_MODE`. The applier Protocol is unchanged, so the Settings
  page, the reachability test, and the HTTPS-enforcement flag are
  unaffected.
- The cert status pill now reflects the actual on-disk certificate at every
  page load: a stale "Provisioning" cached from an earlier click on a
  previous build is cleared and replaced with the truthful state as soon as
  the page loads.
- When the cert request returns the manual-setup instructions (current
  default on VM installs until the host-side cert agent ships), the UI
  shows a structured "Manual setup required" callout with the certbot
  command in a code block and a Copy button, instead of the previous
  single-line red `ApiError: ...` toast.
- New installs are now ready for Let's Encrypt out of the box:
  - `install-gcp.sh` installs `certbot` and creates `/var/www/letsencrypt`
    on first boot of the VM, alongside the existing Docker install.
  - `./install.sh prepare-letsencrypt` installs certbot + creates the
    webroot on any Debian/Ubuntu host. `./install.sh` (full install) also
    runs this best-effort.
  - `docker/nginx.conf` now serves `/.well-known/acme-challenge/` over HTTP
    on port 80 from a new bind mount, so `certbot certonly --webroot -w
    /var/www/letsencrypt` works without stopping nginx.
  - The applier's instruction text has been tightened to include the
    explicit `cp /etc/letsencrypt/live/<fqdn>/{fullchain,privkey}.pem
    docker/certs/...` commands and the `./bioaf restart` that picks up
    the new cert.
- The backend container now mounts `docker/certs` so the networking
  applier can read the live nginx cert. Without this mount the cert pill
  stayed at "Not requested" even after the operator installed a valid
  Let's Encrypt cert.
- `https_enforced` is now driven by the install topology rather than a
  DB flag the operator must toggle: VmNginxApplier reports it as always
  true because nginx.conf unconditionally redirects HTTP to HTTPS. The
  cert card now shows the green "HTTPS is enforced" indicator immediately
  and hides the "Apply HTTPS and restart" button (which had no effect on
  VM installs anyway).
