#!/usr/bin/env bash
set -uo pipefail
source "$SUITE_DIR/lib/http.sh"
source "$SUITE_DIR/lib/gen.sh"
source "$SUITE_PATH/lib/gen.sh"
source "$SUITE_PATH/lib/fixtures.sh"

make_cast

section "create"
req POST /prescriptions/ "$(jq -nc --arg p "$FX_PATIENT" --arg d "$FX_PRESCRIBER" --arg m "$FX_MEDICATION" '{
  patient_id: $p, prescriber_id: $d, medication_id: $m,
  dose_amount: "1 tablet", quantity: 30, fills_authorized: 3,
  instructions: "Take one tablet by mouth daily.", notes: "Suite fixture." }')"
assert_status 201 "POST /prescriptions/ creates a prescription"
assert_jq '.id | test("^[0-9a-f-]{36}$")' "the new prescription has a UUID id"
assert_jq_eq '.status' 'active' "a new prescription starts active"
assert_jq_eq '.fills_used' '0' "a new prescription has no fills used"
assert_jq_eq '.fills_authorized' '3' "fills_authorized round-trips"
assert_jq_eq '.dose_amount' '1 tablet' "dose_amount round-trips"
assert_jq 'has("written_at") and (.written_at != null)' "written_at defaults to now"
RX="$(jqv .id)"

# Three UUIDs go in; three nested objects come back, so rendering a prescription
# does not cost three more requests.
assert_jq_eq '.patient.id' "$FX_PATIENT" "the response nests the patient"
assert_jq_eq '.prescriber.id' "$FX_PRESCRIBER" "the response nests the prescriber"
assert_jq_eq '.medication.id' "$FX_MEDICATION" "the response nests the medication"
assert_jq 'has("patient_id") | not' "patient_id is write-only and not echoed back"
# The nested objects are summaries, not the full records: enough to render a
# prescription, without dragging each resource's whole representation along.
assert_jq '.medication | has("name") and has("ndc") and has("strength") and has("form")' \
  "the nested medication summary carries the fields a label needs"
assert_jq '.patient | has("first_name") and has("last_name") and has("dob")' \
  "the nested patient summary identifies the patient"
assert_jq '.prescriber | has("npi") and has("practitioner_type")' \
  "the nested prescriber summary identifies the prescriber"

req POST /prescriptions/ "$(jq -nc --arg p "$FX_PATIENT" --arg d "$FX_PRESCRIBER" --arg m "$FX_MEDICATION" '{
  patient_id: $p, prescriber_id: $d, medication_id: $m, dose_amount: "2 tablets", quantity: 60 }')"
assert_status 201 "fills_authorized may be left out"
assert_jq_eq '.fills_authorized' '1' "fills_authorized defaults to a single fill"

section "the status a prescription may be created in"
mk_with_status() {
  req POST /prescriptions/ "$(jq -nc --arg p "$FX_PATIENT" --arg d "$FX_PRESCRIBER" --arg m "$FX_MEDICATION" --arg s "$1" '{
    patient_id: $p, prescriber_id: $d, medication_id: $m, dose_amount: "1 tablet", quantity: 30, status: $s }')"
}

mk_with_status "active"
assert_status 201 "a prescription may be created explicitly active"

# completed, cancelled and expired are reached through /fill/ and /cancel/, so
# a client cannot post its way straight into a terminal state.
for terminal in completed cancelled expired; do
  mk_with_status "$terminal"
  assert_status 400 "a prescription cannot be created as '${terminal}'"
  assert_jq '.error.details.status | length > 0' "the '${terminal}' refusal names the status field"
done

mk_with_status "on_hold"
assert_status 400 "a prescription cannot be created on hold"

mk_with_status "nonsense"
assert_status 400 "an unknown status is rejected"

section "validation"
req POST /prescriptions/ '{"dose_amount":"1 tablet","quantity":30}'
assert_status 400 "the three foreign keys are required"
assert_jq '.error.details | has("patient_id") and has("prescriber_id") and has("medication_id")' \
  "the error names every missing foreign key"

req POST /prescriptions/ "$(jq -nc --arg p "$MISSING_UUID" --arg d "$FX_PRESCRIBER" --arg m "$FX_MEDICATION" '{
  patient_id: $p, prescriber_id: $d, medication_id: $m, dose_amount: "1 tablet", quantity: 30 }')"
assert_status 400 "a patient_id that does not exist is rejected"

req POST /prescriptions/ "$(jq -nc --arg p "$FX_PATIENT" --arg d "$FX_PRESCRIBER" --arg m "$FX_MEDICATION" '{
  patient_id: $p, prescriber_id: $d, medication_id: $m, quantity: 30 }')"
assert_status 400 "dose_amount is required"

