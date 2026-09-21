# api-tests

A curl-driven test framework, shared across API test suites. `run.sh`, `lib/`
and `bin/` are the reusable part: request/assertion primitives, an HTML +
JUnit reporter, and a runner that knows nothing about any particular API.
A **suite** is a directory alongside them — `pharmacy-ops/` today — holding
its own `cases/*.sh` and a `suite.env` describing how to reach that API.
Nothing is imported from any service, so a suite exercises its API the way a
client would.

Every run writes `report.html` inside the suite directory — one section per
case file, every exchange kept behind a disclosure — and a `junit.xml` beside
it for CI.

## Running it

```bash
./run.sh
```

With one suite directory present, `run.sh` finds it on its own. With more than
one, pick it explicitly:

```bash
./run.sh -s pharmacy-ops
```

The API must be up. If it is not, `run.sh` prints that suite's `DEV_HINT`
(for `pharmacy-ops`, `cd ../pharmacy-ops && ./scripts/dev.sh`).

Options:

| Flag | Effect |
| --- | --- |
| `-s SUITE` | which suite to run (default: the only one present) |
| `-f PATTERN` | run only case files whose name contains `PATTERN` |
| `-p N` | run up to N case files concurrently (default 1) |
| `-v` | print every request and response |
| `-x` | stop at the first failing assertion (not with `-p`) |
| `-r PATH` | write the HTML report here (default `SUITE/report.html`) |
| `-j PATH` | write JUnit XML here (default `SUITE/junit.xml`) |
| `-R` | skip both reports |
| `-l` | list the case files |
| `-h` | usage |

Configuration comes from the environment:

| Variable | Default |
| --- | --- |
| `BASE_URL` | `http://localhost:8000` |
| `API_USER` | `admin` |
| `API_PASS` | `admin` |
| `SUITE_NAME` | same as `-s` |

```bash
BASE_URL=https://staging.example.com API_PASS=hunter2 ./run.sh -s pharmacy-ops
```

`run.sh` exits non-zero if anything failed, so it drops into CI unchanged.


### Running case files concurrently

Case files share no data. Each one builds its own fixtures from values that
lib/gen.sh makes unique per run, so `-p N` can run N of them at once:

```bash
./run.sh -p 8
```

On this suite that takes the wall clock from about 72s to about 12s, which is
roughly the floor: the longest single case file bounds it, so raising `-p`
past the number of case files buys nothing.

Assertions are written so that concurrency cannot change their outcome --
anything counting rows filters on this run's own fixtures rather than on the
whole collection. A new assertion must do the same, because the database also
holds seed data and every previous run's rows. Each case writes its own event
log and `run.sh` concatenates them in suite order afterwards, so the report
and the JUnit XML come out identical either way.

`-x` stops at the first failing assertion and so cannot be combined with `-p`:
case files already in flight would run to completion regardless.

### The progress display

Attached to a terminal, a run draws one bar per case file in place of the
per-assertion lines, and afterwards prints the full transcript of whichever
cases failed -- the one-line failure summary does not carry the request and
response that explain it.

A bar is drawn against the number of assertions that case made last time,
kept in `SUITE/.assert-counts` (untracked). The number is not knowable up
front, since cases loop over collections and `-f` changes the set, so the
first run of a case shows a plain count and every run after it has a real
denominator.

Redirect stdout, as CI does, and the per-assertion lines come back with no
escape sequences in them. `-v` and `NO_COLOR` also turn the display off, as
does a terminal too short to hold the block without scrolling.

## Adding a suite

A suite is a directory containing:

- `cases/*.sh` — the case files, run in filename order (see below)
- `suite.env` — sourced by `run.sh` before its preflight checks:

  ```bash
  HEALTH_PATH="/api/v1/health/"     # GET this must return 200 before anything runs
  AUTH_PROBE_PATH="/api/v1/patients/" # GET this with API_USER/API_PASS must return 200
  DEV_HINT="cd ../pharmacy-ops && ./scripts/dev.sh"  # printed if either check fails
  # API_ROOT="${BASE_URL}/api/v1"   # optional: overrides lib/http.sh's default
  ```

- optionally, its own `lib/gen.sh` and/or `lib/fixtures.sh` — anything
  domain-specific (an NPI check digit, a `make_patient` builder) belongs here,
  not in the framework's `lib/`. `run.sh` exports `SUITE_PATH` so a case file
  can source them: `source "$SUITE_PATH/lib/gen.sh"`. The framework's own
  `lib/gen.sh` (`$SUITE_DIR/lib/gen.sh`) stays limited to what every suite
  needs — a run tag, `_rand`, `iso_days`, `MISSING_UUID` — with nothing that
  assumes one API's shape.

`report.html` and `junit.xml` default to living inside the suite directory,
and the report's title comes from the suite name (`-s`/`SUITE_NAME`).

## The `pharmacy-ops` suite

The rest of this document — the fixture and generator conventions, the report
layout, and the case list — describes the one suite in this repo today,
`pharmacy-ops/`. A second suite would document itself the same way, in its own
`<suite>/README.md`, once one exists.

### Why it can be run repeatedly

The API soft-deletes: `DELETE` hides a row but never removes it. That shapes
three rules the suite follows.

**Every unique value is generated per run.** `medications.ndc` and
`prescribers.npi` are unique among *live* rows. The suite does not clean up
after itself, so a hard-coded NDC would pass the first time and 409 forever
after. `pharmacy-ops/lib/gen.sh` mints a fresh NDC and NPI on every call —
including a valid NPI check digit, since a random ten-digit string is
rejected — on top of the phone number and email the framework's own
`lib/gen.sh` already mints generically. Both draw from `/dev/urandom` rather
than `$RANDOM`, because this is bash 3.2 (the version macOS ships) where
`$RANDOM` inside `$(...)` inherits the caller's seed and repeats.

