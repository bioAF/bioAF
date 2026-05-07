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

# Generate a stable-but-unique QuotaPreference id. The Cloud Quotas API
# requires it to be lowercase alphanumeric + hyphens, max 63 chars. We
# combine a `bioaf` prefix, the lowercased quota id, and a short epoch
# suffix so re-running the installer creates a fresh preference instead of
# colliding with a previous run.
_bioaf_quota_pref_id() {
    local quota_id="$1"
    local lower
    lower=$(printf '%s' "$quota_id" | tr '[:upper:]_' '[:lower:]-' | tr -cd 'a-z0-9-')
    # Trim to leave room for prefix + suffix (63 char limit).
    lower="${lower:0:40}"
    printf 'bioaf-%s-%s' "$lower" "$(date +%s)"
}

# Submit a QuotaPreference to bump a quota.
#   $1 service        e.g. compute.googleapis.com
#   $2 quota_id       e.g. CPUS-ALL-REGIONS-per-project
#   $3 project        GCP project id
#   $4 preferred_value (integer string)
#   $5 region (opt)   for regional quotas; omit for project-scoped
# Prints the preference id (the last path segment of `name`) on success,
# empty string on failure.
bioaf_quota_request_increase() {
    local service="$1" quota_id="$2" project="$3" preferred="$4" region="${5:-}"
    local pref_id
    pref_id=$(_bioaf_quota_pref_id "$quota_id")
    local token
    token=$(gcloud auth print-access-token 2>/dev/null) || return 1
    local body
    if [ -n "$region" ]; then
        body=$(printf '{"service":"%s","quotaId":"%s","quotaConfig":{"preferredValue":"%s"},"dimensions":{"region":"%s"}}' \
            "$service" "$quota_id" "$preferred" "$region")
    else
        body=$(printf '{"service":"%s","quotaId":"%s","quotaConfig":{"preferredValue":"%s"}}' \
            "$service" "$quota_id" "$preferred")
    fi
    local url="https://cloudquotas.googleapis.com/v1/projects/${project}/locations/global/quotaPreferences?quotaPreferenceId=${pref_id}"
    local resp
    resp=$(curl -fsSL -X POST \
        -H "Authorization: Bearer ${token}" \
        -H "Content-Type: application/json" \
        --data "$body" \
        "$url" 2>/dev/null) || return 1
    local py
    py=$(_bioaf_quota_python)
    if [ -z "$py" ]; then
        # Without python we fall back to the id we generated -- the API uses
        # exactly that as the last segment of `name`.
        printf '%s\n' "$pref_id"
        return 0
    fi
    "$py" -c '
import json, sys
try:
    data = json.loads(sys.stdin.read() or "{}")
except Exception:
    print(""); sys.exit(0)
name = data.get("name") or ""
print(name.rsplit("/", 1)[-1] if name else "")
' <<<"$resp"
}

# Check the current state of a previously-submitted QuotaPreference.
#   $1 project        GCP project id
#   $2 pref_id        preference id returned by bioaf_quota_request_increase
# Prints exactly one of:
#   approved -- grantedValue matches preferredValue, change is live
#   pending  -- reconciling, or awaiting Google review
#   error    -- curl/parse failure (caller may retry)
bioaf_quota_poll() {
    local project="$1" pref_id="$2"
    local token
    token=$(gcloud auth print-access-token 2>/dev/null) || { echo error; return 0; }
    local url="https://cloudquotas.googleapis.com/v1/projects/${project}/locations/global/quotaPreferences/${pref_id}"
    local resp
    resp=$(curl -fsSL \
        -H "Authorization: Bearer ${token}" \
        "$url" 2>/dev/null) || { echo error; return 0; }
    local py
    py=$(_bioaf_quota_python)
    if [ -z "$py" ]; then echo error; return 0; fi
    "$py" -c '
import json, sys
try:
    data = json.loads(sys.stdin.read() or "{}")
except Exception:
    print("error"); sys.exit(0)
qc = data.get("quotaConfig") or {}
preferred = qc.get("preferredValue")
granted = qc.get("grantedValue")
if preferred is not None and granted is not None and str(granted) == str(preferred):
    print("approved"); sys.exit(0)
# Otherwise: either reconciling or sitting in human review queue. Both are
# "pending" from the installers point of view -- we surface a single
# "Google needs to approve this, this is normal" message to the user.
print("pending")
' <<<"$resp"
}

# Walk every desired quota; for each one that is below its target, request
# an increase and poll briefly for an automatic approval. Notifies the user
# at every step. Never aborts on a single-quota failure -- the install
# should still complete; pipeline launches will surface the exact reason
# later if the quota was actually denied.
#
# Inputs:
#   $1 project        GCP project id
#   $2 region         GCE region used by bioAF (regional quotas only)
# Knobs (env):
#   BIOAF_QUOTA_POLL_INTERVAL  seconds between polls   (default 3)
#   BIOAF_QUOTA_POLL_TIMEOUT   max wait per quota      (default 30)
bioaf_quota_ensure_all() {
    local project="$1" region="$2"
    local interval="${BIOAF_QUOTA_POLL_INTERVAL:-3}"
    local timeout="${BIOAF_QUOTA_POLL_TIMEOUT:-30}"

    echo ""
    echo "  Checking GCP quotas needed by bioAF (CPUs and disk)..."
    echo ""

    local row
    while IFS=$'\t' read -r service quota_id scope preferred; do
        local r=""
        [ "$scope" = "region" ] && r="$region"
        local current
        current=$(bioaf_quota_get_current "$service" "$quota_id" "$project" "$r")
        # Treat non-numeric as 0 so we always proceed to ask.
        case "$current" in (''|*[!0-9]*) current=0 ;; esac
        if [ "$current" -ge "$preferred" ]; then
            echo "  ${quota_id}: current limit ${current} already meets target ${preferred}. Skipping."
            continue
        fi
        echo ""
        echo "  ${quota_id}: current ${current}, target ${preferred}."
        echo "  Requesting an automatic quota increase from Google..."
        local pref_id
        pref_id=$(bioaf_quota_request_increase "$service" "$quota_id" "$project" "$preferred" "$r")
        if [ -z "$pref_id" ]; then
            echo "  Could not submit the quota request. Continuing without auto-bump --"
            echo "  if the limit blocks pipeline runs later, you can request the bump"
            echo "  in the Cloud Console: IAM & Admin -> Quotas."
            continue
        fi
        echo "  Submitted (preference id: ${pref_id}). Waiting up to ${timeout}s for"
        echo "  automatic approval..."
        local elapsed=0
        local status="pending"
        while [ "$elapsed" -lt "$timeout" ]; do
            status=$(bioaf_quota_poll "$project" "$pref_id")
            [ "$status" = "approved" ] && break
            sleep "$interval"
            elapsed=$((elapsed + interval))
        done
        case "$status" in
            approved)
                echo "  ${quota_id}: granted automatically (now ${preferred})."
                ;;
            *)
                echo "  ${quota_id}: not auto-approved within ${timeout}s."
                echo "  Google needs to review and approve this quota increase."
                echo "  This is normal -- approval typically takes 1-2 business days."
                echo "  bioAF install will continue. Pipeline launches that depend on"
                echo "  this quota will fail until approval; the run log will surface"
                echo "  the underlying QUOTA_EXCEEDED reason when that happens."
                ;;
        esac
    done < <(bioaf_quota_targets)

    echo ""
}