req POST /prescriptions/ "$(jq -nc --arg p "$FX_PATIENT" --arg d "$FX_PRESCRIBER" --arg m "$FX_MEDICATION" '{
  patient_id: $p, prescriber_id: $d, medication_id: $m, dose_amount: "1 tablet", quantity: 0 }')"
assert_status 400 "a quantity of zero is rejected"

req POST /prescriptions/ "$(jq -nc --arg p "$FX_PATIENT" --arg d "$FX_PRESCRIBER" --arg m "$FX_MEDICATION" '{
  patient_id: $p, prescriber_id: $d, medication_id: $m, dose_amount: "1 tablet", quantity: 30, fills_authorized: 0 }')"
assert_status 400 "authorising zero fills is rejected"

req POST /prescriptions/ "$(jq -nc --arg p "$FX_PATIENT" --arg d "$FX_PRESCRIBER" --arg m "$FX_MEDICATION" \
  --arg w "$(iso_days -1)" --arg e "$(iso_days -30)" '{
  patient_id: $p, prescriber_id: $d, medication_id: $m, dose_amount: "1 tablet", quantity: 30,
  written_at: $w, expires_at: $e }')"
assert_status 400 "an expiry before the written date is rejected"
assert_jq '.error.details.expires_at | length > 0' "the expiry error names the expires_at field"

section "read and update"
req GET "/prescriptions/${RX}/"
assert_status 200 "GET /prescriptions/{id}/ returns the prescription"
assert_jq_eq '.id' "$RX" "the retrieved prescription has the requested id"

req PATCH "/prescriptions/${RX}/" '{"notes":"Patched by the suite."}'
assert_status 200 "PATCH updates the notes"
assert_jq_eq '.notes' 'Patched by the suite.' "the patched notes took effect"
assert_jq_eq '.dose_amount' '1 tablet' "PATCH left the other fields alone"

# fills_used moves only through POST /fill/. If a PATCH could set it, a client
# could route around the status changes that action owns.
req PATCH "/prescriptions/${RX}/" '{"fills_used":99}'
assert_status 200 "PATCH accepts a payload naming fills_used"
assert_jq_eq '.fills_used' '0' "PATCH cannot move fills_used"

req PATCH "/prescriptions/${RX}/" '{"id":"11111111-1111-4111-8111-111111111111"}'
assert_status 200 "PATCH accepts a payload naming id"
assert_jq_eq '.id' "$RX" "PATCH cannot change the id"

req PATCH "/prescriptions/${RX}/" "$(jq -nc --arg e "$(iso_days -3650)" '{expires_at: $e}')"
assert_status 400 "PATCH rejects an expiry before the written date"

req PUT "/prescriptions/${RX}/" "$(jq -nc --arg p "$FX_PATIENT" --arg d "$FX_PRESCRIBER" --arg m "$FX_MEDICATION" '{
  patient_id: $p, prescriber_id: $d, medication_id: $m, dose_amount: "3 tablets", quantity: 90 }')"
assert_status 200 "PUT replaces the prescription"
assert_jq_eq '.dose_amount' '3 tablets' "the replaced dose_amount took effect"

req PUT "/prescriptions/${RX}/" '{"dose_amount":"1 tablet"}'
assert_status 400 "PUT without the foreign keys is rejected"

section "status transitions a client may request"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION"; MOVER="$NEW_ID"

req PATCH "/prescriptions/${MOVER}/" '{"status":"on_hold"}'
assert_status 200 "an active prescription can be put on hold"
assert_jq_eq '.status' 'on_hold' "the status became on_hold"

req PATCH "/prescriptions/${MOVER}/" '{"status":"active"}'
assert_status 200 "a prescription on hold can be made active again"
assert_jq_eq '.status' 'active' "the status became active"

req PATCH "/prescriptions/${MOVER}/" '{"status":"active"}'
assert_status 200 "setting the status it already has is allowed"

for forbidden in cancelled completed expired; do
  req PATCH "/prescriptions/${MOVER}/" "$(jq -nc --arg s "$forbidden" '{status: $s}')"
  assert_status 400 "a client cannot PATCH the status to '${forbidden}'"
done
assert_jq '.error.details.status | has("hint")' "the refusal explains which endpoint owns the transition"

section "missing records"
req GET "/prescriptions/${MISSING_UUID}/"
assert_status 404 "GET on an unknown id is 404"
assert_error_code "not_found" "an unknown id reports not_found"

section "delete removes the record, unlike cancel"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION"; DOOMED="$NEW_ID"
req DELETE "/prescriptions/${DOOMED}/"
assert_status 204 "DELETE on a prescription is 204"
req GET "/prescriptions/${DOOMED}/"
assert_status 404 "a deleted prescription is no longer retrievable"
req DELETE "/prescriptions/${DOOMED}/"
assert_status 404 "deleting the same prescription twice is 404"
