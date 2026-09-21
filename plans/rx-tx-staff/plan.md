# Staff identity — Plan

Status: **implemented** (v4 approved; see "Implementation notes" at the end
for the handful of things that differ from or were not in the text).

### Changes since v3

- Q-D settled: hand-rolled nesting, no `drf-nested-routers` (§3.1).
- Q-E settled: `certified_technician` requires a PTCB/NHA certification (§2.1).
- No open questions remain (§7).

### Changes since v2

- Pharmacy segment keyed by the pharmacy UUID; `/pharmacy-locations/`
  removed; staff nested under pharmacies too — as answered (§3.1).
- Four roles: `pharmacist`, `certified_technician`, `technician`, `intern`.
  State registration required for the first two (§2.1). One interpretation
  on what "certified" implies for the certification fields.
- `drf-nested-routers` assessed (§7).

### Changes since v1

- Model renamed `PharmacyStaff` → `Staff` throughout.
- Live-unique NPI on staff, no role restriction on certification, nested
  routes, demo staff in `seed_local`: all in.
- No subject column and no further reference to the auth plan.
- Embedded Postgres is explicit opt-in (§6).
- Locations become a sub-resource of pharmacies — a restructure of an
  existing resource, not just an addition (§3.1).

## 1. Why

Prescription transfer recordkeeping (21 CFR 1306.25) needs the name of the
pharmacist who communicated a transfer. The only person-shaped thing in the
repo today is `auth.User`, which is an operator credential: a way to sign in,
not a person a state board can look up. This work adds the domain record for
that person and nothing that uses it yet.

**Out of scope, and nothing here prepares for it beyond the record itself:**
the transfer record, attribution or co-sign logic, the policy about who may
perform a transfer, shifts, who was on duty, and any join between a login
and a person.

## 2. Data model

### 2.1 `Staff` — new, `pharmacy/models/staff.py`

Inherits `TimeStampedModel` like every other model: UUID pk, timestamps, soft
delete, `objects` live / `all_objects` everything.

| Field | Type | Notes |
|---|---|---|
| `first_name` | char(100) | required |
| `last_name` | char(100) | required |
| `middle_name` | char(100) | optional |
| `suffix` | char(20) | optional; a name component (Jr., III), never a credential — same split as `Prescriber` |
| `role` | char(24) choice | `pharmacist` / `certified_technician` / `technician` / `intern` (`StaffRole` TextChoices, labels "Pharmacist", "Certified pharmacy technician", "Pharmacy technician", "Pharmacy intern"); required, no default |
| `registration_state` | char(2) | USPS code via the existing `validate_us_state`, uppercased on save; blank allowed (see below) |
| `registration_number` | char(32) | the number the state board issued; whitespace stripped and uppercased on save so uniqueness is not defeated by case; blank allowed (see below) |
| `certification_body` | char(8) choice | `PTCB` / `NHA`, blank allowed |
| `certification_number` | char(32) | blank allowed; stripped on save |
| `npi` | char(10) | blank allowed; the shared `validate_npi` check digit |
| `pharmacy` | FK → `Pharmacy` | required, `PROTECT`, `related_name="staff"`. The employer is the organization, not a site: a chain's technician floats between stores |
| `is_active` | bool | default `True` |

**State registration is both-or-neither**, and required for `pharmacist`
and `certified_technician`, optional for `technician` and `intern`: a number
without the issuing state is not something a board can look up, and a state
without a number says nothing. The required-roles set is one frozenset next
to `StaffRole`, checked in `clean()` and the serializer, so a state that
starts registering plain technicians is a one-line change.

**Certification** (`certification_body` + `certification_number`) is
both-or-neither, and required for `certified_technician`: a certified pharmacy technician is one who holds
a PTCB or NHA certification, so a row with that role and no certification
is a data error. Any other role may carry one; nothing restricts it.

Constraints and indexes:

