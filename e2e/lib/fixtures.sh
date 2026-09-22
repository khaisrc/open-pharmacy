# Fixture builders.
#
# Each helper sets NEW_ID (and leaves the created object in BODY) rather than
# echoing the id. A helper called as "$(make_patient)" would run in a subshell,
# where the STATUS/BODY globals and a failing die() could not reach the case
# file that called it.
#
# Nothing here cleans up: the suite leaves its rows behind by design, which is
# why every unique value comes from lib/gen.sh.
#
# Each helper is a Given step: the public name says what the fixture leaves
# behind, and `given` (lib/http.sh) files every request it makes under that one
# step. The work lives in the underscored twin, so a builder that calls another
# -- make_cast calls four -- reads as a single Given rather than as the
# requests it happens to take.

NEW_ID=""

make_address() { given "an address exists" _make_address "$@"; }
_make_address() {
  local overrides="${1:-}"; [[ -n "$overrides" ]] || overrides='{}'
  local payload
  payload="$(jq -nc --argjson o "$overrides" '{
    line1: "100 Test Way",
    line2: "Suite 2",
    city: "Portland",
    state: "OR",
    postal_code: "97205",
    country: "US"
  } * $o')"
  req POST /addresses/ "$payload"
  [[ "$STATUS" == "201" ]] || die "could not create address"
  NEW_ID="$(jqv .id)"
}

# make_medication [overrides_json]
# Also sets NEW_NDC and NEW_NAME. The name carries its own random suffix, not
# just the run tag: several case files create medications in one run, and a
# search test that matched all of them could not assert an exact count.
NEW_NDC=""
NEW_NAME=""
make_medication() { given "a medication exists" _make_medication "$@"; }
_make_medication() {
  local overrides="${1:-}"; [[ -n "$overrides" ]] || overrides='{}'
  NEW_NDC="$(gen_ndc)"
  NEW_NAME="Testazepam ${RUN_TAG}x$(_rand 1000000)"
  local payload
  payload="$(jq -nc --arg ndc "$NEW_NDC" --arg name "$NEW_NAME" --argjson o "$overrides" '{
    name: $name,
    ndc: $ndc,
    strength: "10 mg",
    form: "tablet"
  } * $o')"
  req POST /medications/ "$payload"
  [[ "$STATUS" == "201" ]] || die "could not create medication"
  NEW_ID="$(jqv .id)"
}

# make_patient ADDRESS_ID [overrides_json] -- also sets NEW_LAST_NAME, unique
# per patient for the same reason make_medication's name is.
NEW_LAST_NAME=""
make_patient() { given "a patient exists" _make_patient "$@"; }
_make_patient() {
  local address_id="$1" overrides="${2:-}"; [[ -n "$overrides" ]] || overrides='{}'
  NEW_LAST_NAME="Patient-${RUN_TAG}x$(_rand 1000000)"
  local payload
  payload="$(jq -nc --arg addr "$address_id" --arg last "$NEW_LAST_NAME" \
                   --arg phone "$(gen_phone)" --arg email "$(gen_email)" --argjson o "$overrides" '{
    first_name: "Testina",
    last_name: $last,
    dob: "1985-03-14",
    gender: "female",
    phone_number: $phone,
    email: $email,
    sms_opt_in: false,
    address_id: $addr
  } * $o')"
  req POST /patients/ "$payload"
  [[ "$STATUS" == "201" ]] || die "could not create patient"
  NEW_ID="$(jqv .id)"
}

# make_prescriber ADDRESS_ID [overrides_json]
make_prescriber() { given "a prescriber exists" _make_prescriber "$@"; }
_make_prescriber() {
  local address_id="$1" overrides="${2:-}"; [[ -n "$overrides" ]] || overrides='{}'
  local payload
  payload="$(jq -nc --arg addr "$address_id" --arg tag "$RUN_TAG" \
                   --arg npi "$(gen_npi)" --argjson o "$overrides" '{
    first_name: "Testopher",
    last_name: ("Prescriber-" + $tag),
    practitioner_type: "MD",
    npi: $npi,
    address_id: $addr
  } * $o')"
  req POST /prescribers/ "$payload"
  [[ "$STATUS" == "201" ]] || die "could not create prescriber"
  NEW_ID="$(jqv .id)"
}

# make_prescription PATIENT_ID PRESCRIBER_ID MEDICATION_ID [overrides_json]
make_prescription() { given "a prescription exists" _make_prescription "$@"; }
_make_prescription() {
  local patient="$1" prescriber="$2" medication="$3" overrides="${4:-}"
  [[ -n "$overrides" ]] || overrides='{}'
  local payload
  payload="$(jq -nc --arg p "$patient" --arg d "$prescriber" --arg m "$medication" --argjson o "$overrides" '{
    patient_id: $p,
    prescriber_id: $d,
    medication_id: $m,
    intake_source: "provider",
    dose_amount: "1 tablet",
    quantity: 30,
    fills_authorized: 1,
    instructions: "Take one tablet by mouth daily.",
    status: "active"
  } * $o')"
  req POST /prescriptions/ "$payload"
  [[ "$STATUS" == "201" ]] || die "could not create prescription"
  NEW_ID="$(jqv .id)"
}

