# Identity provider abstraction: Logto as one adapter, not the design

Status: **implemented** (see "Implementation notes" at the end).

## 1. The problem, measured

"Logto" appears in 17 non-test modules, five settings, the Compose file,
four docs pages, the plan, and the names of the two auth classes. Most of
that is **naming**: the identity server is called by its product name
where the code means "the identity server". A smaller part is **shape**:
things that are true of Logto and would be different under Keycloak, Auth0
or Entra. Swapping providers today means touching all of it at once.

What is genuinely Logto-shaped — everything else is a rename:

| Where | What | Under another provider |
|---|---|---|
| `auth/verifier.py` | `issuer = f"{ENDPOINT}/oidc"`, JWKS at `{issuer}/jwks` | Keycloak: `…/realms/<realm>` and `…/protocol/openid-connect/certs`; Auth0: `https://<tenant>/` and `…/.well-known/jwks.json`. Every OIDC provider publishes both in `{issuer}/.well-known/openid-configuration` |
| `auth/principal.py` | flat custom claims `roles`, `pharmacy_id`, `registration_*`, `supervising_sub`; `scope` as a space-separated string | Keycloak puts roles in `realm_access.roles`; Auth0 namespaces custom claims (`https://example/roles`); Entra uses `scp` for scopes and `roles` for app roles |
| `auth/authentication.py` | the `token_without_email` message names "the JWT customizer" | the mechanism that adds claims is each provider's own |
| `services/staff.py` | the supervisor claim name in two messages | the contract's name, not the provider's |
| `config/settings/*` | `LOGTO_ENDPOINT` is an endpoint, not an issuer | the OIDC term is issuer |
| `docs/deployment.md` | the customizer script, the console steps, suffix-and-suspend deactivation | all provider operations |
| `tests/test_auth_logto_live.py` | Management API, subject tokens, token exchange | Logto's APIs; a Keycloak live test would use its admin API |
| `exceptions.py` | the code `identity_managed_by_logto` | the fact is "managed externally" |

Not Logto-shaped, and staying as it is: the scopes, `HasScope`, `Staff` as
the identity record and its immutability, first-sight registration, the
successor procedure's *effect* on our rows, `WriteContext`, the admin.

## 2. The abstraction: a claims contract and a provider adapter

Two things, and only two:

**The identity claims contract.** What a verified token must say for this
API to know who is acting. Documented once, provider-neutral, in
`pharmacy/auth/claims.py` as the names `Principal.from_claims` reads:

| Claim | Meaning |
|---|---|
| `sub` | the external user id, immutable |
| `email` | required |
| `given_name`, `family_name`, `name` | standard OIDC profile |
| `roles` | list of `StaffRole` names |
| `scope` | space-separated scopes (RFC 8693 / RFC 9068 `scope`) |
| `pharmacy_id` | employer, a Pharmacy UUID; absent for an admin |
| `registration_state`, `registration_number` | board registration |
| `supervising_user_id` | an intern's supervisor (renamed from `supervising_sub`: the contract names the concept, not the token field) |

**The provider adapter.** A small class that knows where a given provider
puts those things and how to reach its keys:

```text
class IdentityProvider:
    issuer: str                       # the `iss` to require
    audience: str                     # the `aud` to require
    algorithms: list[str]             # the allow-list
    def jwks_uri(self) -> str         # from discovery, cached
    def claims(self, raw: dict) -> dict   # raw token claims -> the contract
```

`pharmacy/auth/providers/oidc.py` is the generic one: issuer from
settings, `jwks_uri` from `{issuer}/.well-known/openid-configuration`,
`claims()` the identity mapping (the contract *is* the token). It works
for any provider that can be configured to emit the contract directly —
which Logto does, through its customizer, so **`LogtoProvider` is the OIDC
provider with one line: the issuer is `{ENDPOINT}/oidc`.** A Keycloak
adapter would override `claims()` to lift `realm_access.roles`; an Auth0
one to strip a namespace. Each is a file of under forty lines.

The provider is chosen by one setting, `AUTH_PROVIDER`, a dotted path
defaulting to the Logto adapter. The verifier, the authentication class
and the principal never mention a product again.

## 3. The renames

Mechanical, and all of them, so nothing is half-named:

