# Pharmacy Ops — Implementation Plan

Status: **approved**

## 1. Scope

`pharmacy-ops` is a standalone microservice exposing a public REST API over the pharmacy domain — prescriptions, plus the patients, prescribers, medications, pharmacies and addresses they reference. Django + Django REST Framework, Swagger UI via drf-spectacular, everything under `/api/v1/`.

It runs natively as a plain Python application managed by `uv`: `uv sync` builds the environment, `uv run` executes every command, `uv.lock` pins every version. Prerequisites are **Python 3.12** and **`uv`** — that is the whole list.

**Local development and the test suite use SQLite** (stdlib, no server to install). PostgreSQL 18 is the backend for uat and prod only; nothing in the repo assumes it is reachable.

The backend is chosen entirely by `DATABASE_URL`, read by django-environ. There is no second settings module, no `if sqlite:` branch, and no test-only config file — the difference between local and deployed is one environment variable.

| Tier | `DATABASE_URL` | Ships in the repo? |
|---|---|---|
| **local** | `sqlite:///db.sqlite3` | Yes, in the committed `.env.local` |
| **uat / prod** | `postgres://…` | No — supplied by the environment |

That split is the plan's main source of risk: **the database you develop against is not the database you deploy on.** §8 states exactly what a green local suite does and does not prove.

3.12 is pinned in `requires-python = ">=3.12"` and `.python-version`. Django 6.1 requires >=3.12 and every dependency below supports it, so the pin costs nothing; moving later is a one-line change plus `uv lock --upgrade`. `scripts/dev.sh` exports `UV_PYTHON_DOWNLOADS=never` so version drift fails with `no interpreter found` instead of silently downloading a managed CPython.

## 2. Stack & decisions

Versions were looked up against PyPI on 2026-09-16, not recalled. Pinned as floors in `pyproject.toml` and locked exactly in `uv.lock`.

| Concern | Choice | Version | Why |
|---|---|---|---|
| Runtime | Python | 3.12 | The available interpreter; Django 6.1 requires >=3.12 |
| Local DB | SQLite | stdlib | No server; supports the partial unique indexes §5 depends on |
| Deployed DB | PostgreSQL | 18.6 | uat and prod only |
| Framework | Django | 6.1.1 | Latest stable |
| API | djangorestframework | 3.18.1 | Latest stable |
| OpenAPI/Swagger | drf-spectacular | 0.30.0 | True OpenAPI 3.1; `drf-yasg` is deprecated |
| Swagger UI assets | drf-spectacular-sidecar | 2026.9.1 | `/docs/` works without a CDN (`SWAGGER_UI_DIST = "SIDECAR"`) |
| DB driver | psycopg[binary] | 3.3.5 | v3 is current; the `binary` extra avoids needing `pg_config` on a machine with no Postgres |
| Filtering | django-filter | 26.1 | Latest stable |
| Soft delete | django-safedelete | 1.5.0 | `deleted` timestamp, `all_objects` manager, `undelete()` |
| Config | django-environ | 0.14.0 | 12-factor `.env` |
| CORS | django-cors-headers | 4.9.0 | Latest stable |
| Static files | whitenoise | 6.12.0 | Serves sidecar + browsable-API assets under Gunicorn; `whitenoise.runserver_nostatic` in `INSTALLED_APPS` |
| Server | gunicorn | 26.2.0 | `runserver` locally, Gunicorn for uat/prod |
| Packaging | uv | 0.12.15 | `uv.lock` committed |
| Tests | pytest / pytest-django / factory-boy | 9.1.1 / 4.14.0 / 3.3.3 | |
| Lint/format | ruff | 0.16.8 | Single tool for both |

JWT is not part of this build; `plans/auth/plan.md` covers it.

This set resolved cleanly with `uv lock` against `requires-python = ">=3.12"` on 2026-09-16, at exactly these versions, with no backtracking onto an older Django. That scratch lockfile was not kept, so **step 1 of the build order performs the resolution again** and commits the result. It is expected to pass; if it fails, stop and report the conflict rather than downgrading (§10).

## 3. Running the service

```bash
git clone <repo> && cd pharmacy-ops
./scripts/dev.sh
```

That is the entire procedure, and it is a hard requirement — **Phase 1 is not done until a clone into an empty directory reaches a live `/api/v1/health/` with exactly those two commands, and Phase 2 is not done until they reach an authenticated 200 with seeded data** (§9).

```bash
curl -u admin:admin http://localhost:8000/api/v1/prescriptions/
open http://localhost:8000/api/v1/docs/
```

### `scripts/dev.sh`

The single entry point for local development. It starts with `set -euo pipefail`, exports `UV_PYTHON_DOWNLOADS=never`, and sources `.env.local` (`set -a; . ./.env.local; set +a`, `DJANGO_ENV` defaulting to `local`) so `WEB_PORT` and `DJANGO_ENV` are the same values Django will see. Then:

1. `uv sync --locked` — builds `.venv` from the committed lockfile. `--locked` fails loudly on drift between `uv.lock` and `pyproject.toml`; `--frozen` would skip that check.
2. `uv run python manage.py migrate` — creates `db.sqlite3` on the first run, idempotent after.
3. **local only**: `uv run python manage.py seed_local` (§3b).
4. `exec uv run python manage.py runserver 0.0.0.0:${WEB_PORT:-8000}`.

No wait-for-database step: SQLite is a file `migrate` creates on demand. Because of `set -e`, a failing `migrate` stops the script before the server starts, so it never serves an API on an unmigrated database. Every step is a no-op or a refresh on re-run.

### Host port

