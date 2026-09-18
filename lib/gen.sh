# Run-scoped value generators, generic across suites.
#
# A suite typically never cleans up after itself, so every value an API treats
# as unique among live rows must be fresh on every run and on every call
# within a run. lib/gen.sh mints the run-scoped basics; a suite's own
# lib/gen.sh (sourced after this one) adds whatever its API needs validated
# and unique -- an NPI check digit, say -- on top of these.
#
# Randomness comes from /dev/urandom rather than $RANDOM: this suite targets
# bash 3.2 (the only bash macOS ships), where $RANDOM in a command substitution
# inherits the parent's seed, and helpers called as "$(gen_phone)" would repeat.

: "${RUN_ID:=$(date +%Y%m%d%H%M%S)-$$}"
export RUN_ID

# A short tag embedded in generated names, so rows this suite left behind are
# recognisable in the database later.
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
