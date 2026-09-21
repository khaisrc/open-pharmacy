# Pharmacy Ops — QA / Verification Protocol

Companion to `plan.md`. Read this **after** you believe the bootstrap plan is implemented, and work through it in order.

Audience: an AI assistant verifying its own work, or a second assistant verifying someone else's.

## 0. Ground rules

1. **Nothing counts unless a command was actually run and its real output observed.** Do not report a check as passing because the code "looks right." Every ✅ in your final report must be traceable to a command you ran in this session and its output.
2. **Everything goes through `uv run`.** A bare `python`, `pip` or `pytest` is a plan violation (plan §3) — it runs against whatever interpreter is first on `PATH` rather than the locked environment, so its result is not evidence about the artifact.
3. **Verify against the plan, not against your memory of writing the code.** Keep `plan.md` open. The section references below are the contract.
4. **A failed check is a finding, not a reason to quietly patch and move on.** Record it, fix it, then re-run the *whole* section that contained it — fixes routinely break neighbours.
5. **Do not edit tests to make them pass.** If a test is wrong, say so explicitly in the report and explain why, with the plan section that justifies the change.

## 1. The headline check: clean clone, two commands

This is the plan's one hard acceptance criterion (plan §3, *First run*). It runs twice (plan §9): at **Checkpoint 1** on the placeholder app, where only the `/health/`, `git status` and second-run conditions below apply (there is no auth, no seed data and no Swagger yet), and at **Checkpoint 2** on the finished service, where every condition applies. It is also the check most likely to be skipped, because your working directory already has a warm database, a built `.venv`, and an `.env.local` that was fixed by hand three steps ago. **A passing local `./scripts/dev.sh` proves nothing about a fresh clone.**

```bash
# Clone the committed HEAD into a genuinely empty directory.
TMP=$(mktemp -d)
git clone <repo> "$TMP/pharmacy-ops"
cd "$TMP/pharmacy-ops"

# This command, and only this command.
./scripts/dev.sh
```

In a second shell:

```bash
curl -i -u admin:admin http://localhost:8000/api/v1/prescriptions/
curl -i http://localhost:8000/api/v1/health/
curl -sI http://localhost:8000/api/v1/docs/
```

Pass conditions:

- No third command was needed. No `uv sync` by hand, no `cp .env.example .env`, no `migrate`, no `createsuperuser`, no environment variable exported by hand, and no database to install or start — the only prerequisites are Python 3.12 and `uv` (plan §1).
- `/health/` is 200 **without** credentials. *(Checkpoint 1 and 2.)*
- The prescriptions call returns **200** with a paginated envelope, and — because `.env.local` ships `SEED_DEMO_DATA=true` — `count` is **greater than zero**. An empty list means seeding silently no-opped; that is a failure, not a pass. *(Checkpoint 2 only.)*
- `/docs/` is 200 and the HTML loads its Swagger assets from **this server**, not a CDN. Fetch one asset URL from the page source: it must be a same-origin `/static/drf_spectacular_sidecar/...` path returning 200 (plan §2: sidecar + whitenoise with `runserver_nostatic`). A `jsdelivr`/`unpkg` URL means the sidecar isn't configured; a 404 means whitenoise isn't. *(Checkpoint 2 only.)*
- Open `/docs/` in a browser and confirm the operations are **grouped by resource** — `Prescriptions`, `Patients`, `Prescribers`, `Medications`, `Pharmacies`, `Pharmacy Locations`, `Addresses`, `System` — in that order, collapsed, with no flat list of untagged operations and no stray lowercase group such as `pharmacy-locations` or `api` (plan §6, *Swagger grouping*). A stray group means a viewset is missing its explicit tag and is being inferred from the path. *(Checkpoint 2 only.)*

Then prove the clone is genuinely clean:

```bash
git status --porcelain   # must be empty — dev.sh must not generate tracked files
```

