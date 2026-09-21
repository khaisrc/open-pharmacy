#!/usr/bin/env bash
set -uo pipefail
source "$SUITE_DIR/lib/http.sh"
source "$SUITE_DIR/lib/gen.sh"
source "$SUITE_PATH/lib/gen.sh"
source "$SUITE_PATH/lib/fixtures.sh"

# A prescription's status only ever moves forward, so every scenario below
# starts from a prescription this run created. Re-filling last run's would 409
# the second time the suite ran.
make_cast

section "filling a prescription"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" '{"fills_authorized":3}'; RX="$NEW_ID"

req POST "/prescriptions/${RX}/fill/"
assert_status 200 "the first fill is accepted"
assert_jq_eq '.fills_used' '1' "fills_used is now one"
assert_jq_eq '.status' 'active' "it stays active with fills left"

req POST "/prescriptions/${RX}/fill/"
assert_status 200 "the second fill is accepted"
assert_jq_eq '.fills_used' '2' "fills_used is now two"
assert_jq_eq '.status' 'active' "two of three fills leaves it active"

req POST "/prescriptions/${RX}/fill/"
assert_status 200 "the last authorised fill is accepted"
assert_jq_eq '.fills_used' '3' "fills_used has reached the authorised total"
assert_jq_eq '.status' 'completed' "the prescription is now completed"

req POST "/prescriptions/${RX}/fill/"
assert_status 409 "the fill is refused with 409"
assert_error_code "not_active" "the error code is not_active"
assert_jq_eq '.error.details.status' 'completed' "the refusal reports the current status"

req GET "/prescriptions/${RX}/"
assert_jq_eq '.fills_used' '3' "the refused fill did not increment fills_used"

section "cancelling a prescription"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" '{"fills_authorized":5}'; CANCELLED="$NEW_ID"

req POST "/prescriptions/${CANCELLED}/cancel/"
assert_status 200 "the cancellation is accepted"
assert_jq_eq '.status' 'cancelled' "the prescription is now cancelled"

# Cancelling is a clinical decision, not a record deletion: the prescription
# stays readable, which is the difference between this and DELETE.
req GET "/prescriptions/${CANCELLED}/"
assert_status 200 "it is still readable"
assert_jq_eq '.status' 'cancelled' "the cancelled status persists"

req POST "/prescriptions/${CANCELLED}/cancel/"
assert_status 409 "a second cancellation is refused with 409"
assert_error_code "already_terminal" "the error code is already_terminal"
assert_jq_eq '.error.details.status' 'cancelled' "the refusal reports the current status"

req POST "/prescriptions/${CANCELLED}/fill/"
assert_status 409 "filling it is refused with 409"
assert_error_code "not_active" "the error code is not_active"

section "cancelling is refused once a prescription has completed"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" '{"fills_authorized":1}'; DONE="$NEW_ID"
req POST "/prescriptions/${DONE}/fill/"
assert_jq_eq '.status' 'completed' "a single-fill prescription completes on its first fill"
req POST "/prescriptions/${DONE}/cancel/"
assert_status 409 "the cancellation is refused with 409"
assert_error_code "already_terminal" "the error code is already_terminal"

section "expiry is applied when a fill is attempted"
# Expiry is noticed lazily, on the fill path, rather than by a background
# sweep -- so the status change and the refusal have to arrive together.
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" \
  "$(jq -nc --arg w "$(iso_days -90)" --arg e "$(iso_days -1)" '{fills_authorized: 5, written_at: $w, expires_at: $e}')"
EXPIRED="$NEW_ID"

req GET "/prescriptions/${EXPIRED}/"
assert_jq_eq '.status' 'active' "a past-dated prescription is still active before anyone touches it"

req POST "/prescriptions/${EXPIRED}/fill/"
assert_status 409 "the fill is refused with 409"
assert_error_code "expired" "the error code is expired"
assert_jq_eq '.error.details.status' 'expired' "the refusal reports the new expired status"
assert_jq '.error.details | has("expires_at")' "the refusal reports when it expired"

# The caller's next read has to agree with the error they were just given.
req GET "/prescriptions/${EXPIRED}/"
assert_status 200 "it is still readable"
assert_jq_eq '.status' 'expired' "the refused fill left it marked expired"
assert_jq_eq '.fills_used' '0' "the refused fill did not count against the authorised fills"

req POST "/prescriptions/${EXPIRED}/cancel/"
assert_status 409 "cancelling it is refused with 409"
assert_error_code "already_terminal" "the error code is already_terminal"

section "a prescription that expires in the future still fills"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" \
  "$(jq -nc --arg e "$(iso_days 30)" '{fills_authorized: 2, expires_at: $e}')"
FUTURE="$NEW_ID"
req POST "/prescriptions/${FUTURE}/fill/"
assert_status 200 "the fill is accepted"
assert_jq_eq '.status' 'active' "it stays active while fills remain"

section "a prescription on hold cannot be filled"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" '{"fills_authorized":2}'; HELD="$NEW_ID"
req PATCH "/prescriptions/${HELD}/" '{"status":"on_hold"}'
assert_jq_eq '.status' 'on_hold' "the prescription is on hold"
req POST "/prescriptions/${HELD}/fill/"
assert_status 409 "the fill is refused with 409"
assert_error_code "not_active" "the error code is not_active"

given "it is taken off hold" req PATCH "/prescriptions/${HELD}/" '{"status":"active"}'
req POST "/prescriptions/${HELD}/fill/"
assert_status 200 "it is fillable again"

section "the actions on records that are not there"
req POST "/prescriptions/${MISSING_UUID}/fill/"
assert_status 404 "the fill is 404"
assert_error_code "not_found" "the error code is not_found"

req POST "/prescriptions/${MISSING_UUID}/cancel/"
assert_status 404 "the cancellation is 404"

make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION"; GONE="$NEW_ID"
given "the prescription is deleted" req DELETE "/prescriptions/${GONE}/"
req POST "/prescriptions/${GONE}/fill/"
assert_status 404 "filling a deleted prescription is 404"

section "the actions need credentials"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION"; GUARDED="$NEW_ID"
req_anon POST "/prescriptions/${GUARDED}/fill/"
assert_status 401 "the fill is refused with 401"
req_anon POST "/prescriptions/${GUARDED}/cancel/"
assert_status 401 "the cancellation is refused with 401"
req GET "/prescriptions/${GUARDED}/"
assert_jq_eq '.fills_used' '0' "the unauthenticated fill attempt changed nothing"
assert_jq_eq '.status' 'active' "the unauthenticated cancel attempt changed nothing"
