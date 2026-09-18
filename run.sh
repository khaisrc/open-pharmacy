#!/usr/bin/env bash
# Rerunnable curl test framework, shared across API test suites.
# See README.md. Targets bash 3.2, the version macOS ships.

set -uo pipefail

SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export SUITE_DIR

: "${BASE_URL:=http://localhost:8000}"
: "${API_USER:=admin}"
: "${API_PASS:=admin}"
FILTER=""
SUITE="${SUITE_NAME:-}"
REPORT_PATH=""
JUNIT_PATH=""

usage() {
  cat <<'USAGE'
Usage: ./run.sh [options]

  -s SUITE     which suite to run (a directory with its own cases/) --
               default: the only such directory found, if there is one
  -f PATTERN   run only case files whose name contains PATTERN
  -v           print every request and response
  -x           stop at the first failing assertion
  -r PATH      write the HTML report here (default SUITE/report.html)
  -j PATH      write JUnit XML here (default SUITE/junit.xml)
  -R           skip both reports
  -l           list the case files and exit
  -h           this message

Environment:
  BASE_URL   default http://localhost:8000
  API_USER   default admin
  API_PASS   default admin
  SUITE_NAME same as -s

Every run writes an HTML report next to the suite -- one section per case
file, with the full request and response behind a disclosure on each
assertion -- and a junit.xml beside it for CI.

The suite creates records and never deletes them. Every value the API requires
to be unique is generated per run, so it is safe to run repeatedly against the
same database.
USAGE
}

LIST_ONLY=0
while getopts ":s:f:r:j:Rvxlh" opt; do
  case "$opt" in
    s) SUITE="$OPTARG" ;;
    f) FILTER="$OPTARG" ;;
    r) REPORT_PATH="$OPTARG" ;;
    j) JUNIT_PATH="$OPTARG" ;;
    R) NO_REPORT=1 ;;
    v) export VERBOSE=1 ;;
    x) export FAIL_FAST=1 ;;
    l) LIST_ONLY=1 ;; # case files live under the suite, resolved below
    h) usage; exit 0 ;;
    \?) echo "unknown option -$OPTARG" >&2; usage >&2; exit 2 ;;
    :)  echo "option -$OPTARG needs a value" >&2; exit 2 ;;
  esac
done

# --- suite selection -----------------------------------------------------