`WEB_PORT` (set in `.env.local`, and only there — it is local-only) drives the bind, default **8000**, so a port clash is a one-line edit rather than a code change. The README says so under the quickstart, since a bind error is otherwise a confusing first experience.

`db.sqlite3` sits in the repository root, gitignored alongside its `-wal` and `-shm` sidecars.

### The only commands you'll need

```bash
./scripts/dev.sh                                   # API on :8000, Swagger at /api/v1/docs/
uv run pytest                                      # tests, against SQLite
uv run ruff check .
uv run ruff format .
uv run python manage.py makemigrations
uv run python manage.py migrate
uv run python manage.py seed_local                 # only if you wiped the DB
uv run python manage.py spectacular --fail-on-warn # schema check
```

Everything goes through `uv run`. A bare `python`, `pip` or `pytest` runs against whatever is first on `PATH` rather than the locked environment, which is exactly what the lockfile exists to eliminate. `.venv/` is gitignored.

## 3a. Environments

Three tiers — **local**, **uat**, **prod** — one codebase. `DJANGO_ENV` maps to `config.settings.<env>` and defaults to `local`.

| Tier | Secrets from | In the repo? |
|---|---|---|
| **local** | `.env.local`, read by `local.py` only, via django-environ with `overwrite=False` | **Yes, committed** |
| **uat / prod** | Process environment, populated by the secrets sidecar | No |

`.env.local` is committed deliberately so a fresh clone plus one script works with zero setup. That requires its values to be local-only — a clearly-marked dummy `SECRET_KEY`, throwaway seed credentials — and never valid anywhere else; the SQLite `DATABASE_URL` carries no credentials at all. The file is headed with a comment saying so, and `.gitignore` gains an explicit `!.env.local` negation, since the blanket `.env.*` rule would otherwise exclude it.

**Only `local.py` reads `.env.local`.** If `base.py` read it, a value the sidecar forgot would silently fall back to the committed local one — a missing prod `DATABASE_URL` would become `sqlite:///db.sqlite3` and the check below would see nothing missing.

`.env.example` documents the **full variable contract** uat and prod must satisfy — descriptions, no values, not meant to be copied. Local-only variables (`WEB_PORT`, `SEED_DEMO_DATA`, `LOCAL_SUPERUSER_*`) are not in it.

**The sidecar seam** (sidecar itself out of scope): `base.py` honours an optional `SECRETS_FILE` path, read *before* the environment is consulted, so either common sidecar pattern — shared-volume file or exported variables — is a deployment concern rather than a Django change:

```python
secrets_file = os.environ.get("SECRETS_FILE")
if secrets_file and Path(secrets_file).exists():
    environ.Env.read_env(secrets_file, overwrite=False)
```

**Fail loudly, never fall back.** In `uat.py` and `prod.py` every secret is read with no default, so a missing value raises `ImproperlyConfigured` at import. A `require_settings([...])` helper reports *every* missing name in one error instead of one restart at a time. `prod.py` additionally refuses to boot if `SECRET_KEY` matches the committed local value, if `DEBUG` is true, or if `ALLOWED_HOSTS` is empty or wildcard.

`scripts/dev.sh` is never used outside local; uat and prod run under Gunicorn and bind however the platform dictates.

## 3b. Local seeding

Because authentication is Basic (§7), an unseeded database means every request 401s. So local boot seeds a superuser via a `seed_local` management command in `pharmacy`:

- creates the superuser with `get_or_create`, so it is **idempotent**. `createsuperuser --noinput` is deliberately not used: it exits non-zero when the user exists and would break every boot after the first.
- reads `LOCAL_SUPERUSER_USERNAME` / `_PASSWORD` / `_EMAIL` from `.env.local`, defaulting to `admin` / `admin` / `admin@example.local`.
- with `SEED_DEMO_DATA=true`, inserts demo data — addresses, medications with valid NDCs, prescribers with valid NPIs, patients, pharmacies, and prescriptions wired to them — so every list endpoint, filter and nested route returns something. Seeded in FK order, idempotent via natural keys (NDC, NPI, NCPDP ID). The pharmacies deliberately cover **both shapes**: one independent with a single location and one chain with several, so `/pharmacies/{id}/locations/` and the `location_count` annotation are exercised by a plain `dev.sh` boot rather than only by tests.

**Guarded in two independent places.** `dev.sh` only invokes it when `DJANGO_ENV` is `local`, *and* the command re-checks `DJANGO_ENV` and exits with an error otherwise. One guard in a shell script is a typo away from planting a known-password superuser in a deployed environment.

Swagger's **Authorize** button works for this; `persistAuthorization` is enabled so credentials survive reloads.

## 4. Layout

**One Django app, `pharmacy`**, holding the whole domain. Closely-coupled models that always ship together do not benefit from separate app boundaries — they just multiply `__init__.py`, `apps.py` and migration directories, and force cross-app FK ordering for no gain. A single app also means a single `migrations/` directory, so the initial migration creates all seven tables in one file.

**Models are a package, not a `models.py`** — one module per model, so no file grows into a thousand-line dumping ground. `serializers/` and `views/` mirror that split for the same reason.

