#!/usr/bin/env bash
set -uo pipefail
source "$E2E_DIR/lib/http.sh"
source "$E2E_DIR/lib/gen.sh"
source "$E2E_DIR/lib/fixtures.sh"

section "create"
NDC="$(gen_ndc)"
when "a medication is created with a hyphenated 5-4-2 NDC"
req POST /medications/ "$(jq -nc --arg ndc "$NDC" --arg tag "$RUN_TAG" \
  '{name: ("Testazepam " + $tag), ndc: $ndc, strength: "10 mg", form: "tablet"}')"
assert_status 201 "the medication is created"
assert_jq '.id | test("^[0-9a-f-]{36}$")' "it has a UUID id"
assert_jq_eq '.ndc' "$(ndc_digits "$NDC")" "the NDC is stored as 11 digits"
assert_jq_eq '.formatted_ndc' "$(ndc_display_form "$NDC")" "formatted_ndc is the 5-4-2 form"
assert_jq_eq '.form' 'tablet' "form round-trips"
MED="$(jqv .id)"

section "NDC segment formats all normalise to eleven digits"
# 4-4-2, 5-3-2 and 5-4-1 denote the same product as 5-4-2; the API pads the
# short segment so the uniqueness rule can mean what it says.
check_ndc() {
  local sent="$1" expected="$2" label="$3"
  when "a medication is created with an NDC in ${label} form"
  req POST /medications/ "$(jq -nc --arg ndc "$sent" --arg n "NDC shape $label $RUN_TAG" \
    '{name: $n, ndc: $ndc, form: "capsule"}')"
  assert_status 201 "it is accepted"
  assert_jq_eq '.ndc' "$expected" "it normalises to ${expected}"
}
S442="$(printf '%04d-%04d-%02d' "$(_rand 10000)" "$(_rand 10000)" "$(_rand 100)")"
check_ndc "$S442" "0${S442//-/}" "4-4-2"
S532="$(printf '%05d-%03d-%02d' "$(_rand 100000)" "$(_rand 1000)" "$(_rand 100)")"
check_ndc "$S532" "${S532:0:5}0${S532:6:3}${S532: -2}" "5-3-2"
S541="$(printf '%05d-%04d-%01d' "$(_rand 100000)" "$(_rand 10000)" "$(_rand 10)")"
check_ndc "$S541" "${S541:0:5}${S541:6:4}0${S541: -1}" "5-4-1"

BARE="$(ndc_digits "$(gen_ndc)")"
when "a medication is created with a bare 11-digit NDC"
req POST /medications/ "$(jq -nc --arg ndc "$BARE" --arg n "NDC bare $RUN_TAG" '{name:$n, ndc:$ndc, form:"tablet"}')"
assert_status 201 "it is accepted"
assert_jq_eq '.ndc' "$BARE" "it is stored unchanged"

section "NDC validation"
# A bare ten-digit NDC is genuinely ambiguous -- it could pad to three
# different eleven-digit numbers -- so the API refuses to guess.
when "a medication is created with a bare 10-digit NDC"
req POST /medications/ '{"name":"Ambiguous","ndc":"1234567890","form":"tablet"}'
assert_status 400 "it is rejected as ambiguous"
assert_error_code "validation_error" "the error code is validation_error"
assert_jq '.error.details.ndc | length > 0' "the error names the ndc field"

for bad in "12-34-56" "abcde-1234-01" "" "12345-6789-012"; do
  when "a medication is created with an NDC of '${bad}'"
  req POST /medications/ "$(jq -nc --arg ndc "$bad" --arg n "Bad NDC $RUN_TAG" '{name:$n, ndc:$ndc, form:"tablet"}')"
  assert_status 400 "it is rejected"
done

section "an NDC is unique among live medications"
DUP_NDC="$(gen_ndc)"
when "a medication claims an NDC"
req POST /medications/ "$(jq -nc --arg ndc "$DUP_NDC" --arg n "Original $RUN_TAG" '{name:$n, ndc:$ndc, form:"tablet"}')"
assert_status 201 "the first medication with that NDC is created"
FIRST="$(jqv .id)"

when "a second medication claims the same NDC"
req POST /medications/ "$(jq -nc --arg ndc "$DUP_NDC" --arg n "Duplicate $RUN_TAG" '{name:$n, ndc:$ndc, form:"tablet"}')"
assert_status 409 "it is refused with 409"
assert_error_code "duplicate_ndc" "the error code is duplicate_ndc"
assert_jq_eq '.error.details.existing_id' "$FIRST" "the conflict points at the medication already holding the NDC"

