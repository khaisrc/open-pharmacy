# open-pharmacy

A REST API over the pharmacy domain — prescriptions, and the patients,
prescribers, medications, pharmacies and addresses they reference.

## Quickstart

```bash
git clone <repo> && cd open-pharmacy
docker compose up
```

That is the whole procedure. The only prerequisite is **Docker** with Compose
v2 — no Python, no `uv` and no PostgreSQL on the host. Compose starts
PostgreSQL 18, builds the image, migrates, seeds a superuser plus demo data,
and serves the API.

```bash
curl -u admin:admin http://localhost:8000/api/v1/prescriptions/
open http://localhost:8000/api/v1/docs/
```

**Port already in use?** The published port comes from `WEB_PORT` in
`.env.local`. Change it there; nothing in the code needs touching. The
container always listens on 8000 internally.

## Everything runs in the container

There is no interpreter, no dependency set and no reachable database on the
host, so every command goes through Compose:

```bash
docker compose up                       # API on :8000, Swagger at /api/v1/docs/
docker compose up --build               # after changing dependencies
docker compose down                     # stop; add -v to drop the database volume
docker compose logs -f web

docker compose run --rm web pytest
docker compose run --rm web ruff check .
docker compose run --rm web ruff format .
docker compose run --rm web python manage.py makemigrations
docker compose run --rm web python manage.py seed_local
docker compose run --rm web python manage.py spectacular --fail-on-warn
docker compose exec db psql -U pharmacy open_pharmacy
```

`docker compose run --rm web …` starts the database first and removes the
one-off container afterwards, so none of these need anything running already.

## One database engine everywhere

**PostgreSQL 18 backs every tier, including local development and the test
suite.** The engine the tests exercise is the engine that runs in production,
so the migration path, collation and ordering behaviour, and constraint timing
are verified by an ordinary local run rather than deferred to a deployment.

The backend is selected entirely by `DATABASE_URL`. There is no second settings
module, no vendor branch in the code and no test-only config file.

| Tier | Database | Secrets from |
|---|---|---|
| local | the `db` Compose service | `.env.local`, committed |
| uat / prod | a managed PostgreSQL | the process environment |

`.env.local` is committed deliberately so a fresh clone needs no setup. Every
value in it is a throwaway that is valid nowhere else. `.env.example` documents
the variable contract uat and prod must satisfy.

## Authentication

HTTP Basic against Django's `auth.User`, with `IsAuthenticated` globally.
`/api/v1/health/`, `/schema/`, `/docs/` and `/redoc/` are open. There is **no
authorization** — any authenticated caller can read and write any record,
including patient PHI. That is a deliberate interim state; JWT and the
authorization model are scoped in `plans/auth/plan.md`.

Swagger's **Authorize** button works with the seeded `admin` / `admin`.

## Layout

The whole domain lives in one Django app, `pharmacy`. Models, serializers and
views are packages with one module per resource rather than single large files.

```
config/settings/{base,local,uat,prod}.py   base never reads .env.local
pharmacy/models/        one module per model, re-exported from __init__
pharmacy/serializers/   same split
pharmacy/views/         same split, plus health
pharmacy/validators.py  NPI check digit, NDC and NCPDP normalization
pharmacy/exceptions.py  Conflict (409) and the single error envelope
```

Nothing is ever hard-deleted: `DELETE` soft-deletes and stays recoverable via
`all_objects` / `undelete()`. Unique constraints are partial, scoped to live
rows, so a closed store's NCPDP ID or a discontinued product's NDC can be
reused.