```
pharmacy-ops/
├── pyproject.toml            # deps, ruff, pytest config; requires-python = ">=3.12"
├── .python-version           # 3.12
├── uv.lock                   # committed
├── .env.local                # COMMITTED — local-only values
├── .env.example              # variable contract uat/prod must satisfy
├── scripts/dev.sh            # clone → migrate → seed → runserver
├── db.sqlite3                # gitignored, created on first run
├── manage.py
├── config/
│   ├── settings/
│   │   ├── base.py           # shared; reads the environment, never .env.local
│   │   ├── local.py          # DEBUG on; the only module that loads .env.local
│   │   ├── uat.py            # DEBUG off, secrets required from environment
│   │   └── prod.py           # DEBUG off, plus the anti-footgun guards
│   ├── urls.py               # mounts /api/v1/ + schema routes
│   ├── asgi.py
│   └── wsgi.py
├── pharmacy/
│   ├── apps.py
│   ├── models/
│   │   ├── __init__.py       # imports and re-exports every model
│   │   ├── base.py           # TimeStampedModel + SafeDeleteModel base
│   │   ├── address.py
│   │   ├── patient.py
│   │   ├── prescriber.py
│   │   ├── medication.py
│   │   ├── pharmacy.py           # Pharmacy — the organization
│   │   ├── pharmacy_location.py  # PharmacyLocation — a physical site
│   │   └── prescription.py
│   ├── serializers/          # same six modules, plus pharmacy_location.py
│   ├── views/                # same, plus health.py
│   ├── filters.py
│   ├── validators.py         # NPI check digit, NCPDP/NDC normalization, E.164, postal code
│   ├── exceptions.py         # Conflict + the custom DRF exception handler
│   ├── urls.py               # router; no version prefix here
│   ├── migrations/
│   ├── management/commands/seed_local.py
│   └── tests/
└── plans/
    ├── bootstrap/plan.md     # this document
    └── auth/plan.md          # deferred JWT authn + authz
```

Django resolves `app_label` from the package a model is *imported into*, so every model must be re-exported from `pharmacy/models/__init__.py` — with that, a models package behaves exactly like a `models.py` for `makemigrations`, and no `Meta.app_label` is needed.

URL versioning happens once in `config/urls.py` (`path("api/v1/", include("pharmacy.urls"))`), so app-level routes stay version-agnostic and a future `v2` is additive. DRF's `URLPathVersioning` is enabled so views can read `request.version`.

No Django admin this round: `django.contrib.admin` is not installed and no `/admin/` route exists, so the only authentication surface is the API's Basic Auth.

## 5. Data model

Seven tables: `Address`, `Patient`, `Prescriber`, `Medication`, `Pharmacy`, `PharmacyLocation`, `Prescription`. All get a UUID primary key (public-facing, non-enumerable), `created_at` / `updated_at` from the shared `TimeStampedModel`, and soft delete.

### `Address`

| Field | Type | Notes |
|---|---|---|
| `line1` | char(255) | required |
| `line2` | char(255) | optional — apt, suite, unit |
| `city` | char(100) | required |
| `state` | char(2) | required, USPS code, uppercased on save |
| `postal_code` | char(10) | required, `12345` or `12345-6789` |
| `country` | char(2) | ISO 3166-1 alpha-2, default `US` |

`Patient.address`, `Prescriber.address` and `PharmacyLocation.address` are all `FK → Address`, `PROTECT` (column `address_id`); nullable except on `PharmacyLocation`, where a location without a place is meaningless. Several records can therefore share one row, and editing it changes the address for all of them — intended (a clinic address shared by several prescribers) and settled in §10. A private per-record address would be a `OneToOneField`, which this plan does not build.

### `Patient`

| Field | Type | Notes |
|---|---|---|
| `first_name` | char(100) | required |
| `last_name` | char(100) | required |
| `middle_name` | char(100) | optional |
| `suffix` | char(20) | optional — generational only: Jr., Sr., II, III, IV |
| `dob` | date | required; `dob <= today` validated in `clean()` and the serializer |
| `gender` | char(16) choice | `male` / `female` / `other` / `unknown` (FHIR administrative-gender) |
| `phone_number` | char(20) | required; E.164 (`+15551234567`) |
| `email` | email | optional, indexed |
| `sms_opt_in` | bool | **default `False`** — consent is granted by a patient, never by a default |
| `address` | FK → `Address` | nullable, `PROTECT` |

Indexes on `(last_name, first_name)` and `dob`. `dob <= today` cannot be a database `CheckConstraint` — SQLite rejects non-deterministic functions like `date('now')` inside CHECK, and Postgres documents them as unsupported there — so it is a validation rule, tested as one (§8). The other check constraints in this section compare two columns or a column to a literal, which both backends support. Clinical sex-at-birth is a separate concept, not modelled this round (§10).

### `Prescriber`

| Field | Type | Notes |
|---|---|---|
| `first_name` | char(100) | required |
| `last_name` | char(100) | required |
| `middle_name` | char(100) | optional |
| `suffix` | char(20) | optional — generational only |
| `practitioner_type` | char(16) choice | `MD`, `DO`, `NP`, `PA`, `DDS`, `DMD`, `DPM`, `OD`, `DVM`, `PHARMD`, `RPH`, `CNM`, `CRNA`, `OTHER` |
| `npi` | char(10) | **unique among live rows**, required |
| `address` | FK → `Address` | nullable, `PROTECT` |

`suffix` (name component) and `practitioner_type` (credential) are separate: mixing them made "Jane Smith, MD, Jr." unrepresentable and name sorting unreliable. NPI is validated, not length-checked — 10 digits whose last is a Luhn check digit over the number prefixed with `80840`. A typo'd NPI is otherwise indistinguishable from a real one and propagates into claims.

### `Medication`

| Field | Type | Notes |
|---|---|---|
| `name` | char(255) | required, indexed |
| `ndc` | char(11) | **unique among live rows**, normalized |
| `strength` | char(100) | e.g. "500 mg" |
| `form` | char(32) choice | tablet / capsule / liquid / injection / topical / other |

NDC is stored normalized to 11 digits. Real NDCs arrive in 4-4-2, 5-3-2 and 5-4-1 formats that all denote the same product, so normalizing on save is what makes `unique` mean unique; a display-formatted value is derived for output.

