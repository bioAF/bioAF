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

# Resolve a python interpreter (gcloud bundles one; macOS ships python3).
# Fails closed -- callers should treat empty stdout as "unknown".
_bioaf_quota_python() {
    if command -v python3 >/dev/null 2>&1; then
        echo python3
    elif command -v python >/dev/null 2>&1; then
        echo python
    fi
}

# Read the current effective limit for a quota.
#   $1 service        e.g. compute.googleapis.com
#   $2 quota_id       e.g. CPUS-ALL-REGIONS-per-project
#   $3 project        GCP project id
#   $4 region (opt)   for regional quotas; omit for project-scoped
# Prints the limit as an integer string, or "0" if the quota cannot be
# read or the requested region is not present.
bioaf_quota_get_current() {
    local service="$1" quota_id="$2" project="$3" region="${4:-}"
    local out
    out=$(gcloud alpha quotas info describe "$quota_id" \
            --service="$service" \
            --project="$project" \
            --format=json 2>/dev/null) || { echo 0; return 0; }
    local py
    py=$(_bioaf_quota_python)
    if [ -z "$py" ]; then echo 0; return 0; fi
    REGION="$region" "$py" -c '
import json, os, sys
try:
    data = json.loads(sys.stdin.read() or "{}")
except Exception:
    print("0"); sys.exit(0)
region = os.environ.get("REGION", "")
for entry in (data.get("dimensionsInfos") or []):
    dims = entry.get("dimensions") or {}
    if region:
        if dims.get("region") == region:
            print((entry.get("details") or {}).get("value", "0")); sys.exit(0)
    else:
        if not dims:
            print((entry.get("details") or {}).get("value", "0")); sys.exit(0)
print("0")
' <<<"$out"
}
