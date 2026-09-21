#!/usr/bin/env bash
set -uo pipefail
source "$SUITE_DIR/lib/http.sh"
source "$SUITE_DIR/lib/gen.sh"
source "$SUITE_PATH/lib/gen.sh"
source "$SUITE_PATH/lib/fixtures.sh"

# This run's own cast, so the filter assertions can be exact -- the database
# carries seed data and every previous run's rows, and an absolute count over
# the whole collection would mean nothing.
make_cast
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" \
  "$(jq -nc --arg w "$(iso_days -400)" '{fills_authorized: 2, written_at: $w}')"; OLD_RX="$NEW_ID"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" '{"fills_authorized":2}'; NEW_RX="$NEW_ID"
make_prescription "$FX_PATIENT" "$FX_PRESCRIBER" "$FX_MEDICATION" '{"fills_authorized":2}'; DEAD_RX="$NEW_ID"
given "one of the three is cancelled" req POST "/prescriptions/${DEAD_RX}/cancel/"

section "pagination"
# Scoped to this run's own patient: the count below is read from one page and
# asserted against another, and with -p a case file running alongside this one
# can create a prescription in between. Three prescriptions at a page size of
# two is the same two-page shape, with a count that cannot move under it.
req GET "/prescriptions/?patient=${FX_PATIENT}&page_size=2"
assert_status 200 "the page is returned"
assert_jq '.results | length == 2' "it holds exactly the requested number of results"
assert_jq 'has("count") and has("next") and has("previous")' "it carries count, next and previous"
assert_jq '.next != null' "there is a next page"
assert_jq_eq '.previous' 'null' "the first page has no previous page"
TOTAL="$(jqv .count)"

req GET "/prescriptions/?patient=${FX_PATIENT}&page_size=2&page=2"
assert_status 200 "the second page is returned"
assert_jq '.previous != null' "it has a previous page"
assert_jq_eq '.count' "$TOTAL" "the count is the whole collection, not the page"

req GET "/prescriptions/?page=99999"
assert_status 404 "a page past the end is 404"
assert_error_code "not_found" "the error code is not_found"

req GET "/prescriptions/?page_size=1"
assert_jq '.results | length == 1' "a page size of one returns one result"

section "ordering"
req GET "/prescriptions/?ordering=written_at&page_size=100"
assert_status 200 "the ordered listing is returned"
assert_jq '[.results[].written_at] == ([.results[].written_at] | sort)' "ascending order really is ascending"

req GET "/prescriptions/?ordering=-written_at&page_size=100"
assert_status 200 "the ordered listing is returned"
assert_jq '[.results[].written_at] == ([.results[].written_at] | sort | reverse)' "descending order really is descending"

req GET "/prescriptions/?ordering=created_at&page_size=100"
assert_status 200 "the ordered listing is returned"
assert_jq '[.results[].created_at] == ([.results[].created_at] | sort)' "created_at ascending really is ascending"

# An ordering field the API does not recognise is ignored rather than refused.
req GET "/prescriptions/?ordering=no_such_field"
assert_status 200 "an unknown ordering field is ignored rather than rejected"

section "filtering prescriptions by their relations"
req GET "/prescriptions/?patient=${FX_PATIENT}&page_size=100"
assert_status 200 "the filtered listing is returned"
assert_jq_eq '.count' '3' "it holds this run's three prescriptions"
assert_jq 'all(.results[]; .patient.id == $p)' "every result belongs to that patient" --arg p "$FX_PATIENT"

req GET "/prescriptions/?prescriber=${FX_PRESCRIBER}&page_size=100"
assert_status 200 "the filtered listing is returned"
assert_jq_eq '.count' '3' "it holds this run's three prescriptions"

req GET "/prescriptions/?medication=${FX_MEDICATION}&page_size=100"
assert_status 200 "the filtered listing is returned"
assert_jq_eq '.count' '3' "it holds this run's three prescriptions"

