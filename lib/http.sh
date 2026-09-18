# HTTP + assertion primitives. Sourced by every case file.
#
# Every request and every assertion is appended to $RESULT_DIR/events.jsonl.
# That file, not a shell variable, is how results reach run.sh: each case file
# runs in its own process, so a counter would not survive the return trip. It
# is also what bin/report.py renders, which is why a request records its
# headers and timing even when nothing asserts against them.
#
# Case files run one after another, never concurrently, so appending from each
# in turn cannot interleave.

: "${BASE_URL:=http://localhost:8000}"
: "${API_USER:=admin}"
: "${API_PASS:=admin}"
: "${API_ROOT:=${BASE_URL}/api/v1}"

CASE_NAME="$(basename "${BASH_SOURCE[${#BASH_SOURCE[@]} - 1]}" .sh)"
_BODY_FILE="$(mktemp -t apitestbody)"
_HDR_FILE="$(mktemp -t apitesthdr)"
trap 'rm -f "$_BODY_FILE" "$_HDR_FILE"' EXIT

_EVENTS="${RESULT_DIR}/events.jsonl"

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

_c_green=''; _c_red=''; _c_dim=''; _c_off=''
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  _c_green=$'\033[32m'; _c_red=$'\033[31m'; _c_dim=$'\033[2m'; _c_off=$'\033[0m'
fi

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
    echo "${_c_dim}  -> $method $url${_c_off}"
    [[ -n "$body" ]] && echo "${_c_dim}     body: $body${_c_off}"
    echo "${_c_dim}  <- $STATUS $(echo "$BODY" | head -c 400)${_c_off}"
  fi
}

# Authenticated request: req METHOD PATH [JSON_BODY]
req() { _curl "Basic ${API_USER}" "$1" "$2" "${3-}" -u "${API_USER}:${API_PASS}"; }

# Unauthenticated request: req_anon METHOD PATH [JSON_BODY]
req_anon() { _curl "none" "$1" "$2" "${3-}"; }

# Request with explicit credentials: req_as USER PASS METHOD PATH [JSON_BODY]
req_as() { local u="$1" p="$2"; shift 2; _curl "Basic ${u}" "$1" "$2" "${3-}" -u "${u}:${p}"; }

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
    --arg desc "$desc" \
    --arg detail "$detail" \
    '{t: "assertion", outcome: $outcome, case: $case, section: $section,
      seq: $seq, description: $desc, detail: $detail}' >> "$_EVENTS"
}

pass() {
  _record PASS "$1"
  echo "  ${_c_green}ok${_c_off}   $1"
}

fail() {
  local desc="$1" detail="${2-}"
  _record FAIL "$desc" "$detail"
  echo "  ${_c_red}FAIL${_c_off} $desc"
  [[ -n "$detail" ]] && echo "$detail" | sed 's/^/         /'
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

die() {
  echo "  ${_c_red}SETUP FAILED${_c_off} $1" >&2
  _context | sed 's/^/         /' >&2
  _record FAIL "setup: $1" "fixture creation failed"
  exit 1
}

section() {
  CURRENT_SECTION="$1"
  echo
  echo "${_c_dim}-- $1${_c_off}"
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
