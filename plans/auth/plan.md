# Authentication: bearer tokens, scopes, and Staff as the identity record

Status: **implemented**.

Builds on `plans/rx-tx-fills/` (the service layer, `WriteContext`, the
change history, the fill workflow) and `plans/rx-tx-staff/` (the `Staff`
model).

## 1. Scope

Every API request carries an access token issued by an identity provider.
The service verifies it against the provider's published keys, reads a
provider-neutral set of claims out of it, and treats the token's subject
as the person acting: the first token from a subject creates that person's
`Staff` record, later tokens must describe the same person, and what a
token may do is its scopes. The identity provider is Logto, behind an
adapter; nothing outside that adapter knows it.

Out of scope: single sign-on for the Django admin (it keeps Django
sessions), an API over the change history, and any link between
`auth.User` and `Staff`.

## 2. The identity model

### 2.1 The claims contract

`pharmacy/auth/claims.py` names what a verified token must say. It is the
API's vocabulary for who is acting, independent of the provider:

| Claim | Meaning |
|---|---|
| `sub` | the external user id; immutable for the user's life |
| `email` | required |
| `name`, `given_name`, `family_name` | standard OIDC profile |
| `roles` | list of `StaffRole` names |
| `scope` | space-separated scopes (RFC 9068) |
| `pharmacy_id` | the employer, a Pharmacy UUID; absent for an admin |
| `registration_state`, `registration_number` | the board registration |
| `certification_body`, `certification_number` | the national technician certification (PTCB / NHA) |
| `supervising_user_id` | an intern's supervisor, by external user id |

`Principal.from_claims` reads only these names. A provider adapter is what
turns a provider's native claim layout into them.

### 2.2 Provider adapters

`pharmacy/auth/providers/`:

```text
class IdentityProvider:
    issuer: str                       # the `iss` to require
    audience: str                     # the `aud` to require
    algorithms: ("ES384", "RS256")    # the allow-list; never `none`, never symmetric
    internal_url: str | None          # where this service reaches the provider, if not the issuer
    def jwks_uri(self) -> str
    def claims(self, raw: dict) -> dict   # native claims -> the contract
```

- `OIDCProvider` — the generic adapter. `jwks_uri()` comes from
  `{issuer}/.well-known/openid-configuration`, fetched once per process;
  `claims()` is the identity mapping, for a provider configured to emit the
  contract directly. With `internal_url` set, discovery and the keys are
  fetched there while the issuer stays the public URL a token carries (the
  Compose network; a private subnet behind a proxy). An unreachable
  provider raises `DiscoveryUnavailable`, reported as a 503, because the
  token was not judged.
- `LogtoProvider` — the default: `OIDCProvider` plus a fallback to
  `{issuer}/jwks`, Logto's documented key path, if discovery is down. It
  inherits `claims()` unchanged because Logto's JWT customizer (§8) emits
  the contract. **This file is the only code that knows anything about
  Logto.**
- A provider with its own layout — Keycloak's `realm_access.roles`, Auth0's
  namespaced claims — is a subclass that overrides `claims()`, under forty
  lines, plus a page under `docs/identity-providers/`.

`AUTH_PROVIDER` (a dotted path) chooses the adapter; `get_provider()` builds
it once from `AUTH_ISSUER`, `AUTH_AUDIENCE` and `AUTH_INTERNAL_URL`.

### 2.3 Verification and the principal

- `TokenVerifier` (`pharmacy/auth/verifier.py`): `PyJWKClient(provider.jwks_uri())`,
  built lazily so discovery happens on the first token; `jwt.decode` with
  the adapter's algorithms, issuer and audience, 60 s leeway, and `exp`,
  `iat`, `sub`, `aud`, `iss` required. An unknown `kid` triggers one JWKS
  refresh, so a key rotation at the provider needs no redeploy. `key_set=`
  is the tests' seam: an in-memory `PyJWKSet` instead of a fetch. A token
  whose `kid` is the dev kid is checked against `AUTH_DEV_SIGNING_KEY`'s
  public half when local sets one (§7).
