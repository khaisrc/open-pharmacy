# open-pharmacy

A reference implementation of the backend for a US pharmacy. It is a REST API
over prescriptions and the records they reference: patients, prescribers,
medications, pharmacies, their dispensing locations, the staff they employ and
addresses. Django + Django REST Framework on PostgreSQL 18, packaged as a
single container.

Being a reference implementation means the domain is modelled the way the US
pharmacy ecosystem identifies things, not the way a generic CRUD app would:

- Prescribers and dispensing sites carry an **NPI**, validated against the
  check-digit scheme, so a typo cannot propagate into a claim.
- Medications are keyed by **NDC**, accepted in any of the four segmented
  forms and normalized to the 11-digit HIPAA layout.
- Dispensing sites are keyed by **NCPDP ID** (the pharmacy number on a claim)
  and may carry a **DEA number**; store numbers are unique per chain, not
  globally.
- Staff are keyed by the **state board registration** a board can look up, and
  a location records its **pharmacist-in-charge**.
- Addresses are US postal addresses with USPS state codes and 5 or 9 digit ZIP
  codes; phone numbers are E.164.
- The deployment serves **one pharmacy**: a prescription written without a
  dispensing site is routed to the configured home location.

It is a starting point to build on rather than a product. Authorization, an
external identity provider, dispense events and allergies are among the things
deliberately left for later; see [`plans/`](plans).

## Quickstart

The only prerequisite is Docker with Compose v2. No Python, no PostgreSQL on the
host.

```bash
git clone <repo> && cd open-pharmacy
docker compose up
```

That starts PostgreSQL, builds the image, applies migrations, seeds a superuser
(`admin` / `admin`) plus demo data, and serves the API on port 8000.

```bash
curl -u admin:admin http://localhost:8000/api/v1/prescriptions/
open http://localhost:8000/api/v1/docs/          # Swagger UI
open http://localhost:8000/admin/                # Django admin, same admin / admin
```

The identity server is not part of that default stack -- the API serves
without it. Add it when you want the console or a real sign-in:

```bash
docker compose --profile auth up                 # or: make run
open http://localhost:8442/console/              # identity server console, admin / Passw0rd!
```

Port 8000 taken? Set `WEB_PORT` in the shell (Compose reads the port mapping
before any env file):

```bash
WEB_PORT=9000 docker compose up
```

## Documentation

Everything is under `/api/v1/`, with the OpenAPI schema at `/api/v1/schema/`
and Swagger UI at `/api/v1/docs/`. The written documentation lives in
[`docs/`](docs):

- [The API](docs/api.md) -- authentication, the resource table, sub-resources,
  soft delete, and how lists paginate, filter, search and order.
- [Staff](docs/staff.md) -- staff records, their credentials, and a location's
  pharmacist-in-charge.
- [Prescriptions](docs/prescriptions.md) -- the lifecycle, the fill
  workflow, Rx numbers, and the home location.
- [Errors](docs/errors.md) -- the error envelope and every code the API returns.
- [Development](docs/development.md) -- running the suite and the tooling, plus
  the repository layout.
- [Configuration and deployment](docs/deployment.md) -- settings modules, the
  environment contract, migrations and serving.

Design documents are in [`plans/`](plans); where a plan and the code differ, the
code is the source of truth. Conventions for people and agents working in this
repository are in [`AGENTS.md`](AGENTS.md).
