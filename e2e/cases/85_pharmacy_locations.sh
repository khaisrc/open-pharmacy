#!/usr/bin/env bash
set -uo pipefail
source "$E2E_DIR/lib/http.sh"
source "$E2E_DIR/lib/gen.sh"
source "$E2E_DIR/lib/fixtures.sh"

RX_SEED="$(_rand 900000000)"
make_pharmacy '{"rx_number_seed": '"$RX_SEED"'}'; PH="$NEW_ID"
make_address;       ADDR="$NEW_ID"

section "create under a pharmacy"
NCPDP="$(gen_ncpdp)"
when "a store is opened under that pharmacy"
req POST "/pharmacies/${PH}/locations/" "$(jq -nc --arg ncpdp "$NCPDP" --arg addr "$ADDR" \
                                               --arg phone "$(gen_phone)" '{
  store_number: "0041", ncpdp_id: $ncpdp, location_type: "retail",
  phone_number: $phone, address_id: $addr, is_24_hour: true }')"
assert_status 201 "the location is created"
assert_jq_eq '.ncpdp_id' "$NCPDP" "the NCPDP ID round-trips"
assert_jq_eq '.store_number' '0041' "the store number round-trips"
assert_jq_eq '.is_24_hour' 'true' "is_24_hour round-trips"
assert_jq_eq '.pharmacy.id' "$PH" "the response nests the pharmacy from the path"
assert_jq_eq '.address.id' "$ADDR" "it nests the address it was given"
assert_jq_eq '.pharmacist_in_charge' 'null' "no pharmacist-in-charge is designated yet"
LOC="$(jqv .id)"

when "the store is read back under its pharmacy"
req GET "/pharmacies/${PH}/locations/${LOC}/"
assert_status 200 "the location is returned"
assert_jq_eq '.id' "$LOC" "it is the location that was asked for"

when "the pharmacy's stores are listed"
req GET "/pharmacies/${PH}/locations/"
assert_status 200 "the listing is returned"
assert_jq 'any(.results[]; .id == $l)' "the new location is in it" --arg l "$LOC"

section "the owning pharmacy comes from the path, never the body"
# A location's identity includes its parent, so there is no pharmacy_id write
# field at all -- a body carrying one is ignored rather than obeyed.
make_pharmacy; OTHER_PH="$NEW_ID"

when "a store is opened with a body naming a different pharmacy"
req POST "/pharmacies/${PH}/locations/" "$(jq -nc --arg ncpdp "$(gen_ncpdp)" --arg addr "$ADDR" \
                                               --arg phone "$(gen_phone)" --arg other "$OTHER_PH" '{
  ncpdp_id: $ncpdp, phone_number: $phone, address_id: $addr, pharmacy_id: $other }')"
assert_status 201 "it still succeeds"
assert_jq_eq '.pharmacy.id' "$PH" "the location belongs to the pharmacy in the path"
STRAY="$(jqv .id)"

when "a client tries to move it to that other pharmacy"
req PATCH "/pharmacies/${PH}/locations/${STRAY}/" "$(jq -nc --arg other "$OTHER_PH" '{pharmacy_id: $other}')"
assert_status 200 "the update is accepted"
assert_jq_eq '.pharmacy.id' "$PH" "but the location has not moved"

section "a location is invisible outside its own pharmacy"
when "the store is read under a pharmacy that does not own it"
req GET "/pharmacies/${OTHER_PH}/locations/${LOC}/"
assert_status 404 "it is 404"
assert_error_code "not_found" "the error code is not_found"

when "that other pharmacy's stores are listed"
req GET "/pharmacies/${OTHER_PH}/locations/"
assert_status 200 "the listing is returned"
assert_jq 'all(.results[]; .id != $l)' "it does not contain the location" --arg l "$LOC"

when "the store is deleted through the pharmacy that does not own it"
req DELETE "/pharmacies/${OTHER_PH}/locations/${LOC}/"
assert_status 404 "it is 404 there too"

section "an unknown pharmacy is 404 on every nested route"
# Not an empty list: a client must not be able to tell a pharmacy with no
# stores from a pharmacy that does not exist.
when "stores are listed under a pharmacy that does not exist"
req GET "/pharmacies/${MISSING_UUID}/locations/"
assert_status 404 "it is 404, not an empty page"
assert_error_code "not_found" "the error code is not_found"

