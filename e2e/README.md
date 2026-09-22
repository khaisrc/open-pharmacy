# e2e

A curl-driven end-to-end suite. It talks to a running server over HTTP, logs in with a real password, and imports nothing from `pharmacy/` — which is what separates it from `pharmacy/tests/`, where the same endpoints are driven in process through DRF's `APIClient` with authentication forced, against a database pytest builds and throws away. This suite exercises the API the way a client would, wire format, auth, rate limits and leftover rows included.

`run.sh` drives it, `lib/` holds the request and assertion primitives, and `bin/report.py` renders the run.

Results read as Gherkin. A case file is a **Feature**, each `section` in it a **Scenario**, the fixtures it builds are **Given** steps, every request is a **When**, and the assertions that read the response are the **Then** and **And**s beneath it:

```
Feature: Patients

  Background:
       Given  an address exists

  Scenario: create
       When   POST /patients/
  pass Then   the patient is created
  pass And    it has a UUID id
  fail And    phone_number round-trips
```

Outcomes sit in a gutter to the left of the keywords, so failures stay easy to find while the steps still read as steps.

Every run writes `report.html` into `e2e/` — one Feature per case file, every exchange kept behind a disclosure — and a `junit.xml` beside it for CI. Both are laid out the same way; no one of the three re-derives the structure, because every event in the log names the step it belongs to.

## Running it

The server has to be up, which locally means the Compose stack:

```bash
docker compose up     # in one terminal
./e2e/run.sh          # in another
```

`run.sh` checks `/api/v1/health/` and an authenticated `GET /api/v1/patients/` before it runs anything, and says to start Compose if either fails.

A bare argument names the case files to run, so a single one takes no flag:

```bash
./e2e/run.sh 65_fills
```

Options:

| Flag | Effect |
| --- | --- |
| `PATTERN` | as `-f`, given as an argument rather than a flag |
| `-f PATTERN` | run only case files whose name contains `PATTERN` |
| `-p N` | run up to N case files concurrently (default 8; `1` is sequential) |
| `-v` | print every request and response |
| `-x` | stop at the first failing assertion (runs sequentially) |
| `-r PATH` | write the HTML report here (default `e2e/report.html`) |
| `-j PATH` | write JUnit XML here (default `e2e/junit.xml`) |
| `-R` | skip both reports |
| `-l` | list the case files |
| `-h` | usage |

Configuration comes from the environment:

| Variable | Default |
| --- | --- |
| `BASE_URL` | `http://localhost:8000` |
| `API_USER` | `admin` |
| `API_PASS` | `admin` |
| `JOBS` | same as `-p` (`8`) |

```bash
BASE_URL=https://staging.example.com API_PASS=hunter2 ./e2e/run.sh
```

`run.sh` exits non-zero if anything failed, so it drops into CI unchanged.


### Running case files concurrently

Case files share no data. Each one builds its own fixtures from values that lib/gen.sh makes unique per run, so they run concurrently by default -- eight at a time, which takes this suite from about 72s to about 12s:

```bash
./run.sh            # eight at a time
./run.sh -p 1       # one at a time, streaming to the terminal as it goes
JOBS=1 ./run.sh     # same, for CI that would rather watch the output arrive
```

Twelve seconds is the floor, not a coincidence of the worker count: the longest case file takes about that long run entirely on its own, so raising `-p` past the number of case files changes nothing.

The limit worth knowing is not the worker count but the API's rate limit. `open-pharmacy` throttles an authenticated user to 1000 requests a minute and one full run makes about 250, so a single run is never close -- but five runs inside the same minute are, and the tail of the fifth comes back 429. At one worker a run takes long enough that this cannot happen; at eight it can, if you are iterating in a tight loop. Space the runs out, narrow with `-f`, or raise `THROTTLE_USER` for local work.

Assertions are written so that concurrency cannot change their outcome -- anything counting rows filters on this run's own fixtures rather than on the whole collection. A new assertion must do the same, because the database also holds seed data and every previous run's rows. Each case writes its own event log and `run.sh` concatenates them in suite order afterwards, so the report and the JUnit XML come out identical either way.

Concurrent cases cannot stream to the terminal, so their output is buffered and printed when the run ends rather than as it happens. `-p 1` restores the live stream.

`-x` stops at the first failing assertion, which a concurrent run cannot do: case files already in flight would run to completion regardless. Passing `-x` alone therefore runs the suite sequentially; passing it together with an explicit `-p` greater than one is refused as a contradiction.

### The progress display

Attached to a terminal, a run draws one bar per case file in place of the step lines, and afterwards prints the full transcript of whichever cases failed -- the failure summary names the feature, the scenario and the step, but not the request and response that explain it.

