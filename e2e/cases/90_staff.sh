#!/usr/bin/env bash
set -uo pipefail
source "$E2E_DIR/lib/http.sh"
source "$E2E_DIR/lib/gen.sh"
source "$E2E_DIR/lib/fixtures.sh"

make_pharmacy; PH="$NEW_ID"

section "create"
REG="$(gen_registration)"
LAST="Staffer-${RUN_TAG}x$(_rand 1000000)"
when "a pharmacist is hired by that pharmacy"
req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg last "$LAST" --arg reg "$REG" '{
  first_name: "Testila", last_name: $last, middle_name: "Q", suffix: "Jr.",
  role: "pharmacist", registration_state: "OR", registration_number: $reg }')"
assert_status 201 "the staff member is created"
assert_jq_eq '.role' 'pharmacist' "the role round-trips"
assert_jq_eq '.registration_state' 'OR' "the registration state round-trips"
assert_jq_eq '.registration_number' "$REG" "the registration number round-trips"
assert_jq_eq '.full_name' "Testila Q ${LAST} Jr." "full_name joins every name component it has"
assert_jq_eq '.is_active' 'true' "a new staff member is active"
assert_jq_eq '.pharmacy.id' "$PH" "the response nests the employer from the path"
assert_jq '.pharmacy | keys == ["id","name"]' "the employer is a summary, not the whole pharmacy"
RPH="$(jqv .id)"

when "they are read back under their employer"
req GET "/pharmacies/${PH}/staff/${RPH}/"
assert_status 200 "the staff member is returned"
assert_jq_eq '.id' "$RPH" "it is the record that was asked for"

when "the pharmacy's roster is listed"
req GET "/pharmacies/${PH}/staff/"
assert_status 200 "the roster is returned"
assert_jq 'any(.results[]; .id == $s)' "the new staff member is on it" --arg s "$RPH"

section "a staff record is not a login"
# The domain identity of a person -- the name a board can look up -- and
# deliberately not a way to sign in. Nothing joins it to auth.User.
when "a staff record is read in full"
req GET "/pharmacies/${PH}/staff/${RPH}/"
assert_status 200 "the staff member is returned"
assert_jq 'has("username") | not' "no username is exposed"
assert_jq 'has("user") | not' "no user reference is exposed"
assert_jq 'has("user_id") | not' "no login id is exposed"
assert_jq 'has("password") | not' "no credential is exposed"

section "the employer comes from the path, never the body"
make_pharmacy; OTHER_PH="$NEW_ID"

when "someone is hired with a body naming a different employer"
req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg reg "$(gen_registration)" --arg other "$OTHER_PH" '{
  first_name: "Pathy", last_name: "Bound", role: "pharmacist",
  registration_state: "OR", registration_number: $reg, pharmacy_id: $other }')"
assert_status 201 "it still succeeds"
assert_jq_eq '.pharmacy.id' "$PH" "they are employed by the pharmacy in the path"
BOUND="$(jqv .id)"

when "a client tries to move them to that other employer"
req PATCH "/pharmacies/${PH}/staff/${BOUND}/" "$(jq -nc --arg other "$OTHER_PH" '{pharmacy_id: $other}')"
assert_status 200 "the update is accepted"
assert_jq_eq '.pharmacy.id' "$PH" "but they have not changed employer"

section "a staff member is invisible outside their own pharmacy"
when "they are read under a pharmacy that does not employ them"
req GET "/pharmacies/${OTHER_PH}/staff/${RPH}/"
assert_status 404 "it is 404"
assert_error_code "not_found" "the error code is not_found"

when "that other pharmacy's roster is listed"
req GET "/pharmacies/${OTHER_PH}/staff/"
assert_status 200 "the roster is returned"
assert_jq 'all(.results[]; .id != $s)' "it does not contain them" --arg s "$RPH"

when "they are deleted through the pharmacy that does not employ them"
req DELETE "/pharmacies/${OTHER_PH}/staff/${RPH}/"
assert_status 404 "it is 404 there too"

section "an unknown pharmacy is 404 on every nested route"
when "a roster is listed under a pharmacy that does not exist"
req GET "/pharmacies/${MISSING_UUID}/staff/"
assert_status 404 "it is 404, not an empty page"
assert_error_code "not_found" "the error code is not_found"

