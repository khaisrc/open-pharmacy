# open-pharmacy

A reference implementation of the backend for a US pharmacy. It is a REST API
over prescriptions and the records they reference: patients, prescribers,
medications, pharmacies, their dispensing locations and addresses. Django +
Django REST Framework on PostgreSQL 18, packaged as a single container.

Being a reference implementation means the domain is modelled the way the US
pharmacy ecosystem identifies things, not the way a generic CRUD app would:

- Prescribers and dispensing sites carry an **NPI**, validated against the
  check-digit scheme, so a typo cannot propagate into a claim.
- Medications are keyed by **NDC**, accepted in any of the four segmented
  forms and normalized to the 11-digit HIPAA layout.
- Dispensing sites are keyed by **NCPDP ID** (the pharmacy number on a claim)
  and may carry a **DEA number**; store numbers are unique per chain, not
  globally.
- Addresses are US postal addresses with USPS state codes and 5 or 9 digit ZIP
  codes; phone numbers are E.164.
- The deployment serves **one pharmacy**: a prescription written without a
  dispensing site is routed to the configured home location.

It is a starting point to build on rather than a product. Authorization, an
external identity provider, dispense events and allergies are among the things
deliberately left for later; see `plans/`.

## Quickstart

The only prerequisite is Docker with Compose v2. No Python, no PostgreSQL on the
host.

```bash
git clone <repo> && cd open-pharmacy
docker compose up
```

That starts PostgreSQL, builds the image, applies migrations, seeds a superuser
(`admin` / `admin`) plus demo data, and serves the API on port 8000.

```bash
curl -u admin:admin http://localhost:8000/api/v1/prescriptions/
open http://localhost:8000/api/v1/docs/          # Swagger UI
```

Port 8000 taken? Set `WEB_PORT` in the shell (Compose reads the port mapping
before any env file):

```bash
WEB_PORT=9000 docker compose up
```

## The API

Everything is under `/api/v1/`. The OpenAPI schema is at `/api/v1/schema/`,
Swagger UI at `/api/v1/docs/`, ReDoc at `/api/v1/redoc/`.

### Authentication

HTTP Basic against Django's user table. Every endpoint requires it except
`/api/v1/health/` and the docs. There is **no authorization yet**: any
authenticated user can read and write any record, including patient PHI. The
JWT and role design that replaces this is in `plans/auth/plan.md`.

### Resources

| Path | What it is | Notes |
|---|---|---|
| `/prescriptions/` | A medication order for a patient | `fill/` and `cancel/` actions; see lifecycle below |
| `/patients/` | Who a prescription is written for | `{id}/prescriptions/` |
| `/prescribers/` | Who wrote it, keyed by NPI | `{id}/prescriptions/` |
| `/medications/` | What it names, keyed by NDC | NDC is normalized to 11 digits on write |
| `/pharmacies/` | The organization, independent or chain | `location_count` is computed |
| `/pharmacies/{id}/locations/` | A dispensing site, keyed by NCPDP ID | `{id}/prescriptions/`; carries the pharmacist-in-charge |
| `/pharmacies/{id}/staff/` | A credentialed person the pharmacy employs | pharmacist, certified technician, technician or intern; keyed by state registration |
| `/addresses/` | Postal addresses the records above share | |

All resources support `GET` (list and detail), `POST`, `PATCH`, `PUT` and
`DELETE`. Writes take flat ids (`patient_id`, `address_id`); reads return the
nested object, so rendering a prescription is one request.

Locations and staff are sub-resources of the pharmacy that owns them: the
pharmacy comes from the path, never from the body, and a record cannot be
moved to another pharmacy. An unknown pharmacy is a 404 on every nested
route. Prescriptions still reference a location by flat `pharmacy_location_id`.

### Staff

A staff record is the domain identity of a person -- the name a state board
can look up -- and is deliberately not a login. `role` is one of
`pharmacist`, `certified_technician`, `technician` or `intern`. Two
credentials are recorded separately because a board verifies one and a
certifying body the other, and neither stands in for the other:

- `registration_state` + `registration_number`: the state board registration,
  both or neither, unique per state among live records. Required for a
  pharmacist and a certified technician; optional for a technician or intern,
  since not every state registers them.
- `certification_body` (`PTCB` or `NHA`) + `certification_number`: the
  national technician certification, both or neither. Required for a
  certified technician; any role may hold one.

