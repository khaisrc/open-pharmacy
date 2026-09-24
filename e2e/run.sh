#!/usr/bin/env bash
# Rerunnable curl end-to-end suite for the open-pharmacy API.
# See README.md. Targets bash 3.2, the version macOS ships.

set -uo pipefail

E2E_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export E2E_DIR

: "${BASE_URL:=http://localhost:8000}"
: "${API_USER:=admin}"
: "${API_PASS:=admin}"
FILTER=""
REPORT_PATH=""
JUNIT_PATH=""
: "${JOBS:=8}"
JOBS_SET=0

usage() {
  cat <<'USAGE'
Usage: ./run.sh [options] [PATTERN]

  PATTERN      run only case files whose name contains PATTERN, the same as
               -f (e.g. ./run.sh 65_fills)
  -f PATTERN   run only case files whose name contains PATTERN
  -p N         run up to N case files concurrently (default 8; 1 is sequential)
  -v           print every request and response
  -x           stop at the first failing assertion (runs sequentially)
  -r PATH      write the HTML report here (default e2e/report.html)
  -j PATH      write JUnit XML here (default e2e/junit.xml)
  -R           skip both reports
  -l           list the case files and exit
  -h           this message

Environment:
  BASE_URL   default http://localhost:8000
  API_USER   default admin
  API_PASS   default admin
  JOBS       same as -p (default 8)
  NO_COLOR   set to disable color and the progress display

Results read as Gherkin: a case file is a Feature, each section in it a
Scenario, fixtures are Given steps, requests are Whens, and the assertions
that read a response are the Thens and Ands beneath it.

Every run writes an HTML report into e2e/ -- one feature per case
file, with the full request and response behind a disclosure on each step --
and a junit.xml beside it for CI.

Case files share no data: each builds its own fixtures from run-scoped unique
values, so -p can run them concurrently. Attached to a terminal, a run draws a
progress bar per case file in place of the step lines, and prints the full
transcript afterwards for whichever cases failed. Redirect stdout (as CI does)
and the plain step-by-step output comes back.

The suite creates records and never deletes them. Every value the API requires
to be unique is generated per run, so it is safe to run repeatedly against the
same database.
USAGE
}

LIST_ONLY=0
while getopts ":f:p:r:j:Rvxlh" opt; do
  case "$opt" in
    f) FILTER="$OPTARG" ;;
    p) JOBS="$OPTARG"; JOBS_SET=1 ;;
    r) REPORT_PATH="$OPTARG" ;;
    j) JUNIT_PATH="$OPTARG" ;;
    R) NO_REPORT=1 ;;
    v) export VERBOSE=1 ;;
    x) export FAIL_FAST=1 ;;
    l) LIST_ONLY=1 ;;
    h) usage; exit 0 ;;
    \?) echo "unknown option -$OPTARG" >&2; usage >&2; exit 2 ;;
    :)  echo "option -$OPTARG needs a value" >&2; exit 2 ;;
  esac
done
shift $(( OPTIND - 1 ))

