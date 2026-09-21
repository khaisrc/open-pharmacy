#!/usr/bin/env bash
set -uo pipefail
source "$SUITE_DIR/lib/http.sh"

section "the health check"
req GET /health/
assert_status 200 "it answers 200"
assert_jq_eq '.status' 'ok' "it reports a status of ok"

section "the health check needs no credentials"
# Whatever watches this API has to be able to ask whether it is up without
# holding an account on it.
req_anon GET /health/
assert_status 200 "it still answers 200"
assert_jq_eq '.status' 'ok' "it still reports a status of ok"