- `UniqueConstraint(fields=["registration_state", "registration_number"], condition=Q(deleted__isnull=True) & ~Q(registration_number=""), name="uniq_live_staff_registration")` — the requested uniqueness, partial over live rows like every other identifier here, and excluding blanks so unregistered technicians never collide with each other.
- `UniqueConstraint(fields=["npi"], condition=Q(deleted__isnull=True) & ~Q(npi=""), name="uniq_live_staff_npi")` — the same blank-excluded shape as `PharmacyLocation.npi`.
- Index on `(last_name, first_name)` for search, as on `Prescriber`. The FK and the two unique constraints carry their own.

Both constraint names go into `LIVE_UNIQUE_CONSTRAINTS` in `exceptions.py`
so a concurrent create that slips past the serializer check maps to the 409
rather than a 500.

`is_active` vs soft delete: `is_active=False` is a person who has left or
whose registration lapsed and who must stay resolvable because historical
records will name them. `DELETE` is for a record that should never have
existed. The model docstring says so.

None of the role-keyed rules is a `CheckConstraint`: the pattern here is
validation for rules that need a message, constraints for rules that compare
columns.

No relationship to `auth.User`, no subject column.

### 2.2 `PharmacyLocation.pharmacist_in_charge` — new nullable FK

```python
pharmacist_in_charge = models.ForeignKey(
    "pharmacy.Staff",
    null=True,
    blank=True,
    on_delete=models.PROTECT,
    related_name="pic_of_locations",
)
```

The field, its docstring and the serializer description say exactly one
thing: this is the board-registered designation for the site, held for
defaulting and validation. It is not who was supervising at any moment, and
nothing will read it as such. The name is `pharmacist_in_charge` and nowhere
`supervising_*`, `on_duty_*` or similar.

Validation on assignment (serializer, plus the same rules in the model's
`clean()` so an ORM write with `full_clean` agrees):

1. `role == pharmacist` — a PIC is by definition a pharmacist, and a
   pharmacist always carries a state registration, so a PIC is always
   board-resolvable.
2. `is_active` — you cannot designate someone who has left.
3. `staff.pharmacy == location.pharmacy` — the PIC of an Acme store is Acme
   staff. With locations nested under pharmacies (§3.2) the location's
   pharmacy comes from the URL, so this is a straight comparison.

These are write-time checks only. A PIC who later becomes inactive or
changes employer leaves the designation in place; revoking it is a separate
write. Nothing cascades, nothing infers.

`PROTECT` is a backstop for a future hard delete, as on every other FK;
because safedelete bypasses `on_delete`, the real guard is
`protected_relations` on the staff viewset (§3).

### 2.3 Migration

One migration, `0007_staff`: create `Staff`, add
`pharmacylocation.pharmacist_in_charge`. Generated by `makemigrations`,
renamed, nothing else in it. No data migration: the Acme location seeded by
0003 gets no PIC, because its real board-registered PIC is a deployment fact,
not reference data.

## 3. API

### 3.1 Routing: sub-resources of a pharmacy

Locations and staff become resources *under* the pharmacy that owns them,
and `/api/v1/pharmacy-locations/` is removed:

```
/api/v1/pharmacies/{pharmacy_id}/locations/            GET, POST
/api/v1/pharmacies/{pharmacy_id}/locations/{id}/       GET, PUT, PATCH, DELETE
/api/v1/pharmacies/{pharmacy_id}/locations/{id}/prescriptions/   GET (moved from /pharmacy-locations/{id}/prescriptions/)
/api/v1/pharmacies/{pharmacy_id}/staff/                GET, POST
/api/v1/pharmacies/{pharmacy_id}/staff/{id}/           GET, PUT, PATCH, DELETE
```

`{pharmacy_id}` is the pharmacy's UUID, like every other `{id}` in the API.

**Mechanics.** Hand-rolled; `drf-nested-routers` was assessed and rejected
(§7). DRF's router has no nesting, so:

- a `PharmacyScopedViewSet` mixin in `views/base.py`: `get_queryset()`
  resolves `pharmacy_id` from the URL (404 `not_found` on an unknown or
  soft-deleted pharmacy, so a client cannot tell an empty roster from a
  wrong id — the same shape a bad `{id}` gives), narrows the queryset to
  that pharmacy, and `perform_create()` attaches it. Resolution happens once
  per request and is skipped during schema generation.
