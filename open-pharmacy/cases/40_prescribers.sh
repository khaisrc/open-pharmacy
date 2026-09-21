#!/usr/bin/env bash
set -uo pipefail
source "$SUITE_DIR/lib/http.sh"
source "$SUITE_DIR/lib/gen.sh"
source "$SUITE_PATH/lib/gen.sh"
source "$SUITE_PATH/lib/fixtures.sh"

make_address; ADDR="$NEW_ID"

section "create"
NPI="$(gen_npi)"
req POST /prescribers/ "$(jq -nc --arg addr "$ADDR" --arg npi "$NPI" --arg tag "$RUN_TAG" '{
  first_name: "Testopher", last_name: ("Prescriber-" + $tag), middle_name: "R", suffix: "Jr.",
  practitioner_type: "MD", npi: $npi, address_id: $addr }')"
assert_status 201 "POST /prescribers/ creates a prescriber"
assert_jq_eq '.npi' "$NPI" "the NPI round-trips"
assert_jq_eq '.practitioner_type' 'MD' "practitioner_type round-trips"
assert_jq_eq '.address.id' "$ADDR" "the response nests the address it was given"
PRE="$(jqv .id)"

section "NPI validation"
# An NPI's last digit is a Luhn check digit over the number prefixed with the
# 80840 issuer identifier, so a typo is detectable rather than silently stored.
req POST /prescribers/ "$(jq -nc --arg npi "$(gen_bad_npi)" '{
  first_name: "Bad", last_name: "CheckDigit", practitioner_type: "MD", npi: $npi }')"
assert_status 400 "an NPI with a wrong check digit is rejected"
assert_error_code "validation_error" "a bad check digit reports validation_error"
assert_jq '.error.details.npi | length > 0' "the check-digit error names the npi field"

for bad in "123" "12345678901" "abcdefghij" ""; do
  req POST /prescribers/ "$(jq -nc --arg npi "$bad" '{
    first_name: "Bad", last_name: "Npi", practitioner_type: "MD", npi: $npi }')"
  assert_status 400 "an NPI of '${bad}' is rejected"
done

section "an NPI is unique among live prescribers"
DUP_NPI="$(gen_npi)"
req POST /prescribers/ "$(jq -nc --arg npi "$DUP_NPI" --arg tag "$RUN_TAG" '{
  first_name: "Original", last_name: ("Doc-" + $tag), practitioner_type: "DO", npi: $npi }')"
assert_status 201 "the first prescriber with an NPI is created"
FIRST="$(jqv .id)"

req POST /prescribers/ "$(jq -nc --arg npi "$DUP_NPI" --arg tag "$RUN_TAG" '{
  first_name: "Duplicate", last_name: ("Doc-" + $tag), practitioner_type: "DO", npi: $npi }')"
assert_status 409 "a second live prescriber with the same NPI is refused"
assert_jq '.error.code | test("npi")' "the duplicate reports an NPI-specific conflict code"

req DELETE "/prescribers/${FIRST}/"
assert_status 204 "the original prescriber is deleted"
req POST /prescribers/ "$(jq -nc --arg npi "$DUP_NPI" --arg tag "$RUN_TAG" '{
  first_name: "Reused", last_name: ("Doc-" + $tag), practitioner_type: "DO", npi: $npi }')"
assert_status 201 "the NPI can be reused once the live prescriber holding it is gone"

section "other validation"
req POST /prescribers/ "$(jq -nc --arg npi "$(gen_npi)" '{
  first_name: "Bad", last_name: "Type", practitioner_type: "WIZARD", npi: $npi }')"
assert_status 400 "an unknown practitioner_type is rejected"

req POST /prescribers/ "$(jq -nc --arg npi "$(gen_npi)" '{
  last_name: "NoFirstName", practitioner_type: "MD", npi: $npi }')"
assert_status 400 "first_name is required"

req POST /prescribers/ '{"first_name":"No","last_name":"Npi","practitioner_type":"MD"}'
assert_status 400 "npi is required"

section "read and update"
req GET "/prescribers/${PRE}/"
assert_status 200 "GET /prescribers/{id}/ returns the prescriber"
assert_jq_eq '.id' "$PRE" "the retrieved prescriber has the requested id"

req PATCH "/prescribers/${PRE}/" '{"practitioner_type":"NP"}'
assert_status 200 "PATCH updates practitioner_type"
assert_jq_eq '.practitioner_type' 'NP' "the patched value took effect"
assert_jq_eq '.npi' "$NPI" "PATCH left the NPI alone"

req PATCH "/prescribers/${PRE}/" '{"npi":"0000000000"}'
assert_status 400 "PATCH validates the NPI too"

req PUT "/prescribers/${PRE}/" "$(jq -nc --arg npi "$NPI" --arg tag "$RUN_TAG" '{
  first_name: "Replacopher", last_name: ("Prescriber-" + $tag), practitioner_type: "PA", npi: $npi }')"
assert_status 200 "PUT replaces the prescriber"
assert_jq_eq '.practitioner_type' 'PA' "the replaced practitioner_type took effect"

req PUT "/prescribers/${PRE}/" '{"first_name":"Incomplete"}'
assert_status 400 "PUT without the required fields is rejected"

section "missing records"
req GET "/prescribers/${MISSING_UUID}/"
assert_status 404 "GET on an unknown id is 404"
assert_error_code "not_found" "an unknown id reports not_found"

section "a prescriber's prescriptions"
make_cast
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION"; RX="$NEW_ID"

req GET "/prescribers/${FX_PRESCRIBER}/prescriptions/"
assert_status 200 "GET /prescribers/{id}/prescriptions/ returns a page"
assert_jq_eq '.count' '1' "the new prescriber has exactly the one prescription just written"
assert_jq 'any(.results[]; .id == $rx)' "the prescription appears under its prescriber" --arg rx "$RX"
assert_jq 'all(.results[]; .prescriber.id == $d)' "every prescription listed belongs to that prescriber" --arg d "$FX_PRESCRIBER"

req GET "/prescribers/${MISSING_UUID}/prescriptions/"
assert_status 404 "prescriptions for an unknown prescriber is 404"

section "delete"
make_prescriber "$ADDR"; SPARE="$NEW_ID"
req DELETE "/prescribers/${SPARE}/"
assert_status 204 "DELETE on a prescriber with no prescriptions is 204"
req GET "/prescribers/${SPARE}/"
assert_status 404 "a deleted prescriber is no longer retrievable"

req DELETE "/prescribers/${FX_PRESCRIBER}/"
assert_status 409 "deleting a prescriber who wrote a prescription is 409"
assert_error_code "referenced" "a blocked prescriber delete reports the referenced code"
assert_jq '.error.details.referenced_by | has("prescriptions")' "the conflict names prescriptions as the referencing resource"

section "filters"
req GET "/prescribers/?npi=${NPI}"
assert_status 200 "prescribers can be filtered by npi"
assert_jq_eq '.count' '1' "the npi filter matches exactly one prescriber"
assert_jq_eq '.results[0].id' "$PRE" "the npi filter returns the right prescriber"

req GET "/prescribers/?practitioner_type=PA&page_size=100"
assert_status 200 "prescribers can be filtered by practitioner_type"
assert_jq 'all(.results[]; .practitioner_type == "PA")' "the practitioner_type filter returns only that type"

req GET "/prescribers/?search=${RUN_TAG}&page_size=100"
assert_status 200 "prescribers can be searched"
assert_jq '.count >= 1' "the search finds this run's prescribers"