# A patient + prescriber + medication sharing one address, for prescription
# tests that do not care where the parts came from. Sets FX_ADDRESS,
# FX_PATIENT, FX_PRESCRIBER, FX_MEDICATION, and the two names a search test
# needs to scope itself to this cast.
FX_ADDRESS=""; FX_PATIENT=""; FX_PRESCRIBER=""; FX_MEDICATION=""
FX_MEDICATION_NAME=""; FX_PATIENT_LAST_NAME=""
make_cast() { given "a patient, a prescriber and a medication exist" _make_cast "$@"; }
_make_cast() {
  make_address;                    FX_ADDRESS="$NEW_ID"
  make_medication;                 FX_MEDICATION="$NEW_ID"; FX_MEDICATION_NAME="$NEW_NAME"
  make_patient "$FX_ADDRESS";      FX_PATIENT="$NEW_ID";    FX_PATIENT_LAST_NAME="$NEW_LAST_NAME"
  make_prescriber "$FX_ADDRESS";   FX_PRESCRIBER="$NEW_ID"
}

# make_pharmacy [overrides_json] -- also sets NEW_PHARMACY_NAME, unique per
# call so a search or a name filter can scope itself to this one.
NEW_PHARMACY_NAME=""
make_pharmacy() { given "a pharmacy exists" _make_pharmacy "$@"; }
_make_pharmacy() {
  local overrides="${1:-}"; [[ -n "$overrides" ]] || overrides='{}'
  NEW_PHARMACY_NAME="Testmart ${RUN_TAG}x$(_rand 1000000)"
  local payload
  payload="$(jq -nc --arg name "$NEW_PHARMACY_NAME" --arg phone "$(gen_phone)" \
                   --arg email "$(gen_email)" --argjson o "$overrides" '{
    name: $name,
    legal_name: ($name + " LLC"),
    phone_number: $phone,
    email: $email
  } * $o')"
  req POST /pharmacies/ "$payload"
  [[ "$STATUS" == "201" ]] || die "could not create pharmacy"
  NEW_ID="$(jqv .id)"
}

# make_location PHARMACY_ID ADDRESS_ID [overrides_json]
#
# The pharmacy comes from the path, never the body: a location is a
# sub-resource of the pharmacy that owns it. Also sets NEW_NCPDP and
# NEW_STORE_NUMBER.
NEW_NCPDP=""
NEW_STORE_NUMBER=""
make_location() { given "a pharmacy location exists" _make_location "$@"; }
_make_location() {
  local pharmacy="$1" address_id="$2" overrides="${3:-}"
  [[ -n "$overrides" ]] || overrides='{}'
  NEW_NCPDP="$(gen_ncpdp)"
  NEW_STORE_NUMBER="$(printf '%05d' "$(_rand 100000)")"
  local payload
  payload="$(jq -nc --arg addr "$address_id" --arg ncpdp "$NEW_NCPDP" \
                   --arg store "$NEW_STORE_NUMBER" --arg phone "$(gen_phone)" --argjson o "$overrides" '{
    store_number: $store,
    ncpdp_id: $ncpdp,
    location_type: "retail",
    phone_number: $phone,
    address_id: $addr
  } * $o')"
  req POST "/pharmacies/${pharmacy}/locations/" "$payload"
  [[ "$STATUS" == "201" ]] || die "could not create pharmacy location"
  NEW_ID="$(jqv .id)"
}

# make_staff PHARMACY_ID [overrides_json]
#
# A registered pharmacist by default -- the role every other role is a
# relaxation of, and the only one a location may name as its
# pharmacist-in-charge. Also sets NEW_LAST_NAME, NEW_REGISTRATION and
# NEW_REGISTRATION_STATE.
NEW_REGISTRATION=""
NEW_REGISTRATION_STATE=""
make_staff() { given "a staff member exists" _make_staff "$@"; }
_make_staff() {
  local pharmacy="$1" overrides="${2:-}"; [[ -n "$overrides" ]] || overrides='{}'
  NEW_LAST_NAME="Staffer-${RUN_TAG}x$(_rand 1000000)"
  NEW_REGISTRATION="$(gen_registration)"
  NEW_REGISTRATION_STATE="OR"
  local payload
  payload="$(jq -nc --arg last "$NEW_LAST_NAME" --arg reg "$NEW_REGISTRATION" \
                   --arg state "$NEW_REGISTRATION_STATE" --argjson o "$overrides" '{
    first_name: "Testila",
    last_name: $last,
    role: "pharmacist",
    registration_state: $state,
    registration_number: $reg
  } * $o')"
  req POST "/pharmacies/${pharmacy}/staff/" "$payload"
  [[ "$STATUS" == "201" ]] || die "could not create staff member"
  NEW_ID="$(jqv .id)"
}