`.venv/`, `db.sqlite3` and its `-wal`/`-shm` sidecars must be ignored, not tracked. A committed database file is a finding: it makes "clean clone" meaningless and will quietly ship whatever data was in it.

Finally, stop the server and **run `./scripts/dev.sh` a second time against the same `db.sqlite3`**. The script runs `migrate` and `seed_local` on every invocation; if the second run crashes or errors, the idempotency claim in plan §3b is false (this is exactly the `createsuperuser --noinput` trap the plan calls out).

```bash
# Ctrl-C the first run, then:
./scripts/dev.sh        # must reach a healthy API again, no errors in the log
```

Clean up when done: `rm -rf "$TMP"` — which takes the SQLite file with it.

## 2. Static and structural checks

None of these needs a running server, and all of them are fast — run them first when re-verifying after a fix.

```bash
uv run ruff check .
uv run ruff format --check .
uv run python manage.py spectacular --file /dev/null --fail-on-warn
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py check --deploy --settings=config.settings.prod
```

- `makemigrations --check` is the one people forget: it catches models edited after their migration was written, which produces a service that works locally and fails on a fresh database. It needs no database connection, so there is no excuse for skipping it.
- `--fail-on-warn` on the schema is required at Checkpoint 2 (plan §9); spectacular warnings almost always mean a serializer the docs can't describe. It also needs no database.
- `check --deploy` against prod settings will need the required env vars present; supply throwaway ones inline rather than weakening the settings module.

Confirm the interpreter is the pinned one, and that it is the locked environment rather than a system Python:

```bash
uv run python -V          # must report 3.12.x (plan §1)
cat .python-version       # must read 3.12
```

A mismatch between `.python-version`, `requires-python` in `pyproject.toml`, and what `uv run python -V` actually reports is the drift this pin exists to prevent.

Structural comparison against plan §4:

```bash
find . -path ./.git -prune -o -path ./.venv -prune -o -type f -print | sort
```

Confirm the tree matches the layout in plan §4 — a single `pharmacy` app with `models/`, `serializers/` and `views/` as packages (one module per resource, no `models.py`), split settings (`base`/`local`/`uat`/`prod`), `scripts/dev.sh`, committed `.env.local`, `.python-version` and `uv.lock`, and no `admin.py` or `/admin/` route (plan §4). Confirm `scripts/dev.sh` is executable (`test -x scripts/dev.sh`); a non-executable script breaks the two-command quickstart in §1 and is easy to lose across a copy. Confirm the script uses `uv sync --locked`, not `--frozen` (plan §3) — `--frozen` skips the lockfile freshness check the plan relies on. Then confirm `.gitignore` carries the `!.env.local` negation (plan §3a) — `git check-ignore -v .env.local` and `git check-ignore -v .python-version` must both report *not ignored* — and that it does ignore `.venv/`, `db.sqlite3`, `db.sqlite3-wal` and `db.sqlite3-shm`.

Confirm there is no second settings module, no test-only config file, and no vendor branching anywhere in `config/` or `pharmacy/` (plan §1). The backend is selected by `DATABASE_URL` alone; an `if sqlite:` / `if postgres:` branch in code is the drift that would make the Postgres run in §3 meaningless.

Confirm `.env.local` is read in exactly one place, `config/settings/local.py`, with `overwrite=False` (plan §3a, §8): `grep -rn "env.local" config/` must hit only that file. A read in `base.py` means uat and prod silently inherit local fallbacks, which defeats every `require_settings` check in §5.

Finally, confirm the README's quickstart is the one in plan §3 and that every command it lists can be copy-pasted and run as written. A README that documents a setup path the repo no longer has is stale documentation, and someone will follow it.

## 3. Test suite

```bash
uv run pytest -q
```

(No `DATABASE_URL` override is needed: Django builds SQLite test databases in memory regardless of `NAME`, plan §8.)

Then check the suite is real rather than decorative:

```bash
uv run pytest --collect-only -q | tail -5
```

