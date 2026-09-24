# AGENTS.md

Working notes for coding agents. `docs/` documents the API and the domain, and
`README.md` is the overview that links to it; this file covers how to run things
and the conventions that are easy to violate. Where they disagree, the code wins.
New long-form documentation belongs in `docs/`, not in the README.

## Running commands

There is no interpreter or database on the host. Every command goes through
Compose, which starts PostgreSQL, runs the one-shot `migrate` service, then runs
the command in a throwaway container:

```bash
docker compose run --rm web pytest
docker compose run --rm web pytest pharmacy/tests/test_fills_api.py
docker compose run --rm web ruff check .
docker compose run --rm web ruff format .
docker compose run --rm web python manage.py makemigrations
docker compose run --rm web python manage.py makemigrations --check --dry-run
docker compose run --rm web python manage.py spectacular --fail-on-warn
docker compose up --build        # after changing dependencies
docker compose down -v           # stop and drop the database
```

Do not run `ruff` or `manage.py` from the host `.venv`; the venv is not the
environment CI uses. The one exception is the test suite on a host with no
Docker: `PHARMACY_TEST_DB=embedded uv run pytest` starts a PostgreSQL 16 from
the `pgserver` dev dependency for the session (run `collectstatic` first, the
docs tests need the manifest). It is a convenience, not parity -- Compose is
PostgreSQL 18 and is what CI runs. The variable is read only in
`pharmacy/tests/conftest.py`; settings, Compose and `.env.local` do not know
it exists.

Before handing work back, run the four things CI runs: `pytest`, `ruff check .`,
`ruff format --check .`, and `makemigrations --check --dry-run`. If the
change touches API behavior, run the e2e suite too: `docker compose up -d` and
then `./e2e/run.sh` from the host.

Dependencies are managed with `uv` and pinned in `uv.lock`. Add one by editing
`pyproject.toml` and running `uv lock`, then `docker compose up --build`.

## Layout

```
config/settings/       base.py, then local.py / uat.py / prod.py chosen by DJANGO_ENV
pharmacy/models/       one module per model; UUID pk, timestamps, soft delete in base.py
pharmacy/repositories/ same split; the only place a query is written (base.py: Repository)
pharmacy/services/     same split; every write and every rule (base.py: CrudService)
pharmacy/serializers/  same split; the live-duplicate check in base.py
pharmacy/views/        same split; adapters over the two above, nested routes in base.py
pharmacy/admin/        same split; the Django admin as another adapter (base.py: ServiceModelAdmin)
pharmacy/filters.py    every list filterset
pharmacy/validators.py NPI check digit, NDC and NCPDP normalization
pharmacy/exceptions.py the error envelope and every 409
pharmacy/tests/        factories.py and conftest.py fixtures, then one module per area
e2e/                   curl-driven black-box suite; bash and curl, no imports from pharmacy/
plans/                 design documents, some unimplemented; the code is the source of truth
```

One module per model, and the same name across the five packages. A new
resource means a matching file in `models/`, `repositories/`, `services/`,
`serializers/` and `views/`, a filterset in `filters.py`, and a route in
`pharmacy/urls.py`. `tests/test_layering.py` checks the first three line up.

Most resources ship two serializers: `XSerializer` for the resource's own
endpoints and `XSummarySerializer` for the nested read on another resource.
Writes take flat ids (`patient_id`), reads return the nested summary, so
rendering a prescription is one request.

## Conventions that bite

**Views get querysets from repositories and call services for anything that
changes state. Nothing outside `pharmacy/repositories/` names a manager**
(`objects`, `all_objects`, or a related manager) -- `test_layering.py` fails
the build on one. Reads do not go through services: a GET has queries and no
rules, and a service read would be an empty pass-through; what a read must
not do is define its own queryset, because the locked re-read in a write path
and the list read have to share one definition or their joins drift. A
repository returns querysets (DRF's filtering, pagination and `nested_list`
need one) and owns the `select_related` for its model; a service owns the
transaction, the lock, the refusals and the delete guard
(`protected_relations` lives on the service); a viewset declares `repository`
and `service` and never calls `serializer.save()`. The admin
(`pharmacy/admin/`, one module per model) is held to the same rule:
`ServiceModelAdmin` reads through the repository and routes `save_model` and
`delete_model` through the service, and turns a service refusal into a
message rather than a 500. Every service write takes
a `WriteContext` first, built once per request by the base viewset. Model
methods do not persist. Migrations, `seed_local` and tests use the ORM
directly; that is where the rule stops.

**Re-export every model.** Django resolves `app_label` from the package a model
is imported into, so a model missing from `pharmacy/models/__init__.py` breaks
`makemigrations` rather than failing loudly. Same for serializers and views.

**Soft delete, not `DELETE`.** Every model inherits `TimeStampedModel`
(`SOFT_DELETE`). `objects` is live rows only, `all_objects` is everything;
querysets and viewsets use `objects`. Because safedelete bypasses Django's
`on_delete`, `PROTECT` never fires on a soft delete — a viewset that must refuse
an orphaning delete declares `protected_relations` on `SoftDeleteModelViewSet`
instead. `SOFT_DELETE_CASCADE` is deliberately unused.

