#!/usr/bin/env bash
set -uo pipefail
source "$SUITE_DIR/lib/http.sh"
source "$SUITE_DIR/lib/gen.sh"
source "$SUITE_PATH/lib/gen.sh"
source "$SUITE_PATH/lib/fixtures.sh"

section "create"
NDC="$(gen_ndc)"
req POST /medications/ "$(jq -nc --arg ndc "$NDC" --arg tag "$RUN_TAG" \
  '{name: ("Testazepam " + $tag), ndc: $ndc, strength: "10 mg", form: "tablet"}')"
assert_status 201 "POST /medications/ creates a medication"
assert_jq '.id | test("^[0-9a-f-]{36}$")' "the new medication has a UUID id"
assert_jq_eq '.ndc' "$(ndc_digits "$NDC")" "a hyphenated NDC is stored as 11 digits"
assert_jq_eq '.formatted_ndc' "$(ndc_display_form "$NDC")" "formatted_ndc is the 5-4-2 form"
assert_jq_eq '.form' 'tablet' "form round-trips"
MED="$(jqv .id)"

section "NDC segment formats all normalise to eleven digits"
# 4-4-2, 5-3-2 and 5-4-1 denote the same product as 5-4-2; the API pads the
# short segment so the uniqueness rule can mean what it says.
check_ndc() {
  local sent="$1" expected="$2" label="$3"
  req POST /medications/ "$(jq -nc --arg ndc "$sent" --arg n "NDC shape $label $RUN_TAG" \
    '{name: $n, ndc: $ndc, form: "capsule"}')"
  assert_status 201 "an NDC in ${label} form is accepted"
  assert_jq_eq '.ndc' "$expected" "${label} normalises to ${expected}"
}
S442="$(printf '%04d-%04d-%02d' "$(_rand 10000)" "$(_rand 10000)" "$(_rand 100)")"
check_ndc "$S442" "0${S442//-/}" "4-4-2"
S532="$(printf '%05d-%03d-%02d' "$(_rand 100000)" "$(_rand 1000)" "$(_rand 100)")"
check_ndc "$S532" "${S532:0:5}0${S532:6:3}${S532: -2}" "5-3-2"
S541="$(printf '%05d-%04d-%01d' "$(_rand 100000)" "$(_rand 10000)" "$(_rand 10)")"
check_ndc "$S541" "${S541:0:5}${S541:6:4}0${S541: -1}" "5-4-1"

BARE="$(ndc_digits "$(gen_ndc)")"
req POST /medications/ "$(jq -nc --arg ndc "$BARE" --arg n "NDC bare $RUN_TAG" '{name:$n, ndc:$ndc, form:"tablet"}')"
assert_status 201 "a bare 11-digit NDC is accepted"
assert_jq_eq '.ndc' "$BARE" "a bare 11-digit NDC is stored unchanged"

section "NDC validation"
# A bare ten-digit NDC is genuinely ambiguous -- it could pad to three
# different eleven-digit numbers -- so the API refuses to guess.
req POST /medications/ '{"name":"Ambiguous","ndc":"1234567890","form":"tablet"}'
assert_status 400 "a bare 10-digit NDC is rejected as ambiguous"
assert_error_code "validation_error" "an ambiguous NDC reports validation_error"
assert_jq '.error.details.ndc | length > 0' "the ambiguous NDC error names the ndc field"

for bad in "12-34-56" "abcde-1234-01" "" "12345-6789-012"; do
  req POST /medications/ "$(jq -nc --arg ndc "$bad" --arg n "Bad NDC $RUN_TAG" '{name:$n, ndc:$ndc, form:"tablet"}')"
  assert_status 400 "an NDC of '${bad}' is rejected"
done

section "an NDC is unique among live medications"
DUP_NDC="$(gen_ndc)"
req POST /medications/ "$(jq -nc --arg ndc "$DUP_NDC" --arg n "Original $RUN_TAG" '{name:$n, ndc:$ndc, form:"tablet"}')"
assert_status 201 "the first medication with an NDC is created"
FIRST="$(jqv .id)"

