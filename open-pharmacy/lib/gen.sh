# Pharmacy Ops-specific value generators. Sourced after the framework's
# lib/gen.sh, which this relies on for _rand/_rand9/RUN_TAG.
#
# medications.ndc and prescribers.npi are unique among *live* rows, and the
# suite never cleans up after itself, so a fixed NDC or NPI would pass the
# first time and 409 forever after -- every call needs a fresh one.

# --- NPI ---------------------------------------------------------------------
# An NPI is ten digits whose last is a Luhn check digit computed over the number
# prefixed with the 80840 issuer identifier. A random ten-digit string is
# rejected, so this builds one the validator accepts.
npi_check_digit() {
  local digits="80840$1" total=0 i char digit len
  len=${#digits}
  for (( i = 0; i < len; i++ )); do
    char="${digits:len-1-i:1}"
    digit=$(( 10#$char ))
    if (( i % 2 == 0 )); then
      digit=$(( digit * 2 ))
      (( digit > 9 )) && digit=$(( digit - 9 ))
    fi
    total=$(( total + digit ))
  done
  echo $(( (10 - total % 10) % 10 ))
}

gen_npi() {
  local first_nine; first_nine="$(_rand9)"
  echo "${first_nine}$(npi_check_digit "$first_nine")"
}

# The same number with a deliberately wrong check digit, for the rejection test.
gen_bad_npi() {
  local first_nine good; first_nine="$(_rand9)"
  good="$(npi_check_digit "$first_nine")"
  echo "${first_nine}$(( (good + 1) % 10 ))"
}

# --- NDC ---------------------------------------------------------------------
# Hyphenated 5-4-2, which the API stores as its 11-digit normalised form.
gen_ndc() {
  printf '%05d-%04d-%02d' "$(_rand 100000)" "$(_rand 10000)" "$(_rand 100)"
}

ndc_digits() { echo "${1//-/}"; }

# The 5-4-2 display form the API returns in formatted_ndc.
ndc_display_form() {
  local d; d="$(ndc_digits "$1")"
  echo "${d:0:5}-${d:5:4}-${d:9:2}"
}
