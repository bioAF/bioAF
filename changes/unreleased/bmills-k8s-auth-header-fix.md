### Fixes

- Fix notebook launches failing with 401 Unauthorized after a fresh install
  or compute teardown + redeploy. The out-of-cluster Kubernetes client was
  setting the bearer token via `Configuration.api_key`, which the current
  `kubernetes-python` release silently drops when no OpenAPI security scheme
  references it. Every request went out anonymously and GKE responded 401.
  The client now installs the `Authorization` header on the `ApiClient`
  directly.
- Invalidate the cached Kubernetes API client when `platform_config` reports
  a different cluster endpoint or CA cert than what the cached client was
  built for. Previously a cluster teardown and redeploy would not take
  effect until the 45-minute GCP access token TTL elapsed, so notebook
  launches in that window kept hitting the old (dead) cluster and 401ing.
