#!/usr/bin/env bash
set -uo pipefail
source "$E2E_DIR/lib/http.sh"
source "$E2E_DIR/lib/gen.sh"
source "$E2E_DIR/lib/fixtures.sh"

# A prescription's status only ever moves forward, so every scenario below
# starts from a prescription this run created. Re-filling last run's would 409
# the second time the suite ran.
#
# This file is about the prescription's own status -- what makes it complete,
# what refuses a fill, when a lapsed expiry is recorded. Dispensing is three
# requests through /prescriptions/{id}/fills/ and /fills/{id}/, so a scenario
# that only needs the outcome takes make_dispensed_fill and reads the
# prescription afterwards; 65_fills.sh is where the workflow itself is the
# subject. A scenario about a refusal writes out the request that is refused,
# which is always the first of the three.
make_cast
make_dispensing_team

section "filling a prescription"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" '{"fills_authorized":3}'; RX="$NEW_ID"

make_dispensed_fill "$RX" "$FX_TECHNICIAN" "$FX_PHARMACIST"
when "the prescription is read after the first fill"
req GET "/prescriptions/${RX}/"
assert_status 200 "the prescription is returned"
assert_jq '.fills_used == 1' "fills_used is now one"
assert_jq_eq '.status' 'active' "it stays active with fills left"

make_dispensed_fill "$RX" "$FX_TECHNICIAN" "$FX_PHARMACIST"
when "the prescription is read after the second fill"
req GET "/prescriptions/${RX}/"
assert_jq '.fills_used == 2' "fills_used is now two"
assert_jq_eq '.status' 'active' "two of three fills leaves it active"

make_dispensed_fill "$RX" "$FX_TECHNICIAN" "$FX_PHARMACIST"
when "the prescription is read after the last authorised fill"
req GET "/prescriptions/${RX}/"
assert_jq '.fills_used == 3' "fills_used has reached the authorised total"
assert_jq_eq '.quantity_remaining' '0' "nothing is left to dispense"
assert_jq_eq '.status' 'completed' "the prescription is now completed"

when "a fourth fill is started"
req_staff "$FX_TECHNICIAN" POST "/prescriptions/${RX}/fills/" '{}'
assert_status 409 "it is refused with 409"
assert_error_code "not_active" "the error code is not_active"
assert_jq_eq '.error.details.status' 'completed' "the refusal reports the current status"

when "the prescription is read back"
req GET "/prescriptions/${RX}/"
assert_jq '.fills_used == 3' "the refused fill did not increment fills_used"

section "cancelling a prescription"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" '{"fills_authorized":5}'; CANCELLED="$NEW_ID"

when "an active prescription is cancelled"
req POST "/prescriptions/${CANCELLED}/cancel/"
assert_status 200 "the cancellation is accepted"
assert_jq_eq '.status' 'cancelled' "the prescription is now cancelled"

# Cancelling is a clinical decision, not a record deletion: the prescription
# stays readable, which is the difference between this and DELETE.
when "the cancelled prescription is read back"
req GET "/prescriptions/${CANCELLED}/"
assert_status 200 "it is still readable"
assert_jq_eq '.status' 'cancelled' "the cancelled status persists"

when "it is cancelled a second time"
req POST "/prescriptions/${CANCELLED}/cancel/"
assert_status 409 "it is refused with 409"
assert_error_code "already_terminal" "the error code is already_terminal"
assert_jq_eq '.error.details.status' 'cancelled' "the refusal reports the current status"

when "a fill is started on the cancelled prescription"
req_staff "$FX_TECHNICIAN" POST "/prescriptions/${CANCELLED}/fills/" '{}'
assert_status 409 "it is refused with 409"
assert_error_code "not_active" "the error code is not_active"

section "cancelling is refused once a prescription has completed"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" '{"fills_authorized":1}'; DONE="$NEW_ID"
make_dispensed_fill "$DONE" "$FX_TECHNICIAN" "$FX_PHARMACIST"
when "the single-fill prescription is read back"
req GET "/prescriptions/${DONE}/"
assert_jq_eq '.status' 'completed' "it completes on that first fill"
when "the completed prescription is cancelled"
req POST "/prescriptions/${DONE}/cancel/"
assert_status 409 "it is refused with 409"
assert_error_code "already_terminal" "the error code is already_terminal"

