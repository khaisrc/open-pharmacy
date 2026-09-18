#!/usr/bin/env bash
set -uo pipefail
source "$SUITE_DIR/lib/http.sh"

req GET /health/
assert_status 200 "health check answers 200"
assert_jq_eq '.status' 'ok' "health check reports status ok"

req_anon GET /health/
assert_status 200 "health check is reachable without credentials"
assert_jq_eq '.status' 'ok' "unauthenticated health check reports status ok"
