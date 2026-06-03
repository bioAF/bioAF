### Infrastructure

- Surface the GCE machine type in the Infrastructure > Components cluster
  configuration dropdowns and add e2-family options to the Interactive Pool.
  The pool was previously locked to the n2 family, which has been repeatedly
  stocking out in us-central1 for 8-vCPU scale-ups. Each option now reads
  `<machine-type> - <cpu/memory> - <description>`, and the e2 variants are
  tagged "(high availability)" because the e2 family spills over onto
  whichever generation of host has room. e2-standard-8 is the new
  recommended interactive size.

### Fixes

- Fix notebook launches failing with 401 Unauthorized after a fresh install
  or compute teardown + redeploy. The out-of-cluster Kubernetes client was
  setting the bearer token via `Configuration.api_key`, which the current
  `kubernetes-python` release silently drops when no OpenAPI security scheme
  references it. Every request went out anonymously and GKE responded 401.
  The notebook, cellxgene, and compute adapters now install the
  `Authorization` header on the `ApiClient` directly via `set_default_header`.
- Update the three raw-httpx callers (notebook session sync, notebook
  loadBalancer-IP poller, cellxgene loadBalancer-IP poller) to read the
  bearer token from `ApiClient.default_headers["Authorization"]` instead of
  `Configuration.api_key`. The previous reads would `IndexError` once the
  setter stopped populating `api_key`.
- Invalidate the cached Kubernetes API client for the notebook adapter when
  `platform_config` reports a different cluster endpoint or CA cert than
  what the cached client was built for. Previously a cluster teardown and
  redeploy would not take effect until the 45-minute GCP access token TTL
  elapsed, so notebook launches in that window kept hitting the old (dead)
  cluster and 401ing.
