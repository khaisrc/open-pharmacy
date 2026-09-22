#!/usr/bin/env bash
set -uo pipefail
source "$E2E_DIR/lib/http.sh"
source "$E2E_DIR/lib/gen.sh"
source "$E2E_DIR/lib/fixtures.sh"

make_cast

section "create"
when "a prescription is written for that patient"
req POST /prescriptions/ "$(jq -nc --arg p "$FX_PATIENT" --arg d "$FX_PRESCRIBER" --arg m "$FX_MEDICATION" '{
  patient_id: $p, prescriber_id: $d, medication_id: $m,
  dose_amount: "1 tablet", quantity: 30, fills_authorized: 3,
  instructions: "Take one tablet by mouth daily.", notes: "Suite fixture." }')"
assert_status 201 "the prescription is created"
assert_jq '.id | test("^[0-9a-f-]{36}$")' "it has a UUID id"
assert_jq_eq '.status' 'active' "it starts active"
assert_jq_eq '.fills_used' '0' "it has no fills used"
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

when "a prescription is written without saying how many fills it allows"
req POST /prescriptions/ "$(jq -nc --arg p "$FX_PATIENT" --arg d "$FX_PRESCRIBER" --arg m "$FX_MEDICATION" '{
  patient_id: $p, prescriber_id: $d, medication_id: $m, dose_amount: "2 tablets", quantity: 60 }')"
assert_status 201 "it is created"
assert_jq_eq '.fills_authorized' '1' "fills_authorized defaults to a single fill"

section "the status a prescription may be created in"
mk_with_status() {
  req POST /prescriptions/ "$(jq -nc --arg p "$FX_PATIENT" --arg d "$FX_PRESCRIBER" --arg m "$FX_MEDICATION" --arg s "$1" '{
    patient_id: $p, prescriber_id: $d, medication_id: $m, dose_amount: "1 tablet", quantity: 30, status: $s }')"
}

when "a prescription is written explicitly active"
mk_with_status "active"
assert_status 201 "it is created"

# completed, cancelled and expired are reached through /fill/ and /cancel/, so
# a client cannot post its way straight into a terminal state.
for terminal in completed cancelled expired; do
  when "a prescription is written already '${terminal}'"
  mk_with_status "$terminal"
  assert_status 400 "it is rejected"
  assert_jq '.error.details.status | length > 0' "the refusal names the status field"
done

when "a prescription is written already on hold"
mk_with_status "on_hold"
assert_status 400 "it is rejected"

when "a prescription is written with a status the API does not know"
mk_with_status "nonsense"
assert_status 400 "it is rejected"

section "validation"
when "a prescription is written naming no patient, prescriber or medication"
req POST /prescriptions/ '{"dose_amount":"1 tablet","quantity":30}'
assert_status 400 "the three foreign keys are required"
assert_jq '.error.details | has("patient_id") and has("prescriber_id") and has("medication_id")' \
  "the error names every missing foreign key"

when "a prescription is written for a patient who does not exist"
req POST /prescriptions/ "$(jq -nc --arg p "$MISSING_UUID" --arg d "$FX_PRESCRIBER" --arg m "$FX_MEDICATION" '{
  patient_id: $p, prescriber_id: $d, medication_id: $m, dose_amount: "1 tablet", quantity: 30 }')"
assert_status 400 "it is rejected"

when "a prescription is written with no dose"
req POST /prescriptions/ "$(jq -nc --arg p "$FX_PATIENT" --arg d "$FX_PRESCRIBER" --arg m "$FX_MEDICATION" '{
  patient_id: $p, prescriber_id: $d, medication_id: $m, quantity: 30 }')"
assert_status 400 "dose_amount is required"

when "a prescription is written for a quantity of zero"
req POST /prescriptions/ "$(jq -nc --arg p "$FX_PATIENT" --arg d "$FX_PRESCRIBER" --arg m "$FX_MEDICATION" '{
  patient_id: $p, prescriber_id: $d, medication_id: $m, dose_amount: "1 tablet", quantity: 0 }')"
assert_status 400 "it is rejected"

when "a prescription is written authorising zero fills"
req POST /prescriptions/ "$(jq -nc --arg p "$FX_PATIENT" --arg d "$FX_PRESCRIBER" --arg m "$FX_MEDICATION" '{
  patient_id: $p, prescriber_id: $d, medication_id: $m, dose_amount: "1 tablet", quantity: 30, fills_authorized: 0 }')"
