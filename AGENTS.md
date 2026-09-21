# AGENTS.md

Working notes for coding agents. `README.md` documents the API and the domain;
this file covers how to run things and the conventions that are easy to violate.
Where they disagree, the code wins.

## Running commands

There is no interpreter or database on the host. Every command goes through
Compose, which starts PostgreSQL, runs the one-shot `migrate` service, then runs
the command in a throwaway container:

```bash
docker compose run --rm web pytest
docker compose run --rm web pytest pharmacy/tests/test_prescriptions_api.py -k fill
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
`ruff format --check .`, and `makemigrations --check --dry-run`.

Dependencies are managed with `uv` and pinned in `uv.lock`. Add one by editing
`pyproject.toml` and running `uv lock`, then `docker compose up --build`.

## Layout

```
config/settings/       base.py, then local.py / uat.py / prod.py chosen by DJANGO_ENV
pharmacy/models/       one module per model; UUID pk, timestamps, soft delete in base.py
pharmacy/serializers/  same split; the live-duplicate check in base.py
pharmacy/views/        same split; delete guards and nested routes in base.py
pharmacy/filters.py    every list filterset
pharmacy/validators.py NPI check digit, NDC and NCPDP normalization
pharmacy/exceptions.py the error envelope and every 409
pharmacy/tests/        factories.py and conftest.py fixtures, then one module per area
plans/                 design documents, some unimplemented; the code is the source of truth
```

One module per model, and the same name across the three packages. A new
resource means a matching file in `models/`, `serializers/` and `views/`, a
filterset in `filters.py`, and a route in `pharmacy/urls.py`.

Most resources ship two serializers: `XSerializer` for the resource's own
endpoints and `XSummarySerializer` for the nested read on another resource.
Writes take flat ids (`patient_id`), reads return the nested summary, so
rendering a prescription is one request.

## Conventions that bite

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
`pytestmark = pytest.mark.django_db` and group cases in classes by behaviour.
Build data with the factories in `pharmacy/tests/factories.py` and the fixtures
in `conftest.py` (`api` is an authenticated client, `anon` is not) rather than
creating models inline. Prefer `@pytest.mark.parametrize` over near-duplicate
tests. A refused write is tested on the status code *and* the error `code`.

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
