#!/usr/bin/env bash
set -uo pipefail
source "$E2E_DIR/lib/http.sh"
source "$E2E_DIR/lib/gen.sh"
source "$E2E_DIR/lib/fixtures.sh"

# The dispensing workflow end to end, against the home pharmacy (Acme, seeded
# by migration 0003) rather than a fresh store minted on every run: staff, the
# three records a prescription points at, then the prescription itself and the
# fills taken against it. Every step asserts what it created, because a wrong
# id at the top shows up as a puzzling refusal at the bottom.
#
# Two people, because the workflow needs two: a technician starts a fill and a
# pharmacist verifies it. Both act through X-Staff-Id, which is how a write
# names the person performing it -- the API credentials say which client is
# calling, not who is at the counter.

section "the pharmacy and its staff"

use_home_pharmacy; PHARMACY="$HOME_PHARMACY"; LOCATION="$HOME_LOCATION"

PHARMACIST_LAST="Pharmacist-${RUN_TAG}x$(_rand 1000000)"
PHARMACIST_REG="$(gen_registration)"
when "a pharmacist is hired"
req POST "/pharmacies/${PHARMACY}/staff/" "$(jq -nc \
  --arg last "$PHARMACIST_LAST" --arg reg "$PHARMACIST_REG" '{
    first_name: "Perry", last_name: $last, role: "pharmacist",
    registration_state: "OR", registration_number: $reg }')"
assert_status 201 "the pharmacist is created"
assert_jq_eq '.role' 'pharmacist' "the role round-trips"
assert_jq_eq '.registration_number' "$PHARMACIST_REG" "the state registration round-trips"
assert_jq_eq '.is_active' 'true' "a new hire is active"
assert_jq_eq '.pharmacy.id' "$PHARMACY" "they are employed by the pharmacy in the path"
PHARMACIST="$(jqv .id)"

TECH_LAST="Tech-${RUN_TAG}x$(_rand 1000000)"
TECH_REG="$(gen_registration)"
when "a pharmacy technician is hired"
req POST "/pharmacies/${PHARMACY}/staff/" "$(jq -nc \
  --arg last "$TECH_LAST" --arg reg "$TECH_REG" '{
    first_name: "Tessa", last_name: $last, role: "technician",
    registration_state: "OR", registration_number: $reg }')"
assert_status 201 "the technician is created"
assert_jq_eq '.role' 'technician' "the role round-trips"
assert_jq_eq '.is_active' 'true' "a new hire is active"
assert_jq_eq '.pharmacy.id' "$PHARMACY" "they work at the same pharmacy"
TECH="$(jqv .id)"

section "the patient"

make_address; PATIENT_ADDRESS="$NEW_ID"
PATIENT_LAST="Patient-${RUN_TAG}x$(_rand 1000000)"
PATIENT_PHONE="$(gen_phone)"
when "a patient is registered"
req POST /patients/ "$(jq -nc \
  --arg last "$PATIENT_LAST" --arg addr "$PATIENT_ADDRESS" \
  --arg phone "$PATIENT_PHONE" --arg email "$(gen_email)" '{
    first_name: "Pat", last_name: $last, dob: "1985-03-14", gender: "female",
    phone_number: $phone, email: $email, sms_opt_in: false, address_id: $addr }')"
assert_status 201 "the patient is created"
assert_jq_eq '.last_name' "$PATIENT_LAST" "the name round-trips"
assert_jq_eq '.dob' '1985-03-14' "the date of birth round-trips"
assert_jq_eq '.phone_number' "$PATIENT_PHONE" "the phone number round-trips"
assert_jq_eq '.address.id' "$PATIENT_ADDRESS" "the address is nested on the read"
PATIENT="$(jqv .id)"

section "the prescriber"

PRESCRIBER_NPI="$(gen_npi)"
PRESCRIBER_LAST="Prescriber-${RUN_TAG}x$(_rand 1000000)"
when "a prescriber is registered"
req POST /prescribers/ "$(jq -nc \
  --arg last "$PRESCRIBER_LAST" --arg npi "$PRESCRIBER_NPI" --arg addr "$PATIENT_ADDRESS" '{
    first_name: "Dana", last_name: $last, practitioner_type: "MD",
    npi: $npi, address_id: $addr }')"