# A bare argument is the filter, so a single case file can be run as
# `./run.sh 65_fills` without remembering the flag.
if [[ $# -gt 0 ]]; then
  if [[ -n "$FILTER" ]]; then
    echo "pass the filter either as -f or as an argument, not both" >&2
    exit 2
  fi
  FILTER="$1"
  shift
  if [[ $# -gt 0 ]]; then
    echo "only one filter can be given, got: $*" >&2
    exit 2
  fi
fi

case "$JOBS" in
  ''|*[!0-9]*) echo "-p needs a positive whole number, not '${JOBS}'" >&2; exit 2 ;;
esac
[[ "$JOBS" -lt 1 ]] && { echo "-p needs at least 1" >&2; exit 2; }

# -x stops the run at the first failure, which it cannot do once other case
# files are already in flight: they would run to completion regardless, and
# which of them had got how far would vary between runs. Asking for both at
# once is a contradiction; asking for -x alone just means the default
# concurrency is not what this run wants.
if [[ -n "${FAIL_FAST:-}" ]]; then
  if [[ $JOBS_SET -eq 1 && "$JOBS" -gt 1 ]]; then
    echo "-x and -p cannot be combined: a concurrent run cannot stop at the first failure" >&2
    exit 2
  fi
  JOBS=1
fi

# --- what this suite points at -----------------------------------------------
#
# HEALTH_PATH and AUTH_PROBE_PATH drive the preflight checks below; API_ROOT is
# the prefix lib/http.sh puts in front of every path a case file writes, and
# defaults there to ${BASE_URL}/api/v1. DEV_HINT is what a failed preflight
# tells the reader to run.

HEALTH_PATH="/api/v1/health/"
AUTH_PROBE_PATH="/api/v1/patients/"
DEV_HINT="docker compose up"
export DEV_HINT

: "${REPORT_PATH:=${E2E_DIR}/report.html}"
: "${JUNIT_PATH:=${E2E_DIR}/junit.xml}"

if [[ $LIST_ONLY -eq 1 ]]; then
  ls "$E2E_DIR"/cases/*.sh | xargs -n1 basename
  exit 0
fi

c_green=''; c_red=''; c_bold=''; c_dim=''; c_off=''
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  c_green=$'\033[32m'; c_red=$'\033[31m'; c_bold=$'\033[1m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
fi

# A run reads as Gherkin: a case file is a Feature, the sections inside it are
# Scenarios, and lib/http.sh prints the Given/When/Then steps under them. The
# file name is the only part of that heading run.sh owns.
#
# '30_patients' -> 'Patients'. bash 3.2 has no ${x^}, hence the tr.
feature_title() {
  local n="${1#[0-9]*_}"
  n="${n//_/ }"
  printf '%s%s' "$(printf '%s' "${n:0:1}" | tr '[:lower:]' '[:upper:]')" "${n:1}"
}

feature_heading() { # feature_heading CASE_NAME
  printf '%sFeature:%s %s\n' "$c_bold" "$c_off" "$(feature_title "$1")"
}

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
RESULT_DIR="$(mktemp -d -t apitestsuite.XXXXXX)"
# Without this, a failed mktemp (no -e here) leaves RESULT_DIR empty and every
# path built from it below resolves under /, where writes fail silently one
# by one -- no case file logs an assertion, and a run that did nothing reads
# as zero failures instead of the error it actually was.
if [[ -z "$RESULT_DIR" || ! -d "$RESULT_DIR" ]]; then
  echo "${c_red}mktemp could not create a scratch directory${c_off}" >&2
  exit 2
fi
EVENTS="$RESULT_DIR/events.jsonl"
: > "$EVENTS"
export RUN_ID RESULT_DIR BASE_URL API_USER API_PASS

# The cursor is hidden while the progress display runs, so it has to come back
# on every exit path, not just the clean one.
cleanup() {
  [[ "${CURSOR_HIDDEN:-0}" -eq 1 ]] && printf '\033[?25h'
  rm -rf "$RESULT_DIR"
}
CURSOR_HIDDEN=0
trap cleanup EXIT

echo "${c_bold}open-pharmacy e2e${c_off}  ${c_dim}${BASE_URL}  run ${RUN_ID}${c_off}"

started="$(date +%s)"
files=()
for f in "$E2E_DIR"/cases/*.sh; do
  [[ -n "$FILTER" && "$(basename "$f")" != *"$FILTER"* ]] && continue
  files+=("$f")
done

if [[ ${#files[@]} -eq 0 ]]; then
  echo "${c_red}no case files matched '${FILTER}'${c_off}" >&2
  exit 2
fi

# The widest case name, so the bars line up in a column.
NAME_W=0
for case_path in "${files[@]}"; do
  n="$(basename "$case_path" .sh)"
  [[ ${#n} -gt $NAME_W ]] && NAME_W=${#n}
done

# --- progress display --------------------------------------------------------
#
# Drawn only on a terminal. CI redirects stdout, where cursor-movement escapes
# are worse than the lines they replace, and -v wants the request/response
# stream the display would be fighting for the screen.
#
# The bar for a case is drawn against how many assertions that case made last
# time, recorded in .assert-counts. The count is not knowable up front -- cases
# loop over collections, and -f changes the set -- so the first run of a case
# shows a count with no bar, and every run after that has a real denominator.

BASELINE="$E2E_DIR/.assert-counts"
PROGRESS=0
if [[ -t 1 && -z "${NO_COLOR:-}" && -z "${VERBOSE:-}" ]]; then
  PROGRESS=1
  PROGRESS_LINES=$(( ${#files[@]} + 3 ))
  term_lines="$(tput lines 2>/dev/null || echo 24)"
  # Redrawing by moving the cursor up assumes the block never scrolled off.
  [[ "$term_lines" -lt $(( PROGRESS_LINES + 4 )) ]] && PROGRESS=0
fi

# Case output goes to a file unless it can stream straight to the terminal:
# concurrent cases would interleave, and the progress display needs the screen.
BUFFERED=1
[[ "$JOBS" -eq 1 && $PROGRESS -eq 0 ]] && BUFFERED=0
# lib/http.sh colors its output on -t 1, which a buffered case never sees.
[[ $BUFFERED -eq 1 && -t 1 && -z "${NO_COLOR:-}" ]] && export FORCE_COLOR=1

BAR_W=20
term_cols="$(tput cols 2>/dev/null || echo 80)"
[[ $(( NAME_W + BAR_W + 22 )) -gt "$term_cols" ]] && BAR_W=10

# Bar glyphs. A terminal outside a UTF-8 locale renders these as mojibake, so
# fall back to ASCII there rather than to something worse than the numbers.
# locale(1) resolves LC_ALL/LC_CTYPE/LANG the way the terminal itself does.
BAR_FILL='#'; BAR_EDGE='-'; BAR_REST='-'; RULE_CH='-'
case "$(locale charmap 2>/dev/null)" in
  UTF-8|utf-8|UTF8|utf8) BAR_FILL='━'; BAR_EDGE='╺'; BAR_REST='━'; RULE_CH='─' ;;
esac

_expected() { # _expected CASE -- assertions it made last run, 0 if unknown
  [[ -f "$BASELINE" ]] || { echo 0; return; }
  awk -F'\t' -v c="$1" '$1 == c { print $2; found = 1 } END { if (!found) print 0 }' \
    "$BASELINE" 2>/dev/null || echo 0
}

# _bar DONE TOTAL WIDTH [FILL_COLOR] -- the filled run in FILL_COLOR and the
# remainder dim, parted by a half-width glyph so the leading edge reads as an
# edge rather than as a color change.
_bar() {
  local done="$1" total="$2" width="$3" fill_c="${4:-}" filled i out=""
  if [[ "$total" -le 0 ]]; then filled=0; else filled=$(( done * width / total )); fi
  [[ $filled -gt $width ]] && filled=$width
  [[ $filled -lt 0 ]] && filled=0
  if [[ $filled -gt 0 ]]; then
    out+="$fill_c"
    for (( i = 0; i < filled; i++ )); do out+="$BAR_FILL"; done
    out+="$c_off"
  fi
  if [[ $filled -lt $width ]]; then
    out+="$c_dim"
    if [[ $filled -gt 0 ]]; then
      # The edge glyph marks where the fill stops. An empty bar has no fill to
      # mark the end of, and an edge there reads as a unit of progress.
      out+="$BAR_EDGE"
      for (( i = filled + 1; i < width; i++ )); do out+="$BAR_REST"; done
    else
      for (( i = 0; i < width; i++ )); do out+="$BAR_REST"; done
    fi
    out+="$c_off"
  fi
  printf '%s' "$out"
}

_rule() { # _rule WIDTH
  local width="$1" i out=""
  for (( i = 0; i < width; i++ )); do out+="$RULE_CH"; done
  printf '%s%s%s' "$c_dim" "$out" "$c_off"
}
_tally() { # _tally EVENTFILE PATTERN
  local n
  [[ -f "$1" ]] || { echo 0; return; }
  n="$(grep -c "$2" "$1" 2>/dev/null | tr -dc '0-9')"
  echo "${n:-0}"
}

_draw() { # _draw [FIRST] -- prints exactly $PROGRESS_LINES lines
  local first="${1:-0}" case_file name ev passed failed expect fill
  local done_files=0 tp=0 tf=0 el mins secs
  [[ "$first" -eq 1 ]] || printf '\033[%dA' "$PROGRESS_LINES"
  printf '\033[2K\n'
  for case_file in "${files[@]}"; do
    name="$(basename "$case_file" .sh)"
    ev="$RESULT_DIR/events.${name}.jsonl"
    passed="$(_tally "$ev" '"outcome":"pass"')"
    failed="$(_tally "$ev" '"outcome":"fail"')"
    expect="$(_expected "$name")"
    tp=$(( tp + passed )); tf=$(( tf + failed ))
    # A case that has already failed goes red as it runs, rather than looking
    # healthy until the moment it finishes.
    if [[ "$failed" -gt 0 ]]; then fill="$c_red"; else fill="$c_green"; fi
    printf '\033[2K  %-*s ' "$NAME_W" "$name"
    if [[ -f "$RESULT_DIR/done.${name}" ]]; then
      done_files=$(( done_files + 1 ))
      if [[ "$failed" -gt 0 ]]; then
        printf '%s  %s%d failed%s\n' "$(_bar 1 1 "$BAR_W" "$c_red")" \
          "$c_red" "$failed" "$c_off"
      else
        printf '%s  %d ok\n' "$(_bar 1 1 "$BAR_W" "$c_green")" "$passed"
      fi
    elif [[ -f "$ev" ]]; then
      printf '%s  %d' "$(_bar $(( passed + failed )) "$expect" "$BAR_W" "$fill")" \
        "$(( passed + failed ))"
      [[ "$expect" -gt 0 ]] && printf '/%d' "$expect"
      printf '\n'
    else
      printf '%s  %spending%s\n' "$(_bar 0 1 "$BAR_W")" "$c_dim" "$c_off"
    fi
  done
  el=$(( $(date +%s) - started )); mins=$(( el / 60 )); secs=$(( el % 60 ))
  printf '\033[2K  %s\n' "$(_rule $(( NAME_W + BAR_W + 18 )))"
  printf '\033[2K  %s%d/%d files%s   %d passed  %d failed   %d:%02d\n' \
    "$c_bold" "$done_files" "${#files[@]}" "$c_off" "$tp" "$tf" "$mins" "$secs"
}

run_case() { # run to a per-case buffer, then mark it finished
  local f="$1" name; name="$(basename "$f" .sh)"
  { echo; feature_heading "$name"; bash "$f"; } > "$RESULT_DIR/out.${name}" 2>&1
  local rc=$?
  : > "$RESULT_DIR/done.${name}"
  return $rc
}

aborted=0

# $case_path, not $f: _draw walks "${files[@]}" too, and a loop variable it
# shared with the code below would be left pointing at the last case file on
# every return -- dispatching the same case repeatedly into one event log.
if [[ $BUFFERED -eq 0 ]]; then
  # One case at a time, straight to the terminal: the original behavior, and
  # the only mode in which -x can stop the run where it failed.
  for case_path in "${files[@]}"; do
    echo
    feature_heading "$(basename "$case_path" .sh)"
    bash "$case_path"
    rc=$?
    if [[ $rc -ne 0 && -n "${FAIL_FAST:-}" ]]; then aborted=1; break; fi
  done
else
  if [[ $PROGRESS -eq 1 ]]; then printf '\033[?25l'; CURSOR_HIDDEN=1; _draw 1; fi
  # Even at one worker the case runs in the background, so the poll below can
  # redraw while it works: waiting on it in the foreground would leave the bar
  # frozen until the whole case finished. bash 3.2 has no `wait -n`, so the
  # worker count is held by polling the job table, which doubles as the
  # display's frame clock.
  for case_path in "${files[@]}"; do
    while [[ "$(jobs -rp | wc -l | tr -d ' ')" -ge "$JOBS" ]]; do
      [[ $PROGRESS -eq 1 ]] && _draw
      sleep 0.1
    done
    run_case "$case_path" &
  done
  while [[ "$(jobs -rp | wc -l | tr -d ' ')" -gt 0 ]]; do
    [[ $PROGRESS -eq 1 ]] && _draw
    sleep 0.1
  done
  wait
  if [[ $PROGRESS -eq 1 ]]; then _draw; printf '\033[?25h'; CURSOR_HIDDEN=0; fi
fi

# --- gather the event logs ---------------------------------------------------
#
# In case order rather than completion order: bin/report.py groups by first
# appearance, so this is what puts the report's sections back in suite order
# after a concurrent run.
for case_path in "${files[@]}"; do
  ev="$RESULT_DIR/events.$(basename "$case_path" .sh).jsonl"
  [[ -f "$ev" ]] && cat "$ev" >> "$EVENTS"
done

# --- the transcript ----------------------------------------------------------
#
# The progress display replaced the per-assertion lines, so print back the full
# output of any case that failed -- the assertion text alone does not carry the
# request and response that explain it. Without the display, nothing was shown
# live, so print everything.
if [[ $BUFFERED -eq 1 ]]; then
  for case_path in "${files[@]}"; do
    name="$(basename "$case_path" .sh)"
    out="$RESULT_DIR/out.${name}"
    [[ -f "$out" ]] || continue
    if [[ $PROGRESS -eq 0 ]] || grep -q '"outcome":"fail"' \
         "$RESULT_DIR/events.${name}.jsonl" 2>/dev/null; then
      cat "$out"
    fi
  done
fi

# --- report ------------------------------------------------------------------

elapsed=$(( $(date +%s) - started ))
passed="$(grep -c '"outcome":"pass"' "$EVENTS" 2>/dev/null || true)"
failed="$(grep -c '"outcome":"fail"' "$EVENTS" 2>/dev/null || true)"
passed="$(echo "${passed:-0}" | tr -dc '0-9')"; passed="${passed:-0}"
failed="$(echo "${failed:-0}" | tr -dc '0-9')"; failed="${failed:-0}"

# What the next run's bars measure against. Merged, not overwritten, so a
# filtered run only updates the cases it actually ran.
if [[ $aborted -eq 0 ]]; then
  jq -r 'select(.t == "assertion") | .case' "$EVENTS" 2>/dev/null | sort | uniq -c |
    awk '{ printf "%s\t%s\n", $2, $1 }' > "$RESULT_DIR/counts.new"
  if [[ -s "$RESULT_DIR/counts.new" ]]; then
    if [[ -f "$BASELINE" ]]; then
      awk -F'\t' 'NR == FNR { seen[$1] = 1; print; next } !($1 in seen)' \
        "$RESULT_DIR/counts.new" "$BASELINE" | sort > "$RESULT_DIR/counts.merged"
    else
      sort "$RESULT_DIR/counts.new" > "$RESULT_DIR/counts.merged"
    fi
    mv "$RESULT_DIR/counts.merged" "$BASELINE" 2>/dev/null || true
  fi
fi

echo
if [[ "${failed:-0}" -gt 0 ]]; then
  echo "${c_bold}Failures${c_off}"
  # Each failure named the way the transcript above shows it: which feature,
  # which scenario, and the step that did not hold.
  last=""
  jq -r 'select(.t == "assertion" and .outcome == "fail")
         | [.case, (if (.section // "") == "" then "Background" else .section end),
            (.keyword // "Then"), .description] | @tsv' "$EVENTS" |
    while IFS=$'\t' read -r case scenario keyword desc; do
      # One heading per scenario, however many of its steps failed.
      if [[ "${case}/${scenario}" != "$last" ]]; then
        echo "  ${c_red}x${c_off} ${c_dim}${case}${c_off}  ${scenario}"
        last="${case}/${scenario}"
      fi
      echo "       ${c_dim}${keyword}${c_off} ${desc}"
    done
  echo
fi

if [[ -z "${NO_REPORT:-}" ]]; then
  if python3 "$E2E_DIR/bin/report.py" \
       --events "$EVENTS" \
       --out "$REPORT_PATH" \
       --junit "$JUNIT_PATH" \
       --run-id "$RUN_ID" \
       --base-url "$BASE_URL" \
       --seconds "$elapsed" \
       --suite-name open-pharmacy; then
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
