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
assert_jq_eq '.fills_used' '1' "the first fill increments fills_used"
assert_jq_eq '.status' 'active' "a prescription with fills left stays active"

req POST "/prescriptions/${RX}/fill/"
assert_status 200 "the second fill is accepted"
assert_jq_eq '.fills_used' '2' "the second fill increments fills_used again"
assert_jq_eq '.status' 'active' "two of three fills leaves it active"

req POST "/prescriptions/${RX}/fill/"
assert_status 200 "the last authorised fill is accepted"
assert_jq_eq '.fills_used' '3' "the last fill increments fills_used to the authorised total"
assert_jq_eq '.status' 'completed' "the last authorised fill completes the prescription"

req POST "/prescriptions/${RX}/fill/"
assert_status 409 "filling a completed prescription is refused"
assert_error_code "not_active" "an exhausted prescription reports not_active"
assert_jq_eq '.error.details.status' 'completed' "the refusal reports the current status"

req GET "/prescriptions/${RX}/"
assert_jq_eq '.fills_used' '3' "the refused fill did not increment fills_used"

section "cancelling a prescription"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" '{"fills_authorized":5}'; CANCELLED="$NEW_ID"

req POST "/prescriptions/${CANCELLED}/cancel/"
assert_status 200 "cancelling an active prescription is accepted"
assert_jq_eq '.status' 'cancelled' "the prescription is now cancelled"

# Cancelling is a clinical decision, not a record deletion: the prescription
# stays readable, which is the difference between this and DELETE.
req GET "/prescriptions/${CANCELLED}/"
assert_status 200 "a cancelled prescription is still readable"
assert_jq_eq '.status' 'cancelled' "the cancelled status persists"

req POST "/prescriptions/${CANCELLED}/cancel/"
assert_status 409 "cancelling twice is refused"
assert_error_code "already_terminal" "a second cancel reports already_terminal"
assert_jq_eq '.error.details.status' 'cancelled' "the refusal reports the current status"

req POST "/prescriptions/${CANCELLED}/fill/"
assert_status 409 "filling a cancelled prescription is refused"
assert_error_code "not_active" "filling a cancelled prescription reports not_active"

section "cancelling is refused once a prescription has completed"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" '{"fills_authorized":1}'; DONE="$NEW_ID"
req POST "/prescriptions/${DONE}/fill/"
assert_jq_eq '.status' 'completed' "a single-fill prescription completes on its first fill"
req POST "/prescriptions/${DONE}/cancel/"
assert_status 409 "cancelling a completed prescription is refused"
assert_error_code "already_terminal" "cancelling a completed prescription reports already_terminal"

section "expiry is applied when a fill is attempted"
# Expiry is noticed lazily, on the fill path, rather than by a background
# sweep -- so the status change and the refusal have to arrive together.
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" \
  "$(jq -nc --arg w "$(iso_days -90)" --arg e "$(iso_days -1)" '{fills_authorized: 5, written_at: $w, expires_at: $e}')"
EXPIRED="$NEW_ID"

req GET "/prescriptions/${EXPIRED}/"
assert_jq_eq '.status' 'active' "a past-dated prescription is still active before anyone touches it"

req POST "/prescriptions/${EXPIRED}/fill/"
assert_status 409 "filling an expired prescription is refused"
assert_error_code "expired" "an expired prescription reports the expired code"
assert_jq_eq '.error.details.status' 'expired' "the refusal reports the new expired status"
assert_jq '.error.details | has("expires_at")' "the refusal reports when it expired"

# The caller's next read has to agree with the error they were just given.
req GET "/prescriptions/${EXPIRED}/"
assert_status 200 "the expired prescription is still readable"
assert_jq_eq '.status' 'expired' "the refused fill left the prescription marked expired"
assert_jq_eq '.fills_used' '0' "the refused fill did not count against the authorised fills"

req POST "/prescriptions/${EXPIRED}/cancel/"
assert_status 409 "cancelling an expired prescription is refused"
assert_error_code "already_terminal" "cancelling an expired prescription reports already_terminal"

section "a prescription that expires in the future still fills"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" \
  "$(jq -nc --arg e "$(iso_days 30)" '{fills_authorized: 2, expires_at: $e}')"
FUTURE="$NEW_ID"
req POST "/prescriptions/${FUTURE}/fill/"
assert_status 200 "a prescription expiring in the future can be filled"
assert_jq_eq '.status' 'active' "it stays active while fills remain"

section "a prescription on hold cannot be filled"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" '{"fills_authorized":2}'; HELD="$NEW_ID"
req PATCH "/prescriptions/${HELD}/" '{"status":"on_hold"}'
assert_jq_eq '.status' 'on_hold' "the prescription is on hold"
req POST "/prescriptions/${HELD}/fill/"
assert_status 409 "filling a prescription on hold is refused"
assert_error_code "not_active" "a held prescription reports not_active"

req PATCH "/prescriptions/${HELD}/" '{"status":"active"}'
req POST "/prescriptions/${HELD}/fill/"
assert_status 200 "taking it off hold makes it fillable again"

section "the actions on records that are not there"
req POST "/prescriptions/${MISSING_UUID}/fill/"
assert_status 404 "filling an unknown prescription is 404"
assert_error_code "not_found" "filling an unknown prescription reports not_found"

req POST "/prescriptions/${MISSING_UUID}/cancel/"
assert_status 404 "cancelling an unknown prescription is 404"

make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION"; GONE="$NEW_ID"
req DELETE "/prescriptions/${GONE}/"
req POST "/prescriptions/${GONE}/fill/"
assert_status 404 "filling a deleted prescription is 404"

section "the actions need credentials"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION"; GUARDED="$NEW_ID"
req_anon POST "/prescriptions/${GUARDED}/fill/"
assert_status 401 "filling without credentials is 401"
req_anon POST "/prescriptions/${GUARDED}/cancel/"
assert_status 401 "cancelling without credentials is 401"
req GET "/prescriptions/${GUARDED}/"
assert_jq_eq '.fills_used' '0' "the unauthenticated fill attempt changed nothing"
assert_jq_eq '.status' 'active' "the unauthenticated cancel attempt changed nothing"