req POST /medications/ "$(jq -nc --arg ndc "$DUP_NDC" --arg n "Duplicate $RUN_TAG" '{name:$n, ndc:$ndc, form:"tablet"}')"
assert_status 409 "a second live medication with the same NDC is refused"
assert_error_code "duplicate_ndc" "the duplicate reports duplicate_ndc"
assert_jq_eq '.error.details.existing_id' "$FIRST" "the conflict points at the medication already holding the NDC"

# Uniqueness covers live rows only, so the NDC becomes available again once the
# medication holding it is deleted. This is what makes the suite rerunnable at
# all, and is worth asserting rather than assuming.
req DELETE "/medications/${FIRST}/"
assert_status 204 "the original medication is deleted"
req POST /medications/ "$(jq -nc --arg ndc "$DUP_NDC" --arg n "Reused $RUN_TAG" '{name:$n, ndc:$ndc, form:"tablet"}')"
assert_status 201 "the NDC can be reused once the live medication holding it is gone"

section "other validation"
req POST /medications/ "$(jq -nc --arg ndc "$(gen_ndc)" '{name:"Bad Form", ndc:$ndc, form:"powder"}')"
assert_status 400 "an unknown dosage form is rejected"
assert_jq '.error.details.form | length > 0' "the form error names the form field"

req POST /medications/ "$(jq -nc --arg ndc "$(gen_ndc)" '{ndc:$ndc, form:"tablet"}')"
assert_status 400 "name is required"

req POST /medications/ '{"name":"No NDC","form":"tablet"}'
assert_status 400 "ndc is required"

section "read and update"
req GET "/medications/${MED}/"
assert_status 200 "GET /medications/{id}/ returns the medication"
assert_jq_eq '.id' "$MED" "the retrieved medication has the requested id"

req PATCH "/medications/${MED}/" '{"strength":"20 mg"}'
assert_status 200 "PATCH updates the strength"
assert_jq_eq '.strength' '20 mg' "the patched strength took effect"
assert_jq_eq '.ndc' "$(ndc_digits "$NDC")" "PATCH left the NDC alone"

req PUT "/medications/${MED}/" "$(jq -nc --arg ndc "$NDC" --arg tag "$RUN_TAG" \
  '{name: ("Replacedazepam " + $tag), ndc: $ndc, strength: "5 mg", form: "capsule"}')"
assert_status 200 "PUT replaces the medication"
assert_jq_eq '.form' 'capsule' "the replaced form took effect"

req PUT "/medications/${MED}/" '{"strength":"5 mg"}'
assert_status 400 "PUT without name and ndc is rejected"

section "missing records"
req GET "/medications/${MISSING_UUID}/"
assert_status 404 "GET on an unknown id is 404"
assert_error_code "not_found" "an unknown id reports not_found"

section "delete"
make_medication; SPARE="$NEW_ID"
req DELETE "/medications/${SPARE}/"
assert_status 204 "DELETE on an unprescribed medication is 204"
req GET "/medications/${SPARE}/"
assert_status 404 "a deleted medication is no longer retrievable"

make_cast
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" >/dev/null
req DELETE "/medications/${FX_MEDICATION}/"
assert_status 409 "deleting a prescribed medication is 409"
assert_error_code "referenced" "a blocked medication delete reports the referenced code"
assert_jq '.error.details.referenced_by | has("prescriptions")' "the conflict names prescriptions as the referencing resource"

section "filters"
req GET "/medications/?form=capsule&page_size=100"
assert_status 200 "medications can be filtered by form"
assert_jq 'all(.results[]; .form == "capsule")' "the form filter returns only that form"

make_medication; SEARCHABLE="$NEW_ID"; SEARCH_NAME="$NEW_NAME"
req GET "/medications/?search=$(urlencode "$SEARCH_NAME")&page_size=100"
assert_status 200 "medications can be searched by name"
assert_jq_eq '.count' '1' "the search finds exactly the medication with that name"
assert_jq_eq '.results[0].id' "$SEARCHABLE" "the search returns the right medication"

req GET "/medications/?search=zzz-no-such-medication"
assert_status 200 "a search with no matches is still 200"
assert_jq_eq '.count' '0' "a search with no matches returns an empty page"