assert_status 201 "the prescriber is created"
assert_jq_eq '.npi' "$PRESCRIBER_NPI" "the NPI round-trips"
assert_jq_eq '.practitioner_type' 'MD' "the practitioner type round-trips"
PRESCRIBER="$(jqv .id)"

section "the medication"

MED_NDC="$(gen_ndc)"
MED_NAME="Testazepam ${RUN_TAG}x$(_rand 1000000)"
when "a medication is added to the catalog"
req POST /medications/ "$(jq -nc --arg name "$MED_NAME" --arg ndc "$MED_NDC" '{
  name: $name, ndc: $ndc, strength: "10 mg", form: "tablet" }')"
assert_status 201 "the medication is created"
assert_jq_eq '.name' "$MED_NAME" "the name round-trips"
assert_jq_eq '.ndc' "$(ndc_digits "$MED_NDC")" "the NDC is stored as eleven digits"
assert_jq_eq '.formatted_ndc' "$(ndc_display_form "$MED_NDC")" "the display form is 5-4-2"
MEDICATION="$(jqv .id)"

section "the prescription"

# Thirty tablets, three fills, a year to use them: 90 units authorized in all.
WRITTEN_AT="$(iso_days 0)"
EXPIRES_AT="$(iso_days 365)"
when "the prescription is entered"
req POST /prescriptions/ "$(jq -nc \
  --arg p "$PATIENT" --arg d "$PRESCRIBER" --arg m "$MEDICATION" --arg l "$LOCATION" \
  --arg w "$WRITTEN_AT" --arg e "$EXPIRES_AT" '{
    patient_id: $p, prescriber_id: $d, medication_id: $m, pharmacy_location_id: $l,
    intake_source: "provider",
    dose_amount: "1 tablet", quantity: 30, fills_authorized: 3,
    instructions: "Take one tablet by mouth daily.",
    status: "active", written_at: $w, expires_at: $e }')"
assert_status 201 "the prescription is created"
assert_jq_eq '.status' 'active' "it starts active"
assert_jq_eq '.quantity' '30' "the written quantity is thirty"
assert_jq_eq '.fills_authorized' '3' "three fills are authorized"
assert_jq_eq '.patient.id' "$PATIENT" "it names the patient"
assert_jq_eq '.prescriber.id' "$PRESCRIBER" "it names the prescriber"
assert_jq_eq '.medication.id' "$MEDICATION" "it names the medication"
assert_jq_eq '.pharmacy_location.id' "$LOCATION" "it is routed to the store that was named"
assert_jq '.rx_number > 0' "it is issued an Rx number"
assert_jq '.written_at | startswith($d)' "it was written today" --arg d "${WRITTEN_AT%%T*}"
assert_jq '.expires_at | startswith($d)' "it expires a year out" --arg d "${EXPIRES_AT%%T*}"
# The totals are derived from the fills, so a fresh prescription has all of
# its authorized quantity and none of it spoken for.
assert_jq_eq '.quantity_authorized' '90' "ninety units are authorized in all"
assert_jq_eq '.quantity_dispensed' '0' "nothing has been dispensed"
assert_jq_eq '.quantity_in_progress' '0' "nothing is in progress"
assert_jq_eq '.quantity_remaining' '90' "all ninety units remain"
assert_jq '.fills_used == 0' "no fills have been used"
assert_jq '.fills_remaining == 3' "three fills remain"
RX="$(jqv .id)"
RX_NUMBER="$(jqv .rx_number)"

when "its fills are listed before any exist"
req GET "/prescriptions/${RX}/fills/"
assert_status 200 "the list is returned"
assert_jq '.results | length == 0' "there are no fills yet"

section "a whole fill: the technician starts it"

# Who performed the write is the header, not the credentials, so a fill with
# no X-Staff-Id is refused before anything is reserved.
when "a fill is started without naming the operator"
req POST "/prescriptions/${RX}/fills/" '{"quantity_dispensed":30,"days_supply":30}'
assert_status 400 "it is refused with 400"
assert_error_code "staff_required" "the error code is staff_required"

