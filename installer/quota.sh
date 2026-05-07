#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# bioAF GCP quota auto-request helper.
#
# Sourced by install-gcp.sh. Owns the logic for checking and requesting
# Cloud Quotas API increases for the metrics bioAF needs at install time:
#
#   - CPUS-ALL-REGIONS-per-project       (64)   compute pool headroom
#   - SSD-TOTAL-GB-per-project-region    (1024) GKE pd-balanced + pd-ssd
#   - DISKS-TOTAL-GB-per-project-region  (2048) Packer build & HDD work nodes
#
# Designed to be testable in isolation: every external call (gcloud, curl)
# goes through the user's PATH so test stubs can intercept it. There is no
# global state; callers pass project / region in.
# ---------------------------------------------------------------------------

# Emit one row per desired quota:
#   <service>\t<quotaId>\t<scope>\t<preferredValue>
# Scope is "project" for project-level metrics and "region" for those that
# are dimensioned by region.
bioaf_quota_targets() {
    printf 'compute.googleapis.com\tCPUS-ALL-REGIONS-per-project\tproject\t64\n'
    printf 'compute.googleapis.com\tSSD-TOTAL-GB-per-project-region\tregion\t1024\n'
    printf 'compute.googleapis.com\tDISKS-TOTAL-GB-per-project-region\tregion\t2048\n'
}