`npi` is optional and validated like every other NPI. `is_active=false` is a
person who has left or whose registration lapsed: the row stays, because
records will still name them. `DELETE` is for a record that should never
have existed.

A location's `pharmacist_in_charge` is the board-registered designation for
the site, kept for defaulting and validation. Designating one requires an
active pharmacist employed by the same pharmacy; nothing revokes it on its
own, and it says nothing about who was supervising at any particular moment.

`DELETE` is a soft delete: the row stays in the database, leaves the API, and
its unique identifier (NPI, NDC, NCPDP ID) becomes reusable. A delete that
would orphan live records is refused with a 409.

### Prescription lifecycle

A prescription starts `active`. The table is the complete set of transitions:

| From | To | How |
|---|---|---|
| `active` | `on_hold` | `PATCH {"status": "on_hold"}` |
| `on_hold` | `active` | `PATCH {"status": "active"}` |
| `active` | `completed` | `POST {id}/fill/` uses the last authorized fill |
| `active` | `cancelled` | `POST {id}/cancel/` |
| `active` | `expired` | recorded when a fill, cancel or update finds `expires_at` in the past |

`completed`, `cancelled` and `expired` are terminal. Nothing leaves them, and no
field of a terminal prescription can be changed (409 `already_terminal`).
Setting a terminal status through `PATCH` is a 400 that names the endpoint
which owns that transition.

`POST {id}/fill/` increments `fills_used`, sets `completed` when
`fills_remaining` hits zero, and is a 409 when the prescription is not active,
has no fills left, or has expired. Fills and cancels lock the row, so two
concurrent requests cannot both succeed.

`pharmacy_location_id` is optional on create. When omitted, the prescription is
routed to the **home location**, the store this deployment serves, identified
by `HOME_PHARMACY_LOCATION_NCPDP_ID`. The default points at the Acme Pharmacy
row that migration 0003 seeds into every database.

### Lists

Every list is paginated: `?page=` and `?page_size=` (default 25, max 100), with
`count`, `next`, `previous` and `results` in the body.

| Resource | Filters | `?search=` | `?ordering=` |
|---|---|---|---|
| prescriptions | `patient`, `prescriber`, `medication`, `pharmacy_location`, `pharmacy`, `status`, `written_after`, `written_before` | medication name, patient last name | `written_at`, `created_at` |
| patients | `last_name`, `dob`, `sms_opt_in`, `gender` | first and last name | `created_at`, `last_name`, `dob` |
| prescribers | `npi`, `practitioner_type` | name, NPI | `created_at`, `last_name` |
| medications | `form` | | |
| pharmacies | `name` | name, legal name | `created_at`, `name` |
| pharmacies/{id}/locations | `location_type`, `is_24_hour`, `ncpdp_id`, `state`, `city`, `postal_code` | store number, NCPDP ID | `created_at`, `store_number` |
| pharmacies/{id}/staff | `role`, `is_active`, `registration_state`, `registration_number`, `certification_body`, `npi` | name, registration number, NPI | `created_at`, `last_name` |
| addresses | `state`, `city`, `postal_code` | | |

The nested prescription routes (`/patients/{id}/prescriptions/` and so on)
are the prescription list with one filter pinned, so the same parameters
work there. Prefix an ordering field with `-` for descending.

### Errors

Every error has one shape:

```json
{
  "error": {
    "code": "duplicate_npi",
    "message": "A live prescriber with this NPI already exists.",
    "details": {"field": "npi", "npi": "1234567893", "existing_id": "…"}
  }
}
```

| Status | `code` | When |
|---|---|---|
| 400 | `validation_error` | `details` maps each field to its errors |
| 401 | `not_authenticated` | missing or bad credentials |
| 404 | `not_found` | |
| 409 | `duplicate_<field>` | a live record already holds that NPI, NDC, NCPDP ID, store number or state registration (`duplicate_registration_number`, with `details.registration_state`) |
| 409 | `referenced` | delete refused; `details.referenced_by` counts the live references, including a location whose pharmacist-in-charge is being deleted |
| 409 | `not_active`, `no_fills_remaining`, `expired` | fill refused |
| 409 | `already_terminal` | cancel or update refused; `details.status` is the current status |
| 429 | `throttled` | 100/min anonymous, 1000/min authenticated by default |