when "someone is hired by a pharmacy that does not exist"
req POST "/pharmacies/${MISSING_UUID}/staff/" "$(jq -nc --arg reg "$(gen_registration)" '{
  first_name: "No", last_name: "Employer", role: "pharmacist",
  registration_state: "OR", registration_number: $reg }')"
assert_status 404 "it is 404"

when "a staff member is read under a pharmacy that does not exist"
req GET "/pharmacies/${MISSING_UUID}/staff/${RPH}/"
assert_status 404 "it is 404"

when "a roster is listed under a pharmacy id that is not a UUID"
req GET "/pharmacies/not-a-uuid/staff/"
assert_status 404 "it is 404"
assert_error_code "not_found" "it is the API's error envelope, not Django's HTML page"

section "role"
when "someone is hired into a role that does not exist"
req POST "/pharmacies/${PH}/staff/" '{"first_name":"Wrong","last_name":"Role","role":"wizard"}'
assert_status 400 "it is rejected"
assert_error_code "validation_error" "the error code is validation_error"
assert_jq '.error.details | has("role")' "the error names the role field"

when "someone is hired with no role at all"
req POST "/pharmacies/${PH}/staff/" '{"first_name":"No","last_name":"Role"}'
assert_status 400 "role is required -- there is no default"

when "someone is hired with no first name"
req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg reg "$(gen_registration)" '{
  last_name: "NoFirstName", role: "pharmacist", registration_state: "OR", registration_number: $reg }')"
assert_status 400 "first_name is required"

section "state registration is normalized on write"
# Boards print these in upper case; normalizing keeps "rph.1" and "RPH.1" from
# looking like two registrations to the live-unique check.
LOWER="$(gen_registration | tr '[:upper:]' '[:lower:]')"
when "a pharmacist is hired with a lower-case, padded registration"
req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg reg "  ${LOWER}  " '{
  first_name: "Lower", last_name: "Case", role: "pharmacist",
  registration_state: "or", registration_number: $reg }')"
assert_status 201 "it is accepted"
assert_jq_eq '.registration_state' 'OR' "the state is upper-cased"
assert_jq_eq '.registration_number' "$(echo "$LOWER" | tr '[:lower:]' '[:upper:]')" \
  "the number is stripped and upper-cased"

when "a pharmacist is hired with a state that is not a USPS code"
req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg reg "$(gen_registration)" '{
  first_name: "Bad", last_name: "State", role: "pharmacist",
  registration_state: "XX", registration_number: $reg }')"
assert_status 400 "it is rejected"
assert_jq '.error.details | has("registration_state")' "the error names the state field"

section "a state registration is both halves or neither"
when "someone is hired with an issuing state but no number"
req POST "/pharmacies/${PH}/staff/" '{"first_name":"Half","last_name":"State","role":"technician","registration_state":"OR"}'
assert_status 400 "it is rejected"
assert_jq '.error.details | has("registration_number")' "the error names the registration_number field"

when "someone is hired with a number but no issuing state"
req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg reg "$(gen_registration)" '{
  first_name: "Half", last_name: "Number", role: "technician", registration_number: $reg }')"
assert_status 400 "it is rejected"

section "which roles must hold a registration"
# A pharmacist is licensed everywhere and a certified technician is registered
# by definition; a plain technician or an intern may not be, depending on the
# state.
for role in pharmacist certified_technician; do
  when "a ${role} is hired with no registration"
  req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg role "$role" '{
    first_name: "Unregistered", last_name: "Required", role: $role,
    certification_body: "PTCB", certification_number: "12345678" }')"
  assert_status 400 "it is rejected"
  assert_jq '.error.details | has("registration_number")' "the error names the registration_number field"
done

UNREGISTERED=""
for role in technician intern; do
  when "a ${role} is hired with no registration"
  req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg role "$role" --arg last "Unregistered-${RUN_TAG}" '{
    first_name: "Optional", last_name: $last, role: $role }')"
  assert_status 201 "it is allowed for this role"
  assert_jq_eq '.registration_number' '' "the registration number is blank"
  assert_jq_eq '.registration_state' '' "and so is the issuing state"
  UNREGISTERED="${UNREGISTERED} $(jqv .id)"
done

