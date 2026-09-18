#!/usr/bin/env bash
set -uo pipefail
source "$SUITE_DIR/lib/http.sh"
source "$SUITE_DIR/lib/gen.sh"
source "$SUITE_PATH/lib/gen.sh"
source "$SUITE_PATH/lib/fixtures.sh"

make_address; ADDR="$NEW_ID"

section "create"
PHONE="$(gen_phone)"; EMAIL="$(gen_email)"
req POST /patients/ "$(jq -nc --arg addr "$ADDR" --arg phone "$PHONE" --arg email "$EMAIL" --arg tag "$RUN_TAG" '{
  first_name: "Testina", last_name: ("Patient-" + $tag), middle_name: "Q", suffix: "III",
  dob: "1985-03-14", gender: "female", phone_number: $phone, email: $email,
  sms_opt_in: true, address_id: $addr }')"
assert_status 201 "POST /patients/ creates a patient"
assert_jq '.id | test("^[0-9a-f-]{36}$")' "the new patient has a UUID id"
assert_jq_eq '.phone_number' "$PHONE" "phone_number round-trips"
assert_jq_eq '.sms_opt_in' 'true' "sms_opt_in round-trips"
PAT="$(jqv .id)"

# The patient writes address_id and reads back a nested address object, so one
# request renders a mailing label without a second round-trip.
assert_jq_eq '.address.id' "$ADDR" "the response nests the address it was given"
assert_jq_eq '.address.city' 'Portland' "the nested address carries its own fields"
assert_jq 'has("address_id") | not' "address_id is write-only and not echoed back"

req POST /patients/ "$(jq -nc --arg phone "$(gen_phone)" --arg tag "$RUN_TAG" '{
  first_name: "Addressless", last_name: ("Patient-" + $tag), dob: "1985-03-14", phone_number: $phone }')"
assert_status 201 "a patient can be created without an address"
assert_jq_eq '.address' 'null' "a patient with no address reads back a null address"

section "validation"
req POST /patients/ "$(jq -nc --arg addr "$ADDR" '{
  first_name: "Bad", last_name: "Phone", dob: "1985-03-14", phone_number: "5551234", address_id: $addr }')"
assert_status 400 "a phone number that is not E.164 is rejected"
assert_jq '.error.details.phone_number | length > 0' "the phone error names the phone_number field"

req POST /patients/ "$(jq -nc --arg addr "$ADDR" --arg phone "$(gen_phone)" '{
  first_name: "Bad", last_name: "Gender", dob: "1985-03-14", phone_number: $phone, gender: "alien", address_id: $addr }')"
assert_status 400 "an unknown gender is rejected"

req POST /patients/ "$(jq -nc --arg addr "$ADDR" --arg phone "$(gen_phone)" '{
  first_name: "Bad", last_name: "Dob", dob: "the fourteenth", phone_number: $phone, address_id: $addr }')"
assert_status 400 "a malformed date of birth is rejected"

req POST /patients/ "$(jq -nc --arg addr "$ADDR" --arg phone "$(gen_phone)" '{
  first_name: "Future", last_name: "Dob", dob: "2099-01-01", phone_number: $phone, address_id: $addr }')"
assert_status 400 "a date of birth in the future is rejected"
assert_jq '.error.details.dob | length > 0' "the future-dob error names the dob field"

req POST /patients/ "$(jq -nc --arg addr "$ADDR" --arg phone "$(gen_phone)" '{
  last_name: "NoFirstName", dob: "1985-03-14", phone_number: $phone, address_id: $addr }')"
assert_status 400 "first_name is required"

req POST /patients/ "$(jq -nc --arg addr "$ADDR" --arg phone "$(gen_phone)" '{
  first_name: "No", last_name: "Dob", phone_number: $phone, address_id: $addr }')"
assert_status 400 "dob is required"