A bar is drawn against the number of assertions that case made last time, kept in `e2e/.assert-counts` (untracked). The number is not knowable up front, since cases loop over collections and `-f` changes the set, so the first run of a case shows a plain count and every run after it has a real denominator.

A bar is green while a case is passing and turns red the moment one of its assertions fails, rather than looking healthy until the case ends. The glyphs are box-drawing characters; outside a UTF-8 locale (`locale charmap`) they fall back to ASCII.

Redirect stdout, as CI does, and the per-assertion lines come back with no escape sequences in them. `-v` and `NO_COLOR` also turn the display off, as does a terminal too short to hold the block without scrolling.

## Why it can be run repeatedly

The API soft-deletes: `DELETE` hides a row but never removes it. That shapes three rules the suite follows.

**Every unique value is generated per run.** `medications.ndc` and `prescribers.npi` are unique among *live* rows. The suite does not clean up after itself, so a hard-coded NDC would pass the first time and 409 forever after. `lib/gen.sh` mints a fresh NDC, NPI, NCPDP ID and state-board registration number on every call — including a valid NPI check digit, since a random ten-digit string is rejected — alongside the phone numbers and emails every record needs. All of them draw from `/dev/urandom` rather than `$RANDOM`, because this is bash 3.2 (the version macOS ships) where `$RANDOM` inside `$(...)` inherits the caller's seed and repeats.

**No assertion depends on an absolute count.** The database carries seed data and every earlier run's rows. Tests assert that a record they created appears in a listing, or that a filter scoped to this run's fixtures returns exactly what this run put there — never that a collection holds *N* rows.

**Nothing is reused across runs.** A prescription's status only moves forward: `/fill/` and `/cancel/` 409 once it is completed, cancelled or expired. Every lifecycle scenario creates its own prescription rather than reaching for one that already exists.

The cost of all this is rows left behind — a few dozen addresses, medications, patients, prescribers and prescriptions per run, all named with a `qa<pid>` tag so they are recognizable. To start clean:

```bash
docker compose down -v && docker compose up
```

## The report

`report.html` opens in a browser with no server behind it. It is the same feature file the terminal printed:

- **one collapsible Feature per case file**, showing its pass and fail tallies. A Feature that failed is expanded on load; the rest start closed, so a green run is a single screen.
- **within it, one Scenario per `section "..."`** — plus a `Background` for whatever the case file set up before its first section.
- **under each Scenario, its Given and When steps in the order they ran**, each labeled with its keyword in the same column as the `Then`s below it. A step line carries its English description and a duration -- the method, path and status of its request are on the disclosures beneath it, and a Given that took several requests labels each of them, status included, underneath.
- **under each When, the Then and Ands that read its response.** A failure shows its expected-versus-actual inline, in full.
- **two disclosures per request, stacked: Request then Response**, holding the URL, the query parameters, every header, and the body. JSON bodies are indented and syntax-highlighted; long ones are clipped to the first 20,000 characters (a clipped body is shown plain, because the cut can land mid-token).

Across the top: totals, a text filter over step text, paths and outcomes, a failures-only toggle, an expand-all button and a light/dark switch that follows the OS by default.

The page loads Tailwind and daisyUI for its components and highlight.js for JSON from a CDN, and defines no components of its own, which keeps `bin/report.py` to markup with no stylesheet to maintain. highlight.js arrives as its ES-module core plus the single JSON grammar — 21 KB rather than the 128 KB of the bundle carrying every common language — and a body is highlighted the first time its disclosure opens, so the page does not parse a megabyte of JSON that nobody has asked to see.

The trade is that **the report needs a network connection to render**; opened offline it is readable but unstyled. `Authorization` headers are replaced with `Basic <user> ‹redacted›`, so the report is safe to attach to a ticket.

`junit.xml` carries the same results, one `<testsuite>` per case file and one `<testcase>` per Then, for CI systems and generic viewers (`junit2html`, `xunit-viewer`, and the like). A test's `classname` is its feature and scenario and its `name` is the whole step -- `When POST /patients/ / Then the patient is created` -- so a CI failure list reads on its own, without the report open beside it.

Both are written by `bin/report.py`, which `run.sh` shells out to as `python3` on the host -- not inside Compose, since nothing about rendering an event log needs the project's environment. It is linted with the repository and so assumes the same Python 3.12 the project pins. On an older interpreter the run itself still passes or fails normally; only the report is skipped, with a line saying so.

## What it covers