when "a store is opened under a pharmacy that does not exist"
req POST "/pharmacies/${MISSING_UUID}/locations/" "$(jq -nc --arg ncpdp "$(gen_ncpdp)" --arg addr "$ADDR" \
                                                        --arg phone "$(gen_phone)" '{
  ncpdp_id: $ncpdp, phone_number: $phone, address_id: $addr }')"
assert_status 404 "it is 404"

when "a store is read under a pharmacy that does not exist"
req GET "/pharmacies/${MISSING_UUID}/locations/${LOC}/"
assert_status 404 "it is 404"

when "stores are listed under a pharmacy id that is not a UUID"
req GET "/pharmacies/not-a-uuid/locations/"
assert_status 404 "it is 404"
assert_error_code "not_found" "it is the API's error envelope, not Django's HTML page"

section "NCPDP ID"
when "a second store claims the same NCPDP ID"
req POST "/pharmacies/${PH}/locations/" "$(jq -nc --arg ncpdp "$NCPDP" --arg addr "$ADDR" \
                                               --arg phone "$(gen_phone)" '{
  ncpdp_id: $ncpdp, phone_number: $phone, address_id: $addr }')"
assert_status 409 "it is refused with 409"
assert_error_code "duplicate_ncpdp_id" "the error code names the NCPDP ID"

# Globally unique, not per pharmacy: a claim names the store, not its owner.
when "a store under another pharmacy claims it"
req POST "/pharmacies/${OTHER_PH}/locations/" "$(jq -nc --arg ncpdp "$NCPDP" --arg addr "$ADDR" \
                                                     --arg phone "$(gen_phone)" '{
  ncpdp_id: $ncpdp, phone_number: $phone, address_id: $addr }')"
assert_status 409 "another pharmacy cannot take it either"

for bad in "123" "12345678" "abcdefg" ""; do
  when "a store is opened with an NCPDP ID of '${bad}'"
  req POST "/pharmacies/${PH}/locations/" "$(jq -nc --arg ncpdp "$bad" --arg addr "$ADDR" \
                                                 --arg phone "$(gen_phone)" '{
    ncpdp_id: $ncpdp, phone_number: $phone, address_id: $addr }')"
  assert_status 400 "it is rejected"
done

section "store numbers are unique per pharmacy, not globally"
when "a second store in the same pharmacy takes the number 0041"
req POST "/pharmacies/${PH}/locations/" "$(jq -nc --arg ncpdp "$(gen_ncpdp)" --arg addr "$ADDR" \
                                               --arg phone "$(gen_phone)" '{
  store_number: "0041", ncpdp_id: $ncpdp, phone_number: $phone, address_id: $addr }')"
assert_status 409 "it is refused with 409"
assert_error_code "duplicate_store_number" "the error code names the store number"

when "a store under another pharmacy takes the number 0041"
req POST "/pharmacies/${OTHER_PH}/locations/" "$(jq -nc --arg ncpdp "$(gen_ncpdp)" --arg addr "$ADDR" \
                                                     --arg phone "$(gen_phone)" '{
  store_number: "0041", ncpdp_id: $ncpdp, phone_number: $phone, address_id: $addr }')"
assert_status 201 "another pharmacy may have its own store 0041"

section "NPI"
BAD_NPI="$(gen_bad_npi)"
when "a store is opened with an NPI whose check digit is wrong"
req POST "/pharmacies/${PH}/locations/" "$(jq -nc --arg ncpdp "$(gen_ncpdp)" --arg addr "$ADDR" \
                                               --arg phone "$(gen_phone)" --arg npi "$BAD_NPI" '{
  ncpdp_id: $ncpdp, phone_number: $phone, address_id: $addr, npi: $npi }')"
assert_status 400 "it is rejected"

DUP_NPI="$(gen_npi)"
when "a valid NPI is patched onto the first store"
req PATCH "/pharmacies/${PH}/locations/${LOC}/" "$(jq -nc --arg npi "$DUP_NPI" '{npi: $npi}')"
assert_status 200 "it is accepted"
assert_jq_eq '.npi' "$DUP_NPI" "the NPI round-trips"

