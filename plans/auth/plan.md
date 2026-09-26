# Users via Logto: JWT bearer auth, scopes, and Staff as the identity record

Status: **implemented** (see "Implementation notes" at the end for what
differs from or was not in the text).

The identity server is Logto. Builds on `plans/rx-tx-fills/` (the service
layer, `WriteContext`, the change history, the fill workflow).

## 0. What was verified, on a running Logto

### 0.1 Results

| # | Checked | Result |
|---|---|---|
| 1 | Logto from npm | `@logto/cli` installs, but `logto init` only downloads the server from GitHub releases, which is unreachable here. **The tarball is what made this possible.** On your machine, Docker (`svhd/logto`) or the CLI both work. |
| 2 | Requirements | Node 22 (`^22.14.0` in its `package.json`), PostgreSQL 14+ with its own database, Redis optional. The driver (slonik) needs a TCP `DB_URL`; a Unix-socket URL is rejected. |
| 3 | Discovery | `GET {ENDPOINT}/oidc/.well-known/openid-configuration`: `issuer = http://localhost:3001/oidc`, `jwks_uri = …/oidc/jwks`, `token_endpoint = …/oidc/token`, signing alg **`ES384`** (one EC P-384 key on a fresh install; RS256 only if rotated to RSA). |
| 4 | A real user token | Obtained without a browser (Management API → subject token → token exchange, §0.2). Header `{"alg":"ES384","typ":"at+jwt","kid":…}`; claims `iss`, `aud` = the resource indicator, `sub` = user id, `scope` space-separated, `client_id`, `jti`, `iat`, `exp` (= resource TTL, 900 s). |
| 5 | Verified with PyJWT | `PyJWKClient("…/oidc/jwks")` + `jwt.decode(algorithms=["ES384","RS256"], issuer=…, audience=…)` against the **live** JWKS: accepted, claims below. This is the exact code path §2 builds. |
| 6 | Custom claims | With the customizer in §4, the access token carries `email`, `email_verified`, `name`, `given_name`, `family_name`, `address` (OIDC-shaped object: `formatted`, `street_address`, `locality`, `region`, `postal_code`, `country`), `registration_state`, `registration_number`. Verbatim from the verified token: `"name": "Elena Ortiz"`, `"address": {"formatted": "18 Hillcrest Ave, Ithaca, NY 14850", …}`, `"registration_number": "RPH.023417"`. |
| 7 | A user with no primary email | The customizer `throw`s and `blockIssuanceOnError` is `true`, yet **a token is still issued**, with no custom claims at all (`email` absent). No error in Logto's log. So the API must refuse a token without `email` itself (401 `token_without_email`); it cannot rely on Logto to withhold one. |
| 8 | Scopes are capped by role | A `technician` (read + write) asking for `fills:verify` receives `scope = "prescriptions:read prescriptions:write"` — the extra scope is silently dropped, not an error. The API's `HasScope` is what turns that into a 403. |
| 9 | Python packages | `PyJWT[crypto]` 2.13 and `cryptography` 50 install from PyPI here. The `logto` PyPI package is a web-session SDK; not used. |
| 10 | `sub` stability | Changed the user's email, name and `custom_data` registration through the Management API and minted a new token: `sub` unchanged (`ndr2k9q0pvqk` before and after). `sub` is Logto's `users.id`, assigned at creation; only deleting and recreating the user yields a new one. |
| 11 | Deactivation by suffix | `PATCH /api/users/{id}` to `marco.diaz@example.local-DEACTIVATED` is accepted (frees the real address for the new user) and `PATCH …/is-suspended {true}` makes token issuance fail with `invalid_grant`. The suffix alone does not stop sign-in; suspend as well. |
| 12 | Batch lookup | `GET /api/users?search.id=a&search.id=b&mode=exact&joint=or` resolves several ids in one call — noted for completeness; this plan does not call the Management API from the service. |

### 0.2 Runbook (how it is running here; reusable for Compose)