**How a duplicate becomes a 409, not a 500.** DRF's `ModelSerializer` does not generate a uniqueness validator for a `UniqueConstraint` with a `condition`, so the partial index below is invisible to serializer validation. The serializer therefore normalizes in `validate_ndc` and checks `Medication.objects.filter(ndc=...)` (live rows) itself, raising `Conflict` (§6) with the existing id in `details`; the exception handler maps an `IntegrityError` on `uniq_live_ndc` to the same 409 as a backstop for a concurrent create. `Prescriber.npi` follows the same two-layer pattern.

### `Pharmacy` and `PharmacyLocation`

A pharmacy is modelled as **two tables, always** — an organization and its locations — rather than a single table with an optional parent pointer or a `is_chain` flag. A mom-and-pop shop is one `Pharmacy` with exactly one `PharmacyLocation`; CVS is one `Pharmacy` with thousands. Nothing in the API or the data model special-cases the two, which is the point: an independent that opens a second store becomes correct by inserting one row, not by migrating from one shape to another.

The split is not arbitrary tidiness — it follows the real identifiers. Name, brand and corporate identity belong to the organization; **NCPDP ID, NPI, DEA number, address and phone are per-store** and are what a claim, a routing decision or a transfer actually names. Putting them on a single table would force every chain store to duplicate the name and every independent to carry a nullable parent.

#### `Pharmacy` — the organization

| Field | Type | Notes |
|---|---|---|
| `name` | char(255) | required, indexed — the trading name, e.g. "CVS Pharmacy" |
| `legal_name` | char(255) | optional — the registered entity, when it differs |
| `phone_number` | char(20) | optional; E.164 — corporate contact, not a store line |
| `email` | email | optional |
| `website` | url | optional |

No address: an organization does not dispense, a store does. Corporate mail goes to a location flagged as such if it is ever needed.

`location_count` (live locations) is exposed as a read-only serializer field via an annotation, not stored — a denormalized counter would drift the first time a location is soft-deleted.

#### `PharmacyLocation` — a site

| Field | Type | Notes |
|---|---|---|
| `pharmacy` | FK → `Pharmacy` | required, `PROTECT`, `related_name="locations"` |
| `store_number` | char(20) | optional — the chain's own label for the site, e.g. "04821"; unique per pharmacy among live rows |
| `ncpdp_id` | char(7) | **unique among live rows**, required — the NCPDP/NABP provider ID, the identifier claims are routed on |
| `npi` | char(10) | **unique among live rows**, optional — the organizational NPI for this site |
| `dea_number` | char(9) | optional — required in practice to dispense controlled substances, not enforced here |
| `location_type` | char(16) choice | `retail`, `mail_order`, `specialty`, `compounding`, `hospital_outpatient`, `long_term_care`, `other` (default `retail`) |
| `phone_number` | char(20) | required; E.164 |
| `fax_number` | char(20) | optional; E.164 — still how most transfers arrive |
| `email` | email | optional |
| `address` | FK → `Address` | **required**, `PROTECT` |
| `is_24_hour` | bool | default `False` |

- `npi` reuses the `Prescriber.npi` check-digit validator unchanged — pharmacies carry organizational NPIs issued under the same scheme, so there is one implementation in `validators.py`, not two.
- `ncpdp_id` is normalized to 7 digits (stripping any separators) and validated as format only. The NCPDP check-digit scheme is not implemented here; claiming a check-digit algorithm that has not been verified against the specification would be worse than validating length and charset honestly. Duplicate NCPDP IDs are a 409 by the same two-layer pattern as NDC and NPI.
- `store_number` is unique *per pharmacy*, not globally — two chains both having a store 41 is normal. That is a partial `UniqueConstraint` on `(pharmacy, store_number)` conditioned on `deleted__isnull=True`, and it is skipped when `store_number` is blank.
- Indexes on `pharmacy` (the nested-list path) and `ncpdp_id`.
- Hours of operation beyond `is_24_hour`, delivery radius, and per-location inventory are out of scope (§10).

### `Prescription`

| Field | Type | Notes |
|---|---|---|
| `patient` | FK → `Patient` | `PROTECT` |
| `prescriber` | FK → `Prescriber` | `PROTECT` |
| `medication` | FK → `Medication` | `PROTECT` |
| `pharmacy_location` | FK → `PharmacyLocation` | **nullable**, `PROTECT` — where it is to be dispensed |
| `dose_amount` | char(100) | amount taken, e.g. "2 tablets" — distinct from `Medication.strength` |
| `quantity` | positive int | |
| `fills_authorized` | positive int | default 1, includes the original fill |
| `fills_used` | non-negative int | default 0, `<= fills_authorized` (check constraint) |
| `instructions` | text | the sig, optional |
| `status` | char choice | `draft`, `active`, `on_hold`, `completed`, `cancelled`, `expired` (default `draft`) |
| `written_at` | datetime | defaults to now |
| `expires_at` | datetime | nullable, check constraint `> written_at` |
| `notes` | text | optional |

**Status lifecycle.** `completed`, `cancelled` and `expired` are terminal; nothing leaves them.

| From | To | How |
|---|---|---|
| `draft` | `active` | `PATCH status=active` |
| `active` | `on_hold` | `PATCH status=on_hold` |
| `on_hold` | `active` | `PATCH status=active` |
| `active` | `completed` | automatically, when `fill` makes `fills_used == fills_authorized` |
| `active` | `expired` | automatically, when `fill` is attempted after `expires_at` (the fill is refused with 409) |
| any non-terminal | `cancelled` | `POST /cancel/` only |

Any other `status` in a `PATCH`/`PUT` is a 400 naming the current status and the allowed targets; `cancelled`, `completed` and `expired` are never writable directly. `fill` is allowed only on `active`. `expired` is set lazily on the fill path — no background expiry sweep.