- explicit `path()` entries in `pharmacy/urls.py` built from
  `ViewSet.as_view({...})` — the nested routes are four lines, not a router.
- the child serializers lose their `pharmacy_id` write field (the parent is
  in the URL) and keep the nested `pharmacy: {id, name}` on read. A location
  or staff member can therefore not be moved to another pharmacy by PATCH;
  a resource's identity includes its parent. That is the REST trade-off and
  I am taking it deliberately.
- `nested_list()` stays for the read-only `{id}/prescriptions/` routes.

**Schema tags.** Location operations are tagged "Pharmacy Locations" and
staff operations "Staff" even though their paths start with `/pharmacies/`.
The existing "nested route carries its parent's tag" rule was written for
read-only child lists such as `/patients/{id}/prescriptions/`; a sub-resource
with its own full CRUD is its own group. "Staff" is inserted after "Pharmacy
Locations" in `SPECTACULAR_SETTINGS["TAGS"]` with the description
"Pharmacists, technicians and interns employed by a pharmacy." The pinned
order test is updated.

**What this changes for existing clients.** `/api/v1/pharmacy-locations/`
goes away — with it the cross-pharmacy lookups it served ("every store in
NY", "which store holds NCPDP 1417231"), which now need the pharmacy first —
and `/pharmacies/{id}/locations/` goes from a read-only list to the location
resource itself. Prescriptions are
untouched: `pharmacy_location_id` stays a flat UUID on write, the nested
`pharmacy_location` summary on read is the same shape, the `?pharmacy=` and
`?pharmacy_location=` filters stay, and the home-location default is a
setting, not a route. `views/prescription.py` and
`tests/test_prescriptions_api.py` are not touched — I checked that neither
references the location routes.

**This is a restructure of an existing resource, not part of staff
identity.** It touches `views/pharmacy_location.py`,
`serializers/pharmacy_location.py`, `urls.py`, `filters.py`
(`PharmacyLocationFilter` loses `pharmacy`, now implied by the path), the
README tables, and most of `tests/test_pharmacies_api.py`. There are no
consumers yet per the README, so it is cheap now and expensive later; I am
flagging the size rather than objecting.

### 3.2 Locations: `/pharmacies/{pharmacy_id}/locations/`

`PharmacyLocationViewSet` with the mixin above. Filters, search and ordering
as today minus `pharmacy`: `location_type`, `is_24_hour`, `ncpdp_id`,
`state`, `city`, `postal_code`; search on store number, NCPDP ID; ordering on
`created_at`, `store_number`. NCPDP ID and NPI uniqueness stay global (a
claim does not care who owns the store); store-number uniqueness stays
per-pharmacy.

Serializer changes: `pharmacy_id` write field removed; `pharmacist_in_charge_id`
added (UUID, optional, nullable, write); `pharmacist_in_charge` added (the
staff summary below, or `null`, read). `queryset` gains
`select_related("pharmacist_in_charge")`.

`PharmacyLocationSummarySerializer` — the nested shape on a prescription — is
**unchanged**. No `pharmacist_in_charge` filter on locations.

### 3.3 Staff: `/pharmacies/{pharmacy_id}/staff/` — `StaffViewSet`

- `queryset = Staff.objects.select_related("pharmacy")`
- `protected_relations = (("pic_of_locations", "pharmacy locations"),)` — deleting the PIC of a live location is 409 `referenced`.
- Filters (`StaffFilter`): `role`, `is_active`, `registration_state` (iexact), `registration_number`, `certification_body`, `npi`.
- `search_fields = ["first_name", "last_name", "registration_number", "npi"]`
- `ordering_fields = ["created_at", "last_name"]`

`StaffSerializer` (`serializers/staff.py`), the `PrescriberSerializer` shape:
nested `pharmacy: {id, name}` on read, `full_name` read-only,
`registration_state` normalised and validated, the registration pair checked
with `reject_live_duplicate()` in `validate()` so a PATCH of one half still
checks the pair — 409 `duplicate_registration_number`,
`details.field = "registration_number"`, `details.registration_number`,
`details.registration_state`, `details.existing_id`. `npi` runs
`validate_npi` and the same duplicate check, 409 `duplicate_npi`, like the
prescriber. DRF's generated uniqueness validators are dropped the way
`prescriber.py` does, so a duplicate is a 409 and not a bare 400.

`StaffSummarySerializer`: `id, first_name, last_name, role,
registration_state, registration_number`. That is what a board lookup needs
and what a transfer record will later embed.

### 3.4 Pharmacies: `/pharmacies/`

`protected_relations` gains `("staff", "staff")`: deleting a pharmacy that
still employs live staff is 409 `referenced`, like one that still has
locations. The `locations` action is replaced by the nested routes above.

### 3.5 Errors

| Status | `code` | When |
|---|---|---|
| 400 | `validation_error` | unknown role/state/body, bad NPI check digit, half a registration or half a certification, registration missing for a role that requires it, PIC that is not an active pharmacist of this pharmacy |
| 404 | `not_found` | unknown `{pharmacy_id}` on any nested route |
| 409 | `duplicate_registration_number` | a live staff record already holds that state + number |
| 409 | `duplicate_npi` | a live staff record already holds that NPI |
| 409 | `referenced` | deleting a staff member who is the PIC of a live location, or a pharmacy with live staff or locations |

## 4. Tests

New `pharmacy/tests/test_staff_api.py`, cases grouped by behaviour, data from
a new `StaffFactory` (role pharmacist, state `OR`, sequential registration
number, `pharmacy` sub-factory) plus `pharmacist` and `technician` fixtures
in `conftest.py`.

- **CRUD and shape:** create under a pharmacy reads back nested `pharmacy` (from the URL, not the body), `full_name`; unknown role/state/body → 400; state uppercased; unknown `{pharmacy_id}` → 404 on list, create and detail; a staff member is not visible under another pharmacy's path.
- **Registration:** same state + number → 409 with code and `details.field`; same number in another state → 201; soft-deleted pair reusable; PATCH that would collide → 409; state without number and number without state → 400; missing entirely → 400 for `pharmacist` and `certified_technician` (parametrized), 201 for `technician` and `intern`; two unregistered technicians do not collide.
- **Certification:** body without number and vice versa → 400; both or neither → 201 for `technician`; missing → 400 for `certified_technician`.
- **NPI:** bad check digit → 400; blank allowed; duplicate → 409 `duplicate_npi`; blanks never collide.
- **Filters and search** per field; ordering.
- **Delete guards:** PIC of a live location → 409 `referenced`; deletable once the location drops them or is deleted; pharmacy with live staff → 409, deletable once staff are deleted.
- **PIC on location** (in `test_pharmacies_api.py`): set on create and PATCH, reads back the summary, clearable with `null`; technician → 400; inactive pharmacist → 400; pharmacist employed by another pharmacy → 400.
- **Nested location routes** (`test_pharmacies_api.py`, rewritten for the new paths): everything it covers today — both shapes, `location_count`, identifiers, filters, delete guards — plus 404 on unknown parent and no visibility across parents.
- **Schema/auth** (`test_auth_and_schema.py`): `PROTECTED` and the 404 list updated for the nested paths; tag order updated; the existing tag-hygiene tests cover the rest.
- **Model** (`test_models.py`): both staff constraints raise `IntegrityError` on a live duplicate and not on a deleted or blank one.
- **Seed** (`test_settings_and_seed.py`): seeded staff pass the API validators, like the seeded NPIs.

`tests/test_prescriptions_api.py` is not touched.

## 5. Docs and seed

- `README.md`: resources table (nested paths, `/staff/`, PIC), filter table, error table, the "Development" paragraph for the host path (§6).
- `AGENTS.md`: "Running commands" currently says the tests cannot run from the host; it gets the opt-in host path with its caveat (§6). Compose stays the reference. The "nested routes reuse the child viewset" convention gets a sentence distinguishing a read-only child list from a sub-resource.
- `seed_local`: one demo pharmacist per seeded pharmacy, set as PIC of that pharmacy's first store; a certified technician for the chain (registered, PTCB) and a plain technician for the independent (unregistered, no certification), so every role rule is visible from a plain boot. Idempotent on `(registration_state, registration_number)`, or on name + pharmacy for the unregistered one. Local-only demo data, no change to migration 0003.

## 6. Environment: running the suite here

Verified without touching the repo: `UV_PYTHON_DOWNLOADS=never
UV_PYTHON=/usr/bin/python3.12 uv sync --locked --all-groups` builds `.venv`
from the committed lock against the system 3.12.3; `pgserver` 0.1.4 installs
from PyPI as a manylinux wheel, runs `initdb` and starts in ~9 s as the
current user on a Unix socket under its data directory (no TCP port, no
daemon, no sudo), and `django-environ` parses its URI into a `DATABASES`
entry Django's Postgres backend accepts. **It ships PostgreSQL 16.2, not
18.** Every feature this schema uses exists in both, but a green run on it
is not a PG 18 run; Compose and CI remain the only PG 18 runs.

Changes:

- `pgserver>=0.1.4` in the `dev` group of `pyproject.toml`; `uv lock`
  refreshes `uv.lock` (adds `pgserver`, `fasteners`, `platformdirs`,
  `psutil`). The Dockerfile installs `--all-groups`, so the next
  `docker compose up --build` pulls the 11 MB wheel into the image, where it
  is never started.
- **Opt-in:** `pharmacy/tests/conftest.py` overrides
  pytest-django's documented `django_db_modify_db_settings` session fixture.
  When `PHARMACY_TEST_DB=embedded` is in the process environment it starts
  `pgserver` in a session temp directory, sets `DATABASE_URL` to the
  server's URI and re-derives `settings.DATABASES["default"]` from it
  through the same `env.db()` parser `config/settings/local.py` uses;
  teardown stops the server and deletes the data directory. When the
  variable is absent the fixture does nothing and the configured
  `DATABASE_URL` is used exactly as today — in Compose and CI that is PG 18;
  on a bare host it fails with psycopg's "could not translate host name
  'db'", which is the honest outcome. Either way a line in the terminal
  summary names the engine, its version and whether it was embedded or
  configured.

  The variable is read only by the test conftest, never by settings, so it
  does not go in `.env.example` (that file is the settings contract); it is
  documented in `AGENTS.md` and the README.

  "Set `DATABASE_URL` to it" cannot literally happen before Django reads it:
  pytest-django loads the settings module in its
  `pytest_load_initial_conftests` hook, before any `conftest.py` is
  imported, so the fixture has to rewrite the already-loaded `DATABASES` as
  well as the variable. That is what the documented hook exists for.
- `docker-compose.yml`, `.env.local`, `.env.example`, `config/settings/*` and the Dockerfile are not changed.

So the run here is `PHARMACY_TEST_DB=embedded uv run pytest`: the whole suite
on Postgres, nothing skipped, SQLite not involved.

## 7. Settled questions

Kept for the record; nothing here is open.

| | Answer |
|---|---|
| Model name | `Staff` |
| Subject / login reference | none |
| Registration required for | `pharmacist`, `certified_technician`; optional for `technician`, `intern` |
| Certification required for | `certified_technician`; optional, unrestricted, for every other role |
| Live-unique NPI on staff | yes, blank-excluded |
| Pharmacy path segment | pharmacy UUID (`Pharmacy` has no NPI) |
| `/api/v1/pharmacy-locations/` | removed |
| Staff routes | nested: `/pharmacies/{pharmacy_id}/staff/` only |
| `pharmacist_in_charge` filter on locations | no |
| Demo staff in `seed_local` | yes |
| Embedded Postgres | opt-in via `PHARMACY_TEST_DB=embedded` |
| Nesting mechanics | hand-rolled |

**Why not `drf-nested-routers`.** Verified from PyPI metadata and a scratch
install: API-stable, ~450 lines, "Production/Stable", but a single
maintainer, README that still says "work in progress", and declared support
of Django 4.2–5.2 / DRF 3.14–3.16 only — not the Django 6.1 / DRF 3.18 this
repo runs (it does import and route on them; that is my smoke test, not
their CI). It would replace the four `path()` lines and the queryset
narrowing, not the parent resolution, the 404 or the create-time attach.
About twenty lines saved against an undeclared-version dependency.

## 8. Files

New: `pharmacy/models/staff.py`, `pharmacy/serializers/staff.py`,
`pharmacy/views/staff.py`, `pharmacy/migrations/0007_staff.py`,
`pharmacy/tests/test_staff_api.py`.

Edited: `pharmacy/models/{__init__,pharmacy_location}.py`,
`pharmacy/serializers/{__init__,pharmacy_location}.py`,
`pharmacy/views/{__init__,base,pharmacy,pharmacy_location}.py`,
`pharmacy/urls.py`, `pharmacy/filters.py`, `pharmacy/exceptions.py`,
`config/settings/base.py` (the tag entry only),
`pharmacy/management/commands/seed_local.py`,
`pharmacy/tests/{conftest,factories,test_auth_and_schema,test_models,test_pharmacies_api,test_settings_and_seed}.py`,
`pyproject.toml`, `uv.lock`, `README.md`, `AGENTS.md`.

Not touched: `pharmacy/models/prescription.py`, `pharmacy/views/prescription.py`,
`pharmacy/serializers/prescription.py`, `pharmacy/tests/test_prescriptions_api.py`,
migrations 0001–0006, `docker-compose.yml`, `Dockerfile`, `.env.local`,
`.env.example`, `config/settings/{local,uat,prod}.py`, `.github/`.

Before handing back: `pytest`, `ruff check .`, `ruff format --check .`,
`makemigrations --check --dry-run`, `spectacular --fail-on-warn`, all through
`uv run`, with the database line from the test run quoted so it is clear
which Postgres it was.

## 9. Implementation notes

What was built follows §2–§6. Differences and discoveries, so review can
focus on them:

- **`uv.lock` was spliced by hand.** The sandbox reaches PyPI through a
  mirror that rewrites download URLs (`https://pypi.org/packages/packages/…`)
  and drops file sizes, so a plain `uv lock` would have rewritten every
  entry in the file to mirror-specific URLs. Instead the four new package
  blocks (`pgserver`, `fasteners`, `platformdirs`, `psutil`) were taken from
  that resolution and rewritten to canonical `files.pythonhosted.org` URLs
  with the resolver's hashes and sizes from PyPI's JSON API, then inserted
  into the original lock; every pre-existing line is untouched (64
  insertions, 0 deletions). `uv lock --check` and `uv sync --locked` pass.
  Worth a `uv lock` on a normal network to confirm it is a no-op.
- **`collectstatic` is needed on the host** before the suite: the two docs
  tests (`/docs/`, `/redoc/`) need the static manifest the Docker build
  creates. Documented in README and AGENTS; no code change.
- `reject_live_duplicate()` and `duplicate()` gained an optional `extra=`
  for the second half of a composite identifier, so the 409 for a
  registration carries `details.registration_state` next to
  `details.registration_number`.
- The PIC rules live once, in `pharmacist_in_charge_problem()` in
  `models/pharmacy_location.py`, used by both `PharmacyLocation.clean()` and
  the serializer.
- `sub_resource()` in `urls.py` derives the `{id}/prescriptions/` route from
  the viewset's `@action`s rather than hard-coding it, so a future detail
  action on a scoped viewset is routed automatically. Path segments are
  `<str:…>` rather than `<uuid:…>` so a malformed id returns the API's JSON
  404 instead of Django's HTML one, as on the router's routes.
- `views/tags.py` gained `PHARMACY_ID`, an explicit UUID path parameter,
  because spectacular types a hand-rolled `<str:pharmacy_id>` as a plain
  string.
- The embedded-database fixture has to mutate `settings.DATABASES["default"]`
  *in place* and call `connections.configure_settings()`: `django.db.
  connections` had already cached that dict and handed it to the default
  connection, so rebinding the key left Django pointing at host `db`.
- Suite on the embedded server here: 254 passed, `ruff check`, `ruff format
  --check`, `makemigrations --check --dry-run` and `spectacular
  --fail-on-warn` all clean.