- `Principal` (`principal.py`): frozen — `external_user_id`, `email`,
  `name`, `given_name`, `family_name`, `roles`, `pharmacy_id`,
  `registration_state`, `registration_number`, `certification_body`,
  `certification_number`, `supervising_external_user_id`, `scopes`,
  `staff`, `django_user`.
  `role` is the highest of `roles` by `ROLE_PRECEDENCE`
  (`admin > pharmacist > certified_technician > technician > intern`, in
  `models/staff.py`). `pk` is the subject, so DRF's throttle buckets per
  identity.
- `BearerTokenAuthentication` (`authentication.py`): reads the bearer
  header, verifies, maps the claims through the adapter, builds the
  principal, refuses a token without `email`, and resolves the `Staff` row
  (§4.2). First in `DEFAULT_AUTHENTICATION_CLASSES`; a request without a
  bearer header falls through to Basic or session (§5). Failure codes,
  rendered in the one error envelope: `token_missing`, `token_invalid`,
  `token_expired`, `token_without_email`, `identity_incomplete`,
  `identity_changed`, `supervisor_required`; `identity_unavailable` (503).
- `BearerScheme` (`schema.py`) describes the scheme to drf-spectacular.

Settings: `AUTH_PROVIDER`, `AUTH_ISSUER`, `AUTH_INTERNAL_URL`,
`AUTH_AUDIENCE`, and local-only `AUTH_DEV_SIGNING_KEY`. uat and prod
require issuer and audience through `require_settings`.

## 3. Scopes and roles

