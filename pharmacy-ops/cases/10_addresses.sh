#!/usr/bin/env bash
set -uo pipefail
source "$SUITE_DIR/lib/http.sh"
source "$SUITE_DIR/lib/gen.sh"
source "$SUITE_PATH/lib/gen.sh"
source "$SUITE_PATH/lib/fixtures.sh"

section "create"
req POST /addresses/ '{"line1":"742 Evergreen Ter","line2":"Rear","city":"Springfield","state":"OR","postal_code":"97477"}'
assert_status 201 "POST /addresses/ creates an address"
assert_jq '.id | test("^[0-9a-f-]{36}$")' "the new address has a UUID id"
assert_jq_eq '.line1' '742 Evergreen Ter' "line1 round-trips"
assert_jq_eq '.state' 'OR' "state round-trips"
assert_jq_eq '.country' 'US' "country defaults to US"
assert_jq 'has("created_at") and has("updated_at")' "timestamps are returned"
ADDR="$(jqv .id)"

req POST /addresses/ '{"line1":"1 Lowercase Ln","city":"Portland","state":"or","postal_code":"97205"}'
assert_status 201 "a lowercase state code is accepted"
assert_jq_eq '.state' 'OR' "a lowercase state code is upper-cased on the way in"

section "read"
req GET "/addresses/${ADDR}/"
assert_status 200 "GET /addresses/{id}/ returns the address"
assert_jq_eq '.id' "$ADDR" "the retrieved address has the requested id"

req GET "/addresses/?postal_code=97477&page_size=100"
assert_status 200 "addresses can be filtered by postal_code"
assert_jq 'any(.results[]; .id == $id)' "the new address appears in a filtered list" --arg id "$ADDR"

section "update"
req PATCH "/addresses/${ADDR}/" '{"line2":"Apt 12"}'
assert_status 200 "PATCH updates one field"
assert_jq_eq '.line2' 'Apt 12' "the patched field changed"
assert_jq_eq '.line1' '742 Evergreen Ter' "PATCH left the other fields alone"

req PUT "/addresses/${ADDR}/" '{"line1":"11 Replaced Rd","city":"Eugene","state":"OR","postal_code":"97401"}'
assert_status 200 "PUT replaces the address"
assert_jq_eq '.city' 'Eugene' "the replaced city took effect"
# DRF leaves an optional model field alone when a PUT omits it, so PUT is a
# replace of what was sent, not of the whole record. Worth pinning down: a
# client that expects PUT to clear line2 would be wrong.
assert_jq_eq '.line2' 'Apt 12' "PUT leaves an omitted optional field unchanged"

req PUT "/addresses/${ADDR}/" '{"city":"Eugene"}'
assert_status 400 "PUT without the required fields is rejected"
assert_error_code "validation_error" "an incomplete PUT reports validation_error"

section "validation"
req POST /addresses/ '{"line1":"1 Bad St","city":"Nowhere","state":"ZZ","postal_code":"97205"}'
assert_status 400 "an unknown state code is rejected"
assert_jq '.error.details.state | length > 0' "the state error names the state field"

req POST /addresses/ '{"line1":"1 Bad St","city":"Nowhere","state":"OR","postal_code":"not-a-zip"}'
assert_status 400 "a malformed postal code is rejected"
assert_jq '.error.details.postal_code | length > 0' "the postal code error names the postal_code field"

req POST /addresses/ '{"city":"Nowhere","state":"OR","postal_code":"97205"}'
assert_status 400 "line1 is required"

req POST /addresses/ '{"line1":"1 Ok St","city":"Portland","state":"OR","postal_code":"97205-1234"}'
assert_status 201 "a ZIP+4 postal code is accepted"

section "missing records"
req GET "/addresses/${MISSING_UUID}/"
assert_status 404 "GET on an unknown id is 404"
assert_error_code "not_found" "an unknown id reports not_found"

req PATCH "/addresses/${MISSING_UUID}/" '{"city":"Nowhere"}'
assert_status 404 "PATCH on an unknown id is 404"

req GET "/addresses/definitely-not-a-uuid/"
assert_status 404 "a malformed id is 404 rather than a server error"

section "delete is a soft delete"
make_address; UNUSED="$NEW_ID"
req DELETE "/addresses/${UNUSED}/"
assert_status 204 "DELETE on an unreferenced address is 204"

req GET "/addresses/${UNUSED}/"
assert_status 404 "a deleted address is no longer retrievable"

req DELETE "/addresses/${UNUSED}/"
assert_status 404 "deleting the same address twice is 404"

req GET "/addresses/?page_size=100&postal_code=97205"
assert_jq 'all(.results[]; .id != $id)' "a deleted address is gone from listings" --arg id "$UNUSED"

section "delete is refused while referenced"
make_address; REFERENCED="$NEW_ID"
make_patient "$REFERENCED" >/dev/null
req DELETE "/addresses/${REFERENCED}/"
assert_status 409 "deleting an address a patient still uses is 409"
assert_error_code "referenced" "a blocked delete reports the referenced code"
assert_jq '.error.details.referenced_by | has("patients")' "the conflict names patients as the referencing resource"

req GET "/addresses/${REFERENCED}/"
assert_status 200 "the address survives the refused delete"

make_address; REF_BY_DOC="$NEW_ID"
make_prescriber "$REF_BY_DOC" >/dev/null
req DELETE "/addresses/${REF_BY_DOC}/"
assert_status 409 "deleting an address a prescriber still uses is 409"
assert_jq '.error.details.referenced_by | has("prescribers")' "the conflict names prescribers as the referencing resource"
