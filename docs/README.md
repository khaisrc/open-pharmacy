# Documentation

| Document | What it covers |
|---|---|
| [api.md](api.md) | Base path, authentication, the resource table, sub-resources, soft delete, and how lists paginate, filter, search and order |
| [staff.md](staff.md) | Staff records, the two credentials they carry, `is_active`, and a location's pharmacist-in-charge |
| [prescriptions.md](prescriptions.md) | The prescription lifecycle, the derived quantities, the fill workflow, `X-Staff-Id`, Rx numbers, the intake source, and the home location |
| [errors.md](errors.md) | The error envelope and every code the API returns |
| [development.md](development.md) | Running the suite and the tooling, plus the repository layout |
| [deployment.md](deployment.md) | Settings modules, the configuration contract, migrations, onboarding a pharmacy, and serving |

Design documents live in [`plans/`](../plans). They record the reasoning behind
a change at the time it was made; where a plan and the code differ, the code is
the source of truth. Conventions for people and agents working in the
repository are in [`AGENTS.md`](../AGENTS.md).
