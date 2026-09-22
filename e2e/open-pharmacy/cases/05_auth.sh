#!/usr/bin/env bash
set -uo pipefail
source "$SUITE_DIR/lib/http.sh"
source "$SUITE_DIR/lib/gen.sh"

section "every collection requires credentials"
for entry in addresses:/addresses/ medications:/medications/ patients:/patients/ \
             prescribers:/prescribers/ prescriptions:/prescriptions/; do
  name="${entry%%:*}"; path="${entry#*:}"
  when "an anonymous caller lists ${name}"
  req_anon GET "$path"
  assert_status 401 "it is refused with 401"
  assert_error_code "not_authenticated" "the error code is not_authenticated"
done

section "writes require credentials too"
when "an anonymous caller creates an address"
req_anon POST /addresses/ '{"line1":"1 A St","city":"Portland","state":"OR","postal_code":"97205"}'
assert_status 401 "the write is refused with 401"

when "an anonymous caller deletes a patient who does not exist"
req_anon DELETE "/patients/${MISSING_UUID}/"
assert_status 401 "the delete is refused with 401 rather than 404"

section "credentials are actually checked"
when "the configured user lists patients with the wrong password"
req_as "$API_USER" "definitely-not-the-password" GET /patients/
assert_status 401 "it is rejected"
assert_error_code "authentication_failed" "the error code is authentication_failed"

when "a user nobody has heard of lists patients"
req_as "no-such-user-${RUN_ID:-x}" "whatever" GET /patients/
assert_status 401 "it is rejected"

section "the configured credentials work"
when "the configured user lists patients"
req GET /patients/
assert_status 200 "the listing is returned"
assert_jq 'has("count") and has("results")' "the response is a paginated envelope"
