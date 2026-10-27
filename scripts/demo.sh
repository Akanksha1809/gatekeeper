#!/usr/bin/env bash
# Scripted walkthrough of Gatekeeper's core features against a running
# docker-compose stack. Used to record docs/demo.gif (see
# scripts/record_demo.sh) but is also just a nice way to see the gateway
# work: `./scripts/demo.sh` against `docker-compose up -d`.
set -euo pipefail

BASE="${GATEKEEPER_URL:-http://127.0.0.1:8000}"
ADMIN_USER="${GATEKEEPER_ADMIN_USERNAME:-admin}"
ADMIN_PASS="${GATEKEEPER_ADMIN_PASSWORD:-change-me-admin-password}"

# Raw escapes rather than tput: tput needs a terminfo entry for $TERM, which
# isn't guaranteed to exist (e.g. when recording headlessly), and silently
# produces empty strings on failure -- these always work.
bold=$'\033[1m'
dim=$'\033[2m'
green=$'\033[32m'
cyan=$'\033[36m'
reset=$'\033[0m'

step() {
    echo
    echo "${bold}${cyan}# $1${reset}"
    sleep 0.6
}

run() {
    echo "${dim}\$ ${*}${reset}"
    sleep 0.4
    eval "$@"
    sleep 1.1
}

printf '\n\n\n'
echo "${bold}${green}Gatekeeper -- API Gateway and Traffic Control Platform${reset}"
sleep 1.5

step "1. Liveness and readiness"
run "curl -s $BASE/healthz | jq ."
run "curl -s $BASE/readyz | jq ."

step "2. Bootstrap an API key (admin-only, HTTP Basic auth)"
run "curl -s -u $ADMIN_USER:$ADMIN_PASS -X POST $BASE/admin/api-keys \\
  -H 'Content-Type: application/json' \\
  -d '{\"client_name\": \"demo-client\", \"tier\": \"standard\"}' | tee /tmp/gk_demo_key.json | jq ."

API_KEY=$(jq -r .api_key /tmp/gk_demo_key.json)

step "3. Authenticated request through the gateway -> healthy-service"
run "curl -s -i -H \"X-API-Key: $API_KEY\" $BASE/api/users/42 | head -n 12"

step "4. Missing API key is rejected"
run "curl -s $BASE/api/users/42 | jq ."

step "5. Burst traffic trips the per-route rate limit (429 + Retry-After)"
run "seq 1 80 | xargs -P 60 -I{} curl -s -o /dev/null -w '%{http_code}\\n' \\
  -H \"X-API-Key: $API_KEY\" $BASE/api/users/42 | sort | uniq -c"

step "6. Forcing repeated upstream failures trips the circuit breaker"
run "curl -s -o /dev/null -w 'attempt 1 -> %{http_code}\\n' -H \"X-API-Key: $API_KEY\" \"$BASE/api/demo/flaky/ping?fail=true&status=503\""
run "curl -s -o /dev/null -w 'attempt 2 -> %{http_code}\\n' -H \"X-API-Key: $API_KEY\" \"$BASE/api/demo/flaky/ping?fail=true&status=503\""
run "curl -s -o /dev/null -w 'attempt 3 -> %{http_code}\\n' -H \"X-API-Key: $API_KEY\" \"$BASE/api/demo/flaky/ping?fail=true&status=503\""
run "curl -s -H \"X-API-Key: $API_KEY\" $BASE/api/demo/flaky/ping | jq ."

step "7. Gateway timeout against a slow upstream (route read-timeout: 2s)"
run "curl -s -H \"X-API-Key: $API_KEY\" $BASE/api/demo/slow/ping | jq ."

step "8. Non-sensitive operational diagnostics"
run "curl -s -u $ADMIN_USER:$ADMIN_PASS $BASE/admin/diagnostics | jq ."

echo
echo "${bold}${green}That's Gatekeeper: auth, rate limiting, retries, circuit breaking,${reset}"
echo "${bold}${green}structured errors, and diagnostics -- all config-driven. See README.md.${reset}"
sleep 2
