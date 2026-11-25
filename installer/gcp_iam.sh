#!/usr/bin/env bash
# GCP IAM call retries for install-gcp.sh.
#
#   retry_iam <command...>
#       Runs a gcloud IAM command, retrying only transient failures:
#       - service account propagation lag ("... does not exist"): up to 3 attempts, 5 s apart;
#       - rate limits (HTTP 429, RESOURCE_EXHAUSTED, quota exceeded, rateLimitExceeded): up to 6
#         attempts, waiting 5, 10, 20, 40, 60 s plus up to a quarter more at random, so parallel
#         installs do not retry in step;
#       - anything else (permission, configuration, validation): fails at once.
#       The command's output is printed on success; each failure's output goes to stderr.

_bioaf_iam_rate_limited() {
    printf '%s' "$1" | grep -qiE 'RESOURCE_EXHAUSTED|Quota exceeded|rateLimitExceeded|Too Many Requests|code[: ]+429|HTTPError 429|\b429\b'
}

retry_iam() {
    local attempt=1 rc output delay jitter
    while :; do
        # Capture rc immediately after the assignment; an `if` around it would collapse $? to 0.
        output=$("$@" 2>&1)
        rc=$?
        if [ "$rc" = 0 ]; then
            [ -n "$output" ] && printf '%s\n' "$output"
            return 0
        fi
        printf '%s\n' "$output" >&2
        if printf '%s' "$output" | grep -q "does not exist" && [ "$attempt" -lt 3 ]; then
            printf '  (propagation lag detected; retrying in 5s, attempt %s/3)\n' "$((attempt + 1))" >&2
            sleep 5
        elif _bioaf_iam_rate_limited "$output" && [ "$attempt" -lt 6 ]; then
            delay=$((5 * (1 << (attempt - 1))))
            [ "$delay" -gt 60 ] && delay=60
            jitter=$((RANDOM % (delay / 4 + 1)))
            printf '  (Google Cloud rate limit; retrying in %ss, attempt %s/6)\n' "$((delay + jitter))" "$((attempt + 1))" >&2
            sleep "$((delay + jitter))"
        else
            return "$rc"
        fi
        attempt=$((attempt + 1))
    done
}