Every bullet in plan §8 must have a corresponding test that you can point to by file and name. Walk the list explicitly and map each one:

| §8 requirement | Where |
|---|---|
| Check constraints: `fills_used <= fills_authorized`, `expires_at > written_at` → `IntegrityError` | |
| Status lifecycle: each allowed transition; disallowed `PATCH` target → 400; `fill` on non-`active` → 409; last fill → `completed`; fill past `expires_at` → `expired` + 409; `cancel` on each terminal status → 409 | |
| Future `dob` → 400; missing or non-E.164 `phone_number` → 400 | |
| NPI check digit accept **and** reject; duplicate NPI → 409 | |
| NDC normalization across 4-4-2 / 5-3-2 / 5-4-1 | |
| Duplicate NDC → 409 | |
| Postal-code and USPS state-code formats; unknown `practitioner_type` → 400 | |
| Referenced address/patient/prescriber/medication delete → 409, never 500 | |
| Soft-deleted row 404s on detail, absent from list, present in `all_objects` | |
| Re-create a medication with a previously-deleted NDC → succeeds | |
| `DELETE` and `cancel` produce different states | |
| Constant query count on list regardless of page size | |
| Happy path, 400/404/409, pagination and filtering for every §6 endpoint | |
| 401 on every protected endpoint with missing/bad credentials | |
| health/schema/docs reachable unauthenticated | |
| `/api/v1/schema/` generates warning-free and lists every §6 path | |
| `uat`/`prod` raise `ImproperlyConfigured` on a missing secret | |
| prod guards reject local `SECRET_KEY`, `DEBUG=True`, wildcard `ALLOWED_HOSTS` | |
| `seed_local` idempotent, and refuses when `DJANGO_ENV != local` | |

An empty cell is a finding.

**Confirm which database you actually tested against, and say so.** Do not assert it — print it:

```bash
uv run python manage.py shell -c \
  "from django.db import connection; print(connection.vendor, connection.settings_dict['NAME'])"
```

This must print `sqlite` (plan §8). pytest-django derives its test database from the same settings, so the vendor is what matters here, not the name.

`sqlite` is the correct and expected result — but it is **not** coverage of these four items, which stay open and must be listed as such in the §7 report:

- the migration path applied against a real Postgres 18,
- collation and ordering behaviour behind `search=` and `ordering=`,
- constraint timing, and therefore exactly when `ProtectedError` and `IntegrityError` surface,
- any assertion that depends on database error text — which no test should make anyway.

If a Postgres 18 is available, run the suite against it as well and report both. Nothing in the repo changes; it is one variable (plan §8):

```bash
DATABASE_URL=postgres://user:pass@host:5432/pharmacy_ops uv run pytest -q
```

That run is authoritative where the two disagree, and a disagreement is the most valuable finding this protocol can produce. It is not required for the bootstrap to be called done (plan §9).

Two sanity probes on test quality:

- **Mutation probe.** Temporarily break one real invariant in the source (e.g. drop the `fills_used <= fills_authorized` constraint check, or return 200 instead of 409 from a blocked delete), re-run the suite, and confirm a test *fails*. Revert immediately. A suite that stays green is not testing what it claims.
- **Count probe.** `pytest` passing with 12 tests against a 5-resource CRUD API with the §8 list above is not plausible. Say so if the number is low.

## 4. Behavioral verification against a live API

Static review will not catch these. Drive the running service with `curl` and check status codes, not just that a response arrived. Use `-i` throughout and read the status line.

**Auth (plan §7)**

```bash
curl -i http://localhost:8000/api/v1/prescriptions/                   # 401
curl -i -u admin:wrong http://localhost:8000/api/v1/prescriptions/    # 401
curl -i -u admin:admin http://localhost:8000/api/v1/prescriptions/    # 200
curl -i http://localhost:8000/api/v1/schema/                          # 200, no creds
```