# The unique index excludes blanks, so the second unregistered technician is
# not a duplicate of the first: both are live on the roster at once.
set -- $UNREGISTERED
when "the roster is listed"
req GET "/pharmacies/${PH}/staff/?page_size=100"
assert_status 200 "the roster is returned"
assert_jq 'any(.results[]; .id == $a)' "the unregistered technician is live" --arg a "$1"
assert_jq 'any(.results[]; .id == $b)' "so is the unregistered intern" --arg b "$2"

section "certification is both halves or neither"
when "someone is hired with a certifying body but no number"
req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg reg "$(gen_registration)" '{
  first_name: "Half", last_name: "Body", role: "pharmacist",
  registration_state: "OR", registration_number: $reg, certification_body: "PTCB" }')"
assert_status 400 "it is rejected"
assert_jq '.error.details | has("certification_number")' "the error names the certification_number field"

when "someone is hired with a certification number but no body"
req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg reg "$(gen_registration)" --arg cert "$(gen_certification)" '{
  first_name: "Half", last_name: "Cert", role: "pharmacist",
  registration_state: "OR", registration_number: $reg, certification_number: $cert }')"
assert_status 400 "it is rejected"

when "a pharmacist is hired holding a certification too"
req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg reg "$(gen_registration)" --arg cert "$(gen_certification)" '{
  first_name: "Certified", last_name: "Pharmacist", role: "pharmacist",
  registration_state: "OR", registration_number: $reg,
  certification_body: "NHA", certification_number: $cert }')"
assert_status 201 "any role may hold one"
assert_jq_eq '.certification_body' 'NHA' "the certifying body round-trips"

when "someone is hired with a certifying body the API does not know"
req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg reg "$(gen_registration)" --arg cert "$(gen_certification)" '{
  first_name: "Bad", last_name: "Body", role: "pharmacist",
  registration_state: "OR", registration_number: $reg,
  certification_body: "ACME", certification_number: $cert }')"
assert_status 400 "it is rejected"

section "a certified technician must hold a certification"
when "a certified technician is hired with no certification"
req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg reg "$(gen_registration)" '{
  first_name: "Uncertified", last_name: "Technician", role: "certified_technician",
  registration_state: "OR", registration_number: $reg }')"
assert_status 400 "it is rejected"
assert_jq '.error.details | has("certification_number")' "the error names the certification_number field"

CPHT_REG="$(gen_registration)"
when "they are hired with both credentials"
req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg reg "$CPHT_REG" --arg cert "$(gen_certification)" '{
  first_name: "Certified", last_name: "Technician", role: "certified_technician",
  registration_state: "OR", registration_number: $reg,
  certification_body: "PTCB", certification_number: $cert }')"
assert_status 201 "the staff member is created"
CPHT="$(jqv .id)"

section "a registration is unique per state among live staff"
DUP_REG="$(gen_registration)"
when "a pharmacist claims an Oregon registration"
req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg reg "$DUP_REG" '{
  first_name: "Original", last_name: "Holder", role: "pharmacist",
  registration_state: "OR", registration_number: $reg }')"
assert_status 201 "the first staff member with that registration is created"
HOLDER="$(jqv .id)"

when "a second pharmacist claims the same state and number"
req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg reg "$DUP_REG" '{
  first_name: "Duplicate", last_name: "Holder", role: "pharmacist",
  registration_state: "OR", registration_number: $reg }')"
assert_status 409 "it is refused with 409"
assert_error_code "duplicate_registration_number" "the error code names the registration number"
assert_jq_eq '.error.details.field' 'registration_number' "the conflict names the offending field"
assert_jq_eq '.error.details.registration_number' "$DUP_REG" "it echoes the number"
assert_jq_eq '.error.details.registration_state' 'OR' "and the state that issued it"
assert_jq_eq '.error.details.existing_id' "$HOLDER" "it points at the record already holding it"

# Another pharmacy is no defense: the registration belongs to the state, not
# the employer.
when "another pharmacy hires someone on the same registration"
req POST "/pharmacies/${OTHER_PH}/staff/" "$(jq -nc --arg reg "$DUP_REG" '{
  first_name: "Elsewhere", last_name: "Holder", role: "pharmacist",
  registration_state: "OR", registration_number: $reg }')"
assert_status 409 "it is refused there too"

when "the same number is claimed under another state"
req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg reg "$DUP_REG" '{
  first_name: "Washington", last_name: "Holder", role: "pharmacist",
  registration_state: "WA", registration_number: $reg }')"
assert_status 201 "that is fine -- the pair is what is unique"
WA_HOLDER="$(jqv .id)"

