#!/usr/bin/env bash
set -uo pipefail
source "$SUITE_DIR/lib/http.sh"
source "$SUITE_DIR/lib/gen.sh"
source "$SUITE_PATH/lib/gen.sh"
source "$SUITE_PATH/lib/fixtures.sh"

make_address; ADDR="$NEW_ID"

section "create"
PHONE="$(gen_phone)"; EMAIL="$(gen_email)"
when "a patient is created at that address"
req POST /patients/ "$(jq -nc --arg addr "$ADDR" --arg phone "$PHONE" --arg email "$EMAIL" --arg tag "$RUN_TAG" '{
  first_name: "Testina", last_name: ("Patient-" + $tag), middle_name: "Q", suffix: "III",
  dob: "1985-03-14", gender: "female", phone_number: $phone, email: $email,
  sms_opt_in: true, address_id: $addr }')"
assert_status 201 "the patient is created"
assert_jq '.id | test("^[0-9a-f-]{36}$")' "it has a UUID id"
assert_jq_eq '.phone_number' "$PHONE" "phone_number round-trips"
assert_jq_eq '.sms_opt_in' 'true' "sms_opt_in round-trips"
PAT="$(jqv .id)"

# The patient writes address_id and reads back a nested address object, so one
# request renders a mailing label without a second round-trip.
assert_jq_eq '.address.id' "$ADDR" "the response nests the address it was given"
assert_jq_eq '.address.city' 'Portland' "the nested address carries its own fields"
assert_jq 'has("address_id") | not' "address_id is write-only and not echoed back"

when "a patient is created with no address at all"
req POST /patients/ "$(jq -nc --arg phone "$(gen_phone)" --arg tag "$RUN_TAG" '{
  first_name: "Addressless", last_name: ("Patient-" + $tag), dob: "1985-03-14", phone_number: $phone }')"
assert_status 201 "it is created"
assert_jq_eq '.address' 'null' "the address reads back as null"

section "validation"
when "a patient is created with a phone number that is not E.164"
req POST /patients/ "$(jq -nc --arg addr "$ADDR" '{
  first_name: "Bad", last_name: "Phone", dob: "1985-03-14", phone_number: "5551234", address_id: $addr }')"
assert_status 400 "it is rejected"
assert_jq '.error.details.phone_number | length > 0' "the error names the phone_number field"

when "a patient is created with a gender the API does not know"
req POST /patients/ "$(jq -nc --arg addr "$ADDR" --arg phone "$(gen_phone)" '{
  first_name: "Bad", last_name: "Gender", dob: "1985-03-14", phone_number: $phone, gender: "alien", address_id: $addr }')"
assert_status 400 "it is rejected"

when "a patient is created with a date of birth that is not a date"
req POST /patients/ "$(jq -nc --arg addr "$ADDR" --arg phone "$(gen_phone)" '{
  first_name: "Bad", last_name: "Dob", dob: "the fourteenth", phone_number: $phone, address_id: $addr }')"
assert_status 400 "it is rejected"

when "a patient is created with a date of birth in the future"
req POST /patients/ "$(jq -nc --arg addr "$ADDR" --arg phone "$(gen_phone)" '{
  first_name: "Future", last_name: "Dob", dob: "2099-01-01", phone_number: $phone, address_id: $addr }')"
assert_status 400 "it is rejected"
assert_jq '.error.details.dob | length > 0' "the error names the dob field"

when "a patient is created with no first name"
req POST /patients/ "$(jq -nc --arg addr "$ADDR" --arg phone "$(gen_phone)" '{
  last_name: "NoFirstName", dob: "1985-03-14", phone_number: $phone, address_id: $addr }')"
assert_status 400 "first_name is required"

when "a patient is created with no date of birth"
req POST /patients/ "$(jq -nc --arg addr "$ADDR" --arg phone "$(gen_phone)" '{
  first_name: "No", last_name: "Dob", phone_number: $phone, address_id: $addr }')"
assert_status 400 "dob is required"