if [[ -z "$SUITE" ]]; then
  candidates=()
  for d in "$SUITE_DIR"/*/; do
    [[ -d "${d}cases" ]] && candidates+=("$(basename "$d")")
  done
  if [[ ${#candidates[@]} -eq 1 ]]; then
    SUITE="${candidates[0]}"
  elif [[ ${#candidates[@]} -eq 0 ]]; then
    echo "no suite found (no subdirectory has a cases/ folder)" >&2
    exit 2
  else
    echo "multiple suites found (${candidates[*]}) -- pick one with -s" >&2
    exit 2
  fi
fi

SUITE_PATH="${SUITE_DIR}/${SUITE}"
export SUITE_PATH
[[ -d "$SUITE_PATH/cases" ]] || {
  echo "no such suite: ${SUITE} (expected ${SUITE_PATH}/cases)" >&2
  exit 2
}
: "${REPORT_PATH:=${SUITE_PATH}/report.html}"
: "${JUNIT_PATH:=${SUITE_PATH}/junit.xml}"

# A suite's own config: HEALTH_PATH and AUTH_PROBE_PATH for the preflight
# checks below, and optionally API_ROOT (see lib/http.sh) and DEV_HINT.
# Every suite must provide the first two -- there is no API-shaped default
# to fall back to.
source "$SUITE_PATH/suite.env"
[[ -n "${API_ROOT:-}" ]] && export API_ROOT
[[ -n "${DEV_HINT:-}" ]] && export DEV_HINT

if [[ $LIST_ONLY -eq 1 ]]; then
  ls "$SUITE_PATH"/cases/*.sh | xargs -n1 basename
  exit 0
fi

c_green=''; c_red=''; c_bold=''; c_dim=''; c_off=''
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  c_green=$'\033[32m'; c_red=$'\033[31m'; c_bold=$'\033[1m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
fi

# --- preflight ---------------------------------------------------------------

for dep in curl jq; do
  command -v "$dep" >/dev/null 2>&1 || {
    echo "${c_red}$dep is required${c_off} -- install it with: brew install $dep" >&2
    exit 2
  }
done

health="$(curl -sS -o /dev/null -w '%{http_code}' "${BASE_URL}${HEALTH_PATH}" 2>/dev/null)" || health=""
if [[ "$health" != "200" ]]; then
  echo "${c_red}The API is not answering at ${BASE_URL}${c_off}" >&2
  echo "  GET ${BASE_URL}${HEALTH_PATH} returned '${health:-no response}'" >&2
  [[ -n "${DEV_HINT:-}" ]] && echo "  Start it with:  ${DEV_HINT}" >&2
  exit 2
fi

auth="$(curl -sS -o /dev/null -w '%{http_code}' -u "${API_USER}:${API_PASS}" \
        "${BASE_URL}${AUTH_PROBE_PATH}" 2>/dev/null)" || auth=""
if [[ "$auth" != "200" ]]; then
  echo "${c_red}Credentials rejected${c_off}: ${API_USER} got HTTP ${auth:-no response} on ${AUTH_PROBE_PATH}" >&2
  echo "  Set API_USER / API_PASS if they are not admin / admin." >&2
  exit 2
fi

# --- run ---------------------------------------------------------------------

RUN_ID="$(date +%Y%m%d%H%M%S)-$$"
RESULT_DIR="$(mktemp -d -t apitestsuite)"
EVENTS="$RESULT_DIR/events.jsonl"
: > "$EVENTS"
export RUN_ID RESULT_DIR BASE_URL API_USER API_PASS
trap 'rm -rf "$RESULT_DIR"' EXIT

echo "${c_bold}${SUITE} API suite${c_off}  ${c_dim}${BASE_URL}  run ${RUN_ID}${c_off}"

started="$(date +%s)"
files=()
for f in "$SUITE_PATH"/cases/*.sh; do
  [[ -n "$FILTER" && "$(basename "$f")" != *"$FILTER"* ]] && continue
  files+=("$f")
done

if [[ ${#files[@]} -eq 0 ]]; then
  echo "${c_red}no case files matched '${FILTER}'${c_off}" >&2
  exit 2
fi

aborted=0
for f in "${files[@]}"; do
  echo
  echo "${c_bold}$(basename "$f" .sh)${c_off}"
  bash "$f"
  rc=$?
  if [[ $rc -ne 0 && -n "${FAIL_FAST:-}" ]]; then aborted=1; break; fi
done

# --- report ------------------------------------------------------------------

elapsed=$(( $(date +%s) - started ))
passed="$(grep -c '"outcome":"PASS"' "$EVENTS" 2>/dev/null || true)"
failed="$(grep -c '"outcome":"FAIL"' "$EVENTS" 2>/dev/null || true)"
passed="$(echo "${passed:-0}" | tr -dc '0-9')"; passed="${passed:-0}"
failed="$(echo "${failed:-0}" | tr -dc '0-9')"; failed="${failed:-0}"

echo
if [[ "${failed:-0}" -gt 0 ]]; then
  echo "${c_bold}Failures${c_off}"
  jq -r 'select(.t == "assertion" and .outcome == "FAIL")
         | "\(.case)\t\(.description)"' "$EVENTS" |
    while IFS=$'\t' read -r case desc; do
      echo "  ${c_red}x${c_off} ${c_dim}${case}${c_off}  ${desc}"
    done
  echo
fi

if [[ -z "${NO_REPORT:-}" ]]; then
  if python3 "$SUITE_DIR/bin/report.py" \
       --events "$EVENTS" \
       --out "$REPORT_PATH" \
       --junit "$JUNIT_PATH" \
       --run-id "$RUN_ID" \
       --base-url "$BASE_URL" \
       --seconds "$elapsed" \
       --suite-name "$SUITE"; then
    echo "${c_dim}report: ${REPORT_PATH}${c_off}"
    echo "${c_dim}junit:  ${JUNIT_PATH}${c_off}"
  else
    echo "${c_red}the report could not be written${c_off} (the run itself is unaffected)" >&2
  fi
fi

echo "${c_bold}${passed} passed, ${failed} failed${c_off}  ${c_dim}in ${elapsed}s${c_off}"
[[ $aborted -eq 1 ]] && echo "${c_dim}(stopped early: -x)${c_off}"
[[ "${failed:-0}" -gt 0 ]] && exit 1
exit 0