assert_status 400 "it is rejected"

when "a prescription is written to expire before it was written"
req POST /prescriptions/ "$(jq -nc --arg p "$FX_PATIENT" --arg d "$FX_PRESCRIBER" --arg m "$FX_MEDICATION" \
  --arg w "$(iso_days -1)" --arg e "$(iso_days -30)" '{
  patient_id: $p, prescriber_id: $d, medication_id: $m, dose_amount: "1 tablet", quantity: 30,
  written_at: $w, expires_at: $e }')"
assert_status 400 "it is rejected"
assert_jq '.error.details.expires_at | length > 0' "the error names the expires_at field"

section "read and update"
when "the prescription is read back"
req GET "/prescriptions/${RX}/"
assert_status 200 "the prescription is returned"
assert_jq_eq '.id' "$RX" "it is the prescription that was asked for"

when "a note is added to it"
req PATCH "/prescriptions/${RX}/" '{"notes":"Patched by the suite."}'
assert_status 200 "the prescription is updated"
assert_jq_eq '.notes' 'Patched by the suite.' "the patched notes took effect"
assert_jq_eq '.dose_amount' '1 tablet' "the other fields are untouched"

# fills_used moves only through POST /fill/. If a PATCH could set it, a client
# could route around the status changes that action owns.
when "a client tries to patch fills_used directly"
req PATCH "/prescriptions/${RX}/" '{"fills_used":99}'
assert_status 200 "the update is accepted"
assert_jq_eq '.fills_used' '0' "fills_used did not move"

when "a client tries to patch the id"
req PATCH "/prescriptions/${RX}/" '{"id":"11111111-1111-4111-8111-111111111111"}'
assert_status 200 "the update is accepted"
assert_jq_eq '.id' "$RX" "the id did not change"

when "its expiry is patched to before it was written"
req PATCH "/prescriptions/${RX}/" "$(jq -nc --arg e "$(iso_days -3650)" '{expires_at: $e}')"
assert_status 400 "it is rejected here too"

when "the prescription is replaced"
req PUT "/prescriptions/${RX}/" "$(jq -nc --arg p "$FX_PATIENT" --arg d "$FX_PRESCRIBER" --arg m "$FX_MEDICATION" '{
  patient_id: $p, prescriber_id: $d, medication_id: $m, dose_amount: "3 tablets", quantity: 90 }')"
assert_status 200 "the prescription is replaced"
assert_jq_eq '.dose_amount' '3 tablets' "the new dose_amount took effect"

when "the prescription is replaced without its foreign keys"
req PUT "/prescriptions/${RX}/" '{"dose_amount":"1 tablet"}'
assert_status 400 "it is rejected"

section "status transitions a client may request"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION"; MOVER="$NEW_ID"

when "an active prescription is put on hold"
req PATCH "/prescriptions/${MOVER}/" '{"status":"on_hold"}'
assert_status 200 "it is accepted"
assert_jq_eq '.status' 'on_hold' "the status became on_hold"

when "a prescription on hold is made active again"
req PATCH "/prescriptions/${MOVER}/" '{"status":"active"}'
assert_status 200 "it is accepted"
assert_jq_eq '.status' 'active' "the status became active"

when "it is set to the status it already has"
req PATCH "/prescriptions/${MOVER}/" '{"status":"active"}'
assert_status 200 "that is allowed"

for forbidden in cancelled completed expired; do
  when "a client tries to set the status to '${forbidden}'"
  req PATCH "/prescriptions/${MOVER}/" "$(jq -nc --arg s "$forbidden" '{status: $s}')"
  assert_status 400 "it is refused"
done
assert_jq '.error.details.status | has("hint")' "the refusal explains which endpoint owns the transition"

section "missing records"
when "a prescription that does not exist is read"
req GET "/prescriptions/${MISSING_UUID}/"
assert_status 404 "it is 404"
assert_error_code "not_found" "the error code is not_found"

section "delete removes the record, unlike cancel"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION"; DOOMED="$NEW_ID"
when "a prescription is deleted"
req DELETE "/prescriptions/${DOOMED}/"
assert_status 204 "it is deleted"
when "the deleted prescription is read back"
req GET "/prescriptions/${DOOMED}/"
assert_status 404 "it is no longer retrievable"
when "it is deleted a second time"
req DELETE "/prescriptions/${DOOMED}/"
assert_status 404 "the second delete is 404"