when "only their state is patched, into a collision"
req PATCH "/pharmacies/${PH}/staff/${WA_HOLDER}/" '{"registration_state":"OR"}'
assert_status 409 "it is refused with 409"
assert_error_code "duplicate_registration_number" "the error code names the registration number"

when "only a number is patched onto a record"
req PATCH "/pharmacies/${PH}/staff/${HOLDER}/" "$(jq -nc --arg reg "$(gen_registration)" '{registration_number: $reg}')"
assert_status 200 "it is checked against the state already on file"

when "the Washington record is deleted"
req DELETE "/pharmacies/${PH}/staff/${WA_HOLDER}/"
assert_status 204 "it is deleted"
when "someone else claims that state and number"
req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg reg "$DUP_REG" '{
  first_name: "Reused", last_name: "Holder", role: "pharmacist",
  registration_state: "WA", registration_number: $reg }')"
assert_status 201 "the pair is reusable once the live record holding it is gone"

section "NPI"
when "someone is hired with an NPI whose check digit is wrong"
req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg reg "$(gen_registration)" --arg npi "$(gen_bad_npi)" '{
  first_name: "Bad", last_name: "CheckDigit", role: "pharmacist",
  registration_state: "OR", registration_number: $reg, npi: $npi }')"
assert_status 400 "it is rejected"
assert_jq '.error.details | has("npi")' "the error names the npi field"

DUP_NPI="$(gen_npi)"
when "a valid NPI is patched onto the first pharmacist"
req PATCH "/pharmacies/${PH}/staff/${RPH}/" "$(jq -nc --arg npi "$DUP_NPI" '{npi: $npi}')"
assert_status 200 "it is accepted"
assert_jq_eq '.npi' "$DUP_NPI" "the NPI round-trips"

when "someone else is hired on the same NPI"
req POST "/pharmacies/${PH}/staff/" "$(jq -nc --arg reg "$(gen_registration)" --arg npi "$DUP_NPI" '{
  first_name: "Duplicate", last_name: "Npi", role: "pharmacist",
  registration_state: "OR", registration_number: $reg, npi: $npi }')"
assert_status 409 "it is refused with 409"
assert_error_code "duplicate_npi" "the error code names the NPI"
assert_jq_eq '.error.details.existing_id' "$RPH" "it points at the record already holding it"

# Blank NPIs are excluded from the unique index too, so the many staff without
# one never collide with each other.
when "the roster is listed"
req GET "/pharmacies/${PH}/staff/?page_size=100"
assert_status 200 "the roster is returned"
assert_jq '[.results[] | select(.npi == "")] | length >= 2' \
  "more than one live staff member carries no NPI at all"

section "read and update"
when "a staff member is deactivated"
req PATCH "/pharmacies/${PH}/staff/${RPH}/" '{"is_active":false}'
assert_status 200 "the update is accepted"
assert_jq_eq '.is_active' 'false' "the flag took effect"
assert_jq_eq '.registration_number' "$REG" "they remain resolvable by registration"

when "they are reactivated"
req PATCH "/pharmacies/${PH}/staff/${RPH}/" '{"is_active":true}'
assert_status 200 "the update is accepted"

when "the staff member is replaced, without their middle name"
req PUT "/pharmacies/${PH}/staff/${RPH}/" "$(jq -nc --arg last "$LAST" --arg reg "$REG" --arg npi "$DUP_NPI" '{
  first_name: "Replacila", last_name: $last, role: "pharmacist",
  registration_state: "OR", registration_number: $reg, npi: $npi }')"
assert_status 200 "the staff member is replaced"
assert_jq_eq '.first_name' 'Replacila' "the new first name took effect"
# Optional fields are required=False, so a PUT that omits one leaves it where
# it was rather than blanking it -- the same on every resource here.
assert_jq_eq '.middle_name' 'Q' "the omitted optional field is left alone"

when "the staff member is replaced without their required fields"
req PUT "/pharmacies/${PH}/staff/${RPH}/" '{"first_name":"Incomplete"}'
assert_status 400 "it is rejected"

section "filters, search and ordering"
when "the roster is filtered by registration number"
req GET "/pharmacies/${PH}/staff/?registration_number=${REG}"
assert_status 200 "the filtered listing is returned"
assert_jq_eq '.count' '1' "exactly one staff member holds it"
assert_jq_eq '.results[0].id' "$RPH" "it is the record that was filtered for"