when "the technician starts a fill for the whole written quantity"
req_staff "$TECH" POST "/prescriptions/${RX}/fills/" '{"quantity_dispensed":30,"days_supply":30}'
assert_status 201 "the fill is created"
assert_jq_eq '.status' 'pending' "a new fill is pending"
assert_jq_eq '.sequence' '1' "it is the first fill on this prescription"
assert_jq_eq '.quantity_dispensed' '30' "it is for thirty tablets"
assert_jq_eq '.days_supply' '30' "it carries a thirty-day supply"
assert_jq_eq '.is_partial' 'false' "a whole written quantity is not a partial fill"
assert_jq_eq '.operator.id' "$TECH" "the technician is recorded as the operator"
assert_jq_eq '.verified_by' 'null' "nobody has verified it yet"
assert_jq_eq '.dispensed_at' 'null' "nothing has been dispensed yet"
assert_jq_eq '.rx_number' "$RX_NUMBER" "it carries the prescription's Rx number"
assert_jq_eq '.pharmacy_location.id' "$LOCATION" \
  "it records where the dispensing happens"
FILL="$(jqv .id)"

# A pending fill holds its quantity against the prescription without having
# dispensed any of it: in progress, not dispensed.
when "the prescription is read while the fill is pending"
req GET "/prescriptions/${RX}/"
assert_status 200 "the prescription is returned"
assert_jq_eq '.status' 'active' "it is still active"
assert_jq_eq '.quantity_dispensed' '0' "nothing is dispensed yet"
assert_jq_eq '.quantity_in_progress' '30' "the pending fill holds thirty units"
assert_jq_eq '.quantity_remaining' '60' "sixty units are still free"

section "a whole fill: the pharmacist verifies it"

when "the technician tries to verify their own fill"
req_staff "$TECH" POST "/fills/${FILL}/verify/"
assert_status 400 "it is refused with 400"
assert_error_code "pharmacist_required" "the error code is pharmacist_required"
assert_jq_eq '.error.details.role' 'technician' "the refusal reports the role it saw"

when "the pharmacist verifies the fill"
req_staff "$PHARMACIST" POST "/fills/${FILL}/verify/"
assert_status 200 "the verification is accepted"
assert_jq_eq '.status' 'verified' "the fill is now verified"
assert_jq_eq '.verified_by.id' "$PHARMACIST" "the pharmacist is recorded as the verifier"
assert_jq_eq '.operator.id' "$TECH" "the technician is still the operator"
assert_jq_eq '.dispensed_at' 'null' "verifying does not dispense"

section "a whole fill: completing it"

when "the fill is completed"
req_staff "$PHARMACIST" POST "/fills/${FILL}/complete/"
assert_status 200 "the completion is accepted"
assert_jq_eq '.status' 'completed' "the fill is now completed"
assert_jq '.dispensed_at != null' "it is stamped with the dispensing time"
assert_jq_eq '.quantity_dispensed' '30' "thirty tablets went out the door"
assert_jq_eq '.verified_by.id' "$PHARMACIST" "the verifier is still recorded"

when "the completed fill is completed again"
req_staff "$PHARMACIST" POST "/fills/${FILL}/complete/"
assert_status 409 "it is refused with 409"
assert_error_code "invalid_transition" "the error code is invalid_transition"
assert_jq_eq '.error.details.status' 'completed' "the refusal reports the current status"

section "a whole fill: what the prescription has left"

when "the prescription is read after the first fill"
req GET "/prescriptions/${RX}/"
assert_status 200 "the prescription is returned"
assert_jq_eq '.status' 'active' "two fills left keeps it active"
assert_jq_eq '.quantity_authorized' '90' "ninety units were authorized"
assert_jq_eq '.quantity_dispensed' '30' "thirty have been dispensed"
assert_jq_eq '.quantity_in_progress' '0' "nothing is in progress"
assert_jq_eq '.quantity_remaining' '60' "sixty remain"
assert_jq '.fills_used == 1' "one fill has been used"
assert_jq '.fills_remaining == 2' "two fills remain"

when "the prescription's fills are listed"
req GET "/prescriptions/${RX}/fills/"
assert_status 200 "the list is returned"
assert_jq '.results | length == 1' "there is exactly one fill"
assert_jq '.results[0].id == $f' "it is the fill that was dispensed" --arg f "$FILL"
assert_jq '.results[0].status == "completed"' "it is completed"
assert_jq '.results[0].sequence == 1' "it is numbered one"

