# Connectivity guard for bats tests that reach the real ghcr.io.
#
# ghcr_reachable succeeds when ghcr.io answers with any HTTP status (its /v2/
# endpoint answers 401 without credentials). curl reports 000 when no response
# arrives (DNS, connection or timeout failure), so 000 means unreachable and the
# network tests skip instead of failing.
ghcr_reachable() {
    local code
    code=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' "https://ghcr.io/v2/" 2>/dev/null) || true
    [[ "$code" =~ ^[1-5][0-9][0-9]$ ]]
}
