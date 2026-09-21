# Fixture builders.
#
# Each helper sets NEW_ID (and leaves the created object in BODY) rather than
# echoing the id. A helper called as "$(make_patient)" would run in a subshell,
# where the STATUS/BODY globals and a failing die() could not reach the case
# file that called it.
#
# Nothing here cleans up: the suite leaves its rows behind by design, which is
# why every unique value comes from lib/gen.sh.

NEW_ID=""

make_address() {
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
make_medication() {
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
make_patient() {
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
make_prescriber() {
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
make_prescription() {
  local patient="$1" prescriber="$2" medication="$3" overrides="${4:-}"
  [[ -n "$overrides" ]] || overrides='{}'
  local payload
  payload="$(jq -nc --arg p "$patient" --arg d "$prescriber" --arg m "$medication" --argjson o "$overrides" '{
    patient_id: $p,
    prescriber_id: $d,
    medication_id: $m,
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
make_cast() {
  make_address;                    FX_ADDRESS="$NEW_ID"
  make_medication;                 FX_MEDICATION="$NEW_ID"; FX_MEDICATION_NAME="$NEW_NAME"
  make_patient "$FX_ADDRESS";      FX_PATIENT="$NEW_ID";    FX_PATIENT_LAST_NAME="$NEW_LAST_NAME"
  make_prescriber "$FX_ADDRESS";   FX_PRESCRIBER="$NEW_ID"
}