section "a partial fill: dispensing less than the written quantity"

# A partial fill is one that dispenses less than the prescription was written
# for -- half of it here. is_partial is derived from the two quantities, so it
# cannot go stale, and the prescription's own totals move by what actually
# went out rather than by a whole fill.
when "a second prescription is entered"
req POST /prescriptions/ "$(jq -nc \
  --arg p "$PATIENT" --arg d "$PRESCRIBER" --arg m "$MEDICATION" --arg l "$LOCATION" \
  --arg w "$WRITTEN_AT" --arg e "$EXPIRES_AT" '{
    patient_id: $p, prescriber_id: $d, medication_id: $m, pharmacy_location_id: $l,
    intake_source: "provider",
    dose_amount: "1 tablet", quantity: 30, fills_authorized: 3,
    instructions: "Take one tablet by mouth daily.",
    status: "active", written_at: $w, expires_at: $e }')"
assert_status 201 "the prescription is created"
assert_jq_eq '.quantity' '30' "it is also written for thirty tablets"
assert_jq_eq '.quantity_remaining' '90' "all ninety units remain"
PARTIAL_RX="$(jqv .id)"

when "the technician starts a fill for half the written quantity"
req_staff "$TECH" POST "/prescriptions/${PARTIAL_RX}/fills/" \
  '{"quantity_dispensed":15,"days_supply":15}'
assert_status 201 "the fill is created"
assert_jq_eq '.status' 'pending' "a new fill is pending"
assert_jq_eq '.sequence' '1' "it is the first fill on this prescription"
assert_jq_eq '.quantity_dispensed' '15' "it is for fifteen tablets"
assert_jq_eq '.days_supply' '15' "half the quantity is half the days' supply"
assert_jq_eq '.is_partial' 'true' "it is flagged as a partial fill"
assert_jq_eq '.operator.id' "$TECH" "the technician is recorded as the operator"
PARTIAL_FILL="$(jqv .id)"

when "the pharmacist verifies the partial fill"
req_staff "$PHARMACIST" POST "/fills/${PARTIAL_FILL}/verify/"
assert_status 200 "the verification is accepted"
assert_jq_eq '.status' 'verified' "the fill is now verified"
assert_jq_eq '.verified_by.id' "$PHARMACIST" "the pharmacist is recorded as the verifier"
assert_jq_eq '.is_partial' 'true' "it is still a partial fill"

when "the partial fill is completed"
req_staff "$PHARMACIST" POST "/fills/${PARTIAL_FILL}/complete/"
assert_status 200 "the completion is accepted"
assert_jq_eq '.status' 'completed' "the fill is now completed"
assert_jq '.dispensed_at != null' "it is stamped with the dispensing time"
assert_jq_eq '.quantity_dispensed' '15' "fifteen tablets went out the door"

when "the prescription is read after the partial fill"
req GET "/prescriptions/${PARTIAL_RX}/"
assert_status 200 "the prescription is returned"
assert_jq_eq '.status' 'active' "it stays active"
assert_jq_eq '.quantity_authorized' '90' "ninety units were authorized"
assert_jq_eq '.quantity_dispensed' '15' "fifteen have been dispensed"
assert_jq_eq '.quantity_in_progress' '0' "nothing is in progress"
assert_jq_eq '.quantity_remaining' '75' "seventy-five remain"
# Fills are a fraction once a partial fill exists: half a fill's worth is gone,
# so two and a half are left rather than two.
assert_jq '.fills_used == 0.5' "half a fill has been used"
assert_jq '.fills_remaining == 2.5' "two and a half fills remain"

when "the prescription's fills are listed"
req GET "/prescriptions/${PARTIAL_RX}/fills/"
assert_status 200 "the list is returned"
assert_jq '.results | length == 1' "the partial fill is the only one on the record"
assert_jq '.results[0].id == $f' "it is the fill that was dispensed" --arg f "$PARTIAL_FILL"
assert_jq '.results[0].sequence == 1' "it is numbered one"
assert_jq '.results[0].status == "completed"' "it is completed"
assert_jq '.results[0].is_partial' "it is a partial fill"
assert_jq '.results[0].quantity_dispensed == 15' \
  "its quantity is what the prescription counts as dispensed"
