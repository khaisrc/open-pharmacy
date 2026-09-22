# HTTP + assertion primitives. Sourced by every case file.
#
# Every request and every assertion is appended to
# $RESULT_DIR/events.<case>.jsonl. That file, not a shell variable, is how
# results reach run.sh: each case file runs in its own process, so a counter
# would not survive the return trip. It is also what bin/report.py renders,
# which is why a request records its headers and timing even when nothing
# asserts against them.
#
# The log is per case, not one shared file, because run.sh can run case files
# concurrently (-p). An event carries a whole response body, so concurrent
# appends to one file would exceed PIPE_BUF and interleave mid-line. run.sh
# concatenates these in case order once every case has finished, and reads
# them while they are still being written to draw its progress bars.
#
# Every event also carries the Gherkin step it belongs to, so the HTML report
# and the JUnit XML tell the same Given/When/Then story the terminal does
# rather than each re-deriving one. See "Gherkin steps" below.

: "${BASE_URL:=http://localhost:8000}"
: "${API_USER:=admin}"
: "${API_PASS:=admin}"
: "${API_ROOT:=${BASE_URL}/api/v1}"

CASE_NAME="$(basename "${BASH_SOURCE[${#BASH_SOURCE[@]} - 1]}" .sh)"
_BODY_FILE="$(mktemp -t apitestbody)"
_HDR_FILE="$(mktemp -t apitesthdr)"
trap 'rm -f "$_BODY_FILE" "$_HDR_FILE"' EXIT

_EVENTS="${RESULT_DIR}/events.${CASE_NAME}.jsonl"

STATUS=""
BODY=""
LAST_REQ=""
LAST_SENT=""
LAST_METHOD=""
LAST_URL=""
LAST_AUTH="none"
LAST_MS="0"
CURRENT_SECTION=""
_REQ_SEQ=0

_c_green=''; _c_red=''; _c_bold=''; _c_dim=''; _c_off=''
if [[ ( -t 1 || -n "${FORCE_COLOR:-}" ) && -z "${NO_COLOR:-}" ]]; then
  _c_green=$'\033[32m'; _c_red=$'\033[31m'; _c_bold=$'\033[1m'
  _c_dim=$'\033[2m'; _c_off=$'\033[0m'
fi

# --- Gherkin steps -----------------------------------------------------------
#
# A run reads as a feature file. The case file is the Feature (run.sh prints
# that heading); `section` opens a Scenario; setup wrapped in `given` is a
# Given; every request is a When; and the assertions that read its response are
# the Then and Ands beneath it.
#
# A step says what the caller was trying to do, in English. The method, the
# path and the body are in the event log regardless, and the report shows them
# on the request's own disclosure, so repeating them in the step text would
# spend the one line that could have said why the request was made.
#
# Steps are numbered per case file rather than per section, so an assertion can
# name its step in the event log without the report having to guess which
# request a description belonged to. A Given may cover several requests -- one
# fixture builder often makes four -- which is why the step number is separate
# from the request number, and why nested builders announce nothing: make_cast
# is one Given, not five.

_STEP_SEQ=0        # the step a request or assertion belongs to, within the case
_STEP_KIND=""      # given | when
_STEP_TEXT=""
_THEN_COUNT=0      # assertions recorded against the current step so far
THEN_KEYWORD=""    # Then for the first of them, And for the rest
_GIVEN_DEPTH=0     # >0 while a `given` body is running
_HEADING_SHOWN=0   # whether a Scenario (or Background) heading has been printed
_WHEN_TEXT=""      # set by `when`, consumed by the request that follows

# Steps line up in columns: two spaces, a four-wide outcome gutter, then the
# keyword. Failures stay at the left margin where they are easy to find, and
# the keywords still read as a column.
_STEP_INDENT='              '  # where a step's text starts, for wrapped detail

_print_heading() { # _print_heading TEXT
  echo
  printf '  %s%s%s\n' "$_c_bold" "$1" "$_c_off"
}

_print_step() { # _print_step MARKER MARKER_COLOUR KEYWORD TEXT
  printf '  %s%-4s%s %s%-6s%s %s\n' \
    "$2" "$1" "$_c_off" "$_c_dim" "$3" "$_c_off" "$4"
}

# Steps taken before the first `section` are the case file's Background: they
# build what every scenario in it starts from. A case file with assertions but
# no sections would land them all under that heading, which would be a lie, so
# every case file that asserts opens a section first.
_open_heading() {
  [[ $_HEADING_SHOWN -eq 1 ]] && return 0
  _HEADING_SHOWN=1
  _print_heading "Background:"
}

_begin_step() { # _begin_step KIND TEXT
  _open_heading
  _STEP_SEQ=$(( _STEP_SEQ + 1 ))
  _STEP_KIND="$1"
  _STEP_TEXT="$2"
  _THEN_COUNT=0
}

# given TEXT COMMAND [ARGS...] -- run setup as one Given step.
#
# The command runs in this shell rather than a subshell, so a fixture builder's
# NEW_ID (and a failing die()) still reach the case file. Nested calls fold
# into the outermost step: make_cast says what it leaves behind once, rather
# than narrating the four requests it takes to get there.
given() {
  local text="$1" rc; shift
  if [[ $_GIVEN_DEPTH -eq 0 ]]; then
    _begin_step given "$text"
    _print_step "" "" "Given" "$text"
  fi
  _GIVEN_DEPTH=$(( _GIVEN_DEPTH + 1 ))
  "$@"
  rc=$?
  _GIVEN_DEPTH=$(( _GIVEN_DEPTH - 1 ))
  return $rc
}

# when TEXT -- name the request that follows.
#
# A statement rather than a wrapper like `given`, because a When labels exactly
# one request and the request is often a multi-line payload that reads better
# on its own. `given` wraps because a fixture builder is several requests that
# have to fold into one step.
when() { _WHEN_TEXT="$1"; }

_when() { # _when FALLBACK -- open a When step, unless a Given owns this request
  [[ $_GIVEN_DEPTH -gt 0 ]] && return 0
  local text="$_WHEN_TEXT"
  _WHEN_TEXT=""
  # An unlabelled request still gets a step, named after the wire. It reads as
  # the odd one out among its neighbours, which is the point: the fallback is
  # how a forgotten `when` shows up rather than passing unnoticed.
  [[ -n "$text" ]] || text="$1"
  _begin_step when "$text"
  _print_step "" "" "When" "$text"
}

_next_then() {
  _THEN_COUNT=$(( _THEN_COUNT + 1 ))
  if [[ $_THEN_COUNT -eq 1 ]]; then THEN_KEYWORD="Then"; else THEN_KEYWORD="And"; fi
}

# --- request -----------------------------------------------------------------

# _curl AUTH_LABEL METHOD PATH BODY CURL_AUTH_ARGS...
#
# AUTH_LABEL is what the report shows in place of the Authorization header.
# The credentials themselves are never written to the event log: a report is
# something you attach to a ticket, and admin:admin here is a real password
# somewhere else.
_curl() {
  local auth_label="$1" method="$2" path="$3" body="$4"; shift 4
  local url="${API_ROOT}${path}"
  local args=(-sS -o "$_BODY_FILE" -D "$_HDR_FILE" -w '%{http_code} %{time_total}'
              -X "$method" -H 'Accept: application/json' "$@")
  [[ -n "$body" ]] && args+=(-H 'Content-Type: application/json' -d "$body")

  local measured
  measured="$(curl "${args[@]}" "$url" 2>/dev/null)" || {
    echo "${_c_red}FATAL${_c_off} curl could not reach ${url}" >&2
    [[ -n "${DEV_HINT:-}" ]] && echo "      Is the API running?  ${DEV_HINT}" >&2
    exit 2
  }
  STATUS="${measured%% *}"
  LAST_MS="${measured##* }"
  BODY="$(cat "$_BODY_FILE")"
  LAST_REQ="$method $url"
  LAST_SENT="$body"
  LAST_METHOD="$method"
  LAST_URL="$url"
  LAST_AUTH="$auth_label"
  _REQ_SEQ=$(( _REQ_SEQ + 1 ))

  _emit_request

  if [[ -n "${VERBOSE:-}" ]]; then
    echo "${_c_dim}${_STEP_INDENT}-> $method $url${_c_off}"
    [[ -n "$body" ]] && echo "${_c_dim}${_STEP_INDENT}   body: $body${_c_off}"
    echo "${_c_dim}${_STEP_INDENT}<- $STATUS $(echo "$BODY" | head -c 400)${_c_off}"
  fi
}

# Authenticated request: req METHOD PATH [JSON_BODY]
req() {
  _when "$1 $2"
  _curl "Basic ${API_USER}" "$1" "$2" "${3-}" -u "${API_USER}:${API_PASS}"
}

# Unauthenticated request: req_anon METHOD PATH [JSON_BODY]
req_anon() {
  _when "$1 $2 without credentials"
  _curl "none" "$1" "$2" "${3-}"
}

# Request with explicit credentials: req_as USER PASS METHOD PATH [JSON_BODY]
req_as() {
  local u="$1" p="$2"; shift 2
  _when "$1 $2 as ${u}"
  _curl "Basic ${u}" "$1" "$2" "${3-}" -u "${u}:${p}"
}

# --- the event log -----------------------------------------------------------

# Everything the report needs about the request that just finished. Bodies and
# header values are handed to jq as arguments rather than spliced into a string,
# so a quote or newline in a response cannot corrupt the log.
_emit_request() {
  local resp_headers
  resp_headers="$(tr -d "\r" < "$_HDR_FILE" 2>/dev/null || true)"
  jq -nc \
    --arg case "$CASE_NAME" \
    --arg section "$CURRENT_SECTION" \
    --argjson seq "$_REQ_SEQ" \
    --argjson step "$_STEP_SEQ" \
    --arg stepkind "$_STEP_KIND" \
    --arg steptext "$_STEP_TEXT" \
    --arg method "$LAST_METHOD" \
    --arg url "$LAST_URL" \
    --arg auth "$LAST_AUTH" \
    --arg reqbody "$LAST_SENT" \
    --arg status "$STATUS" \
    --arg seconds "$LAST_MS" \
    --arg respheaders "$resp_headers" \
    --arg respbody "$BODY" \
    '{
      t: "request", case: $case, section: $section, seq: $seq,
      step: $step, step_kind: $stepkind, step_text: $steptext,
      method: $method, url: $url,
      request_headers: (
        [["Accept", "application/json"]]
        + (if $reqbody == "" then [] else [["Content-Type", "application/json"]] end)
        + (if $auth == "none" then [] else [["Authorization", ($auth + " \u2039redacted\u203a")]] end)
      ),
      request_body: $reqbody,
      status: ($status | tonumber? // 0),
      seconds: ($seconds | tonumber? // 0),
      response_headers: (
        $respheaders | split("\n")
        | map(select(index(": ")))
        | map(. as $line | ($line | index(": ")) as $i | [$line[:$i], $line[$i+2:]])
      ),
      response_body: $respbody
    }' >> "$_EVENTS"
}

# --- reading the last response ----------------------------------------------

# jqv FILTER -- echo a value from the last response body ('' if absent/invalid)
jqv() { echo "$BODY" | jq -r "$1" 2>/dev/null || true; }

# --- assertions --------------------------------------------------------------

# An assertion carries the sequence number of the request it read, so the
# report can file it under that request rather than repeating the exchange.
_record() {
  local outcome="$1" desc="$2" detail="${3-}"
  jq -nc \
    --arg outcome "$outcome" \
    --arg case "$CASE_NAME" \
    --arg section "$CURRENT_SECTION" \
    --argjson seq "$_REQ_SEQ" \
    --argjson step "$_STEP_SEQ" \
    --arg keyword "$THEN_KEYWORD" \
    --arg desc "$desc" \
    --arg detail "$detail" \
    '{t: "assertion", outcome: $outcome, case: $case, section: $section,
      seq: $seq, step: $step, keyword: $keyword,
      description: $desc, detail: $detail}' >> "$_EVENTS"
}

pass() {
  _next_then
  _record pass "$1"
  _print_step "pass" "$_c_green" "$THEN_KEYWORD" "$1"
}

fail() {
  local desc="$1" detail="${2-}"
  _next_then
  _record fail "$desc" "$detail"
  _print_step "fail" "$_c_red" "$THEN_KEYWORD" "$desc"
  [[ -n "$detail" ]] && echo "$detail" | sed "s/^/${_STEP_INDENT}/"
  if [[ -n "${FAIL_FAST:-}" ]]; then
    echo "  ${_c_red}stopping (-x)${_c_off}"
    exit 1
  fi
  return 0
}

_context() {
  printf 'request:  %s\n' "$LAST_REQ"
  [[ -n "$LAST_SENT" ]] && printf 'sent:     %s\n' "$(echo "$LAST_SENT" | head -c 300)"
  printf 'response: %s %s\n' "$STATUS" "$(echo "$BODY" | head -c 400)"
}

assert_status() {
  local expected="$1" desc="$2"
  if [[ "$STATUS" == "$expected" ]]; then
    pass "$desc"
  else
    fail "$desc" "$(printf 'expected HTTP %s, got %s\n%s' "$expected" "$STATUS" "$(_context)")"
  fi
}

# assert_status_in "200 201" "desc"
assert_status_in() {
  local expected="$1" desc="$2"
  if [[ " $expected " == *" $STATUS "* ]]; then
    pass "$desc"
  else
    fail "$desc" "$(printf 'expected one of [%s], got %s\n%s' "$expected" "$STATUS" "$(_context)")"
  fi
}

# assert_jq FILTER "desc" [jq args...] -- passes when the filter is true.
# Trailing arguments go to jq, so a value can be bound safely rather than
# spliced into the filter text:
#   assert_jq 'any(.results[]; .id == $id)' "it is listed" --arg id "$ID"
assert_jq() {
  local filter="$1" desc="$2"; shift 2
  if echo "$BODY" | jq -e "$@" "$filter" >/dev/null 2>&1; then
    pass "$desc"
  else
    fail "$desc" "$(printf 'jq filter was not true: %s\n%s' "$filter" "$(_context)")"
  fi
}

# assert_jq_eq 'filter' expected "desc"
assert_jq_eq() {
  local filter="$1" expected="$2" desc="$3"
  local actual; actual="$(jqv "$filter")"
  if [[ "$actual" == "$expected" ]]; then
    pass "$desc"
  else
    fail "$desc" "$(printf '%s: expected %q, got %q\n%s' "$filter" "$expected" "$actual" "$(_context)")"
  fi
}

# The API's single error envelope: {"error": {"code", "message", "details"}}
assert_error_code() {
  local expected="$1" desc="$2"
  assert_jq_eq '.error.code' "$expected" "$desc"
}

# assert_eq actual expected "desc" -- for comparisons not rooted in a response
assert_eq() {
  local actual="$1" expected="$2" desc="$3"
  if [[ "$actual" == "$expected" ]]; then
    pass "$desc"
  else
    fail "$desc" "$(printf 'expected %q, got %q' "$expected" "$actual")"
  fi
}

# --- fixture failures are fatal to the case file, not recorded as test failures

# A Given that cannot be built has not failed an expectation, it has stopped
# the scenario, so it is reported against the Given keyword rather than as a
# Then nobody wrote.
die() {
  THEN_KEYWORD="Given"
  _print_step "fail" "$_c_red" "Given" "$1" >&2
  _context | sed "s/^/${_STEP_INDENT}/" >&2
  _record fail "setup: $1" "fixture creation failed"
  exit 1
}

section() {
  CURRENT_SECTION="$1"
  _HEADING_SHOWN=1
  _print_heading "Scenario: $1"
}

# Percent-encode a value for use in a query string. Generated names contain
# spaces, which curl would otherwise send raw.
urlencode() {
  local s="$1" out="" i c
  for (( i = 0; i < ${#s}; i++ )); do
    c="${s:i:1}"
    case "$c" in
      [a-zA-Z0-9.~_-]) out+="$c" ;;
      *) out+="$(printf '%%%02X' "'$c")" ;;
    esac
  done
  echo "$out"
}