when "a second store claims the same NPI"
req POST "/pharmacies/${PH}/locations/" "$(jq -nc --arg ncpdp "$(gen_ncpdp)" --arg addr "$ADDR" \
                                               --arg phone "$(gen_phone)" --arg npi "$DUP_NPI" '{
  ncpdp_id: $ncpdp, phone_number: $phone, address_id: $addr, npi: $npi }')"
assert_status 409 "it is refused with 409"
assert_error_code "duplicate_npi" "the error code names the NPI"

section "the pharmacist-in-charge"
make_staff "$PH"; PIC="$NEW_ID"

when "the store names that pharmacist as its pharmacist-in-charge"
req PATCH "/pharmacies/${PH}/locations/${LOC}/" "$(jq -nc --arg s "$PIC" '{pharmacist_in_charge_id: $s}')"
assert_status 200 "the designation is accepted"
assert_jq_eq '.pharmacist_in_charge.id' "$PIC" "the location reads back the pharmacist"
assert_jq_eq '.pharmacist_in_charge.role' 'pharmacist' "the summary carries the role"
assert_jq '.pharmacist_in_charge | has("registration_number")' \
  "the summary carries what a board lookup needs"
assert_jq '.pharmacist_in_charge | has("npi") | not' \
  "the summary is not the whole staff record"

when "the designation is cleared"
req PATCH "/pharmacies/${PH}/locations/${LOC}/" '{"pharmacist_in_charge_id":null}'
assert_status 200 "it is accepted"
assert_jq_eq '.pharmacist_in_charge' 'null' "the location names nobody again"

make_staff "$PH" '{"role":"technician","registration_state":"","registration_number":""}'
TECH="$NEW_ID"
when "the store names a technician as its pharmacist-in-charge"
req PATCH "/pharmacies/${PH}/locations/${LOC}/" "$(jq -nc --arg s "$TECH" '{pharmacist_in_charge_id: $s}')"
assert_status 400 "it is rejected"
assert_error_code "validation_error" "the error code is validation_error"
assert_jq '.error.details | has("pharmacist_in_charge_id")' "the error names the field"

make_staff "$PH" '{"is_active":false}'; LAPSED="$NEW_ID"
when "the store names an inactive pharmacist"
req PATCH "/pharmacies/${PH}/locations/${LOC}/" "$(jq -nc --arg s "$LAPSED" '{pharmacist_in_charge_id: $s}')"
assert_status 400 "it is rejected"

make_staff "$OTHER_PH"; FOREIGN="$NEW_ID"
when "the store names a pharmacist employed by another pharmacy"
req PATCH "/pharmacies/${PH}/locations/${LOC}/" "$(jq -nc --arg s "$FOREIGN" '{pharmacist_in_charge_id: $s}')"
assert_status 400 "it is rejected"

when "a store is opened already naming its pharmacist-in-charge"
req POST "/pharmacies/${PH}/locations/" "$(jq -nc --arg ncpdp "$(gen_ncpdp)" --arg addr "$ADDR" \
                                               --arg phone "$(gen_phone)" --arg s "$PIC" '{
  ncpdp_id: $ncpdp, phone_number: $phone, address_id: $addr, pharmacist_in_charge_id: $s }')"
assert_status 201 "it is created"
assert_jq_eq '.pharmacist_in_charge.id' "$PIC" "the new location reads it back"
PIC_STORE="$(jqv .id)"

# Write-time checks only: a designation already made survives the pharmacist
# going inactive, because revoking it is a separate decision.
when "the designated pharmacist is marked inactive"
req PATCH "/pharmacies/${PH}/staff/${PIC}/" '{"is_active":false}'
assert_status 200 "the update is accepted"
when "their store is read back"
req GET "/pharmacies/${PH}/locations/${PIC_STORE}/"
assert_jq_eq '.pharmacist_in_charge.id' "$PIC" "it still names them"
when "the pharmacist is made active again"
req PATCH "/pharmacies/${PH}/staff/${PIC}/" '{"is_active":true}'
assert_status 200 "the update is accepted"