Every response carries an `X-Request-ID` header, echoed from the request if
sent, and every log line for that request includes it.

## Development

There is no interpreter or database on the host, so every command runs through
Compose. `docker compose run --rm web …` starts the database, runs the one-shot
`migrate` service, then runs the command in a throwaway container.

```bash
docker compose up                       # serve, with migrations and seed
docker compose up --build               # after changing dependencies
docker compose down -v                  # stop and drop the database
docker compose logs -f web

docker compose run --rm web pytest
docker compose run --rm web pytest pharmacy/tests/test_prescriptions_api.py -k fill
docker compose run --rm web ruff check .
docker compose run --rm web ruff format .
docker compose run --rm web python manage.py makemigrations
docker compose run --rm web python manage.py spectacular --fail-on-warn
docker compose exec db psql -U pharmacy open_pharmacy
```

The test suite runs against PostgreSQL 18, the same engine as production, in a
database pytest creates and drops. CI (`.github/workflows/test.yml`) runs the
suite, ruff, and `makemigrations --check` through the same Compose file.

Without Docker, the suite can bring its own PostgreSQL. The `pgserver` dev
dependency bundles a server that runs as you, on a socket, with no daemon or
sudo -- but it is PostgreSQL 16, so Compose remains the run that counts:

```bash
UV_PYTHON_DOWNLOADS=never uv sync --locked --all-groups   # needs a Python 3.12 on PATH
uv run python manage.py collectstatic --noinput           # the docs pages need the manifest
PHARMACY_TEST_DB=embedded uv run pytest
```

The last line of the run names the server it used. Without the variable the
suite connects to `DATABASE_URL` exactly as in Compose.

### Layout

```
config/settings/     base.py, then local.py / uat.py / prod.py chosen by DJANGO_ENV
pharmacy/models/     one module per model; soft delete and UUID ids in base.py
pharmacy/serializers/  same split; duplicate checks in base.py
pharmacy/views/      same split; nested routes and delete guards in base.py
pharmacy/filters.py  the list filters above
pharmacy/validators.py  NPI check digit, NDC and NCPDP normalization
pharmacy/exceptions.py  the error envelope and every 409
pharmacy/management/commands/seed_local.py  the superuser and demo data
plans/               design documents; the code is the source of truth where they differ
```

Identifiers are validated and normalized on the way in: an NPI must carry a
valid check digit, an NDC in any segmented form is stored as 11 digits, an NCPDP
ID as 7. Unique constraints are partial and cover live rows only, which is what
lets a soft-deleted record's identifier be reused.

## Configuration and deployment

`DJANGO_ENV` selects the settings module: `local` (default), `uat` or `prod`.
`local` reads the committed `.env.local`, whose every value is a throwaway
valid nowhere else. `uat` and `prod` read the process environment only and
refuse to start with anything missing; `prod` additionally refuses the local
`SECRET_KEY`, `DEBUG=true` and a wildcard `ALLOWED_HOSTS`.

| Variable | Required outside local | Purpose |
|---|---|---|
| `SECRET_KEY` | yes | |
| `DATABASE_URL` | yes | `postgres://user:password@host:5432/dbname` |
| `ALLOWED_HOSTS` | yes | comma separated |
| `DEBUG` | | must be false |
| `CORS_ALLOWED_ORIGINS` | | comma separated |
| `THROTTLE_ANON`, `THROTTLE_USER` | | e.g. `100/min` |
| `HOME_PHARMACY_LOCATION_NCPDP_ID` | | the store this deployment serves; default `4417829` |
| `SECRETS_FILE` | | optional env file a secrets sidecar writes, read before the environment |

`.env.example` lists the same contract without values.

**Migrations are a separate step, never part of server start.** Locally the
`migrate` Compose service runs them once per `up`. In uat and prod, run
`python manage.py migrate --noinput` as a one-shot job before starting the new
replicas; replicas that migrate on boot race each other on the same DDL.

The image's default command is `runserver` for local use. Deployed tiers
override it with gunicorn, which is installed:

```bash
gunicorn config.wsgi --bind 0.0.0.0:8000
```

Both deployed tiers redirect to HTTPS, since Basic Auth over plain HTTP would
leak credentials. `GET /api/v1/health/` returns 200 with
`{"status": "ok", "database": "ok"}`, or 503 when the database is unreachable.