req GET "/prescriptions/?patient=${FX_PATIENT}&status=cancelled&page_size=100"
assert_status 200 "the filtered listing is returned"
assert_jq_eq '.count' '1' "the two filters together return the one cancelled prescription"
assert_jq_eq '.results[0].id' "$DEAD_RX" "it is the prescription that was cancelled"

req GET "/prescriptions/?patient=${MISSING_UUID}"
assert_status_in "200 400" "a patient that does not exist is handled"

section "filtering by status"
req GET "/prescriptions/?status=cancelled&page_size=100"
assert_status 200 "the filtered listing is returned"
assert_jq 'all(.results[]; .status == "cancelled")' "only cancelled prescriptions come back"

req GET "/prescriptions/?status=active&page_size=100"
assert_jq 'all(.results[]; .status == "active")' "only active prescriptions come back"

# An unknown value is a bad request, not an empty result. Worth pinning down
# because the alternative is worse than it looks: 200 with count 0 would make a
# typo in a query string indistinguishable from a correct query that happens to
# match nothing. Every filter on this endpoint validates the same way.
req GET "/prescriptions/?status=not-a-status"
assert_status 400 "an unknown status value is rejected"
assert_error_code "validation_error" "the error code is validation_error"
assert_jq '.error.details.status | length > 0' "the error names the status field"

section "filtering by the date a prescription was written"
req GET "/prescriptions/?written_after=$(iso_days -200)&patient=${FX_PATIENT}&page_size=100"
assert_status 200 "the filtered listing is returned"
assert_jq_eq '.count' '2' "the prescription written 400 days ago is excluded"
assert_jq 'all(.results[]; .id != $old)' "it is not in the results" --arg old "$OLD_RX"

req GET "/prescriptions/?written_before=$(iso_days -200)&patient=${FX_PATIENT}&page_size=100"
assert_status 200 "the filtered listing is returned"
assert_jq_eq '.count' '1' "only the prescription written 400 days ago comes back"
assert_jq_eq '.results[0].id' "$OLD_RX" "it is the right prescription"

req GET "/prescriptions/?written_after=$(iso_days 3650)&patient=${FX_PATIENT}"
assert_status 200 "a date far in the future is accepted"
assert_jq_eq '.count' '0' "nothing was written after today plus ten years"

req GET "/prescriptions/?written_after=not-a-date"
assert_status_in "200 400" "a malformed date is handled rather than crashing"

section "search"
# Searching for this cast's own medication and patient, not the run tag: other
# case files create records carrying the same tag in the same run.
req GET "/prescriptions/?search=$(urlencode "$FX_MEDICATION_NAME")&page_size=100"
assert_status 200 "the search results are returned"
assert_jq_eq '.count' '3' "this cast's three prescriptions are found"
assert_jq 'all(.results[]; .medication.id == $m)' "every hit is for the medication searched for" --arg m "$FX_MEDICATION"

req GET "/prescriptions/?search=$(urlencode "$FX_PATIENT_LAST_NAME")&page_size=100"
assert_status 200 "the search results are returned"
assert_jq_eq '.count' '3' "this cast's three prescriptions are found"
assert_jq 'all(.results[]; .patient.id == $p)' "every hit belongs to the patient searched for" --arg p "$FX_PATIENT"

req GET "/prescriptions/?search=zzz-nothing-matches-this"
assert_status 200 "a search with no matches is still 200"
assert_jq_eq '.count' '0' "the page comes back empty"
assert_jq '.results == []' "its results list is empty"

section "every collection paginates the same way"
for path in /addresses/ /medications/ /patients/ /prescribers/ /prescriptions/; do
  req GET "${path}?page_size=1"
  assert_status 200 "page_size is accepted"
  assert_jq 'has("count") and has("next") and has("previous") and has("results")' \
    "the response is a paginated envelope"
done