**Soft delete vs cancel (plan §5, §6)** — the plan is emphatic that these are different operations, and conflating them is the specific bug it was revised to remove.

1. Create a prescription → 201, note the id.
2. `POST /prescriptions/{id}/cancel/` → 200, then `GET` it → **200** with `status: "cancelled"`, and it is **still in the list**.
3. Create a second prescription, `DELETE` it → 204, then `GET` it → **404**, and it is **absent from the list**.
4. Confirm the deleted row still exists in the database (soft, not hard):
   `uv run python manage.py shell -c "from pharmacy.models import Prescription; print(Prescription.all_objects.count(), Prescription.objects.count())"` — the two numbers must differ.

**The partial unique index (plan §5)** — the classic soft-delete bug:

```
POST /medications/  {ndc: "0002-8215-01", ...}   → 201
DELETE /medications/{id}/                        → 204
POST /medications/  {same ndc}                   → 201, NOT 409/500
```

If the third call fails with a duplicate-key error, the unique constraint was written unconditionally and §5's `condition=Q(deleted__isnull=True)` was not implemented.

**NDC normalization (plan §5)** — POST the *same* product in 4-4-2, 5-3-2 and 5-4-1 form. The first is 201; the others must be **409**, not three separate rows.

**NPI check digit (plan §5)** — `1234567890` must be rejected with **400**. A valid NPI (Luhn over the number prefixed with `80840`) must be accepted. If a random 10-digit string is accepted, only the length was checked.

**Referential protection (plan §5, §6)** — soft-delete a patient who has live prescriptions. Must be **409** with the referencing resource named in the error body, and must **not** be 500, and must **not** succeed and orphan the prescriptions. Repeat for prescriber, medication, a pharmacy location referenced by live prescriptions, a `Pharmacy` that still has live locations, and an address referenced by a patient.

**`fill` and the status lifecycle (plan §5, §6)** — on a `draft` prescription, `fill` → **409**. `PATCH status=active` → 200, then fill until `fills_used == fills_authorized`: the last fill must leave `status: "completed"`, and one more fill → **409**. `PATCH status=cancelled` on any prescription → **400** (only `cancel/` may do that). A prescription cannot be created with `expires_at` already in the past (the `expires_at > written_at` constraint), so activate one, move its `expires_at` behind `now` from a `manage.py shell`, and fill → **409** with `status` now `"expired"`. Then `cancel/` that expired one → **409**.

**Error envelope (plan §6)** — every non-2xx body above must have the shape `{"error": {"code", "message", "details"}}`. A raw DRF `{"detail": "..."}` anywhere means the custom exception handler isn't wired for that path.

**Pagination and N+1 (plan §6)** — `?page_size=1` and `?page_size=1000` (must clamp to 100). Then confirm the query-count test exists and passes; if it doesn't, run a list request with `DEBUG` query logging and count — 3N queries for N prescriptions means `select_related` is missing.

## 5. Security and configuration

- **The committed `.env.local` must contain only throwaway values** (plan §3a). Read it. A real-looking `SECRET_KEY`, or a password reused from anywhere else, is a serious finding. Confirm the local-only warning comment is present at the top.
- **Both seeding guards exist** (plan §3b): `scripts/dev.sh` checks `DJANGO_ENV=local` *and* the command re-checks independently. Exercise the command-level guard — it is the one that fails safe:
  `DJANGO_ENV=prod uv run python manage.py seed_local` → must exit non-zero.