**`pharmacy_location` is nullable but required to fill.** A `draft` prescription need not be routed anywhere yet, so the column allows null; `POST /fill/` on a prescription with no `pharmacy_location` is a **409**, since a fill that names no dispensing site is not a record of anything. The prescription stores the *location*, never the `Pharmacy` — "filled at CVS" identifies nothing, and a transfer between two stores of the same chain has to be representable. The location is re-assignable while the prescription is non-terminal; a full transfer history is a dispense-event model and stays out of scope (§10).

`PROTECT` on all four FKs is deliberate: `CASCADE` would let deleting a patient silently destroy their prescription history, which is the one thing a clinical record must never do. `PROTECT` turns that into a `ProtectedError` the API surfaces as a 409.

### Soft delete — nothing is ever hard-deleted

Every model inherits `SafeDeleteModel` with the `SOFT_DELETE` policy: `delete()` stamps a `deleted` timestamp, `objects` returns live rows, `all_objects` returns everything, `undelete()` restores. Three consequences designed for rather than discovered:

**1. Unique constraints must be partial.** A soft-deleted `Medication` still occupies its NDC, so re-creating that product fails on a row nobody can see — the classic soft-delete bug. Both unique fields are conditional:

```python
UniqueConstraint(fields=["ndc"], condition=Q(deleted__isnull=True), name="uniq_live_ndc")
```

Same for `Prescriber.npi`, `PharmacyLocation.ncpdp_id`, `PharmacyLocation.npi` and the `(pharmacy, store_number)` pair. Uniqueness then means "unique among live rows," which is what was intended — a closed store must not hold its NCPDP ID against the one that replaces it.

**2. `PROTECT` no longer fires, so the guard moves up a layer.** safedelete bypasses `on_delete` when soft-deleting, so soft-deleting a `Patient` would leave live prescriptions pointing at an invisible patient. The destroy handler refuses with **409** when live rows reference the object — including a `Pharmacy` that still has live locations, and a `PharmacyLocation` still named by live prescriptions. Closing a chain is therefore deliberately bottom-up: delete the stores, then the organization. `PROTECT` stays on the FKs as a backstop for a genuine `hard_delete()` in a future purge job. `SOFT_DELETE_CASCADE` is deliberately not used: a prescription written by a prescriber who has since left is still a real prescription.

**3. `DELETE` and `cancel` are different operations.** `POST /prescriptions/{id}/cancel/` is a **clinical** state change — the prescription stays visible with `status=cancelled`. `DELETE /prescriptions/{id}/` is a **record lifecycle** change — the row leaves the API entirely, recoverable. No `restore/` endpoint this round; restoring is a deliberate operator action from a shell (`uv run python manage.py shell -c "from pharmacy.models import Medication; Medication.all_objects.get(pk='…').undelete()"`).

**PHI note, stated once:** `Patient` holds names, dates of birth and contact details. With Basic Auth and no authorization (§7), every authenticated caller can read every patient. That is a deliberate, documented interim state and the main reason `plans/auth/plan.md` matters.

## 6. Endpoints (all under `/api/v1/`)

Seven resources, standard CRUD on each via DRF `ModelViewSet` + router.

### Prescriptions

| Method | Path | Behavior |
|---|---|---|
| GET | `/prescriptions/` | List. Filter by `patient`, `prescriber`, `medication`, `pharmacy_location`, `pharmacy` (via `pharmacy_location__pharmacy`), `status`, `written_at` range; `search=` across medication name and patient last name; `ordering=` on `written_at`/`created_at` |
| POST | `/prescriptions/` | → 201 |
| GET | `/prescriptions/{id}/` | → 200 / 404 |
| PATCH / PUT | `/prescriptions/{id}/` | → 200; `status` only via the §5 transitions, else 400 |
| DELETE | `/prescriptions/{id}/` | Soft-delete, 204 — distinct from `cancel` |
| POST | `/prescriptions/{id}/fill/` | Increment `fills_used`; sets `completed` on the last fill. 409 if not `active`, if already fully filled, if `expires_at` has passed (which also sets `expired`), or if no `pharmacy_location` is set |
| POST | `/prescriptions/{id}/cancel/` | → `cancelled`; 409 if already terminal |

### Patients

| Method | Path | Behavior |
|---|---|---|
| GET | `/patients/` | List. `search=` on first/last name; filter by `last_name`, `dob`, `sms_opt_in` |
| POST | `/patients/` | → 201 |
| GET/PATCH/PUT | `/patients/{id}/` | |
| DELETE | `/patients/{id}/` | Soft-delete; 409 if live prescriptions reference them |
| GET | `/patients/{id}/prescriptions/` | That patient's prescriptions, paginated |

### Prescribers

| Method | Path | Behavior |
|---|---|---|
| GET | `/prescribers/` | List. `search=` on name; filter by `npi`, `practitioner_type` |
| POST | `/prescribers/` | → 201; 400 on a bad NPI check digit; 409 on a duplicate live NPI |
| GET/PATCH/PUT | `/prescribers/{id}/` | |
| DELETE | `/prescribers/{id}/` | Soft-delete; 409 if live prescriptions reference them |
| GET | `/prescribers/{id}/prescriptions/` | That prescriber's prescriptions, paginated |

### Pharmacies

| Method | Path | Behavior |
|---|---|---|
| GET | `/pharmacies/` | List. `search=` on name and legal name; `ordering=` on `name`. Each row carries `location_count` |
| POST | `/pharmacies/` | → 201 |
| GET/PATCH/PUT | `/pharmacies/{id}/` | |
| DELETE | `/pharmacies/{id}/` | Soft-delete; **409 if it still has live locations** |
| GET | `/pharmacies/{id}/locations/` | That pharmacy's locations, paginated — the chain-roster route |