`pharmacy/auth/scopes.py` names every scope once; the same names are
defined at the provider and granted to its roles. `HasScope` checks a
viewset's `required_scopes` per action — a principal holding any one of an
action's scopes passes; an action with no entry fails closed — and refuses
with 403 `insufficient_scope`, `details.required` naming what was needed.
`read` covers list, detail and the nested child lists (a nested list needs
the child's read scope); `write` covers create, update, delete and a
resource's actions.

| Resource | Scopes |
|---|---|
| prescriptions | `prescriptions:read`, `prescriptions:create`, `prescriptions:update` (PATCH, PUT, delete, `cancel/`, always), `prescriptions:update_unfilled` (the same, only while no fill has been started) |
| fills | `fills:read`, `fills:write` (create, PATCH, `unverify/`, `complete/`, `cancel/`), `fills:verify` |
| patients | `patients:read`, `patients:write` |
| prescribers | `prescribers:read`, `prescribers:write` |
| medications | `medications:read`, `medications:write` |
| pharmacies | `pharmacies:read`, `pharmacies:write`, `pharmacies:onboard` (`rx_number_seed`, `step`) |
| locations | `locations:read`, `locations:write` |
| addresses | `addresses:read`, `addresses:write` |
| staff | `staff:read`, `staff:write` |

Twenty-two. The roles, named as `StaffRole` values (`ROLE_SCOPES`):

| Role | Scopes |
|---|---|
| `admin` | all twenty-two; a superuser employed by no particular pharmacy |
| `pharmacist` | all twenty-two |
| `certified_technician`, `technician` | every `:read`; `prescriptions:create`, `prescriptions:update_unfilled`; `fills:write`; `patients:write`; `addresses:write` |
| `intern` | a technician's scopes, and the token must carry `supervising_user_id` (§4.4) |

A user may hold several roles; the provider grants the union of their
scopes and caps a token's `scope` at what the roles grant. Deliberately not
separate: `delete` (a soft delete, under the update scopes) and the change
history (no API).

**The unfilled rule** is the one state-dependent permission: a principal
holding `prescriptions:update_unfilled` but not `prescriptions:update` may
change a prescription only while no fill is `pending`, `verified` or
`completed` — a cancelled fill does not count. `PrescriptionService.update`,
`delete` and `cancel` apply it under the prescription's row lock and refuse
with 403 `insufficient_scope`, `details.required = ["prescriptions:update"]`,
`details.reason = "a fill has been started"`. It is keyed on the scopes the
principal holds, never on a role: the API never reasons about roles for
permissions. `pharmacies:onboard` is enforced the same way in
`PharmacyService` for `rx_number_seed` and `step`.

## 4. `Staff` as the identity record

### 4.1 Schema

| Field | |
|---|---|
| `external_user_id` | `CharField(32)`, blank on a hand-typed row, unique among live rows (`uniq_live_staff_external_user_id`). The token's subject. A row without one is a directory entry that can be named but cannot act. |
| `email` | `EmailField`, unique among live rows when set (`uniq_live_staff_email`). |
| `roles` | `JSONField`, the token's `roles` in full. |
| `role` | the highest of `roles` by `ROLE_PRECEDENCE`; a column because filters and the rules key on it. |
| `pharmacy` | nullable: required for every role but `admin` (`credential_problems()` holds it when asked to). |
| `registration_state`, `registration_number`, `certification_body`, `certification_number` | `credential_problems()` applies: a `pharmacist` without a registration, or a `certified_technician` without a certification, is `identity_incomplete`. |
| `uniq_live_staff_registration`, `uniq_live_staff_npi` | over **active** live rows, so a successor identity can hold a registration once the old row is inactive. |

Other tables reference `Staff.id`, the UUID row id — `Fill.operator_id`,
`Fill.verified_by_id`, `FieldChange.changed_by_staff_id`,
`PharmacyLocation.pharmacist_in_charge_id` — never the external id, which
is one indexed read per request to turn a token into a row. The
pharmacist-in-charge rule reads `"pharmacist" in staff.roles`, so an admin
who is also a pharmacist may be designated.

### 4.2 First sight, then immutability

`services.staff.identify(principal)`, called by the authentication class on
every bearer request:

1. Find the row for `external_user_id`. If found, compare `email`,
   `first_name`, `last_name`, `roles`, `pharmacy_id`, the registration and
   the certification with the claims; any difference is 401
   `identity_changed` and nothing is written. Identity per subject is
   immutable by policy — a change of any of those is a new user at the
   provider — and this comparison is what enforces it, since a provider
   issues a token for an edited user just the same.
2. If not found, register: build the row from the claims
   (`identity_incomplete` if no recognised role, a non-admin without a
   known `pharmacy_id`, a pharmacist without a registration); deactivate
   any **active** row holding the same registration or NPI — the successor
   taking over — then `full_clean()`, insert, and record history for both
   with `changed_by_staff` = the new row. The row is its own first actor.
3. A row with `external_user_id` is read-only through the staff API and the
   admin except `is_active` (409 `identity_managed_externally`).

### 4.3 Changing a person's details

At the provider: deactivate the old user and create the new one with the
real email, profile, roles and custom data (Logto's steps are in
`docs/identity-providers/logto.md`). Nothing to do in the API: the new
user's first request registers the successor and retires the predecessor
(§4.2 step 2). An admin may also set `is_active` by hand. The person's
history is split across two subjects, which is the correct audit answer.

### 4.4 Interns

An intern's token must carry `supervising_user_id`, the subject of a
supervising pharmacist or technician, kept at the provider. The API checks
it is present on every request by a principal whose role is `intern` (401
`supervisor_required`) and stores nothing: who supervised whom, and when
that changed, is the provider's record. The supervisor is not part of the
intern's identity, so §4.2's comparison ignores it.

## 5. Basic Auth, sessions and the admin

A Django-authenticated actor — Basic on the API, a session in the admin —
becomes a `Principal` with `external_user_id = "django:<pk>"`, the user's
email, **every scope**, and `staff = None`. The change history records
`changed_by_user` for these and `changed_by_staff` for token actors
(exactly one, by CHECK). Starting or verifying a fill names a person, so
those two writes need a token principal: a Django actor gets 403
`staff_identity_required`. Everything else works for both. The admin keeps
Django sessions; `auth.User` stays in the schema and stops meaning anything
to the API beyond that.

## 6. Migration

`0010_staff_identity`: `external_user_id`, `email`, `roles` and their
partial unique constraints; `admin` in the `role` choices; `pharmacy`
nullable; the registration and NPI constraints over active rows;
`FieldChange.changed_by_user` nullable with the exactly-one CHECK. A
`RunPython` sets `roles = [role]` on existing rows, and another clears
`changed_by_user` on history rows that named both a login and a staff
member, so the CHECK holds on a database that already has history.

## 7. Local development and tests

**Compose**: the `auth` service is Logto (`svhd/logto`), behind the `auth`
profile (`docker compose --profile auth up`; the API starts without it), on
host ports `AUTH_PORT` 8441 and `AUTH_CONSOLE_PORT` 8442, seeded with
`--dapc` so the first admin can be created offline, with its own `auth`
database created by `scripts/create-auth-db.sh`. Its `ADMIN_ENDPOINT` is
`127.0.0.1`, not `localhost`, because Logto refuses CORS to a `localhost`
origin in production mode. `.env.local` carries
`AUTH_ISSUER=http://localhost:8441/oidc` (the public issuer a token
carries) and `AUTH_INTERNAL_URL=http://auth:3001` (where the API fetches
keys). The one-shot `auth-seed` service, in the same profile, runs
`manage.py seed_auth` (§8.3) after `migrate` and once `auth` is healthy.

**Test users** (`seed_auth.TEST_USERS`, listed with their details in
`docs/identity-providers/test-users.md`): two pharmacists, five certified
technicians, five technicians, two interns and two admins, all with the
password `Passw0rd!`, employed by the home pharmacy (admins by none), with
the registration, certification and supervisor their role needs. The seed
creates or finds each by email, keeps roles and custom data as listed, and
then **checks every one**: a real token (subject token, then token
exchange against the UI application), verified through the adapter and
read through `services.staff.describe` -- the validation a first request
gets -- so a user whose token the API would refuse fails the seed.

**`mint_token`**: `python manage.py mint_token --email … --roles … --pharmacy
… --registration … --certification …` signs a token in the contract with
`AUTH_DEV_SIGNING_KEY`, a throwaway key committed in `.env.local` (newlines
as `\n`). The command refuses outside `DJANGO_ENV=local`, and the verifier
consults the dev key only while the setting is non-empty, which only
`local.py` makes it.

**Tests** need no provider. `tests/tokens.py` generates an EC P-384 key per
session and the verifier is handed its JWKS in memory; `Identity` mints
tokens in the contract and can `register()` a row the way a first request
would; the `pharmacist` / `technician` fixtures are such identities, and
`as_pharmacist` / `as_technician` clients bear their tokens. `test_auth.py`
covers every failure code, scope denial per action, first sight and the
history it writes, `identity_changed`, succession, role precedence, the
admin, the technician's unfilled rule, interns, attribution, key rotation.
`test_providers.py` covers discovery, the internal-URL rewrite, the Logto
fallback, the identity mapping and adapter selection with a stubbed
network. `test_seed_auth.py` runs the seed against a fake of the provider's HTTP
surface, including the users, the token check (the fake signs real
tokens) and the documented list. `tests/providers/test_logto_live.py` runs only with
`LOGTO_LIVE_ENDPOINT` and the sandbox credentials set: a real token by
subject-token exchange, verified by discovery, through the API.

**The curl `e2e/` suite** mints its staff through `E2E_MINT_CMD` (default:
`mint_token` in the Compose `web` container) and acts with bearer tokens;
the API creates each identity's record on first sight, and the cases
compare people by email.

## 8. The Logto adapter

### 8.1 What the adapter knows

The issuer is `{endpoint}/oidc`; the keys are at `{issuer}/jwks` (the
fallback); a fresh install signs with EC P-384 (`ES384`), RSA only if
rotated to it. Configured as §8.3, Logto emits the contract directly.

### 8.2 Facts about Logto the design rests on

All checked against a running instance (v1.43):

- `sub` is Logto's `users.id`, assigned at creation and unchanged by any
  edit to the user — email, name, custom data; only deleting and recreating
  the user yields a new one. That is what makes `external_user_id` a safe
  reference.
- An access token for an API resource carries `iss`, `aud`, `sub`,
  `scope`, `client_id`, `jti`, `iat`, `exp` and nothing about the user;
  `email` and the rest of the contract arrive only through the access-token
  JWT customizer. **A token is issued even when the customizer throws**,
  with no custom claims, regardless of `blockIssuanceOnError` — so the API
  refuses a token without `email` itself.
- A token's `scope` is silently capped at what the user's roles grant; a
  scope the roles do not hold is dropped, not refused. `HasScope` is what
  turns that into a 403.
- Logto enforces a unique primary email; suffixing an old user's email
  with `-DEACTIVATED` frees it, and suspending the user
  (`PATCH /api/users/{id}/is-suspended`) makes token issuance fail. The
  suffix alone does not stop sign-in.
- Logto selects the **admin tenant** by the request's `Host` header
  matching `ADMIN_ENDPOINT`; a request to the internal address must carry
  the public host to reach it.
- The admin tenant's password policy consults `api.pwnedpasswords.com`
  unless the database was seeded with `--dapc`; on a host without internet
  the first admin could otherwise never be created.
- The first admin is an ordinary registration in the admin tenant, open
  only until it has a user; after that Logto flips the tenant's sign-in
  mode, and deleting the user does not reopen it.
- The console obtains a Management API token by signing the admin in with
  authorization code + PKCE for the `admin-console` client, consenting to
  the resource `https://default.logto.app/api`, and exchanging the code.
  That is how the seed does it too.

### 8.3 Configuring an instance

What this API needs from Logto, under the tenant it trusts — done locally
by `manage.py seed_auth`, idempotently, on every boot; elsewhere once, in
the console or through the Management API:

1. An **API resource** whose indicator is `AUTH_AUDIENCE`, token TTL 900 s,
   with every scope in `pharmacy/auth/scopes.py` created on it by name.
2. **Roles** named as `StaffRole` values, each granted the scopes
   `ROLE_SCOPES` lists.
3. An **application** for the UI (single-page, PKCE) that requests the
   resource and its scopes at sign-in.
4. The **access-token JWT customizer** (`seed_auth.CUSTOMIZER`, reproduced in
   `docs/identity-providers/logto.md`): `email`, `email_verified`, `name`,
   `given_name`, `family_name` from Logto's own user fields, `roles` from
   the user's roles, and `pharmacy_id`, the registration, the
   certification and `supervising_user_id` from `customData`.
   Also an **application** named `open-pharmacy-ui` (single-page, PKCE,
   redirect `http://localhost:3000/callback`, token exchange enabled for
   the seed's check and the e2e suite).
5. **Users**: primary email, given and family name, one or more roles, and
   `custom_data` with the employer, the registration for pharmacists and
   certified technicians, the certification for certified technicians, and
   the supervisor for interns. A token that cannot make a valid `Staff` row
   is refused, so the console is where completeness shows.

`seed_auth` also creates the console's first admin from
`AUTH_ADMIN_USERNAME` / `AUTH_ADMIN_PASSWORD` (the Welcome page's
registration, over the same experience API the page uses) and then signs
that admin in as the console does for its Management API token.

## 9. Files

`pharmacy/auth/` — `claims.py`, `providers/{base,oidc,logto}.py`,
`verifier.py`, `principal.py`, `authentication.py`, `permissions.py`,
`scopes.py`, `schema.py`. `pharmacy/management/commands/{mint_token,seed_auth}.py`.
`pharmacy/migrations/0010_staff_identity.py`. Tests: `tokens.py`,
`test_auth.py`, `test_providers.py`, `test_seed_auth.py`,
`test_mint_token.py`, `providers/test_logto_live.py`. Docs: `api.md`
(authentication, scopes), `staff.md` (identity records, changing a
person), `errors.md`, `deployment.md` (the identity provider),
`identity-providers/logto.md`, `identity-providers/test-users.md`,
`development.md`. Compose: the `auth` and `auth-seed` services,
`scripts/create-auth-db.sh`.
