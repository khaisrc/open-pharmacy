#!/usr/bin/env bash
set -uo pipefail
source "$SUITE_DIR/lib/http.sh"
source "$SUITE_DIR/lib/gen.sh"

section "every collection requires credentials"
for path in /addresses/ /medications/ /patients/ /prescribers/ /prescriptions/; do
  req_anon GET "$path"
  assert_status 401 "GET ${path} is 401 without credentials"
  assert_error_code "not_authenticated" "GET ${path} anonymously reports not_authenticated"
done

section "writes require credentials too"
req_anon POST /addresses/ '{"line1":"1 A St","city":"Portland","state":"OR","postal_code":"97205"}'
assert_status 401 "POST /addresses/ is 401 without credentials"

req_anon DELETE "/patients/${MISSING_UUID}/"
assert_status 401 "DELETE is 401 without credentials"

section "credentials are actually checked"
req_as "$API_USER" "definitely-not-the-password" GET /patients/
assert_status 401 "a wrong password is rejected"
assert_error_code "authentication_failed" "a wrong password reports authentication_failed"

req_as "no-such-user-${RUN_ID:-x}" "whatever" GET /patients/
assert_status 401 "an unknown user is rejected"

section "the configured credentials work"
req GET /patients/
assert_status 200 "${API_USER} can list patients"
assert_jq 'has("count") and has("results")' "a collection response is paginated"