- **Prod refuses to boot when misconfigured** (plan §3a). Exercise all three guards, each expecting a startup failure: the committed local `SECRET_KEY`; `DEBUG=True`; `ALLOWED_HOSTS=*`. Also drop one required secret and confirm the error names **every** missing variable at once, per the `require_settings` design.
- **`.env.example` documents the full variable contract with no values** (plan §3a), and covers every variable `uat.py`/`prod.py` actually read. Diff the two by hand — a variable read in settings but absent from the example is a deployment landmine.
- **`scripts/dev.sh` stops on a failed `migrate`** (plan §3, `set -euo pipefail`). Point it at an unwritable path — `DATABASE_URL=sqlite:////nonexistent/db.sqlite3 ./scripts/dev.sh` — and confirm it exits non-zero and never starts the server (nothing listening on the port afterwards). Django's own error output is acceptable here; the requirement is that the script does not carry on.
- **No secrets in the log output** (plan §7). Start the service, make one authenticated request, and read the structured logs: `SECRET_KEY`, the `Authorization` header value and the `DATABASE_URL` must not appear, including in the startup lines.
- **`.env.example` documents `DATABASE_URL` as a Postgres URL for uat and prod** (plan §1), and does **not** list the local-only variables `WEB_PORT`, `SEED_DEMO_DATA` or `LOCAL_SUPERUSER_*` (plan §3a). A `sqlite://` URL reaching a deployed tier would hand it a throwaway database that vanishes on every redeploy, with no error anywhere.
- **Prod does not inherit local fallbacks.** With `DJANGO_ENV=prod` and every required variable set *except* `DATABASE_URL`, the process must fail with `ImproperlyConfigured` naming `DATABASE_URL`. If it boots on SQLite instead, `.env.local` is being read outside `local.py` (plan §3a).
- **The auth-plan constraints are respected.** `plans/auth/plan.md` §7 lists what this build must avoid doing so the JWT migration stays cheap. Read it and confirm nothing in the bootstrap implementation violates it — in particular, that authorization decisions aren't scattered into places that will need unpicking.

## 6. Plan-vs-implementation drift

For each of plan §2, §5, §6 and §7, diff the plan's tables against the code:

- Every field in the plan's §5 tables exists with the stated type, nullability, default and constraint. Spot-check the migration SQL rather than trusting the model file: `uv run python manage.py sqlmigrate <app> 0001`. Note what this does and does not prove: run locally it emits **SQLite** DDL, so it verifies the migration is coherent, not that the Postgres schema is right. Re-run it with a Postgres `DATABASE_URL` to see the DDL that would actually ship to uat or prod.
- Every row of the §6 endpoint tables is routed and behaves as described. `GET /api/v1/schema/` is the fastest cross-check — every documented path should appear, and no undocumented ones should.
- Pinned versions in `pyproject.toml`/`uv.lock` match plan §2. If anything was **downgraded** to resolve a conflict, that is a plan deviation the user explicitly asked to be told about (plan §2, §10) — report it, do not absorb it silently. Note that **Python 3.12 is not such a deviation**: it is the deliberate pin from plan §1. A Python version *other* than 3.12 in `pyproject.toml` or `.python-version` is the deviation to look for.

**Report any deviation, even a defensible one.** The plan's §10 is explicit that a dependency conflict should be brought back to the user rather than worked around. The same applies to any design change made mid-implementation.

## 7. Reporting

Written after Checkpoint 2, this is the document that calls the bootstrap done.

1. **Verdict** — does the acceptance criterion hold (clean clone, two commands, authenticated 200 with a non-empty `count`)? Yes or no, first line.
2. **What ran and what it said** — actual output for `ruff check`, `ruff format --check`, `manage.py check`, `makemigrations --check`, `spectacular --fail-on-warn`, and `pytest`, plus the `connection.vendor` line from §3 proving which database the suite used.
3. **Test inventory** — the §3 table with every cell filled, and the mutation-probe result.
4. **Findings** — anything that failed, was fixed, or deviates. Include what you fixed; "fixed during QA" is information the user needs.
5. **Still not verified** — anything on this list you could not run, and why. An unverifiable check is not a passing check, and "expected to pass" is not a result. **The four SQLite gaps from §3 belong here every time**, unless a Postgres run was actually performed and its output included. A report that omits them is claiming coverage the suite does not have.

Do not report the work as complete while any §1, §3 or §4 check is red.