when "the same number is given in lower case"
req GET "/pharmacies/${PH}/staff/?registration_number=$(echo "$REG" | tr '[:upper:]' '[:lower:]')"
assert_status 200 "the filter is case-insensitive"
assert_jq_eq '.count' '1' "it finds the same record"

when "the roster is filtered by a lower-case issuing state"
req GET "/pharmacies/${PH}/staff/?registration_state=or&page_size=100"
assert_status 200 "the filter is case-insensitive"
assert_jq 'all(.results[]; .registration_state == "OR")' "only Oregon registrations come back"

when "the roster is filtered to certified technicians"
req GET "/pharmacies/${PH}/staff/?role=certified_technician&page_size=100"
assert_status 200 "the filtered listing is returned"
assert_jq 'all(.results[]; .role == "certified_technician")' "only certified technicians come back"
assert_jq 'any(.results[]; .id == $s)' "the certified technician is among them" --arg s "$CPHT"

when "the roster is filtered by certifying body"
req GET "/pharmacies/${PH}/staff/?certification_body=PTCB&page_size=100"
assert_status 200 "the filtered listing is returned"
assert_jq 'all(.results[]; .certification_body == "PTCB")' "only PTCB certifications come back"

when "the roster is filtered to inactive staff"
req GET "/pharmacies/${PH}/staff/?is_active=false&page_size=100"
assert_status 200 "the filtered listing is returned"
assert_jq 'all(.results[]; .is_active == false)' "only inactive staff come back"

when "the roster is filtered by NPI"
req GET "/pharmacies/${PH}/staff/?npi=${DUP_NPI}"
assert_status 200 "the filtered listing is returned"
assert_jq_eq '.count' '1' "exactly one staff member holds it"

when "the roster is searched for a last name"
req GET "/pharmacies/${PH}/staff/?search=${LAST}"
assert_status 200 "the search results are returned"
assert_jq_eq '.count' '1' "it finds the record"
assert_jq_eq '.results[0].id' "$RPH" "it is the record that was searched for"

when "the roster is searched for a registration number"
req GET "/pharmacies/${PH}/staff/?search=${REG}"
assert_status 200 "the search results are returned"
assert_jq_eq '.count' '1' "it finds the record too"

when "the roster is ordered by last name"
req GET "/pharmacies/${PH}/staff/?ordering=last_name&page_size=100"
assert_status 200 "the ordered listing is returned"
assert_jq '[.results[].last_name] == ([.results[].last_name] | sort)' \
  "the page is ordered by last name"

section "deleting the pharmacist-in-charge of a live location is refused"
make_address; PIC_ADDR="$NEW_ID"
make_staff "$PH"; PIC="$NEW_ID"
make_location "$PH" "$PIC_ADDR" "$(jq -nc --arg s "$PIC" '{pharmacist_in_charge_id: $s}')"
PIC_STORE="$NEW_ID"

when "the pharmacist a live store depends on is deleted"
req DELETE "/pharmacies/${PH}/staff/${PIC}/"
assert_status 409 "the delete is refused with 409"
assert_error_code "referenced" "the error code is referenced"
assert_jq '.error.details.referenced_by | has("pharmacy locations")' \
  "the conflict names pharmacy locations as the referencing resource"

given "the location drops the designation" \
  req PATCH "/pharmacies/${PH}/locations/${PIC_STORE}/" '{"pharmacist_in_charge_id":null}'

when "they are deleted again"
req DELETE "/pharmacies/${PH}/staff/${PIC}/"
assert_status 204 "they are deletable once no live location names them"
when "the deleted pharmacist is read back"
req GET "/pharmacies/${PH}/staff/${PIC}/"
assert_status 404 "they are no longer retrievable"

section "delete"
make_staff "$PH"; SPARE="$NEW_ID"
when "a staff member nothing refers to is deleted"
req DELETE "/pharmacies/${PH}/staff/${SPARE}/"
assert_status 204 "they are deleted"
when "the deleted staff member is read back"
req GET "/pharmacies/${PH}/staff/${SPARE}/"
assert_status 404 "they are no longer retrievable"
when "the roster is listed"
req GET "/pharmacies/${PH}/staff/?page_size=100"
assert_jq 'all(.results[]; .id != $s)' "they are no longer on it" --arg s "$SPARE"