when "a patient is created at an address that does not exist"
req POST /patients/ "$(jq -nc --arg addr "$ADDR" --arg phone "$(gen_phone)" --arg missing "$MISSING_UUID" '{
  first_name: "Ghost", last_name: "Address", dob: "1985-03-14", phone_number: $phone, address_id: $missing }')"
assert_status 400 "it is rejected"

section "read and update"
when "the patient is read back"
req GET "/patients/${PAT}/"
assert_status 200 "the patient is returned"
assert_jq_eq '.id' "$PAT" "it is the patient that was asked for"

when "they opt out of text messages"
req PATCH "/patients/${PAT}/" '{"sms_opt_in":false}'
assert_status 200 "the patient is updated"
assert_jq_eq '.sms_opt_in' 'false' "the patched value took effect"
assert_jq_eq '.first_name' 'Testina' "the other fields are untouched"

when "their phone number is patched to something that is not E.164"
req PATCH "/patients/${PAT}/" '{"phone_number":"nonsense"}'
assert_status 400 "a partial update validates it too"

when "the patient is replaced"
req PUT "/patients/${PAT}/" "$(jq -nc --arg phone "$(gen_phone)" --arg tag "$RUN_TAG" '{
  first_name: "Replacina", last_name: ("Patient-" + $tag), dob: "1975-01-01", phone_number: $phone }')"
assert_status 200 "the patient is replaced"
assert_jq_eq '.first_name' 'Replacina' "the new first_name took effect"

when "the patient is replaced without their required fields"
req PUT "/patients/${PAT}/" '{"first_name":"Incomplete"}'
assert_status 400 "it is rejected"

section "missing records"
when "a patient who does not exist is read"
req GET "/patients/${MISSING_UUID}/"
assert_status 404 "it is 404"
assert_error_code "not_found" "the error code is not_found"

section "a patient's prescriptions"
make_cast
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION"; RX="$NEW_ID"

when "that patient's prescriptions are listed"
req GET "/patients/${FX_PATIENT}/prescriptions/"
assert_status 200 "a page of prescriptions is returned"
assert_jq_eq '.count' '1' "it holds exactly the one prescription just written"
assert_jq 'any(.results[]; .id == $rx)' "that prescription is in it" --arg rx "$RX"
assert_jq 'all(.results[]; .patient.id == $p)' "everything listed belongs to that patient" --arg p "$FX_PATIENT"

when "the prescriptions of a patient who does not exist are listed"
req GET "/patients/${MISSING_UUID}/prescriptions/"
assert_status 404 "it is 404"

section "delete"
make_address; SPARE_ADDR="$NEW_ID"
make_patient "$SPARE_ADDR"; SPARE="$NEW_ID"
when "a patient who holds no prescription is deleted"
req DELETE "/patients/${SPARE}/"
assert_status 204 "they are deleted"
when "the deleted patient is read back"
req GET "/patients/${SPARE}/"
assert_status 404 "they are no longer retrievable"

when "a patient who holds a prescription is deleted"
req DELETE "/patients/${FX_PATIENT}/"
assert_status 409 "the delete is refused with 409"
assert_error_code "referenced" "the error code is referenced"
assert_jq '.error.details.referenced_by | has("prescriptions")' "the conflict names prescriptions as the referencing resource"

section "filters"
when "patients are filtered by last name"
req GET "/patients/?last_name=Patient-${RUN_TAG}&page_size=100"
assert_status 200 "the filtered listing is returned"
assert_jq 'all(.results[]; .last_name == $n)' "only that last name comes back" --arg n "Patient-${RUN_TAG}"

when "patients are filtered by date of birth"
req GET "/patients/?dob=1985-03-14&page_size=100"
assert_status 200 "the filtered listing is returned"
assert_jq 'all(.results[]; .dob == "1985-03-14")' "only that date of birth comes back"

when "patients are filtered to those who opted into texts"
req GET "/patients/?sms_opt_in=true&page_size=100"
assert_status 200 "the filtered listing is returned"
assert_jq 'all(.results[]; .sms_opt_in == true)' "only opted-in patients come back"

when "patients are searched for this run's tag"
req GET "/patients/?search=${RUN_TAG}&page_size=100"
assert_status 200 "the search results are returned"
assert_jq '.count >= 1' "this run's patients are found"
