### Networking Settings

- New **Settings -> Networking** page lets an operator configure the public
  hostname and domain, verify the FQDN routes to this instance, request a
  Google-managed TLS certificate, and enforce HTTPS with a controlled
  service restart. The page is a three-card wizard: each card unlocks the
  next based on the underlying state, so the operator cannot request a cert
  before the FQDN is verified or enforce HTTPS before the cert is active.
- The reachability check is a loopback through public DNS: bioAF writes a
  one-time nonce, then calls itself at `https://<fqdn>/api/v1/settings/networking/self-check`
  and confirms the response carries the nonce. This proves not just that DNS
  resolves, but that requests actually return to this bioAF instance.
- HTTPS is attempted first (certificate verification disabled, since the
  cert may not yet cover this hostname), with an HTTP fallback for the
  pre-enforcement state.
- Reachability failures are translated into human-readable detail with a
  next action: DNS resolution failed ("wait for negative DNS cache to
  expire, then retry"), connection refused ("check the Ingress is routing
  port 443 for this FQDN"), TLS handshake failed ("the cert may not yet
  cover this hostname"), and so on. The raw libc error strings no longer
  leak into the UI.
- Clicking **Refresh status** on the certificate card now shows a
  "Refreshing..." state on the link, briefly outlines the status pill on
  completion, and stamps a "Last checked HH:MM:SS" line so the operator can
  see the poll happened even when the status itself did not change.
- bioAF still does not manage DNS or external OAuth/SSO callback URLs.
  Those remain the operator's responsibility and are flagged on the page.
- All mutating actions are gated by the `infrastructure:edit` permission
  and recorded in the audit log.
