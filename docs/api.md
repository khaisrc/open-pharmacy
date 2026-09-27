# The API

Everything is under `/api/v1/`. The OpenAPI schema is at `/api/v1/schema/`,
Swagger UI at `/api/v1/docs/`, ReDoc at `/api/v1/redoc/`.

## Authentication

Every endpoint except `/api/v1/health/` and the docs takes
`Authorization: Bearer <access token>` issued by the identity provider
(`AUTH_ISSUER`) for this API (`AUTH_AUDIENCE`). The token is verified
against the provider's published keys -- signature, issuer, audience,
expiry -- and read into the principal: who (`sub`, the provider's user id),
their email, name, roles, pharmacy and board registration, and the scopes
their roles grant -- the identity claims contract, which the provider is
configured to emit (see [deployment.md](deployment.md) and the provider's
page under [identity-providers/](identity-providers/)). No database lookup
and no call to the provider per request; the token is the identity.

The first request from a user creates their [staff record](staff.md) from
the token; every later token must describe the same person.

HTTP Basic against Django's user table, and the session the admin uses,
still work during the transition. A login holds every scope, is recorded in
the change history as a login rather than a person, and cannot start or
verify a fill -- those name a staff member (403 `staff_identity_required`).

Locally, `python manage.py mint_token` signs a token the API accepts without
a running provider; see [development.md](development.md).

### Scopes

An action needs one of the scopes listed for it; a token without it is a 403
`insufficient_scope` naming what was required. `read` covers list, detail and
the nested child lists (a nested list needs the child's read scope); `write`
covers create, update, delete and the resource's actions.

| Resource | Scopes |
|---|---|
| prescriptions | `prescriptions:read`, `prescriptions:create`, `prescriptions:update` (PATCH, PUT, delete, `cancel/`), `prescriptions:update_unfilled` (the same, only while no fill has been started) |
| fills | `fills:read`, `fills:write` (create, PATCH, `unverify/`, `complete/`, `cancel/`), `fills:verify` |
| patients | `patients:read`, `patients:write` |
| prescribers | `prescribers:read`, `prescribers:write` |
| medications | `medications:read`, `medications:write` |
| pharmacies | `pharmacies:read`, `pharmacies:write`, `pharmacies:onboard` (`rx_number_seed`, `step`) |
| locations | `locations:read`, `locations:write` |
| addresses | `addresses:read`, `addresses:write` |
| staff | `staff:read`, `staff:write` |

Roles at the provider grant them: `admin` and `pharmacist` hold all twenty-two;
`certified_technician`, `technician` and `intern` hold every `:read` plus
`prescriptions:create`, `prescriptions:update_unfilled`, `fills:write`,
`patients:write` and `addresses:write`. A technician may therefore enter a
prescription and change it until a fill is started (a cancelled fill does
not count), after which the change is a 403 with
`details.reason = "a fill has been started"`; verifying a fill and onboarding
a pharmacy are a pharmacist's and an admin's. An intern's token must also
name a supervising pharmacist or technician (`supervising_user_id`), or it
is refused (401 `supervisor_required`).

## Resources

| Path | What it is | Notes |
|---|---|---|
| `/prescriptions/` | A medication order for a patient | `cancel/` action; carries the Rx number and every quantity; see [prescriptions.md](prescriptions.md) |
| `/prescriptions/{id}/fills/` | The dispensings of a prescription | list and create only |
| `/fills/{id}/` | One dispensing | `GET`, `PATCH`; `pending → verified → completed` through `verify/`, `unverify/`, `complete/`, `cancel/`; no collection at `/fills/` |
| `/patients/` | Who a prescription is written for | `{id}/prescriptions/` |
| `/prescribers/` | Who wrote it, keyed by NPI | `{id}/prescriptions/` |
| `/medications/` | What it names, keyed by NDC | NDC is normalized to 11 digits on write |
| `/pharmacies/` | The organization, independent or chain | `location_count` is computed; `rx_number_seed` and `step` on create or update onboard it, read back as `rx_counter` |
| `/pharmacies/{id}/locations/` | A dispensing site, keyed by NCPDP ID | `{id}/prescriptions/`; carries the pharmacist-in-charge |
| `/pharmacies/{id}/staff/` | A credentialed person the pharmacy employs | pharmacist, certified technician, technician or intern; keyed by state registration; see [staff.md](staff.md) |
| `/addresses/` | Postal addresses the records above share | |

All resources support `GET` (list and detail), `POST`, `PATCH`, `PUT` and
`DELETE`, except fills, which have no `PUT`. Writes take flat ids
(`patient_id`, `address_id`); reads return the nested object, so rendering a
prescription is one request.

Every write is recorded column by column in a change-history table (old
value, new value, the staff member the token named or the login, request
path and id), which has no API yet.

## Sub-resources

Locations and staff are sub-resources of the pharmacy that owns them: the
pharmacy comes from the path, never from the body, and a record cannot be
moved to another pharmacy. An unknown pharmacy is a 404 on every nested
route. Prescriptions still reference a location by flat `pharmacy_location_id`.

## Soft delete

`DELETE` is a soft delete: the row stays in the database, leaves the API, and
its unique identifier (NPI, NDC, NCPDP ID) becomes reusable. A delete that
would orphan live records is refused with a 409.

## Lists

Every list is paginated: `?page=` and `?page_size=` (default 25, max 100), with
`count`, `next`, `previous` and `results` in the body.

| Resource | Filters | `?search=` | `?ordering=` |
|---|---|---|---|
| prescriptions | `patient`, `prescriber`, `medication`, `pharmacy_location`, `pharmacy`, `status`, `written_after`, `written_before` | medication name, patient last name | `written_at`, `created_at` |
| prescriptions/{id}/fills | `status`, `operator`, `verified_by` | | `sequence`, `created_at` |
| patients | `last_name`, `dob`, `sms_opt_in`, `gender` | first and last name | `created_at`, `last_name`, `dob` |
| prescribers | `npi`, `practitioner_type` | name, NPI | `created_at`, `last_name` |
| medications | `form` | | |
| pharmacies | `name` | name, legal name | `created_at`, `name` |
| pharmacies/{id}/locations | `location_type`, `is_24_hour`, `ncpdp_id`, `state`, `city`, `postal_code` | store number, NCPDP ID | `created_at`, `store_number` |
| pharmacies/{id}/staff | `role`, `is_active`, `registration_state`, `registration_number`, `certification_body`, `npi` | name, registration number, NPI | `created_at`, `last_name` |
| addresses | `state`, `city`, `postal_code` | | |

The nested prescription routes (`/patients/{id}/prescriptions/` and so on)
are the prescription list with one filter pinned, so the same parameters
work there. Prefix an ordering field with `-` for descending.

## The admin

`/admin/` is Django's admin over every model, for the superuser `seed_local`
creates (`admin` / `admin` locally). It is one more adapter over the same
layers as the API: reads come from the repositories and every write goes
through the services, so an edit made there is audited in the change
history (attributed to the login, with no staff member), a prescription
added there gets its Rx number from the pharmacy's counter, and a write the
service refuses comes back as a message on the page rather than a change.
What the admin cannot do is what has no login-shaped actor: a fill is
started and verified only through the API, by a bearer token that names a
staff member; the admin shows fills, and edits a pending one's quantity and
days' supply exactly as `PATCH` would. The change history is read-only
there, and `RxCounter` is where a pharmacy is onboarded.