# The two people the fill workflow needs, and the pharmacy that employs them.
# A technician starts a fill and only a pharmacist may verify it, so a case
# that dispenses anything needs both. Sets FX_PHARMACY, FX_PHARMACIST and
# FX_TECHNICIAN.
FX_PHARMACIST=""; FX_TECHNICIAN=""
make_dispensing_team() { given "a pharmacy with a pharmacist and a technician exists" _make_dispensing_team "$@"; }
_make_dispensing_team() {
  make_pharmacy;                                  FX_PHARMACY="$NEW_ID"
  make_staff "$FX_PHARMACY";                      FX_PHARMACIST="$NEW_ID"
  make_staff "$FX_PHARMACY" '{"role":"technician"}'; FX_TECHNICIAN="$NEW_ID"
}

# make_dispensed_fill PRESCRIPTION_ID OPERATOR_ID PHARMACIST_ID [overrides_json]
#
# One fill all the way to completed: the technician starts it, the pharmacist
# verifies it, and completing it is what moves the prescription's totals. With
# no overrides the service dispenses one written fill's worth, or whatever is
# left of the authorized quantity.
#
# Three requests folded into one Given, because a case that is about what the
# prescription looks like afterwards should not have to narrate the workflow
# each time. A case that is about the workflow itself writes the three out.
make_dispensed_fill() { given "a fill is dispensed" _make_dispensed_fill "$@"; }
_make_dispensed_fill() {
  local prescription="$1" operator="$2" pharmacist="$3" overrides="${4:-}"
  [[ -n "$overrides" ]] || overrides='{}'
  req_staff "$operator" POST "/prescriptions/${prescription}/fills/" "$overrides"
  [[ "$STATUS" == "201" ]] || die "could not start a fill"
  local fill; fill="$(jqv .id)"
  req_staff "$pharmacist" POST "/fills/${fill}/verify/"
  [[ "$STATUS" == "200" ]] || die "could not verify the fill"
  req_staff "$pharmacist" POST "/fills/${fill}/complete/"
  [[ "$STATUS" == "200" ]] || die "could not complete the fill"
  NEW_ID="$fill"
}

# A pharmacy with one address and one location, for tests about what hangs off
# a store rather than about building one. Sets FX_PHARMACY, FX_PHARMACY_ADDRESS
# and FX_LOCATION.
FX_PHARMACY=""; FX_PHARMACY_ADDRESS=""; FX_LOCATION=""
make_store() { given "a pharmacy with one location exists" _make_store "$@"; }
_make_store() {
  make_pharmacy;                             FX_PHARMACY="$NEW_ID"
  make_address;                              FX_PHARMACY_ADDRESS="$NEW_ID"
  make_location "$FX_PHARMACY" "$FX_PHARMACY_ADDRESS"; FX_LOCATION="$NEW_ID"
}

# The pharmacy this deployment is the backend for -- Acme Pharmacy, seeded by
# migration 0003_seed_acme_pharmacy rather than minted here, so a case that
# wants a real dispensing site does not have to create a fresh one every run.
# Looked up over the API rather than the database: if the seed is ever
# missing, the case fails the same way any other missing fixture would. Sets
# HOME_PHARMACY and HOME_LOCATION.
HOME_PHARMACY_NCPDP_ID="4417829"
HOME_PHARMACY=""; HOME_LOCATION=""
use_home_pharmacy() { given "the home pharmacy is used" _use_home_pharmacy "$@"; }
_use_home_pharmacy() {
  req GET "/pharmacies/?name=$(urlencode "Acme Pharmacy")"
  [[ "$STATUS" == "200" ]] || die "could not look up the home pharmacy"
  HOME_PHARMACY="$(jqv '.results[0].id')"
  [[ -n "$HOME_PHARMACY" && "$HOME_PHARMACY" != "null" ]] || die "the home pharmacy is not seeded"

  req GET "/pharmacies/${HOME_PHARMACY}/locations/?ncpdp_id=${HOME_PHARMACY_NCPDP_ID}"
  [[ "$STATUS" == "200" ]] || die "could not look up the home pharmacy location"
  HOME_LOCATION="$(jqv '.results[0].id')"
  [[ -n "$HOME_LOCATION" && "$HOME_LOCATION" != "null" ]] || die "the home pharmacy location is not seeded"
}