req POST /patients/ "$(jq -nc --arg addr "$ADDR" --arg phone "$(gen_phone)" --arg missing "$MISSING_UUID" '{
  first_name: "Ghost", last_name: "Address", dob: "1985-03-14", phone_number: $phone, address_id: $missing }')"
assert_status 400 "an address_id that does not exist is rejected"

section "read and update"
req GET "/patients/${PAT}/"
assert_status 200 "GET /patients/{id}/ returns the patient"
assert_jq_eq '.id' "$PAT" "the retrieved patient has the requested id"

req PATCH "/patients/${PAT}/" '{"sms_opt_in":false}'
assert_status 200 "PATCH updates sms_opt_in"
assert_jq_eq '.sms_opt_in' 'false' "the patched value took effect"
assert_jq_eq '.first_name' 'Testina' "PATCH left the other fields alone"

req PATCH "/patients/${PAT}/" '{"phone_number":"nonsense"}'
assert_status 400 "PATCH validates the phone number too"

req PUT "/patients/${PAT}/" "$(jq -nc --arg phone "$(gen_phone)" --arg tag "$RUN_TAG" '{
  first_name: "Replacina", last_name: ("Patient-" + $tag), dob: "1975-01-01", phone_number: $phone }')"
assert_status 200 "PUT replaces the patient"
assert_jq_eq '.first_name' 'Replacina' "the replaced first_name took effect"

req PUT "/patients/${PAT}/" '{"first_name":"Incomplete"}'
assert_status 400 "PUT without the required fields is rejected"

section "missing records"
req GET "/patients/${MISSING_UUID}/"
assert_status 404 "GET on an unknown id is 404"
assert_error_code "not_found" "an unknown id reports not_found"

section "a patient's prescriptions"
make_cast
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION"; RX="$NEW_ID"

req GET "/patients/${FX_PATIENT}/prescriptions/"
assert_status 200 "GET /patients/{id}/prescriptions/ returns a page"
assert_jq_eq '.count' '1' "the new patient has exactly the one prescription just written"
assert_jq 'any(.results[]; .id == $rx)' "the prescription appears under its patient" --arg rx "$RX"
assert_jq 'all(.results[]; .patient.id == $p)' "every prescription listed belongs to that patient" --arg p "$FX_PATIENT"

req GET "/patients/${MISSING_UUID}/prescriptions/"
assert_status 404 "prescriptions for an unknown patient is 404"

section "delete"
make_address; SPARE_ADDR="$NEW_ID"
make_patient "$SPARE_ADDR"; SPARE="$NEW_ID"
req DELETE "/patients/${SPARE}/"
assert_status 204 "DELETE on a patient with no prescriptions is 204"
req GET "/patients/${SPARE}/"
assert_status 404 "a deleted patient is no longer retrievable"

req DELETE "/patients/${FX_PATIENT}/"
assert_status 409 "deleting a patient who holds a prescription is 409"
assert_error_code "referenced" "a blocked patient delete reports the referenced code"
assert_jq '.error.details.referenced_by | has("prescriptions")' "the conflict names prescriptions as the referencing resource"

section "filters"
req GET "/patients/?last_name=Patient-${RUN_TAG}&page_size=100"
assert_status 200 "patients can be filtered by last_name"
assert_jq 'all(.results[]; .last_name == $n)' "the last_name filter returns only that name" --arg n "Patient-${RUN_TAG}"

req GET "/patients/?dob=1985-03-14&page_size=100"
assert_status 200 "patients can be filtered by dob"
assert_jq 'all(.results[]; .dob == "1985-03-14")' "the dob filter returns only that date"

req GET "/patients/?sms_opt_in=true&page_size=100"
assert_status 200 "patients can be filtered by sms_opt_in"
assert_jq 'all(.results[]; .sms_opt_in == true)' "the sms_opt_in filter returns only opted-in patients"

req GET "/patients/?search=${RUN_TAG}&page_size=100"
assert_status 200 "patients can be searched"
assert_jq '.count >= 1' "the search finds this run's patients"
