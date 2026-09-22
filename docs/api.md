# The API

Everything is under `/api/v1/`. The OpenAPI schema is at `/api/v1/schema/`,
Swagger UI at `/api/v1/docs/`, ReDoc at `/api/v1/redoc/`.

## Authentication

HTTP Basic against Django's user table. Every endpoint requires it except
`/api/v1/health/` and the docs. There is **no authorization yet**: any
authenticated user can read and write any record, including patient PHI. The
JWT and role design that replaces this is in [`plans/auth/plan.md`](../plans/auth/plan.md).

## Resources

| Path | What it is | Notes |
|---|---|---|
| `/prescriptions/` | A medication order for a patient | `cancel/` action; carries the Rx number and every quantity; see [prescriptions.md](prescriptions.md) |
| `/prescriptions/{id}/fills/` | The dispensings of a prescription | list and create only |
| `/fills/{id}/` | One dispensing | `GET`, `PATCH`; `pending → verified → completed` through `verify/`, `unverify/`, `complete/`, `cancel/`; no collection at `/fills/` |
| `/patients/` | Who a prescription is written for | `{id}/prescriptions/` |
| `/prescribers/` | Who wrote it, keyed by NPI | `{id}/prescriptions/` |
| `/medications/` | What it names, keyed by NDC | NDC is normalized to 11 digits on write |
| `/pharmacies/` | The organization, independent or chain | `location_count` is computed |
| `/pharmacies/{id}/locations/` | A dispensing site, keyed by NCPDP ID | `{id}/prescriptions/`; carries the pharmacist-in-charge |
| `/pharmacies/{id}/staff/` | A credentialed person the pharmacy employs | pharmacist, certified technician, technician or intern; keyed by state registration; see [staff.md](staff.md) |
| `/addresses/` | Postal addresses the records above share | |

All resources support `GET` (list and detail), `POST`, `PATCH`, `PUT` and
`DELETE`, except fills, which have no `PUT`. Writes take flat ids
(`patient_id`, `address_id`); reads return the nested object, so rendering a
prescription is one request.

Any write may carry an `X-Staff-Id` header naming the staff member performing
it; a fill needs one. See [prescriptions.md](prescriptions.md). Every write
is recorded column by column in a change-history table (old value, new value,
login, staff member, request path and id), which has no API yet.

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
started and moved through its statuses only through the API, with
`X-Staff-Id`; the admin shows fills, and edits a pending one's quantity and
days' supply exactly as `PATCH` would. The change history is read-only
there, and `RxCounter` is where a pharmacy is onboarded.