```text
# PostgreSQL 16 from the pgserver wheel, on TCP because slonik wants a host:port
pg_ctl -D /tmp/logto-run/pgdata -o '-h 127.0.0.1' -o '-p 54329' start ; psql -c 'CREATE DATABASE logto'
# seed (creates the default + admin tenants, OIDC keys, sign-in experience)
cd /tmp/logto && DB_URL=postgres://postgres@127.0.0.1:54329/logto node packages/cli/lib/index.js db seed --swe
# run
cd packages/core && DB_URL=… ENDPOINT=http://localhost:3001 ADMIN_ENDPOINT=http://localhost:3002 \
  NODE_ENV=production TRUST_PROXY_HEADER=1 node .
```

Management API without a browser (sandbox only — on your machine the
console's Welcome page creates the admin and an M2M app the normal way):
one `INSERT` of a machine-to-machine application in the `default` tenant
plus a row in `applications_roles` for the seeded "Logto Management API
access" role, then `POST /oidc/token` with `grant_type=client_credentials`
and `resource=https://default.logto.app/api`. From there, everything in §4
was done through `POST /api/resources`, `/api/resources/{id}/scopes`,
`/api/roles`, `/api/users`, `/api/users/{id}/roles`,
`PUT /api/configs/jwt-customizer/access-token`. A user token without a
sign-in flow: set `customClientMetadata.allowTokenExchange` on the SPA app,
`POST /api/subject-tokens {"userId"}`, then `POST /oidc/token` with
`grant_type=urn:ietf:params:oauth:grant-type:token-exchange`. That is also
how the integration test in §7 will mint tokens.

Logto is still running here (core on :3001, admin on :3002, the database
on :54329) with the resource, scopes, roles, customizer and three users
from §0.1 in place.

## 1. What changes

Today: HTTP Basic against `auth.User`; a fill names the person acting
through an `X-Staff-Id` header, a FK to `Staff`, whose rows are typed in
through `/pharmacies/{id}/staff/`; the history records `changed_by_user`
(FK `auth.User`) and `changed_by_staff`.

After:

- The API accepts `Authorization: Bearer <Logto access token>` on every
  endpoint (or, during the transition, Basic/session as today). A token is
  verified against Logto's JWKS and yields a `Principal`: `external_user_id`, email,
  name, role, pharmacy, registration, scopes — all from the claims, no
  lookup.
- **`Staff` becomes the identity record.** A token's first appearance
  inserts a `Staff` row carrying `external_user_id` (the Logto subject)
  and what the token says; the row is never updated from a token again; a
  later token for the same `external_user_id` with a different identity is
  refused. Every record that names a person keeps pointing at **`Staff.id`**,
  the UUID row id, exactly as it does now; `external_user_id` is only how the
  row is found for a token. `X-Staff-Id` is gone: the person is whoever the
  token is for.
- **Scopes replace the role check.** "Only a pharmacist can verify" becomes
  "only a token with `fills:verify`"; Logto roles grant scopes (§2.1).
- The Django admin keeps session login; its writes and Basic-authenticated
  writes are attributed to the Django user (§5).

## 2. Token verification and the principal

`pharmacy/auth/` (new package):

- `verifier.py` — `LogtoTokenVerifier`: `PyJWKClient(f"{LOGTO_ENDPOINT}/oidc/jwks", cache_keys=True)`;
  `jwt.decode(token, key, algorithms=["ES384", "RS256"], issuer=f"{LOGTO_ENDPOINT}/oidc", audience=LOGTO_API_RESOURCE, leeway=60, options={"require": ["exp", "iat", "sub", "aud", "iss"]})`.
  `alg: none` and symmetric algorithms are excluded by the allow-list. An
  unknown `kid` triggers one JWKS refresh (PyJWKClient), so key rotation
  needs no redeploy. JWKS unreachable is a 503 `identity_unavailable`, not a
  401: the token was not judged. Verified against the live instance (§0.1
  item 5).
- `principal.py` — frozen `Principal(external_user_id, email, name, given_name, family_name, roles, role, pharmacy_id, registration_state, registration_number, supervising_external_user_id, scopes, staff)`.
  `external_user_id = claims["sub"]`, read in exactly this one place.
  `roles` is the token's list; `role` is the highest by
  `ROLE_PRECEDENCE = [admin, pharmacist, certified_technician, technician, intern]`
  in `pharmacy/models/staff.py`, next to `StaffRole`. `is_authenticated = True`; `has(scope)`.
- `authentication.py` — DRF `LogtoJWTAuthentication`: reads the bearer
  header, verifies, builds the principal, then **resolves or creates the
  `Staff` row** (§3) and attaches it. Failure codes, rendered by the existing
  exception handler: `token_missing`, `token_invalid`, `token_expired`,
  `token_without_email`, `identity_incomplete` (the claims cannot make a
  valid `Staff` row — e.g. a pharmacist with no registration, a non-admin
  with no or an unknown `pharmacy_id`, no recognised role),
  `identity_changed` (the claims differ from the row for this
  `external_user_id`), `supervisor_required` (§3.4). `WWW-Authenticate: Bearer`.
- `permissions.py` — `HasScope`: a viewset declares
  `required_scopes` per action; a principal lacking one is 403
  `insufficient_scope` with `details.required`. An action with no entry is
  denied: fail closed.
- `scopes.py` — the table below, the single definition the viewsets and the
  docs use.

Settings: `LOGTO_ENDPOINT`, `LOGTO_API_RESOURCE`, required in uat/prod
through `require_settings`, defaulted in `.env.local` to the Compose
service. `REST_FRAMEWORK` authentication classes:
`[LogtoJWTAuthentication, BasicAuthentication, SessionAuthentication]`;
permissions `[IsAuthenticated, HasScope]`.

### 2.1 Scopes and roles

`read` covers list, detail and the nested child lists (a nested list needs
the *child's* read scope); `write` covers create, update, delete and the
resource's actions. Prescriptions are the one resource where who may change
a record depends on its state, so they have two update scopes:

| Resource | Scopes |
|---|---|
| prescriptions | `prescriptions:read`, `prescriptions:create`, `prescriptions:update` (PATCH, PUT, delete, `cancel/`, always), `prescriptions:update_unfilled` (the same, only while no fill has been started — §2.2) |
| fills | `fills:read`, `fills:write` (create, PATCH, `unverify/`, `complete/`, `cancel/`), **`fills:verify`** |
| patients | `patients:read`, `patients:write` |
| prescribers | `prescribers:read`, `prescribers:write` |
| medications | `medications:read`, `medications:write` |
| pharmacies | `pharmacies:read`, `pharmacies:write`, **`pharmacies:onboard`** (`rx_number_seed`, `step`) |
| locations | `locations:read`, `locations:write` |
| addresses | `addresses:read`, `addresses:write` |
| staff | `staff:read`, `staff:write` |

Twenty-two. Logto roles, named exactly as `StaffRole` values because the
token's `roles` claim populates `Staff.roles` (§4). A user may hold
several; Logto grants the union of their scopes, and `Staff.role` is the
highest by precedence:

| Role | Scopes |
|---|---|
| `admin` | all twenty-two — a superuser, employed by no particular pharmacy |
| `pharmacist` | all twenty-two: anything on prescriptions, fills, locations, and the rest |
| `certified_technician`, `technician` | every `:read`; `prescriptions:create`, `prescriptions:update_unfilled`; `fills:write`; `patients:write`; `addresses:write`. **Not** `prescriptions:update`, not `fills:verify`, not `pharmacies:onboard`, not reference-data writes |
| `intern` | exactly a technician's scopes, **and** the token must carry `supervising_sub` (§3.4) or it is refused |

Deliberately not separate: `delete` (a soft delete, under the update
scopes) and the change history (no API). Scopes are silently capped by role
at Logto (§0.1 item 8); `HasScope` is what turns a missing one into a 403.

### 2.2 The unfilled rule

`HasScope` lets a prescription update through on either update scope.
`PrescriptionService.update`, `delete` and `cancel` then apply the one
state-dependent rule in the system: a principal holding
`prescriptions:update_unfilled` but not `prescriptions:update` may change a
prescription only while **no fill has been started** — no `Fill` row that
is `pending`, `verified` or `completed`; cancelled fills do not count,
because a cancelled fill means nothing was dispensed or reserved. Past that
point the write is 403 `insufficient_scope` with
`details.required = ["prescriptions:update"]` and
`details.reason = "a fill has been started"`, so the client knows it is the
state, not the user, that changed. The check runs under the prescription's
row lock, like every other rule that reads the fills. The rule is keyed on
the scopes the principal holds, never on its role: the API never reasons
about roles for permissions.

## 3. `Staff` as the identity record

### 3.1 Schema

| Field | Change |
|---|---|
| `external_user_id` | **New.** `CharField(32)`, nullable, unique among live rows (`uniq_live_staff_external_user_id`). The Logto subject. Null on a row typed in by hand, which is a directory entry that cannot act. |
| `email` | **New.** `EmailField`, blank for hand-typed rows, unique among live rows when set (`uniq_live_staff_email`). |
| `first_name`, `last_name` | from `given_name` / `family_name`; `middle_name`, `suffix` stay blank unless the token carries them (it does not, today). |
| `roles` | **New.** `JSONField` list of `StaffRole` values, the token's `roles` claim in full. |
| `role` | the highest of `roles` by `ROLE_PRECEDENCE`; stays a column because filters, the PIC rule and the registration rule key on it. `StaffRole` gains **`admin`**. |
| `pharmacy` | **becomes nullable**: from the `pharmacy_id` claim, required for every role except `admin`, refused if it names no live pharmacy. `credential_problems()` gains that rule. |
| `registration_state`, `registration_number` | from the claims; `credential_problems()` still applies, so a `pharmacist` token without them is `identity_incomplete`. Not required for `admin`. |
| `uniq_live_staff_registration`, `uniq_live_staff_npi` | condition becomes `deleted IS NULL AND is_active` — so a successor identity can take a registration once the old row is inactive (§3.3). |
| everything else | unchanged, `Fill` and `FieldChange` FKs included. |

**Other tables reference `Staff.id`, the UUID row id — not
`external_user_id`.** `Fill.operator_id`, `Fill.verified_by_id`,
`FieldChange.changed_by_staff_id` and `PharmacyLocation.pharmacist_in_charge_id`
are unchanged. The external id cannot be an FK target anyway (hand-typed
rows have none), and nothing would be gained: it is one indexed read per
request to turn a token into a row, and everything after that is the row.

The pharmacist-in-charge rule reads `"pharmacist" in staff.roles`, so an
admin who is also a pharmacist can be designated; an admin who is not,
cannot.

### 3.2 First sight, then immutability

On every authenticated request, after the token verifies:

1. `repositories.staff.find(external_user_id=…)`; if found, compare
   `email`, `first_name`, `last_name`, `roles`, `pharmacy_id`,
   `registration_state`, `registration_number` with the claims. Any
   difference → 401 `identity_changed`, message: "This user's identity
   changed in Logto; deactivate it and create a new user." Nothing is
   written. This is what enforces the policy; Logto cannot (§0.1 item 7
   showed it issues regardless).
2. If not found: `services.staff.register(ctx, principal)`, in one
   transaction — `full_clean()` for the credential rules; then, for a
   registration or NPI the claims carry that an **active row with a
   different `external_user_id`** holds, that row is set `is_active=False`
   with a history row whose request path says which token did it (the
   successor taking over, §3.3); then insert, with one history row per
   column and `changed_by_staff` = the new row itself. A hand-typed row
   (no external id) holding the registration is deactivated the same way:
   it was the directory entry for the same person.
3. A row with `external_user_id` set is **read-only through
   `/pharmacies/{id}/staff/` and the admin** (409
   `identity_managed_by_logto`), except `is_active`, which an admin may set
   by hand as well.

Logto-suspended users never get a token (§0.1 item 11), so a suspended
identity cannot act whatever its local row says.

### 3.3 The deactivation procedure (runbook, `docs/staff.md`)

To change anything about a person: in Logto, suffix the old user's email
with `-DEACTIVATED` and suspend it, then create the new user with the real
email, profile, `custom_data` and roles. Nothing to do in the API: the new
user's first request registers the successor row and deactivates the old
one (§3.2 step 2); an admin can also set `is_active` by hand at any time.
The person's history is split across two `external_user_id`s, which is the
correct audit answer.

### 3.4 Interns act under supervision — recorded in Logto

An intern's token must carry `supervising_sub`: the Logto subject of the
pharmacist or technician who supervises them, kept in the intern's
`custom_data` in Logto. The API checks that a principal whose `role` is
`intern` carries a non-empty claim (401 `supervisor_required` otherwise)
and does nothing else with it: who supervised whom, and when that changed,
is Logto's record (its audit log and the user's `custom_data`), not ours.
No column on `Fill` or `FieldChange`; the intern's own `Staff` row is what
the fill and the history name. A non-intern's token may carry the claim; it
is ignored. The supervisor is not part of the intern's immutable identity,
so the §3.2 comparison ignores it.

## 4. Logto configuration

1. **API resource**: indicator `LOGTO_API_RESOURCE`, token TTL 900 s.
   Scopes: the nineteen in §2.1.
2. **Roles**: `admin`, `pharmacist`, `certified_technician`, `technician`,
   `intern`, with the scopes in §2.1. A user may hold several.
3. **Application** for the UI: SPA (PKCE), the API resource and its scopes
   requested at sign-in; the UI sends the bearer on every request.
4. **JWT customizer** (access token). The script below is the §0.1 one
   minus the address, plus `roles` and `pharmacy_id`; `context.user.roles`
   is available to it (§0, `JwtCustomizerUserContext`):
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
   The `roles`, `pharmacy_id` and `supervising_sub` claims are **not yet verified** on the
   running instance (the address removal is trivial); they are the first
   thing I check when implementation starts, with the script re-applied.
   Precedence among roles is applied by the API, not here, so the token
   says what Logto knows and our code says what it means.
5. **Users**: created by the admin with primary email, given/family name,
   one or more roles, and `custom_data` `{pharmacy_id, registration_state,
   registration_number, supervising_sub}` (`pharmacy_id` not needed for an
   admin-only user; `supervising_sub` — the supervisor's Logto user id,
   shown in the console's user list — required for an intern)
   — the API refuses a token that cannot make a valid `Staff` row, so the
   console is where completeness is enforced by feedback, and the API is
   where it is enforced by refusal.

## 5. Basic Auth, sessions and the admin

Kept for the transition. A Django-authenticated actor (Basic on the API,
session in the admin) gets a `Principal` with
`external_user_id = "django:<pk>"`, the user's email, **every scope**, and
`staff = None`. (A Logto `admin` is different: it has a `Staff` row and can
do everything, fills included.) Consequences:

- The change history records `changed_by_user` for these and
  `changed_by_staff` for token actors: exactly one is set (CHECK).
- Creating or verifying a fill needs a `Staff` row to name, so those two
  writes require a token principal: a Django actor gets 403
  `staff_identity_required`. Everything else works as today.
- The admin's `Staff` page shows `external_user_id`, `email`, `roles` and `source`
  (derived: `logto` / `manual`) and refuses edits to Logto-sourced rows as
  §3.2 says.

## 6. Migration

`0010_staff_identity`: add `Staff.external_user_id`, `Staff.email` and
`Staff.roles` with their partial unique constraints; `admin` in the `role`
choices; `Staff.pharmacy` nullable; swap the registration and NPI
constraints' conditions to active rows; make `FieldChange.changed_by_user`
nullable and add the exactly-one CHECK. Existing dev `Staff` rows get
`roles = [role]` by `RunPython` (arithmetic on a column that exists) and no
external id: directory entries. Depends on `0009`.

## 7. Local development and tests

**Compose** gains an `auth` service (`svhd/logto:1.43`, published on host
ports `AUTH_PORT` 8441 and `AUTH_CONSOLE_PORT` 8442 since 3001/3002 are
taken on the host; `DB_URL=postgres://pharmacy:pharmacy@db:5432/auth`) and
an init script
creating the `logto` database on `db`; `web` gets `LOGTO_ENDPOINT` and
`LOGTO_API_RESOURCE` in `.env.local`. First boot: the console's Welcome
page, then §4. (Here, the §0.2 runbook is the equivalent and is running.)

**Tests** mint their own tokens: `tests/tokens.py` generates an EC P-384
key per session, and the verifier's one seam — `LogtoTokenVerifier(key_set=...)`
— takes the matching in-memory `PyJWKSet`. Fixtures: `as_pharmacist` and
`as_technician` become token-bearing clients (same names, so the fill tests
change only in setup); `api` stays `force_authenticate` for the CRUD suites
(Basic posture, all scopes). New `test_auth_logto.py`: every failure code;
scope denial per action across the nineteen; first sight creates the
`Staff` row with history; `identity_changed`; a successor identity
deactivating its predecessor; role precedence; an admin with no pharmacy
operating and verifying a fill; a technician allowed to create and to
update an unfilled prescription, refused once a fill is pending, allowed
again if that fill is cancelled, allowed on `POST /fills/`; an intern
refused without `supervising_sub` and allowed with one; key rotation. `test_auth_logto_live.py` runs only
when `LOGTO_LIVE_ENDPOINT` and the sandbox M2M credentials are set, mints a
real token by subject-token exchange (§0.2) and sends it through the API —
skipped loudly otherwise. The `X-Staff-Id` tests go.

**`mint_token`**: `python manage.py mint_token --email … --roles pharmacist
--pharmacy <id> --scopes …` signs a token with a dev key the verifier
accepts only when `DJANGO_ENV=local` and `LOGTO_DEV_SIGNING_KEY` is set —
the two-guard pattern `seed_local` uses.

## 8. Files

Created: `pharmacy/auth/` (`verifier.py`, `principal.py`,
`authentication.py`, `permissions.py`, `scopes.py`),
`migrations/0010_staff_identity.py`, `tests/tokens.py`,
`tests/test_auth_logto.py`, `tests/test_auth_logto_live.py`,
`management/commands/mint_token.py`.

Modified: `models/staff.py` (`external_user_id`, `email`, `roles`, `admin`,
nullable `pharmacy`, `ROLE_PRECEDENCE`, `SUPERVISING_ROLES`, constraint
conditions), `models/field_change.py` (`changed_by_user` nullable), `services/staff.py` (`register`, the
read-only rule), `services/context.py` (`principal`, `staff`),
`services/prescription.py` (the unfilled rule), `services/fill.py`
(operator and verifier from the context), `services/history.py`, `serializers/staff.py`,
`views/base.py` (`ctx` from the principal; header resolution removed),
every viewset (`required_scopes`), `views/tags.py` (bearer security scheme
replaces the header parameter), `admin/base.py`, `admin/staff.py`,
`config/settings/*`, `.env.example`, `.env.local`, `docker-compose.yml`,
`pyproject.toml` + `uv.lock` (`PyJWT[crypto]`, spliced as before),
`seed_local.py` (no change to staff rows: they stay hand-typed directory
entries), the fill and pharmacy tests' setup, `docs/api.md` (authentication,
scopes), `docs/staff.md` (identity, deactivation), `docs/prescriptions.md`
(`X-Staff-Id` → bearer), `docs/deployment.md` (variables, Logto),
`docs/development.md` (Compose, `mint_token`), `AGENTS.md`.

## 9. What to verify on your Compose instance

All done here against the sandbox instance except item 5:

1. Discovery: `issuer` and `jwks_uri` as §0.1 item 3.
2. A UI-obtained token decodes to the resource `aud`, the role's scopes,
   and the custom claims.
3. Bearer → 200; no header → 401 `token_missing`; a technician on
   `verify/` → 403 `insufficient_scope`.
4. Rotate the signing key in the console; the next request still succeeds.
5. The `roles` and `pharmacy_id` claims from the §4 script (to be verified
   here first, at implementation start).

## 10. Open questions

None.

## 11. Implementation notes

What was built follows §1–§9. Differences and discoveries:

- **Verified on the running Logto first**, as §9 item 5 asked: with the §4
  customizer re-applied, a token for a user holding two roles carries
  `"roles": ["pharmacist", "technician"]`, and `pharmacy_id` and
  `supervising_sub` come through from `custom_data`. The sandbox instance
  now also holds the full resource (22 scopes), the five roles with their
  grants, and `test_auth_logto_live.py` passes against it end to end: a
  real token, verified against the live JWKS, creates the `Staff` row and
  is refused a scope it did not ask for.
- **Twenty-two scopes, not twenty-one** — the plan's count was off by one;
  the table was right.
- **`PyJWT[crypto]` was spliced into `uv.lock` by hand** (the four blocks
  `pyjwt`, `cryptography`, `cffi`, `pycparser`), as the staff work did,
  because this sandbox's PyPI mirror rewrites every URL on `uv lock` and
  does not even list the `faker` version already locked, so `uv lock
  --check` cannot run here. File sizes were taken from the real host's
  `Content-Length`; `uv sync --frozen` installs cleanly. Worth a `uv lock`
  on a normal network to confirm it is a no-op.
- **`pharmacy/exceptions.py` imports DRF's exception handler lazily**:
  `rest_framework.views` evaluates the configured authentication classes on
  import, which import the services, which import `exceptions` — a cycle
  once the auth class lived in this app.
- **`Principal.pk`** exists because DRF's `UserRateThrottle` keys on
  `request.user.pk`; a principal's is its subject, so throttling is per
  identity.
- **`HasScope` lets an unsupported method through to the 405** rather than
  failing closed on an action the view does not serve (a `PUT` on a fill).
- **Registration on first sight runs `full_clean()` after retiring the
  predecessor**, because Django validates the partial unique constraint in
  `full_clean()` and the constraint is over active rows.
- `credential_problems()` gained a keyword-only `pharmacy_id`, checked only
  when passed (the model and the token registration pass it; the serializer
  takes the employer from the URL and does not).
- The admin's `Staff` page shows `email`, `source` (logto / manual) and
  makes every field but `is_active` read-only on a Logto-sourced row; the
  service refuses the rest anyway.
- **The curl-driven `e2e/` suite mints its staff.** `req_staff` sends a
  bearer token; `make_staff_identity PHARMACY ROLE` gets one from
  `E2E_MINT_CMD` (default: `manage.py mint_token` in the Compose `web`
  container), signed with a throwaway `LOGTO_DEV_SIGNING_KEY` now
  committed in `.env.local`, and the API creates the staff record on the
  token's first request. The two fill cases compare people by email, and
  assert the new refusals (403 `staff_identity_required` for a login, 403
  `insufficient_scope` naming `fills:verify` for a technician). Run here
  against a dev server on the embedded Postgres with a wasm `jq` shim:
  745 passed, 0 failed.
- **Public versus internal endpoint.** Logto writes its public `ENDPOINT`
  into every token's `iss`, and under Compose that is a host port the
  `web` container cannot reach; `LOGTO_INTERNAL_ENDPOINT` (optional) is
  where the keys are fetched when it differs, and the issuer stays the
  public URL. The same split serves a deployment behind a proxy.
- Run on the embedded PostgreSQL 16.2: 506 tests pass and 1 skips (the live
  test, off without `LOGTO_LIVE_ENDPOINT`; it passes when pointed at the
  sandbox instance). `ruff check`, `ruff format --check`,
  `makemigrations --check` and `spectacular --fail-on-warn` are clean.
- **Superseded in part by `plans/auth-provider/plan.md`**: the Logto-named
  classes, settings, error code and claim described above were renamed
  and the provider-specific knowledge moved behind an adapter. Where this
  document says `LogtoJWTAuthentication`, `LOGTO_ENDPOINT`,
  `identity_managed_by_logto` or `supervising_sub`, read
  `BearerTokenAuthentication`, `AUTH_ISSUER`, `identity_managed_externally`
  and `supervising_user_id`.
