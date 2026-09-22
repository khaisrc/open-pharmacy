# Configuration and deployment

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

## Migrations

**Migrations are a separate step, never part of server start.** Locally the
`migrate` Compose service runs them once per `up`. In uat and prod, run
`python manage.py migrate --noinput` as a one-shot job before starting the new
replicas; replicas that migrate on boot race each other on the same DDL.

## Onboarding a pharmacy

A prescription's Rx number comes from its pharmacy organization's counter,
the `RxCounter` row (`pharmacy`, `last_issued`, `step`). Until that row
exists with `last_issued` set, `POST /prescriptions/` for any of the
pharmacy's stores answers 409 `rx_counter_not_seeded`.

Locally, `seed_local` onboards every pharmacy it knows -- the Acme home
pharmacy from migration 0003 and the two demo chains -- with `last_issued`
at 1,000,000,000, and never rewinds one on a re-run. In uat and prod no
migration creates the row: the starting value is a fact about the pharmacy,
set at or above the highest number it already holds, and belongs to the
onboarding step alongside the pharmacy's own record.

## Serving

The image's default command is `runserver` for local use. Deployed tiers
override it with gunicorn, which is installed:

```bash
gunicorn config.wsgi --bind 0.0.0.0:8000
```

Both deployed tiers redirect to HTTPS, since Basic Auth over plain HTTP would
leak credentials. `GET /api/v1/health/` returns 200 with
`{"status": "ok", "database": "ok"}`, or 503 when the database is unreachable.
