#!/usr/bin/env bash
set -uo pipefail
source "$E2E_DIR/lib/http.sh"
source "$E2E_DIR/lib/gen.sh"
source "$E2E_DIR/lib/fixtures.sh"

make_address; ADDR="$NEW_ID"

section "create"
NAME="Testmart ${RUN_TAG}x$(_rand 1000000)"
when "a pharmacy is created"
req POST /pharmacies/ "$(jq -nc --arg name "$NAME" --arg phone "$(gen_phone)" --arg email "$(gen_email)" '{
  name: $name, legal_name: ($name + " LLC"), phone_number: $phone, email: $email,
  website: "https://example.local" }')"
assert_status 201 "the pharmacy is created"
assert_jq_eq '.name' "$NAME" "the name round-trips"
PH="$(jqv .id)"

# location_count is annotated by the viewset's queryset, so it is a read-time
# field: a create serializes the instance it just saved and leaves it out.
when "the new pharmacy is read back"
req GET "/pharmacies/${PH}/"
assert_status 200 "the pharmacy is retrievable"
assert_jq_eq '.location_count' '0' "a pharmacy with no stores counts none"

section "locations and staff are reached through their pharmacy"
# Both used to be -- or would naturally be -- collections of their own. They
# are sub-resources now, so the flat paths are not routed at all.
when "a client asks for pharmacy locations as a collection of their own"
req GET /pharmacy-locations/
assert_status 404 "there is no such collection"

when "a client asks for staff as a collection of their own"
req GET /staff/
assert_status 404 "there is no such collection"

when "the pharmacy's locations are listed under it"
req GET "/pharmacies/${PH}/locations/"
assert_status 200 "the listing is returned"
assert_jq_eq '.count' '0' "the new pharmacy has none yet"

when "the pharmacy's staff are listed under it"
req GET "/pharmacies/${PH}/staff/"
assert_status 200 "the listing is returned"
assert_jq_eq '.count' '0' "the new pharmacy employs nobody yet"

section "location_count counts live stores only"
make_location "$PH" "$ADDR"; FIRST_STORE="$NEW_ID"
make_location "$PH" "$ADDR"; SECOND_STORE="$NEW_ID"

when "the pharmacy is read back"
req GET "/pharmacies/${PH}/"
assert_status 200 "the pharmacy is returned"
assert_jq_eq '.location_count' '2' "it counts both stores"

when "one of the stores is deleted"
req DELETE "/pharmacies/${PH}/locations/${SECOND_STORE}/"
assert_status 204 "the store is deleted"
when "the pharmacy is read back again"
req GET "/pharmacies/${PH}/"
assert_jq_eq '.location_count' '1' "the soft-deleted store is no longer counted"

section "read and update"
when "the pharmacy's legal name is patched"
req PATCH "/pharmacies/${PH}/" '{"legal_name":"Testmart Holdings LLC"}'
assert_status 200 "the pharmacy is updated"
assert_jq_eq '.legal_name' 'Testmart Holdings LLC' "the patched value took effect"
assert_jq_eq '.name' "$NAME" "the name is untouched"

when "its phone number is patched to something that is not E.164"
req PATCH "/pharmacies/${PH}/" '{"phone_number":"5035550123"}'
assert_status 400 "it is rejected"
assert_error_code "validation_error" "the error code is validation_error"

section "missing records"
when "a pharmacy that does not exist is read"
req GET "/pharmacies/${MISSING_UUID}/"
assert_status 404 "it is 404"
assert_error_code "not_found" "the error code is not_found"

section "a pharmacy that still has stores cannot be deleted"
when "a pharmacy with a live store is deleted"
req DELETE "/pharmacies/${PH}/"
assert_status 409 "the delete is refused with 409"
assert_error_code "referenced" "the error code is referenced"
assert_jq '.error.details.referenced_by | has("pharmacy locations")' \
  "the conflict names pharmacy locations as the referencing resource"

when "its last store is deleted"
req DELETE "/pharmacies/${PH}/locations/${FIRST_STORE}/"
assert_status 204 "the store is deleted"
when "the pharmacy is deleted again"
req DELETE "/pharmacies/${PH}/"
assert_status 204 "it is deletable once no live store references it"
when "the deleted pharmacy is read back"
req GET "/pharmacies/${PH}/"
assert_status 404 "it is no longer retrievable"

section "a pharmacy that still employs staff cannot be deleted"
make_pharmacy; EMPLOYER="$NEW_ID"
make_staff "$EMPLOYER"; EMPLOYEE="$NEW_ID"

when "a pharmacy with a live employee is deleted"
req DELETE "/pharmacies/${EMPLOYER}/"
assert_status 409 "the delete is refused with 409"
assert_error_code "referenced" "the error code is referenced"
assert_jq '.error.details.referenced_by | has("staff")' \
  "the conflict names staff as the referencing resource"
assert_jq_eq '.error.details.referenced_by.staff' '1' "it counts the one employee"

when "the employee is deleted"
req DELETE "/pharmacies/${EMPLOYER}/staff/${EMPLOYEE}/"
assert_status 204 "they are deleted"
when "the pharmacy is deleted again"
req DELETE "/pharmacies/${EMPLOYER}/"
assert_status 204 "it is deletable once nobody live works there"