section "filters, search and ordering"
when "the pharmacy's stores are filtered by NCPDP ID"
req GET "/pharmacies/${PH}/locations/?ncpdp_id=${NCPDP}"
assert_status 200 "the filtered listing is returned"
assert_jq_eq '.count' '1' "exactly one location matches"
assert_jq_eq '.results[0].id' "$LOC" "it is the location that was filtered for"

when "they are filtered to the ones open around the clock"
req GET "/pharmacies/${PH}/locations/?is_24_hour=true"
assert_status 200 "the filtered listing is returned"
assert_jq 'all(.results[]; .is_24_hour == true)' "only 24-hour stores come back"
assert_jq 'any(.results[]; .id == $l)' "the 24-hour store is among them" --arg l "$LOC"

when "they are filtered to retail stores"
req GET "/pharmacies/${PH}/locations/?location_type=retail&page_size=100"
assert_status 200 "the filtered listing is returned"
assert_jq 'all(.results[]; .location_type == "retail")' "only retail stores come back"

when "they are filtered by a lower-case state code"
req GET "/pharmacies/${PH}/locations/?state=or&page_size=100"
assert_status 200 "the filter is case-insensitive"
assert_jq 'all(.results[]; .address.state == "OR")' "only Oregon stores come back"

when "they are searched for that NCPDP ID"
req GET "/pharmacies/${PH}/locations/?search=${NCPDP}"
assert_status 200 "the search results are returned"
assert_jq_eq '.count' '1' "the NCPDP ID finds its store"

when "they are ordered by store number"
req GET "/pharmacies/${PH}/locations/?ordering=store_number&page_size=100"
assert_status 200 "the ordered listing is returned"
assert_jq '[.results[].store_number] == ([.results[].store_number] | sort)' \
  "the page is ordered by store number"

section "a location's prescriptions"
make_cast
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" \
  "$(jq -nc --arg l "$LOC" '{pharmacy_location_id: $l}')"
RX="$NEW_ID"

when "the store's prescriptions are listed"
req GET "/pharmacies/${PH}/locations/${LOC}/prescriptions/"
assert_status 200 "a page of prescriptions is returned"
assert_jq_eq '.count' '1' "it holds exactly the one prescription written there"
assert_jq 'any(.results[]; .id == $rx)' "that prescription is in it" --arg rx "$RX"
assert_jq 'all(.results[]; .pharmacy_location.id == $l)' \
  "everything listed was dispensed at that location" --arg l "$LOC"

when "they are listed under a pharmacy that does not exist"
req GET "/pharmacies/${MISSING_UUID}/locations/${LOC}/prescriptions/"
assert_status 404 "it is 404"

when "they are listed under the wrong pharmacy"
req GET "/pharmacies/${OTHER_PH}/locations/${LOC}/prescriptions/"
assert_status 404 "it is 404 there too"

section "delete"
when "a store that filled a prescription is deleted"
req DELETE "/pharmacies/${PH}/locations/${LOC}/"
assert_status 409 "the delete is refused with 409"
assert_error_code "referenced" "the error code is referenced"
assert_jq '.error.details.referenced_by | has("prescriptions")' \
  "the conflict names prescriptions as the referencing resource"

when "a store nothing refers to is deleted"
req DELETE "/pharmacies/${PH}/locations/${STRAY}/"
assert_status 204 "it is deleted"
when "the deleted store is read back"
req GET "/pharmacies/${PH}/locations/${STRAY}/"
assert_status 404 "it is no longer retrievable"

# Soft delete frees the identifier: the NCPDP ID is unique among live rows.
when "another store's NCPDP ID is noted"
req GET "/pharmacies/${PH}/locations/${PIC_STORE}/"
FREED_NCPDP="$(jqv .ncpdp_id)"
when "that store is deleted"
req DELETE "/pharmacies/${PH}/locations/${PIC_STORE}/"
assert_status 204 "it is deleted"
when "a new store claims the freed NCPDP ID"
req POST "/pharmacies/${PH}/locations/" "$(jq -nc --arg ncpdp "$FREED_NCPDP" --arg addr "$ADDR" \
                                               --arg phone "$(gen_phone)" '{
  ncpdp_id: $ncpdp, phone_number: $phone, address_id: $addr }')"
assert_status 201 "the NCPDP ID is reusable once the live location holding it is gone"