**Uniqueness is partial and invisible to DRF.** The live-row unique constraints
are `UniqueConstraint(condition=...)`, which DRF generates no validator for, so
the check is explicit: call `reject_live_duplicate()` from the serializer. Also
register the constraint name in `LIVE_UNIQUE_CONSTRAINTS` so the `IntegrityError`
from a concurrent create maps to the same 409 rather than a 500. This is what
lets a soft-deleted record's NPI, NDC, NCPDP ID or store number be reused.

**Raise, never hand-build, an error body.** `pharmacy.exceptions.exception_handler`
flattens everything into `{"error": {code, message, details}}`. Use `Conflict(...)`
with an explicit `code=` for a 409 and `duplicate(...)` for a duplicate
identifier; a bare `Conflict` flattens to the code `"conflict"`. Details keys
are named for what a client would guess, and the same key means the same thing
across endpoints (e.g. `details.status` for the current status).

**Nested routes reuse the child viewset.** `/patients/{id}/prescriptions/` goes
through `nested_list(PrescriptionViewSet, request, patient=...)` so the child's
`select_related`, filterset, search, ordering and pagination stay defined once.
Do not re-implement a child list on the parent.

**Sub-resources are scoped by the URL.** Locations and staff live under
`/pharmacies/{pharmacy_id}/` with full CRUD: `PharmacyScopedViewSet` resolves
the parent (404 when unknown), narrows `get_queryset()` before any filter, and
attaches it on create, so the serializer has no `pharmacy_id` field. The
routes are laid out by `sub_resource()` in `pharmacy/urls.py`, not the router.
A read-only child list (`nested_list`) and a sub-resource are different
things; use the former when the child has its own top-level resource.

**`is_active` is not soft delete.** An inactive staff member has left but
stays resolvable because records name them; `DELETE` is for rows that should
never have existed. `PharmacyLocation.pharmacist_in_charge` is the board
designation, validated when set and never inferred from anything else.

**Every write is audited, from the service.** `services.history` diffs a
row before and after and writes a `FieldChange` per changed column in the
writer's transaction; `CrudService` and the prescription and fill services
call it, nothing else does. A write that bypasses the services leaves no
history, which is one more reason nothing bypasses them.

**Totals are derived, never stored.** What a prescription has dispensed is
the sum over its fills, computed on read (the fills are prefetched) and under
the lock in a write path. The authorized-quantity rule is a service rule held
by the prescription's row lock, not a CHECK; the database constraints on a
fill are single-row facts only. `is_partial` is derived the same way.

**`X-Staff-Id` is resolved in one place**, `ServiceViewSetMixin.ctx`, into
`WriteContext.staff`. Services that need a person read `ctx.staff`; nothing
reads the header. When a token carries staff identity, that is the seam.

**Identifiers are normalized on the way in**, in `validators.py`: an NPI must
carry a valid Luhn check digit, an NDC in any segmented form is stored as 11
digits, an NCPDP ID as 7. ASCII digits only.

**Migrations are never part of server start.** The image has no entrypoint; the
`migrate` Compose service runs them once per `up`, and deployed tiers run
`migrate --noinput` as a pre-deploy job. Replicas that migrate on boot race on
the same DDL. Every model change needs its migration in the same commit — CI
fails otherwise.

**Settings never fall back to local values.** `config/settings/base.py` does not
read `.env.local`; `uat` and `prod` read the process environment only and refuse
to start with anything missing, and `prod` additionally refuses the committed
`SECRET_KEY`, `DEBUG=true` and a wildcard `ALLOWED_HOSTS`. A new variable goes in
`.env.example` (contract, no values) and, if local needs it, `.env.local`.
`.env.local` is committed on purpose: every value in it is a throwaway valid
nowhere else. Never put a real secret there.

**Schema stays clean.** Viewsets are tagged through `views/tags.py`; custom
actions carry `@extend_schema`. `spectacular --fail-on-warn` must pass.

## Tests

`pytest-django` with `DJANGO_SETTINGS_MODULE=config.settings.local`. Modules set
`pytestmark = pytest.mark.django_db` and group cases in classes by behavior.
Build data with the factories in `pharmacy/tests/factories.py` and the fixtures
in `conftest.py` (`api` is an authenticated client, `anon` is not) rather than
creating models inline. People and places come from Faker (reseeded once per
session in `conftest.py`, so runs are reproducible); identifiers that must be
unique or valid -- NPI, NDC, NCPDP, Rx numbers -- come from sequences. A test
that needs a particular name sets it explicitly. `seed_local` uses Faker the
same way, from a fixed seed, so a re-run finds the same people. Prefer
`@pytest.mark.parametrize` over near-duplicate tests. A refused write is
tested on the status code *and* the error `code`.

The `e2e/` suite is the other half and is written in bash, not Python. It runs
against a served stack over real HTTP, so it is where authentication, the error
envelope on the wire and the rate limiter are actually exercised. It never
imports from `pharmacy/` and never cleans up after itself, which is why every
unique value it sends is generated per run. `e2e/README.md` has the conventions;
follow them rather than inventing a second style there.

## Style

Ruff is the only formatter and linter: line length 100, `E,F,I,UP,B,DJ,C4`,
migrations excluded. Comments explain why a non-obvious choice was made, not
what the line does — match the density already in the file. Docstrings on
modules and non-trivial classes and helpers.

## Commits

Imperative, sentence case, no `feat:`-style prefix, and about the change rather
than the file: *Serve nested list routes through the child viewset*. A body is
for the reasoning when it is not obvious, wrapped at 72 columns. Branch off
`main`; do not commit or push unless asked.
