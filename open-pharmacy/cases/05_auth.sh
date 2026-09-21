#!/usr/bin/env bash
set -uo pipefail
source "$SUITE_DIR/lib/http.sh"
source "$SUITE_DIR/lib/gen.sh"

section "every collection requires credentials"
for path in /addresses/ /medications/ /patients/ /prescribers/ /prescriptions/; do
  req_anon GET "$path"
  assert_status 401 "it is refused with 401"
  assert_error_code "not_authenticated" "the error code is not_authenticated"
done

section "writes require credentials too"
req_anon POST /addresses/ '{"line1":"1 A St","city":"Portland","state":"OR","postal_code":"97205"}'
assert_status 401 "the write is refused with 401"

req_anon DELETE "/patients/${MISSING_UUID}/"
assert_status 401 "the delete is refused with 401 rather than 404"

section "credentials are actually checked"
req_as "$API_USER" "definitely-not-the-password" GET /patients/
assert_status 401 "a wrong password is rejected"
assert_error_code "authentication_failed" "the error code is authentication_failed"

req_as "no-such-user-${RUN_ID:-x}" "whatever" GET /patients/
assert_status 401 "an unknown user is rejected"

section "the configured credentials work"
req GET /patients/
assert_status 200 "the listing is returned"
assert_jq 'has("count") and has("results")' "the response is a paginated envelope"
