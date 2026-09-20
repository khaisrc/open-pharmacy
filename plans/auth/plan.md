# Authentication & Authorization — Deferred Work

Status: **not scheduled** — this is follow-up work, deliberately out of scope for the initial build in `plans/bootstrap/plan.md`.

## 1. Why this is separate

The initial service ships with HTTP Basic authentication and no authorization: any authenticated user can read and write any prescription. That is a reasonable starting point for a service with no consumers yet, but it is not a shippable posture for a public-facing API holding prescription data. This document captures the target design so the deferral is a decision with a known exit, not an omission.

**What Basic Auth gets wrong for this service, specifically:**

- Credentials are base64, which is encoding rather than encryption, and they travel on *every* request. A single logged request line or a moment of plain HTTP leaks a reusable password, not a short-lived token.
- There is no expiry. A leaked credential is valid until someone notices and rotates it by hand.
- It forces this microservice to own a `auth.User` table, making this service a de facto identity service and coupling it to user lifecycle concerns that belong elsewhere.
- Every authenticated caller is equally privileged, so a patient-facing client and an internal batch job cannot be told apart.
- Password hash verification runs on every single request, which is deliberately slow work (that is the point of a password hash) sitting in the hot path of every API call.

## 2. Target authentication: JWT bearer tokens

Stateless verification against an identity provider's JWKS, not `djangorestframework-simplejwt` and not DRF's `TokenAuthentication` — both assume this service owns the user table and performs a database lookup per request.

**Design**

- A custom `JWTAuthentication` (DRF `BaseAuthentication` subclass) reads the `Authorization: Bearer <token>` header.
- Validates the RS256 signature against the IdP's JWKS, plus `exp`, `nbf`, `iss`, and `aud`. Uses `PyJWT` with `PyJWKClient`, with the key set cached in Django's cache and refreshed on unknown `kid` so key rotation needs no redeploy.
- Returns a lightweight frozen `Principal` dataclass — `subject_id`, `subject_type`, `scopes` — rather than a Django `User`. No database lookup, no local user row.
- `subject_type` is one of `patient`, `prescriber`, `service`, `admin`, read from a namespaced custom claim.
- Service-to-service callers use the OAuth2 client-credentials grant, so machine and human callers share one code path.
- Clock skew tolerance of ~60s; reject tokens with `alg: none` or any symmetric algorithm in production config.

**Open question to resolve before starting:** who issues the tokens? An existing IdP (issuer URL + audience needed), or nothing yet — in which case add a dev-only HS256 issuer and a `mint_token` management command, structured so swapping in a real JWKS is a settings change. If the service instead sits behind a gateway that already authenticates, replace this section with a signed `X-Service-Identity` header from the gateway; §3 onward is unaffected.

## 3. Target authorization

Four layers. Scope checks alone are not sufficient.

**Layer 1 — Scopes at the view.** `prescriptions:read` and `prescriptions:write`, enforced by a `HasScope` permission class. A coarse gate only; it answers "may this caller use this endpoint," never "may this caller touch this record."

**Layer 2 — Row-level queryset narrowing.** The load-bearing layer. `get_queryset()` narrows by principal *before* any client-supplied filter is applied:

```python
qs = Prescription.objects.all()
p = request.principal
if p.subject_type == "patient":
    qs = qs.filter(patient_id=p.subject_id)
elif p.subject_type == "prescriber":
    qs = qs.filter(prescriber_id=p.subject_id)
elif p.subject_type in ("service", "admin"):
    pass  # unrestricted
else:
    qs = qs.none()  # fail closed on an unrecognized subject type
```

`patient_id` as a *query parameter* is never trusted for authorization: a patient sending `?patient_id=<someone else>` still receives only their own rows, because narrowing happens first. On write, `patient_id` and `prescriber_id` are derived from the token and marked read-only in the serializer for non-service callers.

**Layer 3 — 404 rather than 403 on detail routes.** A 403 confirms that a record exists. With UUID primary keys, a 404 leaks nothing. Layer 2 produces this behavior for free, since an out-of-scope object is simply not in the queryset.

**Layer 4 — Action-level rules.** Patients are read-only. Only a prescriber or service principal may create, update, fill, or cancel, and only against their own prescriptions. These live in the model/serializer transition logic rather than in view permissions, so they hold no matter which endpoint is the entry point.

## 4. Audit logging

Independent of the above and arguably more urgent: every read and write of a prescription appends an audit record capturing principal, subject type, prescription ID, action, timestamp, request ID, and source IP. Who-saw-what is the first question asked after any incident involving health data, and it cannot be reconstructed after the fact. Append-only table, never exposed through the public API.

## 5. Supporting hardening

- **No PHI in logs or URLs.** Log the principal's subject ID; never medication names, patient identifiers, or free-text instructions.
- **TLS terminated upstream**, with `SECURE_PROXY_SSL_HEADER` configured so Django recognizes the scheme.
- **Throttling by principal** rather than by IP once identity is trustworthy.
- **Token replay window** kept small: short-lived access tokens (5–15 min) with refresh handled by the IdP.

## 6. Migration path from Basic Auth

Both authentication classes can run simultaneously, which makes this a non-breaking rollout:

1. Add `JWTAuthentication` alongside `BasicAuthentication` in `DEFAULT_AUTHENTICATION_CLASSES`. Existing callers keep working.
2. Introduce `Principal`, and synthesize one for Basic-authenticated users (`subject_type="admin"`) so downstream code has a single shape to consume.
3. Add layers 1–4 behind a feature flag, defaulting off.
4. Migrate clients to bearer tokens; monitor the audit log for remaining Basic Auth usage.
5. Turn the flag on, then remove `BasicAuthentication` and the dependency on `auth.User` for API access.

## 7. Constraints on the initial build

So that the migration above stays cheap, the initial implementation should:

- Read the caller from a single accessor rather than touching `request.user` throughout view and serializer code, so introducing `Principal` is one edit.
- Keep `patient_id` and `prescriber_id` as writable serializer fields **with a comment** marking them as fields that become token-derived and read-only.
- Route all list queries through `get_queryset()` rather than querying `Prescription.objects` directly in view bodies, so layer 2 has exactly one insertion point.
- Avoid foreign keys from `Prescription` to `auth.User`. The Basic Auth user is an operator credential, not a domain actor.
- Add the request-id middleware from day one, since audit records reference it.

## 8. Rough sizing

Authentication (§2) is roughly a day once the IdP question is answered. Authorization (§3) is two to three days including tests, most of it in the test matrix — every layer needs both a positive and a negative case per subject type. Audit logging (§4) is about a day. The migration (§6) is gradual and mostly operational.
