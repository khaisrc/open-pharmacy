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
| `AUTH_PROVIDER` | | the identity provider adapter; default `pharmacy.auth.providers.logto.LogtoProvider` |
| `AUTH_ISSUER` | yes | the provider's issuer URL, what it writes into a token's `iss` |
| `AUTH_INTERNAL_URL` | | where this service reaches the provider for discovery and keys when that is not the public URL -- the Compose network locally, a private address behind a proxy |
| `AUTH_AUDIENCE` | yes | this API's identifier at the provider, the token audience |
| `AUTH_DEV_SIGNING_KEY` | local only | a PEM key `mint_token` signs dev tokens with; read by `local.py` alone |

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
set at or above the highest number it already holds. It is set through the
API -- `rx_number_seed` and `step` on `POST` or `PATCH /pharmacies/{id}/`
-- or in the admin's RxCounter page.

## The identity provider

Users are created at the identity provider, sign in through the UI, and the
UI sends the access token on every request. The API verifies the token
against the provider's published keys and reads the **identity claims
contract** out of it (`pharmacy/auth/claims.py`): `sub`, `email`, the OIDC
profile names, `roles`, `scope`, `pharmacy_id`, the board registration, the
technician certification, and `supervising_user_id` for an intern. Nothing in the service knows which
product is behind that; an adapter in `pharmacy/auth/providers/` knows
where the provider publishes its keys and how its native claims map onto
the contract. `AUTH_PROVIDER` names the adapter; `AUTH_ISSUER` and
`AUTH_AUDIENCE` are the `iss` and `aud` a token must carry.

Whatever the provider, it needs, once:

1. **This API as an audience** (`AUTH_AUDIENCE`) with every scope in
   `pharmacy/auth/scopes.py` defined on it by name, and access tokens of
   about 15 minutes.
2. **Roles** named `admin`, `pharmacist`, `certified_technician`,
   `technician`, `intern`, each granted the scopes `ROLE_SCOPES` in that
   module lists. A user may hold several.
3. **An application** for the UI that requests this audience and its
   scopes at sign-in.
4. **Tokens that carry the contract** -- every provider has its own
   mechanism for adding claims; the adapter's page says how.
5. **Users** with a primary email, given and family name, one or more
   roles, and the employer / registration / supervisor data the contract
   needs. The API refuses a token that cannot make a valid staff record, so
   completeness is enforced by refusal.

Signing keys rotate at the provider without a redeploy: the API refreshes
its cached key set on an unknown key id.

Adapters and their configuration: [Logto](identity-providers/logto.md)
(the default; locally `manage.py seed_auth` configures it). Adding another is a subclass of `OIDCProvider` that
overrides `claims()` where the native layout differs, plus a page like
Logto's.

## Serving

The image's default command is `runserver` for local use. Deployed tiers
override it with gunicorn, which is installed:

```bash
gunicorn config.wsgi --bind 0.0.0.0:8000
```

Both deployed tiers redirect to HTTPS, since Basic Auth over plain HTTP would
leak credentials. `GET /api/v1/health/` returns 200 with
`{"status": "ok", "database": "ok"}`, or 503 when the database is unreachable.
