# Development

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

## Running the suite without Docker

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

## Layout

```
config/settings/     base.py, then local.py / uat.py / prod.py chosen by DJANGO_ENV
pharmacy/models/     one module per model; soft delete and UUID ids in base.py
pharmacy/serializers/  same split; duplicate checks in base.py
pharmacy/views/      same split; nested routes and delete guards in base.py
pharmacy/filters.py  the list filters in docs/api.md
pharmacy/validators.py  NPI check digit, NDC and NCPDP normalization
pharmacy/exceptions.py  the error envelope and every 409
pharmacy/management/commands/seed_local.py  the superuser and demo data
docs/                this documentation
plans/               design documents; the code is the source of truth where they differ
```

Identifiers are validated and normalized on the way in: an NPI must carry a
valid check digit, an NDC in any segmented form is stored as 11 digits, an NCPDP
ID as 7. Unique constraints are partial and cover live rows only, which is what
lets a soft-deleted record's identifier be reused.
