# Development

There is no interpreter or database on the host, so every command runs through
Compose. `docker compose run --rm web …` starts the database, runs the one-shot
`migrate` service, then runs the command in a throwaway container.

```bash
docker compose up                       # serve, with migrations and seed
docker compose up --build               # after changing dependencies
docker compose --profile auth down -v   # stop everything and drop the database
docker compose logs -f web

docker compose run --rm web pytest
docker compose run --rm web pytest pharmacy/tests/test_fills_api.py
docker compose run --rm web ruff check .
docker compose run --rm web ruff format .
docker compose run --rm web python manage.py makemigrations
docker compose run --rm web python manage.py spectacular --fail-on-warn
docker compose exec db psql -U pharmacy open_pharmacy
```

The test suite runs against PostgreSQL 18, the same engine as production, in a
database pytest creates and drops. CI (`.github/workflows/test.yml`) runs the
suite, ruff, and `makemigrations --check` through the same Compose file.

A second suite lives in `e2e/`: bash and curl against a running server, logging
in with a real password and importing nothing from `pharmacy/`. It is the
client's-eye view that `pharmacy/tests/` cannot give, since those drive the
same endpoints in process with authentication forced. Start the stack, then
run it from the host:

```bash
docker compose up -d
./e2e/run.sh                            # every case file, eight at a time
./e2e/run.sh 65_fills                   # one case file
./e2e/run.sh -f prescriptions -p 1      # one file, streaming as it goes
```

It writes `e2e/report.html`, a self-contained Gherkin-shaped record of every
request and response the run made. CI (`.github/workflows/e2e.yml`) runs the
same thing against the same Compose stack and attaches that report to the run.
`e2e/README.md` covers the rest.

## The identity server locally

The identity server, the `auth` service (Logto), sits behind the `auth`
Compose profile, so a plain `docker compose up` leaves it out. `make start`
turns the profile on, and `NO_AUTH=1` turns it back off:

```bash
docker compose --profile auth up    # or: make start
docker compose up                   # or: make start NO_AUTH=1
docker compose --profile auth stop  # or: make stop; keeps the database and Logto's state
```

The API does not need it to start. It fetches the provider's keys on the
first request that presents a provider token, and a token from
`manage.py mint_token` is verified against `AUTH_DEV_SIGNING_KEY` without any
fetch at all -- which is how the unit tests, the e2e suite and CI work. Start
the profile when you want the console, or to sign in against the real
provider.

It runs against a second database on the same Postgres: the console on
`http://127.0.0.1:8442`, tokens from `http://localhost:8441`. Both host
ports come from the shell (`AUTH_PORT`, `AUTH_CONSOLE_PORT`) like
`WEB_PORT`; `AUTH_ISSUER` in `.env.local` must name the same port as
`AUTH_PORT`, since the provider writes it into every token's issuer.

Open the console at `127.0.0.1`, not `localhost`. The console is served on
one port and calls the default tenant's management API on the other, and
Logto sends the CORS headers for that cross-origin call only when the
browser's origin matches its `ADMIN_ENDPOINT`. Logto's own start script
forces `NODE_ENV=production`, and in production it drops any origin whose
hostname is the literal string `localhost` as unsafe. So `ADMIN_ENDPOINT`
names `127.0.0.1` -- the same host, but a hostname that survives the check.
Reaching the console on `localhost:8442` instead loads the page and signs
you in, then fails every management call with a CORS error in the browser
console:

    Access to fetch at 'http://localhost:8441/api/users' from origin
    'http://localhost:8442' has been blocked by CORS policy

The one-shot `auth-seed` service (`manage.py seed_auth`) creates the
console's first admin from `AUTH_ADMIN_USERNAME` / `AUTH_ADMIN_PASSWORD` in
`.env.local` (`admin` / `Passw0rd!`), configures the provider for this API
-- the resource, its scopes, the roles, the token customizer, the UI
application -- and creates the [test users](identity-providers/test-users.md)
(two pharmacists, five certified technicians, five technicians, two interns,
two admins; all `Passw0rd!`), checking each one's token the way the API
reads it. A re-run finds everything and changes nothing. Logto opens the
first admin's registration only until an admin exists, so to change the
username, drop the `auth` database (`docker compose --profile auth down -v`; without the
profile, `down` leaves the running `auth` container behind). First boot:
open the console and sign in as the seeded admin to create users; the rest
is already configured ([identity-providers/logto.md](identity-providers/logto.md)).
For curl as one of the seeded users, ask the provider for their token
(`make -s token AS=elena_ortiz`; see
[identity-providers/test-users.md](identity-providers/test-users.md#getting-a-token)).
For an identity of your own, without the provider, mint a token the API
accepts locally:

```bash
docker compose run --rm web python manage.py mint_token --generate-key   # once; paste into .env.local
docker compose run --rm web python manage.py mint_token --email rph@example.local \
    --roles pharmacist --pharmacy <pharmacy uuid> --registration NY RPH.023417
```

The key is read by `local.py` only, so the command and the tokens it mints
mean nothing outside local.

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
pharmacy/repositories/  same split; the only place a query is written
pharmacy/services/   same split; every write and every rule
pharmacy/serializers/  same split; duplicate checks in base.py
pharmacy/views/      same split; adapters: parse, call the service, serialize
pharmacy/admin/      same split; the Django admin as another adapter over the services
pharmacy/filters.py  the list filters in docs/api.md
pharmacy/validators.py  NPI check digit, NDC and NCPDP normalization
pharmacy/exceptions.py  the error envelope and every 409
pharmacy/management/commands/seed_local.py  the superuser and demo data
docs/                this documentation
plans/               design documents; the code is the source of truth where they differ
e2e/                 curl-driven end-to-end suite, run from the host against a served stack
```

Identifiers are validated and normalized on the way in: an NPI must carry a
valid check digit, an NDC in any segmented form is stored as 11 digits, an NCPDP
ID as 7. Unique constraints are partial and cover live rows only, which is what
lets a soft-deleted record's identifier be reused.

## Layers

A request goes view → service → repository → model. Views read through
`repository.all()` and call a service for anything that changes state; the
service owns the transaction, the row lock and every refusal; the repository
is the only place a query is written. `pharmacy/tests/test_layering.py`
enforces it. The reasoning, and where the rule stops, is in `AGENTS.md`.