### Pharmacy locations

| Method | Path | Behavior |
|---|---|---|
| GET | `/pharmacy-locations/` | List across all pharmacies. Filter by `pharmacy`, `location_type`, `is_24_hour`, `ncpdp_id`, and `address__state` / `address__city` / `address__postal_code`; `search=` on store number, NCPDP ID and the parent pharmacy's name |
| POST | `/pharmacy-locations/` | → 201; 400 on a bad NPI check digit or malformed NCPDP ID; 409 on a duplicate live `ncpdp_id`, `npi`, or `(pharmacy, store_number)` |
| GET/PATCH/PUT | `/pharmacy-locations/{id}/` | |
| DELETE | `/pharmacy-locations/{id}/` | Soft-delete; 409 if live prescriptions reference it |
| GET | `/pharmacy-locations/{id}/prescriptions/` | Prescriptions routed to that store, paginated |

Locations get a top-level collection as well as the nested one because the useful queries — every store in a state, the store holding a given NCPDP ID — cut across pharmacies. Creation always names a `pharmacy_id`; there is no nested-create route, which keeps one write path per resource.

### Addresses

| Method | Path | Behavior |
|---|---|---|
| GET | `/addresses/` | List. Filter by `state`, `city`, `postal_code` |
| POST | `/addresses/` | → 201 |
| GET/PATCH/PUT | `/addresses/{id}/` | |
| DELETE | `/addresses/{id}/` | Soft-delete; 409 if a live patient, prescriber or pharmacy location references it |

### Medications

| Method | Path | Behavior |
|---|---|---|
| GET | `/medications/` | List. `search=` on name and NDC; filter by `form` |
| POST | `/medications/` | → 201; 409 on duplicate normalized NDC |
| GET/PATCH/PUT | `/medications/{id}/` | |
| DELETE | `/medications/{id}/` | Soft-delete; 409 if live prescriptions reference it |

Plus `/health/`, `/schema/`, `/docs/` (Swagger UI), `/redoc/`, all unauthenticated (§7).

### Swagger grouping

**Every operation is tagged with its resource**, so Swagger UI and ReDoc render one collapsible group per model rather than one flat list of thirty-odd endpoints:

| Tag | Covers |
|---|---|
| `Prescriptions` | `/prescriptions/` and its `fill` / `cancel` actions |
| `Patients` | `/patients/`, including `/patients/{id}/prescriptions/` |
| `Prescribers` | `/prescribers/`, including `/prescribers/{id}/prescriptions/` |
| `Medications` | `/medications/` |
| `Pharmacies` | `/pharmacies/`, including `/pharmacies/{id}/locations/` |
| `Pharmacy Locations` | `/pharmacy-locations/`, including `/pharmacy-locations/{id}/prescriptions/` |
| `Addresses` | `/addresses/` |
| `System` | `/health/` |

Three things make that hold rather than approximately hold:

1. **Tags are declared, not inferred.** drf-spectacular derives a tag from the first path segment when none is given, which is close but wrong at the edges — it would emit a lowercase `pharmacy-locations` group and give `/health/` its own stray one. Each viewset therefore carries an explicit `@extend_schema(tags=[...])` at the class level.
2. **A nested route is tagged with its parent**, not with the model it returns. `/patients/{id}/prescriptions/` sits under `Patients`, because that is where someone asks "what can I do with a patient?" — the operation is reached through a patient, and the flat `/prescriptions/?patient=` filter already lives under `Prescriptions`.
3. **Group order is pinned, not alphabetical.** `SPECTACULAR_SETTINGS["TAGS"]` lists the tags in the order above — `Prescriptions` first because it is the resource the service exists for, `Addresses` and `System` last because they are supporting detail — each with a one-line `description` that renders as the group's subtitle. Alphabetical order would open the docs on `Addresses`.

`SWAGGER_UI_SETTINGS` sets `docExpansion: "list"` so the page opens on the group list with operations collapsed, `tagsSorter` left unset so the pinned order survives, and `operationsSorter: "method"` for a stable order within a group. `persistAuthorization` stays on (§3b).

### Serializer shape for the foreign keys

Prescriptions **write flat and read nested**, so a client does not need four round-trips to render one prescription:

- **Write:** `patient_id`, `prescriber_id`, `medication_id`, `pharmacy_location_id` as UUIDs (`PrimaryKeyRelatedField`).
- **Read:** `medication` as `{id, name, ndc, strength, form}`, `patient` as `{id, first_name, last_name, dob}`, `prescriber` as `{id, first_name, last_name, npi, practitioner_type}`, `pharmacy_location` as `{id, store_number, ncpdp_id, phone_number, pharmacy: {id, name}, address: {…}}` — the store's identity is useless without the chain name, so that one nesting goes two levels deep.
- Patients, prescribers and pharmacy locations do the same for addresses: `address_id` in, nested `address` out.
- `PharmacyLocation` writes `pharmacy_id` and reads a nested `{id, name}`; `Pharmacy` does not embed its locations (a chain would return thousands) — `/pharmacies/{id}/locations/` is the paginated route for that, and the list carries `location_count`.
- The prescription list queryset uses `select_related("patient", "prescriber", "medication", "patient__address", "prescriber__address", "pharmacy_location", "pharmacy_location__pharmacy", "pharmacy_location__address")`; the location list uses `select_related("pharmacy", "address")` and the pharmacy list annotates `location_count` with a filtered `Count`. Without those, one list request becomes 3N queries; a test asserts the count stays constant as the page grows.

### Conventions