section "expiry is applied when a fill is attempted"
# Expiry is noticed lazily, on the fill path, rather than by a background
# sweep -- so the status change and the refusal have to arrive together.
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" \
  "$(jq -nc --arg w "$(iso_days -90)" --arg e "$(iso_days -1)" '{fills_authorized: 5, written_at: $w, expires_at: $e}')"
EXPIRED="$NEW_ID"

when "a prescription whose expiry has passed is read"
req GET "/prescriptions/${EXPIRED}/"
assert_jq_eq '.status' 'active' "it is still active before anyone touches it"

when "a fill is started against it"
req_staff "$FX_TECHNICIAN" POST "/prescriptions/${EXPIRED}/fills/" '{}'
assert_status 409 "it is refused with 409"
assert_error_code "expired" "the error code is expired"
assert_jq_eq '.error.details.status' 'expired' "the refusal reports the new expired status"
assert_jq '.error.details | has("expires_at")' "the refusal reports when it expired"

# The caller's next read has to agree with the error they were just given.
when "it is read again after the refusal"
req GET "/prescriptions/${EXPIRED}/"
assert_status 200 "it is still readable"
assert_jq_eq '.status' 'expired' "the refused fill left it marked expired"
assert_jq '.fills_used == 0' "the refused fill did not count against the authorised fills"

when "the expired prescription is cancelled"
req POST "/prescriptions/${EXPIRED}/cancel/"
assert_status 409 "it is refused with 409"
assert_error_code "already_terminal" "the error code is already_terminal"

section "a prescription that expires in the future still fills"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" \
  "$(jq -nc --arg e "$(iso_days 30)" '{fills_authorized: 2, expires_at: $e}')"
FUTURE="$NEW_ID"
when "a fill is started on a prescription that expires next month"
req_staff "$FX_TECHNICIAN" POST "/prescriptions/${FUTURE}/fills/" '{}'
assert_status 201 "the fill is accepted"
assert_jq_eq '.status' 'pending' "it starts out pending"
assert_jq_eq '.sequence' '1' "it is the prescription's first fill"

section "a prescription on hold cannot be filled"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" '{"fills_authorized":2}'; HELD="$NEW_ID"
when "a prescription is put on hold"
req PATCH "/prescriptions/${HELD}/" '{"status":"on_hold"}'
assert_jq_eq '.status' 'on_hold' "it is on hold"
when "a fill is started on the held prescription"
req_staff "$FX_TECHNICIAN" POST "/prescriptions/${HELD}/fills/" '{}'
assert_status 409 "it is refused with 409"
assert_error_code "not_active" "the error code is not_active"

given "it is taken off hold" req PATCH "/prescriptions/${HELD}/" '{"status":"active"}'
when "a fill is started again"
req_staff "$FX_TECHNICIAN" POST "/prescriptions/${HELD}/fills/" '{}'
assert_status 201 "it is fillable once more"

section "the actions on records that are not there"
when "a fill is started on a prescription that does not exist"
req_staff "$FX_TECHNICIAN" POST "/prescriptions/${MISSING_UUID}/fills/" '{}'
assert_status 404 "it is 404"
assert_error_code "not_found" "the error code is not_found"

when "a prescription that does not exist is cancelled"
req POST "/prescriptions/${MISSING_UUID}/cancel/"
assert_status 404 "it is 404"

make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION"; GONE="$NEW_ID"
given "the prescription is deleted" req DELETE "/prescriptions/${GONE}/"
when "a fill is started on the deleted prescription"
req_staff "$FX_TECHNICIAN" POST "/prescriptions/${GONE}/fills/" '{}'
assert_status 404 "it is 404"

section "the actions need credentials"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION"; GUARDED="$NEW_ID"
when "an anonymous caller starts a fill"
req_anon POST "/prescriptions/${GUARDED}/fills/" '{}'
assert_status 401 "it is refused with 401"
when "an anonymous caller cancels it"
req_anon POST "/prescriptions/${GUARDED}/cancel/"
assert_status 401 "it is refused with 401"
when "the prescription is read back"
req GET "/prescriptions/${GUARDED}/"
assert_jq '.fills_used == 0' "the unauthenticated fill attempt changed nothing"
assert_jq_eq '.status' 'active' "the unauthenticated cancel attempt changed nothing"