| File | Covers |
| --- | --- |
| `cases/00_health.sh` | `/health/`, with and without credentials |
| `cases/05_auth.sh` | every collection rejects anonymous access; wrong passwords fail |
| `cases/10_addresses.sh` | CRUD, state and postal validation, soft delete, delete blocked while referenced |
| `cases/20_medications.sh` | CRUD, all four NDC segment formats, ambiguous NDC, duplicate-NDC conflict |
| `cases/30_patients.sh` | CRUD, E.164 and date-of-birth validation, nested address, `/patients/{id}/prescriptions/` |
| `cases/40_prescribers.sh` | CRUD, NPI check digit, duplicate-NPI conflict, `/prescribers/{id}/prescriptions/` |
| `cases/50_prescriptions.sh` | CRUD, nested summaries, read-only fields, which statuses a client may set |
| `cases/60_lifecycle.sh` | what a prescription's status does: completion, `/cancel/`, refusals, lazy expiry |
| `cases/65_fills.sh` | the dispensing workflow: a technician starts a fill, a pharmacist verifies and completes it, whole and partial, and the totals each one leaves behind |
| `cases/70_query.sh` | pagination, ordering, filters, search |
| `cases/80_pharmacies.sh` | CRUD, `location_count`, delete blocked while stores or staff reference it |
| `cases/85_pharmacy_locations.sh` | locations as a sub-resource of their pharmacy: CRUD, NCPDP and store-number uniqueness, the pharmacist-in-charge, `{id}/prescriptions/` |
| `cases/90_staff.sh` | CRUD, the four roles, state registration and technician certification, NPI, delete blocked while a location names them |

`bin/report.py` renders the run; `lib/http.sh` writes the event log it reads.

## Adding a case

Case files are run in filename order, each in its own bash process. A new one starts like this:

```bash
#!/usr/bin/env bash
set -uo pipefail
source "$E2E_DIR/lib/http.sh"
source "$E2E_DIR/lib/gen.sh"
source "$E2E_DIR/lib/fixtures.sh"

section "what this scenario is about"
when "an address is created"
req POST /addresses/ '{"line1":"1 A St","city":"Portland","state":"OR","postal_code":"97205"}'
assert_status 201 "the address is created"
assert_jq_eq '.state' 'OR' "state round-trips"
```

`when "..."` names the request that follows and is what the step line shows, so write it as what the caller is doing rather than as the method and path -- those are a disclosure away in the report. A request sent without one still gets a step, named after the wire; it reads as the odd one out among its neighbors, which is how a forgotten `when` makes itself visible.

Write each assertion as the rest of its Then: the keyword and the When above it are already on the line, so `"the address is created"` reads as a step where `"POST /addresses/ creates an address"` would repeat both.

`req METHOD PATH [JSON]` sends an authenticated request and leaves the result in `$STATUS` and `$BODY`; `req_anon` and `req_as USER PASS` are the unauthenticated and explicit-credential versions. `req_staff STAFF_ID METHOD PATH [JSON]` adds the `X-Staff-Id` header, which is how a write names the person performing it -- the fill workflow refuses without one, and the credentials say which client is calling, not who is at the counter. The assertions are `assert_status`, `assert_status_in`, `assert_jq`, `assert_jq_eq`, `assert_eq` and `assert_error_code`. `assert_jq` passes trailing arguments to `jq`, so a value binds safely instead of being spliced into the filter:

```bash
assert_jq 'any(.results[]; .id == $id)' "it is listed" --arg id "$ID"
```

`lib/fixtures.sh` builds records — `make_address`, `make_medication`, `make_patient`, `make_prescriber`, `make_prescription`, `make_pharmacy`, `make_location` and `make_staff`, plus `make_cast`, `make_store` and `make_dispensing_team` for a whole set at once. `make_dispensed_fill` is the one that is a workflow rather than a record: it starts a fill, verifies it and completes it, which is what moves a prescription's totals. Each sets `$NEW_ID` rather than echoing, so that a failure during setup can stop the file; calling one as `$(make_patient)` would run it in a subshell where that cannot work. A failed fixture is reported as a setup failure, not as a test failure.

Each is also a Given step, announcing what it leaves behind rather than the requests it takes to get there — `make_cast` prints one `Given` line, not four. Setup a case does inline, outside a fixture builder, says so the same way:

```bash
given "it is taken off hold" req PATCH "/prescriptions/${HELD}/" '{"status":"active"}'
```

`given TEXT COMMAND [ARGS...]` runs the command in the current shell, so `$NEW_ID` and a failing `die` still reach the case file, and files every request it makes under that one step.

`section "..."` opens a Scenario, both in the terminal and in the report, so a new group of steps needs no other bookkeeping. Steps taken before the first `section` are the file's `Background`, which is why a case file that asserts anything opens a section first.

Every request is logged for the report whether or not anything asserts against it, so nothing extra is needed to make a new case show up there.