- Trailing slashes on; all `{id}` are UUIDs.
- Pagination: page-number, `?page=` & `?page_size=` (default 25, max 100), envelope `{count, next, previous, results}`.
- Errors: one shape via a custom DRF exception handler — `{"error": {"code", "message", "details"}}`. A `Conflict` exception (`APIException`, 409) is what views and serializers raise for blocked deletes, duplicate NDC/NPI, refused fills and refused cancels; the handler maps `ProtectedError` and unique-constraint `IntegrityError` to the same 409, naming the conflicting resource in `details`.
- Deleted rows are invisible everywhere: all viewset querysets use the live-only `objects` manager.

## 7. Cross-cutting

- **Authentication: HTTP Basic** against Django's `auth.User`, with `IsAuthenticated` as the global default. Open: `/api/v1/health/`, `/schema/`, `/docs/`, `/redoc/`.
- **Authorization: none.** Any authenticated user may read and write anything — no row-level filtering, roles or scopes, deliberately deferred to `plans/auth/plan.md`, whose §7 lists what this plan must avoid so the migration stays cheap.
- **Throttling:** DRF anon/user rate limits (e.g. 100/min), configurable via env.
- **CORS:** django-cors-headers, allowed origins from env.
- **Logging:** JSON structured logs to stdout with a request-id middleware. `SECRET_KEY`, `DATABASE_URL` and the `Authorization` header never appear in log output, including at startup.
- **Security:** `SECURE_*` settings in prod, `DEBUG=False`, `ALLOWED_HOSTS` from env. Basic Auth sends reversible base64 on every request, so prod sets `SECURE_SSL_REDIRECT=True` and `SECURE_PROXY_SSL_HEADER`; the service must never be exposed over plain HTTP.

## 8. Tests

`pharmacy/tests/` mirrors the package split — one module per model/resource, plus `test_settings.py` and `test_seed.py`.

- **Model:** check-constraint violations (`fills_used <= fills_authorized`, `expires_at > written_at`) raise `IntegrityError` at the database.
- **Status lifecycle:** every allowed §5 transition succeeds; every disallowed `PATCH` target is 400; `fill` on a non-`active` prescription is 409; `fill` with no `pharmacy_location` is 409; the last fill sets `completed`; a fill after `expires_at` sets `expired` and returns 409; `cancel` on each terminal status is 409.
- **Validation:** future `dob` → 400, non-E.164 `phone_number` → 400, NPI check-digit accept/reject (on both `Prescriber` and `PharmacyLocation`, against the one shared validator), NDC normalization across 4-4-2/5-3-2/5-4-1, NCPDP normalization and length rejection, duplicate NDC/NPI/NCPDP → 409, postal-code and USPS state formats, unknown `practitioner_type` or `location_type` rejected.
- **Pharmacy shapes:** a single-location independent and a multi-location chain are both creatable and behave identically; `location_count` matches the live location rows and drops when one is soft-deleted; `(pharmacy, store_number)` collides within one pharmacy (409) but not across two; a location with a blank `store_number` does not collide with another blank one.
- **Referential integrity:** deleting a referenced address/patient/prescriber/medication/pharmacy-location returns 409, not 500; deleting a `Pharmacy` with live locations returns 409, and succeeds once they are deleted.
- **Soft delete:** a deleted row vanishes from list and detail (404) but survives in `all_objects`; re-creating a medication with a previously-deleted NDC succeeds, as does re-using a closed store's NCPDP ID; `DELETE` and `cancel` produce different states.
- **Query count:** listing prescriptions issues a constant number of queries regardless of page size.
- **API:** happy path per endpoint, 400/404/409 paths, pagination, filtering, and 401 for every protected endpoint without valid credentials.
- **Auth:** health/schema/docs reachable unauthenticated; everything else not.
- **Schema:** `/api/v1/schema/` generates without warnings and lists every endpoint. Every operation carries **exactly one** tag, every tag it carries is one of the eight declared in `SPECTACULAR_SETTINGS["TAGS"]`, and no declared tag is empty — so a new endpoint cannot quietly land in an inferred group, which is the only way this grouping rots.
- **Settings:** `uat` and `prod` raise `ImproperlyConfigured` on a missing secret; the prod guards reject the local `SECRET_KEY`, `DEBUG=True` and wildcard `ALLOWED_HOSTS`.
- **Seeding:** `seed_local` is idempotent across two runs and refuses to run outside `local`.

Target: every endpoint above has at least one passing test.

### Where the tests run

`uv run pytest`, against **SQLite**. pytest-django builds a fresh test database per run, and for SQLite Django always builds it **in memory** regardless of the configured `NAME`, so the suite never touches `db.sqlite3`. No `DATABASE_URL` override is needed.

**Watch the slash count if you ever set a file-path `DATABASE_URL` by hand** — django-environ follows the URL spec, which is not what most people expect (verified, not assumed):

| URL | Resulting `NAME` |
|---|---|
| `sqlite:///tmp/test.db` | `tmp/test.db` — **relative to the working directory** |
| `sqlite:////tmp/test.db` | `/tmp/test.db` — absolute, four slashes |
| `sqlite://:memory:` | `:memory:` |

`.env.local` uses `sqlite:///db.sqlite3` deliberately: repo-relative is the intent. This is also why `local.py` must read the file with `overwrite=False` — read with `overwrite=True`, the committed `DATABASE_URL` would clobber any override and a Postgres run would silently test SQLite while appearing to work.

### What SQLite covers, and what it does not

**Covered, genuinely:** the check constraints, NPI/NDC validation, serializer and viewset behaviour, soft-delete visibility, the 400/401/404/409 paths, pagination, filtering, the query-count guard, the settings guards, and seed idempotency. SQLite supports partial indexes, so `uniq_live_ndc` and the deleted-NDC-reuse test exercise the real mechanism.

