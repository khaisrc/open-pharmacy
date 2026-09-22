# Run-scoped value generators.
#
# The suite never cleans up after itself, so every value the API treats as
# unique among live rows must be fresh on every run and on every call within
# a run: a hard-coded NDC or NPI would pass the first time and 409 forever
# after. Generic helpers come first; the identifiers this API validates are
# built on top of them.
#
# Randomness comes from /dev/urandom rather than $RANDOM: this suite targets
# bash 3.2 (the only bash macOS ships), where $RANDOM in a command substitution
# inherits the parent's seed, and helpers called as "$(gen_phone)" would repeat.

# --- generic -----------------------------------------------------------------

: "${RUN_ID:=$(date +%Y%m%d%H%M%S)-$$}"
export RUN_ID

# A short tag embedded in generated names, so rows this suite left behind are
# recognizable in the database later.
RUN_TAG="qa${RUN_ID##*-}"

# _rand N -- a uniform integer in [0, N).
_rand() {
  local hex
  hex="$(head -c 4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  echo $(( (0x$hex & 0x3FFFFFFF) % $1 ))
}

_rand9() { printf '%09d' "$(_rand 1000000000)"; }

gen_phone() { printf '+1555%07d' "$(_rand 10000000)"; }

gen_email() { echo "${RUN_TAG}.$(_rand9)@example.local"; }

# An ISO-8601 instant offset from now by N days (negative for the past).
iso_days() {
  local days="$1"
  [[ "$days" == [+-]* ]] || days="+${days}"
  if date -v+1d >/dev/null 2>&1; then
    date -u -v"${days}d" +%Y-%m-%dT%H:%M:%SZ      # BSD date (macOS)
  else
    date -u -d "${days} days" +%Y-%m-%dT%H:%M:%SZ # GNU date
  fi
}

# Well-formed but certainly absent from the database, for 404 tests.
MISSING_UUID="00000000-0000-4000-8000-000000000000"

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
# Hyphenated 5-4-2, which the API stores as its 11-digit normalized form.
gen_ndc() {
  printf '%05d-%04d-%02d' "$(_rand 100000)" "$(_rand 10000)" "$(_rand 100)"
}

ndc_digits() { echo "${1//-/}"; }

# The 5-4-2 display form the API returns in formatted_ndc.
ndc_display_form() {
  local d; d="$(ndc_digits "$1")"
  echo "${d:0:5}-${d:5:4}-${d:9:2}"
}

# --- NCPDP ID ----------------------------------------------------------------
# Seven digits, unique among live locations. No check digit: the API validates
# length and charset only, deliberately.
gen_ncpdp() { printf '%07d' "$(_rand 10000000)"; }

# --- state board registration ------------------------------------------------
# A staff member's registration number is unique per state among live records,
# and the API upper-cases it on write. Generated already upper-case so a
# round-trip assertion reads as one, leaving the normalization test to say so
# on purpose.
gen_registration() { printf 'RPH%09d' "$(_rand 1000000000)"; }

# A technician certification number: free text to the API, unique here only so
# two fixtures never look like the same credential.
gen_certification() { printf '%010d' "$(_rand 1000000000)"; }
