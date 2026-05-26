### Networking Settings

- New Settings -> Networking page lets an operator configure the public hostname
  and domain, run a loopback reachability test against the FQDN, request a
  Google-managed TLS certificate, and enforce HTTPS with a controlled restart.
  bioAF does not manage DNS: the operator points an A record at the cluster IP
  and bioAF then verifies routing with a nonce-based self-check before the next
  step unlocks. Each action is audit-logged and gated by
  `infrastructure:edit`.