**Not covered** — these stay unverified until the suite runs against Postgres, and a report on a local run must say so rather than implying green means done:

- **The migration path against a real Postgres 18** — the only thing that proves the schema is buildable. SQLite's table-rebuild `ALTER` hides problems Postgres raises.
- **Collation and ordering** — the two backends disagree on case ordering and on `icontains` over non-ASCII, so `search=`/`ordering=` assertions can pass in one and fail in the other.
- **Constraint timing** — Postgres checks FKs at statement end and supports deferral; SQLite differs, so errors can surface at different points.
- **Anything asserting on database error text**, which no test should do.

Closing the gap costs one environment variable and no repo change:

```bash
DATABASE_URL=postgres://user:pass@host:5432/pharmacy_ops uv run pytest
```

That run is authoritative and gates deployment to uat or prod. It is out of scope for the bootstrap (§9).

## 9. Build order

Each phase ends with a verification step that is actually run, not inspected. Nothing moves past a red checkpoint.

### Phase 1 — foundation

1. `pyproject.toml`, `.python-version`, and `uv.lock` from `uv lock`; confirm `uv run python -V` reports 3.12.x. This surfaces any Django 6.1 incompatibility **before any app code exists**. If something does not support 6.1: stop, report the specific conflict, downgrade nothing unilaterally.
2. `.env.local`, `.env.example`, `scripts/dev.sh` in its final form, `manage.py`, and a minimal `config/` (settings stubbed to `base.py` + `local.py`) serving `/api/v1/health/`. Include a **stub `seed_local`** in `pharmacy` that logs "nothing to seed yet" and exits 0, so `dev.sh` runs end to end now and is not edited again at step 8.

**Checkpoint 1 — the skeleton boots from a clean clone:**

```bash
git clone <repo> /tmp/pharmacy-ops-check && cd /tmp/pharmacy-ops-check
./scripts/dev.sh
curl http://localhost:8000/api/v1/health/
```

Two commands to a live health endpoint — the Phase 1 half of the §3 hard requirement, checked where it is cheap to fix, before ten steps of application code are stacked on a broken `dev.sh`. The authenticated, seeded 200 is Checkpoint 2's job. `qa.md` §1 says which of its pass conditions apply here.

### Phase 2 — the service

3. Split settings (`base`/`local`/`uat`/`prod`), `require_settings`, prod guards. Only `local.py` reads `.env.local`, with `overwrite=False`.
4. `pharmacy/models/base.py` (`TimeStampedModel` + `SafeDeleteModel`), `exceptions.py` (`Conflict` + handler), `views/health.py`.
5. The model modules and the initial migration — one migration creating all seven tables, with the partial unique indexes (NDC, NPI, NCPDP, `(pharmacy, store_number)`) written in from the start, never retrofitted. Verified with `makemigrations --check --dry-run`.
6. `validators.py` and the serializer modules, including the shared NPI check digit and the NDC/NCPDP normalization.
7. View modules, `filters.py`, the `fill` and `cancel` actions, the §5 transitions, nested prescription routes, router, `/api/v1/` mount.
8. drf-spectacular + sidecar assets + whitenoise, including the declared tags, the pinned `TAGS` order and the `SWAGGER_UI_SETTINGS` above; Basic auth, throttling, CORS, logging; the real `seed_local`.
9. Tests (§8).
10. README — two-command quickstart, the `WEB_PORT` note, and one line that local is SQLite while uat/prod are Postgres.

**Checkpoint 2 — full verification:**

```bash
uv run ruff check .
uv run ruff format --check .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py spectacular --fail-on-warn
uv run pytest
```

Then, against a freshly deleted `db.sqlite3`:

```bash
./scripts/dev.sh
curl http://localhost:8000/api/v1/health/
curl -u admin:admin http://localhost:8000/api/v1/prescriptions/
open http://localhost:8000/api/v1/docs/
```

**Acceptance:** from an empty database the full suite passes against SQLite, the schema generates warning-free, and an authenticated list request returns seeded demo data. `qa.md` is the full protocol behind this checkpoint.

The Postgres run in §8 is **not** part of acceptance — there is no Postgres in this environment. It is the gate for deploying to uat or prod, and the report should list it as outstanding rather than omitting it.

## 10. Open questions

**Nothing is open.**

Settled: dependencies track latest stable; auth is Basic with no authorization (JWT + authz deferred); patients, prescribers, medications, pharmacies and addresses are local models owned by this service; `Address` is shared by FK rather than owned one-to-one; `dose_amount` replaces `dosage`; soft delete everywhere via django-safedelete, with no `restore/` endpoint and no Django admin; the status lifecycle is the §5 table, with no background expiry job; no sex-at-birth field; no dispense-event or allergy models; the service runs natively under `uv` on Python 3.12, on SQLite locally and Postgres 18 in uat/prod; the whole domain lives in one `pharmacy` app with models, serializers and views as packages (§4).

Also settled on pharmacies (§5): the organization/location split is unconditional, so an independent and a chain are the same shape; per-store identifiers (NCPDP ID, NPI, DEA) live on the location and never on the organization; a prescription names a location, not a chain, and cannot be filled without one; `location_count` is annotated, never stored; the NCPDP check digit is not validated, only the format; and opening hours beyond `is_24_hour`, per-location inventory, dispense events and store-to-store transfer history are out of scope this round.

The one thing that could still change the shape of the work is a dependency conflict at build **step 1**. §2 recorded a clean resolution on 2026-09-16, so it is expected to pass; if it does not, bring the specific conflict back rather than downgrading anything.