# Uniqueness covers live rows only, so the NDC becomes available again once the
# medication holding it is deleted. This is what makes the suite rerunnable at
# all, and is worth asserting rather than assuming.
when "the medication holding the NDC is deleted"
req DELETE "/medications/${FIRST}/"
assert_status 204 "it is deleted"
when "a third medication claims the freed NDC"
req POST /medications/ "$(jq -nc --arg ndc "$DUP_NDC" --arg n "Reused $RUN_TAG" '{name:$n, ndc:$ndc, form:"tablet"}')"
assert_status 201 "the NDC is reusable once the live medication holding it is gone"

section "other validation"
when "a medication is created in a dosage form that does not exist"
req POST /medications/ "$(jq -nc --arg ndc "$(gen_ndc)" '{name:"Bad Form", ndc:$ndc, form:"powder"}')"
assert_status 400 "it is rejected"
assert_jq '.error.details.form | length > 0' "the error names the form field"

when "a medication is created with no name"
req POST /medications/ "$(jq -nc --arg ndc "$(gen_ndc)" '{ndc:$ndc, form:"tablet"}')"
assert_status 400 "name is required"

when "a medication is created with no NDC"
req POST /medications/ '{"name":"No NDC","form":"tablet"}'
assert_status 400 "ndc is required"

section "read and update"
when "the medication is read back"
req GET "/medications/${MED}/"
assert_status 200 "the medication is returned"
assert_jq_eq '.id' "$MED" "it is the medication that was asked for"

when "only its strength is patched"
req PATCH "/medications/${MED}/" '{"strength":"20 mg"}'
assert_status 200 "the medication is updated"
assert_jq_eq '.strength' '20 mg' "the patched strength took effect"
assert_jq_eq '.ndc' "$(ndc_digits "$NDC")" "the NDC is untouched"

when "the medication is replaced"
req PUT "/medications/${MED}/" "$(jq -nc --arg ndc "$NDC" --arg tag "$RUN_TAG" \
  '{name: ("Replacedazepam " + $tag), ndc: $ndc, strength: "5 mg", form: "capsule"}')"
assert_status 200 "the medication is replaced"
assert_jq_eq '.form' 'capsule' "the new form took effect"

when "the medication is replaced without its name and NDC"
req PUT "/medications/${MED}/" '{"strength":"5 mg"}'
assert_status 400 "it is rejected"

section "missing records"
when "a medication that does not exist is read"
req GET "/medications/${MISSING_UUID}/"
assert_status 404 "it is 404"
assert_error_code "not_found" "the error code is not_found"

section "delete"
make_medication; SPARE="$NEW_ID"
when "a medication nobody has been prescribed is deleted"
req DELETE "/medications/${SPARE}/"
assert_status 204 "it is deleted"
when "the deleted medication is read back"
req GET "/medications/${SPARE}/"
assert_status 404 "it is no longer retrievable"

make_cast
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION"
when "a medication somebody has been prescribed is deleted"
req DELETE "/medications/${FX_MEDICATION}/"
assert_status 409 "the delete is refused with 409"
assert_error_code "referenced" "the error code is referenced"
assert_jq '.error.details.referenced_by | has("prescriptions")' "the conflict names prescriptions as the referencing resource"

section "filters"
when "medications are filtered to capsules"
req GET "/medications/?form=capsule&page_size=100"
assert_status 200 "the filtered listing is returned"
assert_jq 'all(.results[]; .form == "capsule")' "only capsules come back"

make_medication; SEARCHABLE="$NEW_ID"; SEARCH_NAME="$NEW_NAME"
when "medications are searched for that one's name"
req GET "/medications/?search=$(urlencode "$SEARCH_NAME")&page_size=100"
assert_status 200 "the search results are returned"
assert_jq_eq '.count' '1' "exactly one medication matches"
assert_jq_eq '.results[0].id' "$SEARCHABLE" "it is the medication that was searched for"

when "medications are searched for a name nothing carries"
req GET "/medications/?search=zzz-no-such-medication"
assert_status 200 "it is still 200"
assert_jq_eq '.count' '0' "the page comes back empty"
