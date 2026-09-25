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
| `LOGTO_ENDPOINT` | yes | the Logto instance's public URL; a token's issuer must be `{LOGTO_ENDPOINT}/oidc` |
| `LOGTO_INTERNAL_ENDPOINT` | | where this service fetches Logto's keys (`…/oidc/jwks`) when that is not the public URL -- the Compose network locally, a private address behind a proxy |
| `LOGTO_API_RESOURCE` | yes | this API's resource indicator in Logto, the token audience |
| `LOGTO_DEV_SIGNING_KEY` | local only | a PEM key `mint_token` signs dev tokens with; read by `local.py` alone |

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

## The identity server

Users are created in Logto, sign in through the UI, and the UI sends the
access token on every request. Logto needs, once, under the tenant the API
trusts:

1. **An API resource** whose indicator is `LOGTO_API_RESOURCE` (token TTL
   900 s), with every scope in `pharmacy/auth/scopes.py` created on it by
   name.
2. **Roles** named `admin`, `pharmacist`, `certified_technician`,
   `technician`, `intern`, each granted the scopes `ROLE_SCOPES` in that
   module lists. A user may hold several.
3. **An application** for the UI (single-page, PKCE) that requests the
   resource and its scopes at sign-in.
4. **The access-token JWT customizer**, so the token carries what the API
   needs to know who is acting. `name` and `profile.givenName`/`familyName`
   are Logto's own user fields; `customData` holds the rest:
   ```text
   const getCustomJwtClaims = async ({ token, context }) => {
     const user = context?.user;
     if (!user) { return {}; }
     return {
       email: user.primaryEmail,
       email_verified: Boolean(user.primaryEmailVerified),
       name: user.name,
       given_name: user.profile?.givenName,
       family_name: user.profile?.familyName,
       roles: (user.roles ?? []).map((role) => role.name),
       pharmacy_id: user.customData?.pharmacy_id,
       registration_state: user.customData?.registration_state,
       registration_number: user.customData?.registration_number,
       supervising_sub: user.customData?.supervising_sub,
     };
   };
   ```
   Logto issues a token even when the customizer throws, so the API, not
   Logto, refuses a token without `email` (401 `token_without_email`).
5. **Users**: primary email, given and family name, one or more roles, and
   `custom_data` `{pharmacy_id, registration_state, registration_number}`
   (`pharmacy_id` is this API's UUID for the employer; not needed for an
   admin-only user; `supervising_sub` for an intern). A token that cannot
   make a valid staff record is refused, so the console is where
   completeness shows.

Signing keys rotate in Logto's console without a redeploy: the API refreshes
its cached key set on an unknown key id.

## Serving

The image's default command is `runserver` for local use. Deployed tiers
override it with gunicorn, which is installed:

```bash
gunicorn config.wsgi --bind 0.0.0.0:8000
```

Both deployed tiers redirect to HTTPS, since Basic Auth over plain HTTP would
leak credentials. `GET /api/v1/health/` returns 200 with
`{"status": "ok", "database": "ok"}`, or 503 when the database is unreachable.
