# Logto

The default identity provider adapter, `pharmacy.auth.providers.logto.LogtoProvider`.
What it knows about Logto: the issuer is `{endpoint}/oidc`, the keys are at
`{issuer}/jwks` (used as a fallback when OIDC discovery is unreachable), and
a fresh install signs with EC P-384. Configured as below, Logto emits the
identity claims contract directly, so the adapter maps nothing.

## Settings

| Variable | Value |
|---|---|
| `AUTH_PROVIDER` | `pharmacy.auth.providers.logto.LogtoProvider` (the default) |
| `AUTH_ISSUER` | the instance's public endpoint plus `/oidc`, e.g. `https://auth.example.com/oidc` -- what Logto writes into a token's `iss` |
| `AUTH_INTERNAL_URL` | where this service reaches the instance when that is not the public URL (`http://auth:3001` under Compose) |
| `AUTH_AUDIENCE` | the API resource indicator below |

## Configuring the instance

In the console (or through the Management API), under the tenant the API
trusts:

1. **An API resource** whose indicator is `AUTH_AUDIENCE`, token TTL 900 s,
   with every scope in `pharmacy/auth/scopes.py` created on it by name.
2. **Roles** named as `StaffRole` values, each granted the scopes
   `ROLE_SCOPES` lists.
3. **An application** for the UI (single-page, PKCE) that requests the
   resource and its scopes at sign-in.
4. **The access-token JWT customizer**, which is how Logto puts the
   contract into the token. `name` and `profile.givenName`/`familyName` are
   Logto's own user fields; `customData` holds the rest:
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
       supervising_user_id: user.customData?.supervising_user_id,
     };
   };
   ```
   Logto issues a token even when the customizer throws, so the API, not
   Logto, refuses a token without `email` (401 `token_without_email`).
5. **Users**: primary email, given and family name, one or more roles, and
   `custom_data` `{pharmacy_id, registration_state, registration_number}`
   (`pharmacy_id` is this API's UUID for the employer; not needed for an
   admin-only user; `supervising_user_id` -- the supervisor's Logto user id,
   shown in the console's user list -- for an intern).

## Changing a person's details

Identity per subject is immutable in the API (see [staff.md](../staff.md)),
so a change of email, name, role or registration is a new Logto user:
suffix the old user's primary email with `-DEACTIVATED`, suspend the user
(`PATCH /api/users/{id}/is-suspended`), and create the new one. Logto
enforces a unique primary email, which is what the suffix is for; a
suspended user is refused a token. Nothing to do in the API: the new user's
first request creates the successor record and retires the old one.

## Locally

`docker compose --profile auth up` runs Logto as the `auth` service (the
profile keeps it out of the default stack, which the API does not need it
for), seeded with `--dapc`
(the admin tenant's password policy otherwise consults
api.pwnedpasswords.com, which an offline host cannot reach), with its
console on `http://127.0.0.1:8442` (127.0.0.1 rather than localhost: see
[../development.md](../development.md), Logto blocks a `localhost` origin
on its management API) and the first admin created by the
one-shot `auth-seed` service from `AUTH_ADMIN_USERNAME` /
`AUTH_ADMIN_PASSWORD` in `.env.local`. Logto selects the admin tenant by the
request's `Host` header matching `ADMIN_ENDPOINT`, which is why that seed
sends the public host while travelling to the internal address.