| From | To |
|---|---|
| `LogtoTokenVerifier`, `LogtoJWTAuthentication`, `LogtoJWTScheme` | `TokenVerifier`, `BearerTokenAuthentication`, `BearerScheme` |
| `LOGTO_ENDPOINT`, `LOGTO_INTERNAL_ENDPOINT`, `LOGTO_API_RESOURCE`, `LOGTO_DEV_SIGNING_KEY` | `AUTH_ISSUER`, `AUTH_INTERNAL_URL`, `AUTH_AUDIENCE`, `AUTH_DEV_SIGNING_KEY` — plus `AUTH_PROVIDER` (new). The issuer is the public one a token carries (`http://localhost:8441/oidc` under Compose); the internal URL is where discovery and keys are fetched when that differs |
| `identity_managed_by_logto` | `identity_managed_externally` |
| `supervising_sub` (claim) | `supervising_user_id` — one line in the customizer script |
| `tests/test_auth_logto.py`, `tokens.py` docstrings | `test_auth.py`; the issuer is "the identity provider" |
| `tests/test_auth_logto_live.py` | `tests/providers/test_logto_live.py` — it is the Logto adapter's own test and stays Logto-shaped on purpose |
| Compose service, database, init script | already `auth` |
| `docs/deployment.md` "The identity server" section | split: the contract and the settings stay; the Logto console steps, customizer script and deactivation procedure move to `docs/identity-providers/logto.md`, the template for the next provider's page |
| `plans/auth/plan.md` | §0 (what was verified on Logto) and §4 (Logto configuration) are Logto's and stay; everywhere else "Logto" becomes "the identity provider" |
| Error messages | "the identity server's JWT customizer" → "the identity provider's token" |

Docstrings that explain *why* something is the way it is because of Logto
(the customizer issuing regardless of `blockIssuanceOnError`; ES384 on a
fresh install) keep the name: those are facts about Logto, recorded where
the code works around them.

## 4. What does not change

- The scopes and roles, and the Logto roles' grants.
- `Staff` as the identity record, `external_user_id`, immutability,
  first-sight registration, the successor rule. None of it knows which
  provider issued the subject.
- `Principal` (its fields are the contract), `HasScope`, `WriteContext`.
- The Basic/session transition path and the admin.
- The database: no migration. `external_user_id` is already a neutral name.
- The API's behaviour and every error code but one.

## 5. Discovery, and what it costs

Fetching `{issuer}/.well-known/openid-configuration` once per process
(cached like the JWKS, refreshed on failure) is what makes the adapter
generic: no provider-specific path in our code. It adds one request at
start-up and the same 503 `identity_unavailable` if the provider is away.
The Logto adapter may still hard-code `jwks_uri` as a fallback for
discovery being unreachable; it is not required.

## 6. Tests

- `test_auth.py` (today's `test_auth_logto.py`) runs unchanged against the
  generic provider with the in-memory key set: it already tests the
  contract, not Logto.
- A `test_providers.py` with one case per adapter: given a raw token in
  that provider's native layout, `claims()` returns the contract. For
  Logto that is the identity mapping; it is the place a Keycloak mapping
  would be pinned before a Keycloak exists to talk to.
- The live test stays Logto's.

## 7. Order of work

1. `AUTH_*` settings and `AUTH_PROVIDER`; `providers/base.py`, `oidc.py`,
   `logto.py`; the verifier takes a provider. Tests green.
2. Renames (classes, files, error code, claim). Tests green.
3. Docs split; plan wording.

One commit per step, each leaving every test green. Roughly a day; no
behaviour change a client can see except the renamed error code and, if
you take it, the renamed claim.

## 8. Questions

1. **Rename the `supervising_sub` claim** to `supervising_user_id` in the
   contract (and the Logto customizer)? It is the one change visible on the
   Logto side. Default: yes.
2. **`AUTH_ISSUER` as the setting**, with the Logto adapter given the full
   `…/oidc` issuer, or keep an endpoint setting the adapter completes?
   Default: issuer — it is the OIDC term and what every other provider
   asks for.
3. **Discovery at start-up**, or hard-coded paths per adapter? Default:
   discovery, with the adapter allowed to pin `jwks_uri` as a fallback.

## 9. Implementation notes

Built as §2–§3, with the three §8 defaults taken (the claim is
`supervising_user_id`; the setting is `AUTH_ISSUER`; discovery, with the
Logto adapter pinning `{issuer}/jwks` as a fallback). Differences:

- **`AUTH_INTERNAL_URL`** (renamed from `LOGTO_INTERNAL_ENDPOINT`) is
  applied by the adapter to *both* discovery and the JWKS URI: the
  discovery document names the public `jwks_uri`, and the adapter swaps its
  origin for the internal one before fetching.
- **The contract lives in `pharmacy/auth/claims.py`** as named constants;
  `Principal.from_claims`, `mint_token` and the test issuer read and write
  those names, so a contract change is one file and its callers.
- **`AUTH_PROVIDER` is resolved once per process** through
  `providers.get_provider()` with a `set_provider()` seam for tests; the
  verifier builds its `PyJWKClient` lazily so discovery happens on the
  first token, not at import.
- **`HasScope`'s error details and the two service refusals now render
  scope names as strings** (an `Enum` leaked into `details` before).
- The Logto-shaped names that remain are where §3 said they would be:
  `providers/logto.py`, `tests/providers/test_logto_live.py`,
  `management/commands/seed_auth.py`, the `auth` Compose service's image and
  comments, `docs/identity-providers/logto.md`, the two plans, and the
  `.env.example` hints that show Logto's issuer shape as an example.
- **Verified**: the customizer on the sandbox Logto was re-applied with the
  renamed claim and the live adapter test passes through discovery; the
  unit suite and the curl e2e suite are green on the renamed settings.