**No assertion depends on an absolute count.** The database carries seed data
and every earlier run's rows. Tests assert that a record they created appears
in a listing, or that a filter scoped to this run's fixtures returns exactly
what this run put there — never that a collection holds *N* rows.

**Nothing is reused across runs.** A prescription's status only moves forward:
`/fill/` and `/cancel/` 409 once it is completed, cancelled or expired. Every
lifecycle scenario creates its own prescription rather than reaching for one
that already exists.

The cost of all this is rows left behind — a few dozen addresses, medications,
patients, prescribers and prescriptions per run, all named with a `qa<pid>` tag
so they are recognisable. To start clean:

```bash
cd ../pharmacy-ops && docker compose down -v && ./scripts/dev.sh
```

### The report

`report.html` opens in a browser with no server behind it. It is organised the
way the run was written:

- **one collapsible section per case file**, showing its pass and fail tallies.
  A section that failed is expanded on load; the rest start closed, so a green
  run is a single screen.
- **within a section, the `section "..."` headings from the case file**, then
  every request in the order it was sent — method, path, status, duration.
- **under each request, the assertions that read its response.** A failure shows
  its expected-versus-actual inline, in full.
- **two disclosures per request, stacked: Request then Response**, holding the
  URL, the query parameters, every header, and the body. JSON bodies are
  indented and syntax-highlighted; long ones are clipped to the first 20,000
  characters (a clipped body is shown plain, because the cut can land
  mid-token).

Across the top: totals, a text filter over paths and assertion text, a failures-only
toggle, an expand-all button and a light/dark switch that follows the OS by default.

Requests that nothing asserts against — the fixtures a case builds before it can
test anything — are dimmed and tagged `setup`. They are kept because they often
explain the request after them.

The page loads Tailwind and daisyUI for its components and highlight.js for
JSON from a CDN, and defines no components of its own, which keeps
`bin/report.py` to markup with no stylesheet to maintain. highlight.js arrives
as its ES-module core plus the single JSON grammar — 21 KB rather than the
128 KB of the bundle carrying every common language — and a body is highlighted
the first time its disclosure opens, so the page does not parse a megabyte of
JSON that nobody has asked to see.

The trade is that **the report needs a network connection to render**; opened
offline it is readable but unstyled. `Authorization` headers are replaced with
`Basic <user> ‹redacted›`, so the report is safe to attach to a ticket.

`junit.xml` carries the same results, one `<testsuite>` per case file and one
`<testcase>` per assertion, for CI systems and generic viewers
(`junit2html`, `xunit-viewer`, and the like).

### What it covers

| File | Covers |
| --- | --- |
| `cases/00_health.sh` | `/health/`, with and without credentials |
| `cases/05_auth.sh` | every collection rejects anonymous access; wrong passwords fail |
| `cases/10_addresses.sh` | CRUD, state and postal validation, soft delete, delete blocked while referenced |
| `cases/20_medications.sh` | CRUD, all four NDC segment formats, ambiguous NDC, duplicate-NDC conflict |
| `cases/30_patients.sh` | CRUD, E.164 and date-of-birth validation, nested address, `/patients/{id}/prescriptions/` |
| `cases/40_prescribers.sh` | CRUD, NPI check digit, duplicate-NPI conflict, `/prescribers/{id}/prescriptions/` |
| `cases/50_prescriptions.sh` | CRUD, nested summaries, read-only fields, which statuses a client may set |
| `cases/60_lifecycle.sh` | `/fill/` and `/cancel/`: completion, refusals, lazy expiry |
| `cases/70_query.sh` | pagination, ordering, filters, search |

`bin/report.py` renders the run; `lib/http.sh` writes the event log it reads.

### Adding a case

Case files are run in filename order, each in its own bash process. A new one
starts like this:

```bash
#!/usr/bin/env bash
set -uo pipefail
source "$SUITE_DIR/lib/http.sh"
source "$SUITE_DIR/lib/gen.sh"
source "$SUITE_PATH/lib/gen.sh"
source "$SUITE_PATH/lib/fixtures.sh"

section "what this group is about"
req POST /addresses/ '{"line1":"1 A St","city":"Portland","state":"OR","postal_code":"97205"}'
assert_status 201 "POST /addresses/ creates an address"
assert_jq_eq '.state' 'OR' "state round-trips"
```

`req METHOD PATH [JSON]` sends an authenticated request and leaves the result in
`$STATUS` and `$BODY`; `req_anon` and `req_as USER PASS` are the unauthenticated
and explicit-credential versions. The assertions are `assert_status`,
`assert_status_in`, `assert_jq`, `assert_jq_eq`, `assert_eq` and
`assert_error_code`. `assert_jq` passes trailing arguments to `jq`, so a value
binds safely instead of being spliced into the filter:

```bash
assert_jq 'any(.results[]; .id == $id)' "it is listed" --arg id "$ID"
```

`pharmacy-ops/lib/fixtures.sh` builds records — `make_address`,
`make_medication`, `make_patient`, `make_prescriber`, `make_prescription`, and
`make_cast` for all four at once. Each sets `$NEW_ID` rather than echoing, so
that a failure during
setup can stop the file; calling one as `$(make_patient)` would run it in a
subshell where that cannot work. A failed fixture is reported as a setup
failure, not as a test failure.

`section "..."` groups what follows, both in the terminal and in the report, so
a new group of assertions needs no other bookkeeping.

Every request is logged for the report whether or not anything asserts against
it, so nothing extra is needed to make a new case show up there.
